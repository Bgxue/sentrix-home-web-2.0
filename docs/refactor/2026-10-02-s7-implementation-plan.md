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

**归属已定（application 层）**；待定的是**要不要在 S7a 做**，取决于 1.0 里它是否真的可达。
核查中，结论落地后回填这里。

### 3.2 S7a 明确不做的

| 项 | 理由 |
|---|---|
| `entity-groups` 的两处关系字段 | 1.0 前端不读（§2.4） |
| 实体合并时的关系边迁移 | 2.0 无实体级合并，无落点（§2.5） |
| `get_core_memory` 工具 | 属 core memory 子系统；2.0 未建 `agent_core_memory_*` 表 |
| 关系假设 / 角色假设两条线 | `frozen.md` 冻结，且 1.0 前端本就是死的 |

---

## 4. 施工记录

（随做随记）

---

## 8. 当前进度与下一步

**当前：S7a 动工前，核查已完成，任务表已定（T201–T206）。**

**第一件事：T201**（`GET /entities/{id}` 加 `relationships[]`）。

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
