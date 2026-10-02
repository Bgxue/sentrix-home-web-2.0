# S7 实施计划（家庭关系与关系图谱）

> **本文件最后一节（§8）写「跑到哪了、下一步是什么」。新会话先读它。**
> §8 与正文冲突时，以 §8 为准——正文是动工前写的，§8 随每次提交更新。

---

## 1. 裁决记录

| # | 事项 | 日期 | 裁决 |
|---|---|---|---|
| ① | S7 怎么切 | 2026-10-02 | 切三段 **S7a → S7b → S7c**，按依赖顺序做 |
| ② | `FamilyGraphService`（导入后自动跑的纯文本模型推断）搬不搬 | 2026-10-02 | **搬，照 1.0 接进导入管线** |

### 裁决①的分段与依赖

| 段 | 内容 | 依赖 | 规模 |
|---|---|---|---|
| **S7a** | 通用 `relationships` 的读写打通 + 了结 S6 遗留三条 | 无 | 中 |
| **S7b** | `family_*` 家庭图谱全套（6 张表 + 仓储 + 读写路由 + `FamilyGraphService` + 导入管线触发） | 无 | 大 |
| **S7c** | 助手引用（`get_person_profile` 扩到 membership + `relationships[]`） | **依赖 S7b** | 小 |

**为什么 S7c 必须在 S7b 之后**：1.0 的 `person_profile_digest`（`db.py:2840`）读的是
`family_person_memberships` + `family_relationships` + `family_person_portraits`，
**不是**通用 `relationships` 表。所以助手引用的是 S7b 的数据，顺序不能换。

---

## 2. 依赖核查结论（2026-10-02，动工前）

### 2.1 被核查推翻的表述

| 原表述 | 实测 | 证据 |
|---|---|---|
| 设计 §8「S7 = family graph + runs + relationships + membership + 关系确认」——读起来是一套 | **1.0 是两套并存的存储，前端各用一套** | §2.2 |
| S6 §8.12「`relationships` 表归 S7」——像表还没建 | **表已建，列比 1.0 还全**（`inverse_predicate` 直接进建表语句） | `migrations/0006_identity.sql:177-194` |
| S6 §8.12「读路径是 S7」 | **仓储层不欠**：`list_for_entity` 已存在且已被生产调用 | `application/identity/run_insights.py:179` |
| 初判「前端不调 `POST /family-graph/runs` → runs 是死路」 | **自动那条是活的**：导入批次收尾触发 | `pipeline.py:733` → `:737` |

### 2.2 1.0 的两套关系存储（**不能只取其一**）

| | 通用 `relationships` | 受约束 `family_relationships` |
|---|---|---|
| 建表 | `db.py:688-700` | `db.py:720-739` |
| 谓词 | 自由文本，**无白名单** | `RELATION_INVERSES` 白名单（`family_graph.py:1-38`，38 条正反配对） |
| 写入者 | `maintain_observation_entities`（`db.py:3943/3948`）、事件共现（`:2526`）、`confirm_relationship_hypothesis`（`:6525`） | `set_family_relationship`（`:5808`，**一次落两行**正+反）、`FamilyGraphService` |
| 机制 | 同三元组合并 evidence + `MAX(confidence)` + `revision+1` | `superseded` 链 + `locked` + `family_relationship_blocks`（防模型恢复用户撤回） |
| HTTP | `/api/relationships*`（`app.py:2098-2182`） | `/api/family-graph/*`（`app.py:1648-1733`） |
| 1.0 前端用在 | 关系图弹窗、**实体详情**（`app.js:1031/1039`）、实体列表计数（`app.js:678`） | 「家庭人物」页（`people_graph.js`） |
| 2.0 现状 | **表与仓储均已在**，无 HTTP 端点、无写入者、0 行 | **四张表一张都没建** |

**两套词表不一致且不许统一**：`RELATION_INVERSES`（细粒度，「孩子」的逆是「父亲/母亲」）
vs `INVERSE_RELATION`（粗粒度，含「同事/老师/照护者」，「孩子」的逆是「父母」）。

### 2.3 自动推断是活的（裁决②的依据）

`pipeline.py:733`（`finish_ingest_batch` 收尾）在 `scope_id` 存在时调
`_trigger_family_analysis`（`:737`）：用 **event watermark** 门控
（`event_count <= watermark` 就不跑），输入是**已落库的语义文本**（`images=None`，
`input_mode: "semantic_text_only"`），起后台线程跑 `FamilyGraphService.run`。

前端不调的只是**手动触发路由** `POST /family-graph/runs`（`api.js:110
startFamilyGraphRun` 全仓无调用）。自动那条是
`family_person_memberships(source='model')` / `family_relationships(source='model')` /
`family_person_portraits` 的**唯一生产者**。

反面对照：`_maybe_trigger_person_insight`（VLM 那条，写 hypotheses）需要
`SENTRIX_PERSON_INSIGHT_SCOPES` **环境变量白名单**才跑——所以 1.0 前端不读
`relationship_hypotheses` 是因为它**默认根本不执行**，裁决① 判死它站得住。

### 2.4 S6 遗留三条：前端承重面

| 遗留项 | 1.0 前端读吗 | 证据 |
|---|---|---|
| `GET /entities/{id}` 的 `relationships[]` | **读** | `app.js:1031`、`:1039` |
| `GET /entities` 的 `relationship_count` | **读** | `app.js:678` |
| `entity-groups` 的合并 `relationships` | 不读 | — |
| `entity-groups` 的 `relationship_count` | 不读 | 全 `src/` 仅 `app.js:678` 一处命中 |

→ S7a 做前两条（承重），后两条**不搬**，理由记档。

### 2.5 S7a 侧的三处新缺口（原计划没预料到）

1. **事件共现整条缺失。** `ConfirmedPersonEvents`（`assign_events.py:359`）docstring
   `:370-374` 原文写着延期理由是「**2.0 没有任何地方读 `relationships`**（那是 S6 的实体页），
   写进去就是一张没有读者的表」。**S7a 自己就是把读者造出来的那一步**——延期理由被
   现实追上，与 `check_capability_map.py` 的反向规则同形。
   → **任务顺序由此确定：先造读者（T201/T202），再补写入者（T204/T205）。**
2. **`semantic_claims` 没有任何写路径。** 端口 docstring 自述「只读面」（S6 T130）。
   见 §3 的 T206。
3. **2.0 没有实体级合并。** 1.0 `db.py:4369` 的「合并时迁移关系边」在 2.0 **无落点**
   （实体合并候选是冻结项）→ 记为无落点，不做。

### 2.6 已经对齐、不用动的

- **`inverse_predicate` 恒 NULL 是对齐的**：1.0 的 `create_relationship` INSERT 语句里
  根本没有这一列，只有冻结的 `confirm_relationship_hypothesis` 才回填。
- **`list_for_entity` 不过滤 status 是对齐的**：1.0 `db.py:5590` 的实体分支连
  `retracted` 都返回；只有 `list_person_relationships`（`db.py:5601`）有
  `status != 'retracted'`。2.0 两处均已对齐。

---

## 3. S7a 任务表

**顺序即依赖顺序。T201/T202 先造读者，T204/T205 再补写入者。**

| # | 任务 | 落点 | 钉住的行为 |
|---|---|---|---|
| **T201** | `GET /entities/{id}` 加 `relationships[]` | `application/identity/read_entity_detail.py`、`api/schemas/identity.py`、`api/routes/identity.py` | `list_for_entity` **无 status 过滤**、`ORDER BY updated_at DESC`、行带 `subject_name`/`object_name`（已有，不新增查询） |
| **T202** | `GET /entities` 加 `relationship_count` | `domain/identity/models.py`、`repositories/entities.py`、`ports.py`、`api/schemas/identity.py`、`api/routes/identity.py` | 1.0 是**两条 `COUNT(*)` 相加**：同一条关系给**两端各计 1**，`status != 'retracted'`。**要翻转** `tests/contract/test_identity_routes.py:1080 test_the_catalogue_has_no_relationship_count_key` |
| **T203** | `/api/relationships` 五条路由 | 新建 `api/routes/relationships.py` + `api/schemas/relationships.py`；`main.py` 注册 | GET `{nodes,edges,relationships}` 含 `kind=="person"` 分支；POST 缺字段 400、默认 `confidence=0.5`/`status="pending"`；batch 的 `entity_by_name` → `canonical_name`/`family_role` 两级解析、逐条 error、错误串 `[:200]`；confirm/retract 404 |
| **T204** | 写入者：「出现在」/「记录于」 | `application/media/maintain_entities.py`、`api/routes/media.py` | `(entity,"出现在",place)` / `(entity,"记录于",time)`；**自环保护** `entity_id != place/time_entity_id`；`evidence_ids = [observation_id, event_id] if event_id else [observation_id]`；`confidence` 传 observation 自己的。**要翻转** `tests/unit/test_maintain_entities.py:393 test_it_does_not_create_relationships` |
| **T205** | 写入者：「共同出现」 | `application/memories/assign_events.py`（`ConfirmedPersonEvents.refresh_participants`，`:399`；在 `:426` upsert 之后） | 同 event 内 `role='visible_subject'` 且 confirmed 的参与者两两组合；`sorted()` 归一；N = 同时含两人且均 `visible_subject` 的 **DISTINCT event 数**（跨全部 event）；`confidence = min(0.9, 0.45 + 0.1*N)`；`status='pending'`。**2.0 无 `person_status` 列 → 改 JOIN `entities.status`** |
| **T206** | claim 写入（`maintain_relationship_claim`） | 待 §3.1 的核查结论定 | 见 §3.1 |

### 3.1 T206 的待定项

用户 2026-09-28 **裁决 A**（`tests/integration/test_relationship_repository.py:1-9` 原文）：

> **这一层只写关系行**：1.0 的 `create_relationship` 还会在 `status == 'active'` 时调
> `maintain_relationship_claim` 往事实层写，2.0 把那一半留给 **application**——
> `infrastructure-purity` 契约不允许基础设施层直接调用例。
> **且不回滚**：claim 维护失败时这一行留着。

**归属已定（application 层）**；**可达性核查结论：可达，所以做了。**

证据（2026-10-02 核查）：1.0 前端**三处**建关系传的都是 `status: "active"`
（`src/app.js:1732` / `:1765` / `:1831`），保存后的提示语是「已保存家庭关系：X，
**并写入长期记忆**」——所以这不是边角路径，是主路径。photobench 与共现那两条路
传的都是 `pending`，触达不到。关系假设确认（`db.py:6525`）也传 `active`，
但那条线已随裁决① 冻结。

**范围收窄**：`maintain_semantic_claim` 是通用函数，1.0 有六个维度
（relationship + 画像的 activity / place / capture / clothing / identity）；
2.0 只有 `dimension="relationship"` 一个调用者可达——画像那五个的写入者
`rebuild_person_memory` 在 2.0 没有，`person_event_memory` / `person_patterns`
两张表也没建。**到达不了的分支只省了一处**：`dimension == "clothing"` 的
`normalize_clothing` 归一。

落实为 T206（`0ffb1c6`）：仓储加 `maintain_claim`（只写行），编排在
`application/identity/create_relationship.py`。

### 3.2 S7a 明确不做的

| 项 | 理由 |
|---|---|
| `entity-groups` 的两处关系字段 | 1.0 前端不读（§2.4） |
| 实体合并时的关系边迁移 | 2.0 无实体级合并，无落点（§2.5） |
| `get_core_memory` 工具 | 属 core memory 子系统；2.0 未建 `agent_core_memory_*` 表 |
| 关系假设 / 角色假设两条线 | `frozen.md` 冻结，且 1.0 前端本就是死的 |

---

## 4. 施工记录

### 4.1 S7a（T201–T206）

| 任务 | 提交 | 关键判断 |
|---|---|---|
| T201 `GET /entities/{id}` 加 `relationships[]` | `fdaebcd` | 三处照搬：两端都算、**不过滤 status**（`retracted` 也返回）、`updated_at DESC`。1.0 这个端点调 `list_relationships(entity_id)` **不带 scope**，照搬不带。**翻转** S6 的 `test_the_detail_has_no_relationships_key` |
| T202 `GET /entities` 加 `relationship_count` | `6a8bf5d` | 1.0 是**两条 COUNT 相加**，所以一条关系给两端各记 1；只排 `retracted`（与 `cluster_count` 同口径、与 `photo_count` 相反）。**翻转** S6 的 `test_the_catalogue_has_no_relationship_count_key` |
| T203 `/api/relationships` 五条路由 + 批量导入 | `699defb` | 新增端口方法 `list_relationships(scope_id)`——**1.0 有、2.0 漏掉的形状**（1.0 那个函数两条分支只搬了一条）。batch 走用例 `ImportRelationships`。**发现两处口径差异**：GET 两条分支状态口径相反；写侧三端点 1.0 投影各不相同而前端一个都不读 |
| T204 补回「出现在」/「记录于」 | `0d16cba` | T127 有意收窄的那一半。自环**挡在调用点**（`create_relationship` 自己不做自环校验）。**翻转** S6 的 `test_it_does_not_create_relationships` |
| T205 事件共现「共同出现」 | `81dfa18` | 延期理由（「没有读者」）被 T201–T203 自己消掉。**1.0 那条自连接 SQL** 落在 `cooccurrence_event_count`。恒 `pending`——1.0 原话 *Suggest co-occurrence only* |
| T206 关系确认「写入长期记忆」 | `0ffb1c6` | `semantic_claims` 的**第一个写入者**。按裁决 A 分两半：仓储只写行，编排在 application。两条反直觉照搬：`confirm` **不写** claim；`relationship` **不在多值维度**里 |

**本段的三次「延期理由被自己追上」**（同一个形状，值得单独记）：

1. T127 的「实体本身不依赖关系就有用」→ 被 T201/T202 消掉；
2. `ConfirmedPersonEvents` 的「2.0 没有任何地方读 relationships」→ 被 T201–T203 消掉；
3. `test_*_has_no_relationship_*_key` 两条测试的「没有读路径，恒空会被误读」→ 同上。

这三条与 S6 的 `check_capability_map.py` 反向规则是同一条纪律的**人工版**：
**登记/注释里写的延期理由，要在条件成立时被主动回来删掉**，不能只靠门禁。

**变异测试的一次「假存活」**：T206 的 M6（合并时不取 `MAX(confidence)`）第一版存活，
查出原因是**经路由走时关系行自身的置信度已经单调**（`create` 取 `MAX`），喂进 claim
的值只增不减。但它**不是等价变异**——claim 的合并键用**对方的名字**，两个同名实体会
并到一行，那时后写的低置信度就会撞上先写的高置信度。补了一条同名实体用例才杀掉。
（S6 记过三种成因：字节码缓存 / 指错目标用例 / 真等价；这次是**第四种**：
**变异被上游不变量掩盖**。）

**另两处顺手查出的旧账**：

- `EventRepository.dissolve` 的登记引错了出处——它写着「s5-deviations 第 15/35 条记
  随 S7」，而那两条讲的是 `graph_violations`，与 `dissolve` 无关。已改正。
- s5-deviations 第 15/35 条（`graph_violations` 算完即弃）**了结**：随
  `person_graph.py` 一起判死（裁决① 判死的是整个文件）。

---

## 8. 当前进度与下一步

### 8.1 S7a 完成（2026-10-02）

**S7a 全部了结：T201–T206 六项，六个提交**（`fdaebcd` `6a8bf5d` `699defb` `0d16cba`
`81dfa18` `0ffb1c6`），外加计划本身 `0385a45`。定点变异共 **30 处，全部被杀**
（T201 4 / T202 4 / T203 7 / T204 5 / T205 6 / T206 7，其中 T206 的 M6 是补了用例之后
才杀掉的）。

**S6 遗留三条的处置**（§2.4 的承重判断）：

| 遗留项 | 处置 |
|---|---|
| `GET /entities/{id}` 的 `relationships[]` | **做了**（T201）——1.0 前端读 |
| `GET /entities` 的 `relationship_count` | **做了**（T202）——1.0 前端读 |
| `entity-groups` 的合并 `relationships` | **不做**——1.0 前端不读 |
| `entity-groups` 的 `relationship_count` | **不做**——同上 |

**S7a 明确不做的**（§3.2）：`get_core_memory`（属 core memory 子系统，2.0 无表）、
实体合并时的关系边迁移（2.0 无实体级合并）、关系假设与角色假设（冻结）。

**S7a 的终态验证**（2026-10-02，`make ci`）：静态门禁全绿
（ruff / format / mypy 185 个文件 / arch 五契约 + 两条可达性 / check-docs 含 OpenAPI 契约），
coverage **1868 passed / 1 failed / 29 skipped**（S6 时是 1836 passed）。

**那一条 failed 是已知的模型侧 flaky，不是回归**：
`tests/e2e/test_assistant_turn.py::test_the_answer_never_leaks_internal_identifiers`
（§8.1 记着「不要修」；S6 的全量跑失败的是同一条）。`make ci` 会因此在 coverage 一步
停住、**`web-check` 不跑**——已单独补跑并通过（tsc + oxlint + vitest + vite build）。

### 8.2 下一步：S7b（家庭图谱）

**S7b 是 S7 里最大的一段**：`family_*` 六张表 + 仓储 + `RELATION_INVERSES`（38 条白名单）
+ `GET /family-graph` 与三个写路由 + block 机制 + scope merge +
**`FamilyGraphService`（导入后自动跑的纯文本模型推断）**。

**动工前照旧先做依赖核查。** S7a 这一段又出了三处「计划外」：端口漏了一个 1.0 有的
方法形状（`list_relationships`）、`semantic_claims` 完全没有写侧、写侧三个端点在 1.0
里投影各不相同。S7b 的表更多、与外键和唯一约束的关系更密，核查只会更值。

**S7b 已经知道的坑**（S7a 核查时顺手记下的）：

- `family_graph_service.py` 是**纯文本路径**（`images=None`），只有 person_insights 的
  `infer_person_graph` 才传图——两条链不能混。
- 1.0 的 `_trigger_family_analysis` 用 **event watermark** 门控，而 2.0 的
  `ingest_batches` 至今 0 行、`assets.batch_id` 全 NULL——**触发点要另找落点**。
- `family_relationships` 同无序对可以有多条 effective 行，靠 `ORDER BY locked DESC,
  revision DESC` 取用；去重靠 `set_family_relationship` 主动 supersede。
- 1.0 有两条**真死代码**：`list_effective_family_memberships`（含测试零调用）、
  `update_person_identity_state`（仅测试）。搬的时候要判。

**门禁提醒**：
- 接线后 `scripts/check_method_reachability.py` 的 `PENDING_METHODS` 里
  `RelationshipRepository.retract`（`:71`）与 `.list_person_relationships`（`:78`）
  **必须删**——反向规则会报红要求删。
- 新建 `api/routes/relationships.py` / `api/schemas/relationships.py` 要同时更新
  `docs/architecture/module-map.md`（含包级 `__init__.py` 依赖边与 `main.py` 的列）。
- `packages/contracts/openapi/sentrix-v1.yaml` 用 `$(PY) scripts/export_openapi.py` 重生成。
- **变异脚本必须关字节码缓存**（`PYTHONDONTWRITEBYTECODE=1`），S6 踩过一次。

**S6 教训照用**：动工前先核依赖，不要照任务表的「依赖」栏直接动手——
S6 **五次**任务定义被核查推翻，三次是汇总性描述错了。
