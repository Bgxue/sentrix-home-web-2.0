# S5 违背 / 偏离审计（人脸与身份层 · 收尾与画像）

审计对象：`docs/refactor/2026-09-23-s5-implementation-plan.md`。
基线：1.0 提交 `2d2cee7c`（`behaviour-baseline.md` 钉的那一个），
实读的是工作树 HEAD `fba87be9`（两者在人脸与身份层无差异——`git log` 显示这两条提交
都只动 photobench 遥测）。2.0 起点提交 `f663522`。

**这份文档有两层。** §1–§2 是第一轮**规划**的产物：那时只有 Task 69 落地，
所以那些条目的「代价 / 现状」写的是**计划形态**。§2b / §2c / §2d 是**第二轮的实施记录**：
用户裁决（§2c.1）、五条顾虑的处置（§2c.3 / §2c.4）、Task 69/70/71 实测带出来的东西
（§2b / §2d）。**已落地的部分与未落地的部分在同一份文档里是分开写的**——
不把「计划里打算这么做」与「实际观察到是这样」混成一句话。

---

## 0. 一句话摘要

S5 的 33 条移植项里，**27 条原样照搬**，**6 条要改**（其中 4 条是修 1.0 的缺陷、
2 条是 2.0 分层的结构性改法）；另有 **8 条明确不移植**（3 条是 benchmark 专属或
benchmark 泄漏、3 条是死代码、2 条是遗留表）；**7 条需要用户裁决**（1.0 没有对应实现，
或与 `frozen.md` 冲突）。

**没有任何一条偏离是凭喜好的**——每条都能指到 1.0 的具体行，或者指到本仓库的一条既有契约。

---

## 1. 审计表

### 1.1 实测证据（先摆证据，再谈改不改）

| # | 事项 | 证据 | 判决 |
|---|---|---|---|
| 1 | **AdaFace 路径在 1.0 是死的** | `model_clients.py:1629` `self.identity_adapter = None`；`grep -rn "\.identity_adapter\s*="` 全仓只有这一处赋值 + `tests/test_face_clustering.py` 的 6 处测试赋值；唯一调用点 `app.py:2299` `pipeline.face.identity_adapter.embed(_crop)` → 必抛 `AttributeError` → 被上一行的裸 `except Exception: return []`（`app.py:2310`）吞掉。1.0 自己的测试：`tests/test_face_clustering.py:126` `@unittest.skip("legacy identity_adapter contract removed; identity is fixed to buffalo_l")`。 | **不移植** |
| 2 | 实际生效的身份向量是 buffalo_l | `model_clients.py:1772-1787` 与 `:1799-1840` 两个分支都把 `"embedding_model": "buffalo_l"` 写死；`_recognition_embed` 走 `buffalo_l/w600k_r50.onnx`（`:1690-1716`）。 | **按 buffalo_l 移植** |
| 3 | `FACE_EMBEDDING_MODE=adaface` 是无人读的配置 | `platform_profile.py:273` `face_embedding_is_adaface()` 全仓**零调用**；`face_embedding_mode()` 只被 `:305` 的 summary 打日志用。启动脚本 `scripts/runtime/start_sentrix_api_8091.sh` 里那一串 `ADAFACE_*` 没有任何读取者。 | **不移植**（见第 3 条裁决） |
| 4 | **全局重聚类没有生产调用者** | `recluster_faces`（`db.py:4956`）的两个调用点：`scripts/benchmarks/ingest_album3_v2.py:67`、`scripts/benchmarks/ingest_household_face_benchmark.py:57`。生产导入路径（`pipeline.py:316-320` / `:443-470`）只调 `add_face_instance`。 | **证据不变，结论被裁决③改判为「搬」**——做成显式维护入口，仍不自动调用。见 §2c.1 |
| 5 | `_attach_uncertain_faces` / `_sync_face_entity_statuses` 只被重聚类调 | `db.py:5038-5039` 是它们唯一的调用点，都在 `recluster_faces` 内。 | **不移植** |
| 6 | **benchmark 空间名硬编码进生产代码** | `db.py:1313-1324` `_migrate_evaluation_spaces`：`WHERE name LIKE 'PhotoBench%' OR name LIKE 'photobench%' OR id LIKE '%_e2b' OR id LIKE 'photobench%'`。 | **不移植**（只移植 `include_in_people` 机制） |
| 7 | 又一处为 benchmark 开的旁路 | `agent_runtime/tools.py:688` 注释自陈「直接查 entities 表解析人物（**绕过 list_entities 的 include_in_people 过滤，benchmark scope 也能用**）」。 | **不移植** |
| 8 | `_seed_face_detect` 第三级是死路径 | `app.py:2296-2310` 的整图 embedding 兜底调 `identity_adapter.embed`，而 `identity_adapter` 恒 `None`（同第 1 条）→ 该级**永远返回 `[]`**。 | **修**（要么不搬这一级，要么把它接到真实的向量适配器上） |
| 9 | `POST /api/people/seed` 必崩 | `app.py:2349` 的返回语句用了 `m_role`；`grep -n "m_role" app.py` 显示只有 `:2373`（在 `seed_persons_batch` 内）定义过它，`:2349` 在 `seed_person_identity` 里。走到 return 即 `NameError`。 | **修** |
| 10 | 画像的引用校验有白名单后门 | `person_portraits.py:119-126` `_evidence_ref_valid`：`kind == "person_moment"` 才做存在性比对；`kind in ("relationship", "semantic_claim", "appearance")` **直接 `return True`**。 | **修**（见第 2 节逐条） |
| 11 | 确定性兜底画像通不过自己的校验（实跑验证过） | 在 153 上直接跑 `deterministic_portrait` 的样本：有证据时 41 汉字、无证据时 35 汉字，而 `validate_portrait`（`:133`）要求 **80–500**；无证据时 themes 为 0，又违反 `:138` 的 2–6。→ `person_insights.py:238-241` 的兜底**任何情况下都会落进最后的 `continue`**，兜底机制实际不存在。 | **修** |
| 12 | 画像校验失败完全静默 | `person_insights.py:232-241` 里 `errors` 被赋值 4 次，**从不写日志、不落 `person_insight_runs.error`、不写 stats**。 | **修** |
| 13 | `person_insights` 的向量去重从未生效 | `person_insights.py:271` `select_representatives(candidates, {}, limit=12, per_event=2)` —— 第二个参数是空 dict，而去重逻辑（`:92-97`）要求 `vector` 非空。只有测试传了真向量（`tests/test_person_insights.py:122`）。 | **待裁决**（修 or 照搬） |
| 14 | 图推断的输入缺项 | `person_insights.py:309-318` 的 `graph_payload` 里 `cooccurrence: {}` / `moments: []` / `devices: {}` 恒空，而提示词（`person_graph.py:14-15`）宣称输入包含这三样。 | **待裁决** |
| 15 | `graph_violations` 算完即弃 | `person_graph.py:187` 返回，`person_insights.py:327/346` 只取 `roles` / `relationships`。 | **随 S7** |
| 16 | 两个 0.30 语义不同 | `db.py:4815` 的 `threshold=0.30` 是**余弦阈值**；`db.py:4868` 的 `if quality >= 0.30` 是**质量门槛**（低于它的已 verified 人脸不去匹配已有簇，直接建新簇）。数值相同、互不影响。 | **原样照搬，两个独立常量** |
| 17 | 模型版本字段名与语义不符 | `model_clients.py:1784/1831` 的 `embedding_version = os.getenv("FACE_MODEL_NAME", "buffalo_l")` —— 变量叫 MODEL_NAME 却存进 version。**有真实后果**：签名比对（`db.py:4876-4884`）比较 `(model_name, model_version)`，所以换模型包时旧簇会被整体跳过。 | **原样照搬 + 注释写明** |
| 18 | `person_bridge` 的真实位置 | `retrieval_indexes.py:20`（`_FIELD_TYPES` 列表）与 `:180`（`("person_bridge", "people")`）。**S4 的计划文档第 309 行把它记成 `retrieval_indexes.py:150`，那是笔误。** | 修正引用 |
| 19 | `purge_unanchored_facts` 的真实行号 | `db.py:3433-3447`。**S4 的计划文档第 313 行记成 `db.py:3439-3453`，那是笔误**（3439 已在函数体内）。 | 修正引用 |
| 20 | 画像的自动触发在默认装配下不生效 | `backend/pipeline.py:764-769`：`SENTRIX_PERSON_INSIGHT_SCOPES` 默认空串 → allowlist 为空 → `if scope_id not in allowlist: return None` → **任何 scope 都不自动跑**。全仓没有任何部署文件设置过这个变量（`grep` 只命中代码与测试）。 | **待裁决**（「增量触发」这条描述与 1.0 默认装配不符） |
| 21 | `person_insights` 的四个模块全是「有接线、默认断路」 | 唯一的生产自动调用点是 `backend/pipeline.py:725` 的 `_maybe_trigger_person_insight`，被上一行挡住；其余全是 HTTP 显式请求（`app.py:1820/1844`）或 benchmark（`scripts/benchmarks/evaluate_person_insights.py:154`）。 | **待裁决**（决定 Task 85 的验收定义） |

### 1.2 结构性偏离（无行为影响，但要登记）

| # | 1.0 的形状 | 2.0 的形状 | 为什么 |
|---|---|---|---|
| 22 | `entities` 的 `identity_state` / `role_state` / `name_state` 三列是运行期 `ALTER TABLE` 加的（`db.py:1240-1243`） | 写进 `0006_identity.sql` 的 `CREATE TABLE` | 2.0 的 schema 由编号迁移管理（`database.py` 的 docstring 明确说取代 1.0 的 `CREATE TABLE IF NOT EXISTS` + 运行期 ALTER） |
| 23 | `face_instances.cluster_id` 原本 NOT NULL，1.0 用「建新表 → `INSERT SELECT` → `DROP` → `RENAME`」重建（`db.py:1257-1311`） | 一次建对（可空） | 同上；1.0 那次重建是为了让「低置信度人脸作为无簇证据存在」成为可能，2.0 直接在 DDL 里表达 |
| 24 | `_refresh_event_participants` / `resegment_events_for_confirmed_entity` 是 `MemoryStore` 的方法，被 `confirm_face_cluster` 直接调用 | 参与者投影是仓储能力、编排在 application 用例里；`confirm` 在 Task 81 时回头接上 | 2.0 的分层契约：application 只依赖 domain 端口，仓储不反向依赖上层 |
| 25 | `confirm_face_cluster` 一个函数里做四件事（写身份 / 刷事件投影 / 重分段 / 重建人物记忆） | 拆成 Task 73（身份四件）+ Task 81（投影与重分段）+ Task 85（画像侧） | 拆的理由是**避免循环依赖**：投影要读 mentions，mentions 由 confirm 写。顺序错就是 S0/S1 那种「单独看都对、合起来循环」 |
| 26 | `MemorySpace` 在 1.0 是 `create_memory_space` 的一个函数 | 2.0 迁进 `domain/spaces/models.py` + `ports.py` | **1.0 没有对应实现**——这是设计 §2.3 的既定目标（`container.py:85-87` 的注释自己写着「待记忆空间用例落地时，把 `MemorySpace` 一并迁进 domain」）。不属于「新设计」 |
| 27 | 人脸模型是模块级单例（`app.py:61` import 期构造 `FaceAdapter()`） | 装配在 `Container.build()` | 2.0 的唯一装配点是容器；但**「每进程一份」这个后果需要设计确认**（见计划文档「顾虑」第 3 条） |

### 1.3 明确不移植（8 条）

| # | 能力 | 1.0 位置 | 理由 |
|---|---|---|---|
| 28 | AdaFace / MagFace 适配器 | `face_embeddings.py:72-221`、`platform_profile.py:257-274` | 见证据 1/3：死代码 + 700 MB PyTorch checkpoint + `PYTHONNOUSERSITE` / NVIDIA 库路径两个坑（启动脚本里为此写了两段注释） |
| 29 | ~~全局重聚类 + uncertain 认领 + 实体状态同步~~ **已由裁决③改判为移植** | `db.py:4956/5048/5118` | 见证据 4/5：调用点全在 `scripts/benchmarks/`。**这条判决作废**：用户裁决③要求它作为**显式维护入口**（Task 84），仍不自动调用。保留这一行是为了让「为什么当初判不搬、后来为什么改判」都可查 |
| 30 | benchmark 空间名黑名单 | `db.py:1313-1324` | 见证据 6：**硬编码 benchmark 内容进生产代码**，与 `frozen.md` 冻结硬编码泄漏条目是同一判据。移植的只是 `include_in_people` 这个机制 |
| 31 | 绕过 `include_in_people` 的查人物 | `agent_runtime/tools.py:688` | 见证据 7 |
| 32 | `persons` 遗留表与 `/api/persons` | `db.py:515/1327/3461/3499`、`app.py:1498/1505` | 无生产写入者（`upsert_person` 全仓零调用）；1.0 自己把 legacy person 迁进 native entity（`_migrate_legacy_persons`）；`frozen` 判据是「表 0 行」的同类 |
| 33 | `pairwise_metrics` 与聚类评估脚本 | `face_clustering.py:149-182`、`scripts/benchmarks/evaluate_lfw_clusters.py`、`scan_cluster_params.py` | 只在 benchmark / 测试里用 |
| 34 | `apply_relationship_threshold` | `person_graph.py:104-116` | 死代码：`normalize_person_graph` 不调它，全仓只有测试调 |
| 35 | `find_graph_violations` 的返回值 | `person_graph.py:87-103` → `:187` | 算完即弃（证据 15）；随 S7 |

---

## 2. 逐条证据（改的那 6 条）

### 第 1 条 · AdaFace：不移植

1.0 的 `FACE_EMBEDDING_MODE=adaface` 与 `ADAFACE_*` 四个环境变量在
`scripts/runtime/start_sentrix_api_8091.sh` 的 `exec env` 行里挂着，看起来像生产配置。
实际读取它们的是 `platform_profile.face_embedding_mode()`，而它的唯一消费者是
`summary()`（启动时打日志），`face_embedding_is_adaface()` **零调用**。

真正决定身份向量的是 `FaceAdapter._detect_retina_tiled_unlocked`：
两个返回分支都把 `"embedding_model": "buffalo_l"` 写死，向量的来源是
`_recognition_embed`（`buffalo_l/w600k_r50.onnx`）。而唯一会把 `identity_adapter`
设成 AdaFace 的地方**不存在**——`FaceAdapter.__init__` 把它设成 `None` 之后再无赋值。

判决：**2.0 按 buffalo_l 移植**。这条偏离的证据强度是「源码 + 1.0 自己的 skip 标记」，
属于「证据强」那一档。**但如果用户认为 AdaFace 是产品意图（brief 里点名了它），
那就不是移植而是「把 1.0 断掉的接线接上」——那是新功能，需要单独立项。**

### 第 2 条 · 画像引用校验的白名单后门：要修

1.0 原文（`person_portraits.py:119-126`）：

```python
def _evidence_ref_valid(pack, ref):
    kind = str(ref.get("kind") or "")
    ref_id = str(ref.get("id") or "")
    if kind == "person_moment":
        return any(moment["id"] == ref_id for moment in pack.get("moments") or [])
    if kind in ("relationship", "semantic_claim", "appearance"):
        return True
    return False
```

只有 `person_moment` 一种引用被真正核验存在性。另外三种直接放行。

**为什么这不只是「宽松」**：用户已定的第 1 条是「**强制证据引用校验**」。
一个模型只要把 `kind` 写成 `"relationship"`，就能让任意虚构的 `id` 通过校验——
而这正是这条机制存在的理由（提示词只示范了 `person_moment` 一种，模型却可能输出别的）。

**修法（建议，待裁决）**：三种 kind 各自去 `pack` 的对应层里核验存在性
（`relationships` / `semantic_claims` / `appearance` 在 `compile_portrait_evidence` 的
证据包里都已经分层放好了，见 `:43-118`）。三种都不在契约里时 → 判无效（现在是判无效，
这一点不变）。

**代价**：会更严格，1.0 benchmark 里通过的画像可能被判无效。
所以这条要**同时**改验收定义并重跑 1.0 的画像样本（`person_portraits.py` 的 8 条测试）。

### 第 3 条 · 确定性兜底画像：要修

实跑证据（在 153 上直接调 `deterministic_portrait`）：

```
TEXT: 从照片看，他常照顾孩子，他常做饭，他常散步。他从照片看常出现在这些瞬间里，让人感到熟悉和安心。
hanzi= 41 themes= 3
validate: (False, ['portrait too short: 41 hanzi < 80'])
EMPTY validate: (False, ['portrait too short: 35 hanzi < 80', 'themes must be 2-6, got 0'])
```

两条返回路径都必然违反 80 汉字下限，无证据路径还额外违反 2–6 themes。
`person_insights.py:232-241` 的三级降级因此**必然落到最后的 `continue`**：

```python
portrait = self.gamma.write_person_portrait(pack, role="writer")
ok, errors = validate_portrait(pack, portrait)
if not ok:
    portrait = self.gamma.write_person_portrait(pack, role="repair")
    ok, errors = validate_portrait(pack, portrait)
if not ok:
    portrait = deterministic_portrait(pack)
    ok, errors = validate_portrait(pack, portrait)
if not ok:
    continue            # ← 任何情况都会走到这里
```

判决：**修**。两条候选：把兜底文案写到 80 字以上；或把「兜底」定义为**不受长度校验约束**
（在 `validate_portrait` 上加一个 `source="deterministic"` 的分支）。
**建议后者**——理由是长度下限是给模型输出设的护栏，不是给确定性模板设的；
但这条**要用户点头**，因为它改的是校验器的语义。

### 第 4 条 · 校验失败静默：要修

`person_insights.py:232-241` 的 `errors` 从不被消费。1.0 的后果：
「这个人物这一轮没有画像」这件事在 `person_insight_runs` 里**没有任何痕迹**，
`status` 仍是 `completed`。

判决：**修**——把 `errors` 计数写进 run 的 `stats`（例如 `portrait_failures`），
并在 `s5-deviations.md` 实施期章节登记。理由：这是 2.0 的一条既有纪律
（`vector/ports.py` 的 `VectorIndexStatus.degraded/reason`：「降级原因必须能一路传到响应」）。

### 第 5 条 · `_seed_face_detect` 的第三级与 `m_role`：要修

两条都是**静态可判的死路/崩溃**（证据 8/9）。判决：
- 第三级整图兜底**要么不搬，要么接到真实的向量适配器**——**不要照搬一条永远返回 `[]` 的分支**
  （S4 的先例：FTS5 已实测可用，就不留一条永不执行的回退分支，理由写在计划文档第 312 行）。
- `m_role` 改成 `family_role`（形参名见 `app.py:2315-2320` 的签名）。

### 第 6 条 · `select_representatives` 的空向量字典：待裁决

`person_insights.py:271` 传 `{}`。修它要先把 `vector_by_asset` 从
`memory_vectors(space='visual', source_type='face_instance')` 读出来——
那是一个新增的读路径。**这条不修也能跑**，只是 `duplicate_threshold=0.94` 形同虚设。
**建议修**（代价小、行为可解释），但标成「待裁决」以免自作主张。

---

## 2b. Task 69 实施期实测到的偏离与发现

Task 69 已在隔离副本落地（commit `2824a5f`，`make ci` 全绿）。它带出四条要登记的东西：

| # | 事项 | 判决 |
|---|---|---|
| 36 | **`zip(...)` 加了 `strict=True`** —— 唯一一处字节级偏离 1.0 原文（`face_clustering.py:15`）。理由：ruff 的 B905 是 `make ci` 的硬门禁；长度在上一行已经判断过，行为不变。 | **改**（工具链强制，无行为影响） |
| 37 | **`docs/architecture/module-map.md` 有一行重复**：`sentrix/application/retrieval/retrieve_evidence.py` 在表里出现了**两次**（真实仓库 `bd3af31` 上第 66 / 67 行，两行**逐字节相同**）。隔离副本里 Task 69 重写表格后是 1 行。`check_module_map.py` 两种状态都报 0 问题。 | **保留修正**（生成器的必然结果；若要零无关改动可以加回去，但下次再跑生成器还会掉）。**根因见 §2c 第 40 条**——这是 checker 的一个洞，不是偶然 |
| 38 | **`continue` vs `break` 是一条杀不掉的变异** —— 32 处变异里 31 处被杀，存活的就是它（候选簇已降序排过，两者等价）。 | **登记为「未被门禁覆盖」**，不写测试硬凑 |
| 39 | **`recluster_faces` 的口径与 `FaceClusterer` 默认值不一致**：`db.py:4956` 的 `minimum_quality=0.55`，而 `face_clustering.py:67` 的类默认是 `0.30`。Task 69 只搬了 domain 的默认值，那个 `0.55` 属于被判死的 benchmark-only 调用方。 | **登记**；若第 4 条裁决改判「要全局重聚类」，这个 0.55 要一起搬 |

**同时实测确认了两件事**（不影响判决，只加证据）：
`pairwise_metrics` 与 `AdaFaceAdapter` / `MagFaceAdapter` 在全仓**确实没有生产调用者**（证据 1/33 复核通过）；
1.0 的四个纯函数里**没有任何 `os.getenv`**，所以「读 env 的落成模块级常量」这条预设
在这个范围里不适用（那些读取全在被判死的适配器里）。

---

## 2c. 2026-09-23 用户裁决与新增证据

上一轮在第 18 个任务处按约定停下报告，提出 7 条待裁决。**用户已裁决**，逐条登记如下。
这一节与 §2b 的区别：§2b 是 Task 69 实测带出来的，本节是**裁决本身**加 Task 70 实测带出来的。

### 2c.1 四条裁决（照办）

| # | 问题 | 裁决 | 落实位置 |
|---|---|---|---|
| ① | 计划做多大 | **先 S5a + S5b**（Task 69–83）；S5c（84–86）这次不做，另写 | 计划文档「任务拆分」 |
| ② | 画像三张表的冻结 | **解冻 `person_portrait_revisions` + `person_moments`，仍冻结 `person_insight_runs`** | 迁移 `0006_identity.sql`、`frozen.md`、`capability-map.jsonl`（见下「落点判断」） |
| ③ | 全局重聚类 | **搬，作为显式的维护入口**（1.0 的 `recluster_faces` 只在 benchmarks 调用；2.0 做成维护端点或命令，**不自动跑**） | 计划文档 Task 83 之后新增的维护入口一节 |
| ④ | facts 候选来源 | **按 1.0 原样：模型自报**（`analyze_image` 输出里的 `facts` 数组） | 计划文档 Task 82；**并推翻本文件第 30 条**（见下） |

**①的附带记录**：上一轮「本切片 18 个任务 > 约定的 15」这条被裁决成两段，S5a（69–76，
不需要 GPU）与 S5b（77–83，需要 GPU / ONNX）。S5c（84–86，需要 VLM）另写。

**③为什么是「搬」而不是「按证据走」**：证据面（`recluster_faces` 的生产调用者只有
`scripts/benchmarks/` 两个脚本）**没有变**，第 4 条证据仍然成立。裁决改变的是它的**结论**：
不搬的代价是「一册照片导完后，本该合并的簇只能靠用户手点合并，聚类质量差了没有补救手段」，
而这个代价用户不接受。所以 2.0 的做法是**把 1.0 那条只在 benchmark 里的路径，显式做成一个
维护入口**——不是恢复自动调用。**这是明确的用户决定，不是「按证据走」的结果**，
记在这里以免下一次审计拿第 4 条证据来反问为什么搬了。

**②的落点判断（用户把这一条留给我判断，理由如下）**：**两张解冻的表随迁移 0006 一起建**
（而不是留到 S5c 再开 0007）。判据是 0005 那条推迟理由的**性质**：

- 0005 的注释写「参与者与实体都依赖 S5 的 entities，那两张表随 S5 一起建，**不在这里留空壳**」。
  那次推迟**是被悬空的外键目标逼出来的**——`event_participants REFERENCES entities(id)` 而
  `entities` 当时根本不存在，想建也建不了。「不留空壳」是那条约束的**后果**，不是独立的规矩。
- 到 0006，`entities` / `face_clusters` / `events` / `observations` 全在同一文件里，
  **全族的外键目标都闭合了**，没有任何一条表需要推迟。
- 拆开的代价是真的：`person_moments` 引用 `events` / `observations` / `assets` /
  `face_instances`，`person_portrait_revisions` 引用 `entities`——留到 0007 意味着那份迁移要重述
  一堆跨切片的引用，且「建表」与「接功能」挤在同一次提交里，回滚粒度变粗。

**代价如实登记**：S5a 与 S5b 期间这两张表**没有任何写入者**——这正是 S4 栽过两次的形状
（「零调用的纯规则」、「零调用的 `SummarizeEvent`」）。区别写在计划文档里，并且做成了
**一条机械检查**：新模块必须能在生产装配里找到一个调用者，找不到就红。这条检查**只覆盖模块**，
不覆盖表——理由是表没有「能力」语义，它是 schema 声明，而模块有。**如果用户认为这个区分不成立，
处置是把这两张表挪到 S5c 的 0007 再从 0006 删掉**，代价是重跑一次 Task 70 的门禁。

**④推翻了本文件第 30 条。** 第 30 条原文（`s4-deviations.md`）：

> 没有 `detail` 字段）；事实由 `maintain_fact` 规则决定，**不由模型自报（S5）**。

裁决④明确要求 facts 的候选来源**按 1.0 原样由模型自报**，所以第 30 条括号里的
「（S5）」这个记法**作废**。`s4-deviations.md` 第 30 条与第 608/619 行已经按裁决就地改正并
注明依据。**注意这不改变第 30 条的主体**（2.0 的观察器确实不搬 `detail` 的四个字段），
作废的只是它对 `facts` 来源去向的记法。

### 2c.2 三条按证据定下的（上一轮已定，本轮照办）

| # | 事项 | 依据 |
|---|---|---|
| 甲 | 身份向量按实测走 **buffalo_l / `w600k_r50`（512 维）**，不接 AdaFace | 证据 1 / 2 / 3（AdaFace 在 1.0 是死代码，1.0 自己的测试标了 `skip`） |
| 乙 | 全局重聚类**不做自动调用** | 证据 4（调用点全在 benchmarks）。注意：**这条与裁决③不冲突**——裁决③要的是「显式维护入口」，不是「恢复自动调用」 |
| 丙 | `entity` 检索通道**留在 S5b**（Task 83） | `s4-implementation-plan.md:1632` 把 `entity` / `text_ann` 两路记在 S5 / S6；`person_bridge` 的写侧依赖 S5 的 mentions，没有身份层它无从填 |

### 2c.3 新增证据

| # | 事项 | 证据 | 判决 |
|---|---|---|---|
| 40 | **`check_module_map.py` 抓不到重复行，这是 checker 的一个洞** | 见「第 40 条的完整根因」一节 | **报告 + 补检查** |
| 41 | `frozen.md` 与 `capability-map.jsonl` 的冻结清单是**单向校验** | `check_capability_map.py` 只查「map 里 frozen 的条目有没有在 frozen.md 里写理由」，**不查反向**。实测：`frozen.md` 列了「画像修订 person_portrait_revisions」，而 `capability-map.jsonl` 里**根本没有这条条目**（`grep '"frozen"'` 15 条，无画像修订），两种状态都放行。 | **补上条目**（裁决②已解冻它，按 `migrated` / `pending` 记），并登记这个单向性 |
| 42 | `person_moments.run_id` 指向被冻结的表 | 1.0 的 DDL 里 `run_id TEXT REFERENCES person_insight_runs(id)`（`db.py:1148`）。裁决②仍冻结 `person_insight_runs`（不建表），所以 0006 里**这一列取消**——留着它就是一条指向不存在表的外键。 | **取消该列**（已在 `0006_identity.sql` 的注释里写明） |
| 43 | 迁移 0006 是**一次建对**，1.0 的三处运行期 ALTER 不搬 | 1.0 的 `entities.identity_state/role_state/name_state`（`db.py:1240-1243`）、`face_instances.validity`（`:1238`）、`relationships.inverse_predicate`（`:1244-1246`）都是 `_ensure_columns` 事后补的列 | 写进 `CREATE TABLE`（结构性偏离，见证据 22/23） |

#### 第 40 条的完整根因（`check_module_map.py` 为什么放行重复行）

**现象**：真实仓库 `bd3af31` 的 `docs/architecture/module-map.md` 第 66 / 67 行是
**逐字节相同的两行**（`md5sum` 相同），第一列都是
`sentrix/application/retrieval/retrieve_evidence.py`。全表 109 行、108 个唯一路径，
**这是唯一一处重复**（`grep -oE '^\| \`[^\`]+\.py\`' | sort | uniq -d` 只命中它一个）。
`check_module_map.py` 在「有重复」与「删掉重复」两种状态下都报 `0 个问题`。

**来历**（`git log -S` 定位）：`ee690e1`（Task 64）把它从 0 加到 1，
**`9f14c5d`（Task 65）从 1 加到 2**——是一条手工编辑造成的重复，不是生成器的产物。
它随 `f663522` 一路带到 `bd3af31`。隔离副本里 Task 69 重写表格时它掉了（现在 1 行）。

**根因不是「判据不覆盖重复行」，而是「判据的容器把重复行吃掉了」**——两处，各自独立：

1. **文件集合那一侧用的是 `set`。** `documented_paths()` 返回
   `{match.group(1) for line in ...}`，重复路径在集合里**自然只留一份**。而 `main()`
   的比对是 `in_code - in_doc` / `in_doc - in_code`，两个 `set` 做差集——
   **重复行在这个模型里根本不是一个可观测量**，无论多少行都一样。
2. **依赖边那一侧用的是 `dict`。** `documented_edges()` 写
   `deps[path] = parse_cell(...)`，**后一行覆盖前一行**。两条完全相同的行 → 覆盖后结果不变，
   仍然相等。所以 `compare()` 也无话可说。

**要注意的不对称**：这两个容器**不是同样瞎**。如果重复的两行内容**不同**，
第 2 条（`dict` 覆盖）反而会报红——它比的是最后一次覆盖的结果，写错的那一份会被抓。
第 1 条（`set`）则连这都抓不到。**所以这个洞的准确形状是：
「逐字节相同的重复行完全不可见；内容不同的重复行只能被依赖列那一半偶然抓到。」**

**为什么它属于「声称在检查、其实没有」那一族**：`test_module_map.py` 为这个脚本写了
**四条反向测试**（未记录模块、`--src` 传错基准、依赖列写错、行少了列），
唯独**没有一条针对重复行**。而 `check_module_map.py` 的开头注释专门强调
「列数不足的行会被报错而不是跳过：跳过等于给依赖校验开后门——那行写什么都不会红」——
它已经想到了「一行被整行跳过」这一类，**但没想到「两行塌成一行」这一类**。
两者是同一个病的两个变体：**校验模型里不存在的东西，就是没被校验的东西。**

**修法**（已实施，见计划文档）：在 `main()` 的路径比对之前加一条
「表格行数 == 唯一路径数」的断言，并把重复的路径名逐个报出来；同时在
`test_module_map.py` 里加一条**反向测试**（造一份含重复行的表，断言 checker 报红），
保证这条检查自己也有门禁。

### 2c.4 顾虑①的处置：`make ci` 真假红灯判据

限制条件（实测）：153 同机在跑 1.0 的 8091（RSS 11.7 GB）、vLLM 8500、photobench runner、
2.0 的 8191/4274，`make ci` 的 `coverage` 那一步被 SIGKILL 过，e2e 抖动过两次，
而**单独重跑失败文件是绿的**。如果不把判据固化，就会拿假红灯去改代码。

**判据固化成一个脚本** `/home/asus/s5verify/ci.sh`（不写在人脑里），顺序执行：

1. **跑之前先看环境**：1 分钟负载 ≤ 2.0 且 `MemAvailable` ≥ 12 GB，否则**不跑**、
   等窗口（最多 30 分钟）后报 `SKIPPED`。**绝不在高负载下跑**——那种红灯没有信息量。
   实测这条真的会挡下来：Task 70 的一次调用就打印了
   `### 环境不满足（load1=2.19 需<=2.0 ...），等 60s` 然后才跑。
2. 跑 `make ci`，**原始输出整份落在 `logs/ci-<时间戳>-run<N>.log`**，跑几次留几次。
3. 绿 → `CI_OK`。
4. 红且日志里有 `已杀死 / Killed / SIGKILL` → 判**疑似假红**（`CI_KILLED`），
   重新等窗口后重跑，最多 2 次；两次都带这个指纹才按真红处理。
5. 红且是测试失败 → **先单独重跑那些失败文件**。单跑绿 → `CI_FLAKE`，整跑重试；
   单跑也红 → 真红，必须修。
6. **任何情况下都不在「红灯未过第 4/5 条」时改代码。**

这条协议自己也被实测过一次：Task 70 的第一次 `make ci` 是 `CI_RED_UNKNOWN`，
日志尾部是 `make: *** [Makefile:22：lint] 错误 1` 加 4 条 ruff 报错（3 条 E501 + 1 条 SIM117）
——**是真红，且判据正确地没有把它误判成抖动**（它没有 SIGKILL 指纹，也没有测试失败可单跑）。

## 2d. Task 70 / 71 实施期实测到的偏离与发现

两颗提交（`b18c571` Task 70、`67030f0` Task 71）都在隔离副本里落地，`make ci` 全绿。
带出六条要登记的东西。

| # | 事项 | 判决 |
|---|---|---|
| 44 | **两张解冻的表在 S5a / S5b 期间没有写入者**。`person_moments` 与 `person_portrait_revisions` 随 `0006_identity.sql` 建了出来，而它们的写入者在 S5c（Task 85 / 86）。**这正是 S4 栽过两次的形状**（「零调用的纯规则」「零调用的 `SummarizeEvent`」）。 | **保留**，代价与判据见 §2c.1「②的落点判断」。**区别在于：这条机械检查只覆盖模块，不覆盖表**——新增的 `scripts/check_reachability.py` 会拦住「模块没有生产调用者」，但一张没人写的表它看不见。把这个不对称写在这里，不假装它不存在。**用户若认为这个区分不成立，处置是把两张表挪到 S5c 的 0007 并从 0006 删掉**（代价：重跑一次 Task 70 的门禁） |
| 45 | **`link_confirmed_mention` 把一条规则从调用点下沉进了仓储**：1.0 在调用点用 `if entity.get("status") == "confirmed"` 挡「只有已确认的实体才写提及」，2.0 挡在仓储里并改成抛 `ValueError`（`repositories/entities.py:494-498`）。 | **改**（Task 71 唯一一处结构偏离）。理由写在代码注释里：这条规则一旦漏判，观察的人物快照里就会混进一个聚类猜出来的候选人，而它是 `person_score` 与 `person_bridge` 的输入——**一处漏判，后面全是事实**。1.0 的调用点从不违反它，所以生产路径上行为等价；变化的是漏判的代价从「静默污染」变成「一眼可见」。**风险如实记**：规则换了执行位置，Task 72/73 的用例必须各有一条「非 confirmed 不写提及」的断言，否则这条防线在调用侧就没有门禁 |
| 46 | **`maintain_entity_property_values`（多值合并）不移植** | 1.0 的调用点全在语义实体抽取（`db.py:3840-4032` 的 atmosphere / `semantic_details`），那是 S7；本切片没有调用者，收进来就是「零调用的能力」。理由写进了 `repositories/entities.py` 的模块 docstring |
| 47 | `list_clusters` 沿用 1.0 的 `INNER JOIN memory_spaces` | **照搬，不改**。后果是：scope 在 `memory_spaces` 里没有行时，该 scope 的簇**一条都查不到**（不是查全量）。这是 1.0 的实际行为，测试的 fixture 因此必须建 `memory_spaces` 行。改成 `LEFT JOIN` 会让「没登记的空间」与「没有簇」这两个状态混起来 |
| 48 | **Task 70 / 71 的红灯 A 都是收集期错误，不合格** | 建新模块时 `ModuleNotFoundError` 是 TDD 的天然红灯，但它**只证明「测试先于实现落地」，不证明任何行为被钉住**。**处置：Task 72 起每个任务在提交后追加红灯 B（定点变异）**——把实现里的要害那一行改掉、重跑门禁，让红灯**指向具体的断言**。这条纪律已写进计划文档的「逐任务定义」开头 |
| 49 | `get_face_instance` 带出四个 1.0 也带、但本切片暂时用不上的字段（`asset_path` / `file_name` / `mime_type` / `media_type`） | **保留**。它们来自 1.0 的同一条 SQL，Task 75 的裁剪端点要 `asset_path`；删掉另外三个反而要多写一行注释解释「为什么单独摘掉」。**不是投机**——是「照抄一条已经存在的查询」与「按当前需要裁剪它」之间的选择，这里选前者 |

**另外两条如实记录**：

- **Task 70 的红灯 / 绿灯原文在计划文档里是后补的**。落地当时跑过（10 条全红 → 10 条全绿），
  但原始日志在重跑时被覆盖掉了；文档里贴的是一次**刻意复现**的原文
  （把 `0006_identity.sql` 临时移走再跑），不是落地当时那一跑的字节。**这一点必须写明**，
  因为「红灯原文」的可信度全在它是不是当时那一跑。Task 71 起日志一次一存，不再有这个问题。
- **Task 71 的 19 条门禁里没有一条是只断言「不抛异常」的**：每一条都断言了具体的
  字段值、行数或异常类型（含一条 `ValueError` 与两条 `IntegrityError` 语义的对照）。

## 2e. Task 72 / 73 实施期实测到的偏离与发现

两颗提交（`3e469ab` Task 72、`5c30554` Task 73）落地，`make ci` 全绿，红灯 B 有原文。
带出五条，其中**第一条与第二条推翻了本计划自己的写法**。

| # | 事项 | 判决 |
|---|---|---|
| 50 | **计划的「全部四件都写 `entity_revisions`」是错的。** 实读 1.0 `db.py:5152-5193` 的 `confirm_face_cluster`：它**不调** `_record_entity_revision`。写审计的只有三处——`_merge_cluster_into_person`（`user_name_merge`，同名合并那一路）、`merge_face_clusters`（`user_merge`，默认参数写在签名 `db.py:4776`）、`split_face_instance`（`user_split`，`db.py:4797`）。**reject 不可能写**——它删的正是那个实体的 `entity_revisions` 行。 | **按 1.0 实现**（Task 73 没有自造第四条修订）。计划文档那一行是**规划期的错误**，已在 Task 73 一节改正。若产品真要「每次确认都留一条 `user_confirm` 修订」，那是一处**有意的偏离**，需要用户点头 + 同时改这里 |
| 51 | **一处从 1.0 继承来的地雷（探针实测，未改）**：对一个**已经 confirmed** 的簇，用**同名**再确认一次，会走同名合并分支，而目标实体就是它自己 → `clu_1.status = 'rejected'`（`member_count` 随后被 refresh 写回 1）。根因在 `db.py:5157-5159`：`existing = self.find_confirmed_person_by_name(scope_id, name)` 先查、命中即合并，**没有排除「命中的就是本簇的实体」**。1.0 同样如此。 | **~~照搬 + 登记，未改~~ 已按用户裁决①修掉（Task 74，`11ab962`）**。改法是目标命中本簇时短路。原文的裁决记录与红灯证据见 §2f。 |
| 52 | **异常带 `status_code` 是一个新约定**：application 层不许 import `api/errors`（`application-purity` 契约），所以 400/404/409 定在 `review_cluster.py` 的三个异常类上（`InvalidReviewRequestError` / `ClusterNotFoundError` / `ReviewConflictError`）。 | **登记 + 待用户确认统一口径**。现有的 `TurnNotFoundError` 不带码、由路由硬编码 404；两种做法都不算错，但**同一个仓库里两种并存会让「状态码从哪来」变成看运气**。Task 75 落地前需要定一个 |
| 53 | **`delete_candidate` 比 1.0 多删三处**：`entity_observations` / `event_participants` / `event_entities`（外加 0006 里已建、S5c 才写的 `person_moments` / `person_appearance_evidence`）。 | **改，理由不是「顺手帮 Task 81 干活」**：2.0 每次连接都 `PRAGMA foreign_keys = ON`（`database.py` 的 `connect()`），1.0 没开。少删一行，某天这条 DELETE 会整体失败。**投影刷新仍留给 Task 81**——删干净与重建是两件事 |
| 54 | **`check_reachability` 的判据有假阴性**：第一版把「所有 `ast.Name` 载入名」与该模块公开符号求交，于是别处一个**形参**叫 `pose_bucket`，就让 `domain/identity/faces.py` 的同名公开函数被当成「已经用过」，模块被判成已接生产；而 `PENDING_WIRING` 的反向规则还会因此报错要求删掉一条本来正确的登记。同族还有常量名撞车（`VISUAL_SPACE`）。 | **收紧判据**（`ee2ddad`）：改成「这个名字是**从那个模块 import 进来的**，而且被读用过」——名字空间由导入关系给出，不由拼写给出。**并补两条反向测试**（形参撞名必须仍然报红 / `from ... import` 后用过的必须算数），否则收紧后的判据自己又变成一条没人验的规则。**这一条值得单独记**：它是「声称在检查、其实没有」那一族的又一个实例，而且是**实施期被撞出来的**，不是审出来的 |

**红灯 B（定点变异）实测结果**——每个任务挑 3 条要害行为，改掉实现里那一行，重跑门禁，
让红灯**指向具体断言**，然后改回：

| 任务 | 变异 | 红的那条用例 |
|---|---|---|
| 72 | `quality < MINIMUM_MATCH_QUALITY` → `<=` | `test_quality_exactly_at_the_gate_still_matches`（`assert 'cluster_5299834c33b2' == 'clu_home'`） |
| 72 | 资格闸门去掉 `validity == "verified"` 这一半 | `test_a_face_that_is_not_identity_eligible_is_stored_without_a_cluster`、`test_an_ineligible_face_still_writes_its_vector_without_a_cluster`（2 failed） |
| 72 | 删掉签名比对整段 | `test_a_cluster_whose_prototypes_carry_another_signature_is_skipped` |
| 73 | `confidence = MAX(confidence, 1)` → `confidence = confidence` | `test_confirm_marks_the_person_and_the_cluster`（`assert 0.4 == 1.0`） |
| 73 | reject 的判据 `== CONFIRMED` → `!=` | 三条（含仓储兜底的 `ValueError: confirmed person entities cannot be deleted`） |
| 73 | 拆分的实例查找 `find_instance(cluster_id, fiid)` → `get_instance(fiid)` | `test_split_of_an_instance_from_another_cluster_is_a_404`（`DID NOT RAISE`） |

**这六条才是「行为真的被钉住」的证据**——红灯 A（收集期 `ModuleNotFoundError`）只证明
「测试先于实现落地」，不证明任何一条断言有牙齿。原文在
`/home/asus/s5verify/logs/task72-redB.txt` 与 `task73-redB.txt`。

**另外一条实测到的环境事实**：Task 73 的第一次 `make ci` 触发了**真实的 SIGKILL 假红**
（`make: *** [Makefile:56: coverage] 已杀死`），`ci.sh` 按判据重跑 run1 后全绿。
两段原始输出都留在 `logs/ci-20260923-122113-run0.log` 与 `-run1.log`。
**这是顾虑①那条判据第一次在真实工作里救下一次误判**——不重跑就会拿假红灯去改代码。

---

## 2f. Task 74 实施期实测到的偏离与发现（含用户裁决①的落地）

`11ab962`（Task 74）落地，`make ci` 全绿（869 passed / 16 skipped / coverage 97.69%），
红灯 B 有原文。

### 第 51 条的处置：修掉，并且这一次根因比规划期写得更硬

规划期只说了「1.0 同样如此，照搬 + 登记」。实施期把 1.0 的两条同名确认路径并排读，
发现**这条守卫是 1.0 自己写过的，只是漏了一处**：

| 1.0 位置 | 同名查询命中后 | 有没有排除「命中的就是本簇/本人」 |
|---|---|---|
| `db.py:5157` `confirm_face_cluster`（簇入口） | 直接 `_merge_cluster_into_person` | **没有** ← 缺陷 |
| `db.py:5251` `confirm_person_entity`（人入口） | `if existing and existing["entity_id"] != entity_id:` | **有** |

所以修复不是「给 1.0 加一条新判据」，而是**把 1.0 自己写在人入口的那条判据补到簇入口**
（`review_cluster.py:138`）。这个根因改变了这条修复的性质：它不再是一个产品行为的选择，
而是**同一份代码里两处写法不一致**——按用户的判断修掉它是恢复一致性，不是改变设计。

**缺陷的实际后果**（定点变异实测原文）：对一个已确认的簇用同名再确认，返回的
`ConfirmResult.merged_into` 是**它自己**（`assert 'ent_1' is None` 失败），
簇被置成 `rejected`、`member_count = 0`——**这个人的脸全部从人物视图里消失**，
而且界面看到的是「这个簇没了」，没有任何一处日志或审计能指出发生了什么。

**三条测试钉住它**（`test_review_cluster.py`），缺一条都不够：

| 用例 | 钉的是什么 |
|---|---|
| `test_reconfirming_a_cluster_with_its_own_name_keeps_it` | 精确名自命中：簇保持 confirmed、`member_count` 不变、不留「并入了自己」的审计 |
| `test_reconfirming_through_one_of_its_own_aliases_keeps_it` | 别名自命中——走的是 `find_confirmed_person_by_name` 的**第二段**（扫 aliases），与精确名是两条不同的路径 |
| `test_a_different_cluster_with_the_same_name_still_merges` | **反向**：守卫只排除本簇自己的实体，别人的同名簇照样并。没有这一条，修复可以被实现成「同名一律不合并」而测试仍然全绿——那等于把同名合并整个功能关掉 |

**红灯 B 原文**：把 `if existing is not None and existing.entity_id != cluster.entity_id:`
改回 `if existing is not None:` → 前两条红（`assert 'ent_1' is None`）、第三条仍绿。

### 第 55 条 · `make ci` 在 153 上被 SIGKILL 的**真实根因**：glibc arena 膨胀

顾虑①原来只把「coverage 会被 OOM」当成环境噪声并固化成重跑判据。实施期读到了内核日志，
根因是可复现、可修的：

```
oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),global_oom,...task=pytest,pid=148543
Out of memory: Killed process 148543 (pytest) total-vm:39128280kB, anon-rss:18174300kB
```

**pytest 自己涨到 18 GB**（全机 61 GB，同期 1.0 的 8091 + llama-server + cursor-server
已占 43 GB，swap 打满）。同样的测试在同一台机器上**限住 glibc arena 后峰值只有 10.1 GB**：

```
MALLOC_ARENA_MAX=2 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 .venv/bin/pytest
→ 846 passed, 16 skipped in 124.64s
   Maximum resident set size (kbytes): 10146904
```

torch / onnxruntime 会按核数开线程，每个线程一个 64 MiB 的 glibc arena，**默认不限**，
于是多线程测试进程的 RSS 随线程数线性膨胀，与测试内容无关。所以：

- 顾虑①的重跑判据**仍然有效且必须保留**（真实的内存竞争还会来自别的服务）；
- 但 `make ci` 在 153 上应当**默认带 `MALLOC_ARENA_MAX=2`**，把假红的来源去掉，
  而不是每次都靠重跑消化它。这是环境配置，不是代码问题。

### 第 56 条 · `onnxruntime-gpu` 的 CUDA EP 需要**先 `import torch`**，否则静默退化成 CPU

依赖试装（S5b 的闸门）实测：装了 `onnxruntime-gpu==1.30.0` 之后
`ort.get_available_providers()` 里有 `CUDAExecutionProvider`，但**真建 session 时失败**：

```
Failed to load library .../onnxruntime/capi/libonnxruntime_providers_cuda.so with error:
libcublasLt.so.13: cannot open shared object file: No such file or directory
Failed to create CUDAExecutionProvider. Require cuDNN 9.* and CUDA 13.*.
```

而且 **`InferenceSession` 不报错**——它退回 CPU 并正常返回结果。这是「看起来在跑、
其实在 CPU 上跑」那一族的又一例：`get_available_providers()` 只说明 EP 编译进去了，
**不说明运行期能加载**，必须真建一个 session 并读 `sess.get_providers()` 才算验过。

根因与修法：venv 里 cu13 的 `libcublasLt.so.13` 在
`.venv/lib/python3.11/site-packages/nvidia/cu13/lib/`，不在动态加载路径上。而 **torch 启动时
会把它们预载进进程**，所以：

| 做法 | 结果 |
|---|---|
| 不设 `LD_LIBRARY_PATH`，先 `import onnxruntime` 再建 session | **退化成 CPU** |
| 不设 `LD_LIBRARY_PATH`，**先 `import torch`** 再建 session | `['CUDAExecutionProvider', 'CPUExecutionProvider']` ✔ |
| 设 `LD_LIBRARY_PATH` 指向 `nvidia/cu13/lib` 等 | ✔（但要靠启动环境，1.0 不这么做） |

Task 77 的 `onnx_runtime.py` 因此**必须自己负责预载**（不能依赖启动脚本设环境变量，
也不能依赖「调用方恰好先 import 过 torch」——那是隐式顺序依赖，正是本仓库一路在防的东西）。

### 第 57 条 · 第 52 条（`status_code` 口径统一）本轮**没有做**

裁决③要求把异常 `status_code` 口径统一到「领域错误带码、路由只映射」，这会动 S3 的
`TurnNotFoundError`（目前不带码、由路由硬编码 404），属跨切片改动。Task 74 不碰它，
留给 Task 75（路由落地时一并改）——**它仍然是一条未结的账**。

### 第 58 条 · 裁决②（两张解冻的表挪到 S5c）本轮**没有做**

`person_portrait_revisions` / `person_moments` 不在 0006 里建，计划 Task 85/86 才落地；
S5a/S5b 期间没有写入者。Task 74 不涉及迁移。**这条只是登记「尚未执行」，不是偏离。**

---

## 2g. Task 77 实施期实测到的偏离与发现（真权重 + 53 张真照片）

`9193a71`（Task 77）落地，`make ci` 全绿（898 passed / 16 skipped / coverage 97.69%），
红灯 B 四条有原文。

**真数据实测**（`album3-v2-source/photos` 53 张，RetinaFace-R50 分块）：
检出 **142 张脸**、**46 张照片有人脸**、单张最多 **19 张**、7 张无人脸；
session 的 provider 链是 `['CUDAExecutionProvider', 'CPUExecutionProvider']`——**CUDA
真的生效了**（这是「先 import torch 预载」那条修法在真链路上的验证）。显存没被撑爆：
单 session 预算 256 MiB + 并发闸门默认 1。

### 第 59 条 · `detect()` 返回的是**候选**不是人脸，而且 RetinaFace 的关键点过不了几何判据

两件事都是实测出来的，都不是推测：

1. **细pass 的入选阈值是 0.1。** 实测 `2017-11-05 151427.jpg` 上有一张脸的置信度是
   **0.282**。所以调用方**不能**按粗pass的 0.3 过滤——那会把细pass的收获整个丢掉，
   而细pass正是为「粗pass全军覆没」准备的。
2. **低分 anchor 的偏移量可以解出任意离谱的框。** 实测 `2017-11-05 151451.jpg`
   （**11394×2944 的全景**）上有一个候选：框 `[9730, 1680, 10490, 2548]` 落在图内，
   而它的 5 个关键点整批落在 `x ≈ -400..-60`——框与关键点互相矛盾。
3. 推论：**RetinaFace 自己的关键点在 `landmark_sanity` 下一个都过不了。** 实测前 6 张
   真照片里，所有 ≥64px 的图内候选**全部被拒**（`rejected > 0 且 passed == 0`）。
   这正是 1.0 `model_clients.py:1788` 那句注释的实测依据：

   > Use SCRFD's own (reliable) landmarks, not RetinaFace's, whose geometry is
   > unreliable even on large faces.

   **所以 Task 78 的「用 SCRFD 的关键点替换掉 RetinaFace 的」是必要步骤，不是优化。**
   少了它，每一张脸都会停在 `face_validity='uncertain'`、`identity_eligible` 恒假，
   一张脸也进不了簇。

**判决：照搬 1.0，不在检测层裁剪或修关键点。** 1.0 靠 `_expand_crop` 与 SCRFD 复核两道
下游防线丢这些候选，2.0 保持同一分层。契约写在 `detect()` 的返回值说明里，由 Task 78 的
适配器负责筛。**在这一层加裁剪会改变与 1.0 的可比性**，所以没有做。

### 第 60 条 · 一次**没变红的变异**暴露了测试自己的洞

第一版 `test_nms_threshold_of_one_keeps_everything` 用阈值 `1.0` 去测「`iou <= threshold`
是含等号的上界」。变异（`<=` → `<`）**没有变红**——因为那一对框的 IoU 只有 0.68，
`<= 1.0` 与 `< 1.0` 结果完全一样。用例看起来在钉边界，**其实一次都没走到边界上**。

改成 IoU **精确等于**阈值的构造才真的钉住：A `[0,0,10,10]`（面积 100）与
B `[0,0,5,10]`（面积 50），并集 100、交集 50，IoU = 0.5 在二进制浮点下**精确**。

**同一条族里的第二个洞**：变异脚本用 `sed` 改写实现，而 `sed` 不匹配时**静默成功**——
那会把「红灯」变成一次没人验的空操作，与 Task 72 那次的 `check_reachability` 假阴性同类。
脚本因此加了 `apply()`：变异后先 `grep` 确认那一行真的变成了变异后的形态，否则中止。

这两条合起来是一句话：**变异测试本身也要有反向门禁**——没变红的变异要先怀疑变异没落上去
或者用例没走到边界，而不是直接宣布「这个实现点不重要」。

---

## 2h. Task 78 实施期实测到的偏离与发现（真链路第一次跑通时撞出来的六个坑）

``c0518e6``（Task 78）落地，`make ci` 全绿（`920 passed / 16 skipped / coverage 98%`）。这一节的六条里，**前四条全是
「不报错、只是结果不对」那一族**——没有一条会让调用方看到异常，全部是跑真权重 + 真照片时
撞出来的；它们没有一条能靠读 1.0 的源码发现，因为 1.0 的代码在同样的位置也是这么写的。

| # | 事项 | 判决 / 处置 |
|---|---|---|
| 61 | **`FACE_VALIDITY_VERIFIED` / `FACE_VALIDITY_UNCERTAIN` 落在 `domain/identity/models.py`**，不在适配器里。计划与 1.0 都把这两个串写成适配器里的裸字面量。 | **改**。理由是 Task 79 的 seed 用例也要写同一个值，而 `application-purity` 契约不许 application 层 import infrastructure——常量留在适配器里，用例就只能再抄一遍字面量，那正是「一个值散落三处」的开头 |
| 62 | **`FACE_MODEL_ROOT` 在 1.0 是两个意思。** `FaceAnalysis(root=...)` 收的是 insightface 的**根**（模型在 `<root>/models/<name>/`），而 `_load_buffalo_recognition` 把同一个变量直接当**模型目录**用；两者只在默认值下碰巧一致（`~/.insightface` vs `~/.insightface/models`）。**实测代价**：把模型目录当 root 传进去，insightface 找不到模型会**去 GitHub 下载**——进程挂在一条到 github.com 的 HTTPS 连接上，`utime` 只有 1.55 秒、`pytest` 一个字都不打，直到超时。第一次真跑就是这么卡住的。 | **改**：`FACE_MODEL_ROOT` 只保留一个意思（insightface 根），模型目录由 `MODELS_SUBDIR` 派生；并在 `_build_analysis` 加一条 **fail-fast**（模型目录不存在直接抛）。「静默联网挂死」于是变成一条 `error` 字段里的降级 |
| 63 | **insightface 的 `providers` 必须是裸字符串列表**，与 `provider_options` 是两个**平行**列表。给它 onnxruntime 那种 `(name, options)` 元组，它打一行 `EP Error ... Falling back to ['CPUExecutionProvider']` 然后**在 CPU 上把整条链路跑完**。 | **改**（`provider_chain(...)` + `face_onnx_provider_options(...)`）。这是第 56 条「静默退化成 CPU」的同族第二例：上一例是运行库没预载，这一例是入参形态不对，**两例的观测点都是 `session.get_providers()`** |
| 64 | **第三级（整图兜底）接活**——处置证据 8 那条死路径：1.0 调的是恒为 `None` 的 `identity_adapter`，`AttributeError` 被裸 `except` 吞掉，永远返回 `[]`。2.0 把它接在真实的识别 session 上。**照搬的四个常数（置信度 0.99 / 质量 0.8 / 面积比 1.0 / frontal）从来没有被任何一张真照片检验过**——它们随死路径一起躺了不知道多久。 | **改 + 如实登记**：常数原样照搬（这样以后想调的人有一个可比的对象），但它们**不是实测值**，注释里写明了 |
| 65 | **`detect()` 在权重缺席时直接返回空表**（1.0 判的是 `FACE_ENABLED`）。 | **改**。缺权重是允许的部署形态，不是异常；判 `available` 让「这台机器没有人脸能力」与「这张图没有脸」在调用方是两件事 |
| 66 | **真权重那层用例的取样从 6 张改成 10 张。** 实测：这一册相册**前 6 张（远景合影）一张 ≥64px 的脸都没有**，取 6 张会得到「一张脸都没有」——用例红了，而红的是**测试的假设**不是代码。 | **改**，并把「这一批里必须有脸」写成一条显式的失败信息（同 `test_face_detector.py` 那条全景断言）。**这是 S4「只断言数量」那条教训的另一面**：数量写死会随数据变红，完全不写又会漏掉「一张脸都没有」这种真故障 |

### 真数据实测（2026-09-23，整册 53 张，与 Task 77 用的是同一批）

| 量 | 实测 |
|---|---|
| RetinaFace 候选 | **315** |
| 过 `FACE_MIN_SIZE=64` 的候选 | **117** |
| 适配器输出的人脸（每张都带 512 维向量） | **117** |
| 其中 `verified` | **30** |
| 有候选的照片 | 46 / 53 |
| 适配器有输出的照片 | **32 / 53** |
| 整册耗时 | 69 秒（一次加载 session） |

provider 链实测是 `['CUDAExecutionProvider', 'CPUExecutionProvider']`——第 62/63 两条修完之后，
检测与识别**都真的在 GPU 上**。

**`FACE_MIN_SIZE=64` 是这条链路上真正的瓶颈，而它是照搬来的**：1.0 的部署脚本
（`start_sentrix_api_8091.sh`）里没有设这个变量，所以默认值 64 一直在生效。一册远景合影
里 315 个候选有 198 个小于 64px，全被丢掉。**这一条不改**——它属于「多小的脸才算一个人」
的**产品口径**，不是 bug；但「检出了多少张脸」这个数字对它是敏感的，所以记在这里。

### 两条判定差一个量级（第 59 条的验收）

真照片上 **117 张脸带向量、只有 30 张 `verified`**。所以「有向量」与「够格进簇」必须分成
两条断言：Task 77 那条 `test_retinaface_landmarks_do_not_survive_the_geometry_gate` 钉的是
「照搬 RetinaFace 的关键点会全灭」，Task 78 的
`test_scrfd_landmarks_are_what_lets_a_real_face_be_verified` 钉的是「换成 SCRFD 之后真的有
30 张活下来」——**两条合起来才构成「替换是必要的、而且有效」**。

### 红灯 B（定点变异）实测结果

每个任务挑要害行为改掉实现里那一行、重跑门禁，让红灯**指向具体的断言**，然后改回。
脚本带 `apply()`：变异后先确认那一行真的变成了变异后的形态，否则中止
（`sed` 不匹配时静默成功，会把红灯变成一次没人验的空操作——第 60 条的教训）。

| 变异 | 红的那条用例 |
|---|---|
| 分数闸门 `score >= 0.7` → `score > 0.7` | `test_the_score_gate_is_seventy_hundredths` |
| 资格闸门去掉质量那一半（`... and quality >= min_quality`） | `test_the_quality_gate_is_what_separates_verified_from_eligible` |
| 面积闸门整条删掉（`... and area_ratio >= 0.003`） | `test_the_area_gate_is_three_thousandths` |
| 放大重试的框不换算回原图（`value / scale` → `value`） | `test_an_identity_photo_falls_back_to_an_upscaled_retry` |

原文在 `/home/asus/s5verify/logs/task78-redB.txt`。

---

## 2i. Task 79 实施期实测到的偏离与发现（导入路径接线 + 身份预置）

``d328878``（Task 79）落地，`make ci` 全绿（`928 passed / 16 skipped / coverage 98%`）。这一节记四条**结构性**偏离
（计划的文件清单与实施时落点不一致，逐条写清为什么）与一条**杀不掉的变异**。

### 计划 vs 实施：文件清单的四处偏离

| # | 计划写的 | 实际落的 | 为什么 |
|---|---|---|---|
| 67 | `application/media/import_album.py`【改】 | `application/media/import_assets.py`【改】 + `api/routes/media.py`【改】 | **顺序约束在 `ImportAssets.execute` 里**，不在 `ImportAlbum` 里——后者只按文件循环调前者（``ImportAlbum.execute`` 的循环体只有一行 ``self._importer.execute(...)``）。人脸登记必须插在「写观察」与「事件归并」之间，那两件事都在 `ImportAssets` 里；改 `import_album.py` 改不出这个顺序。接线点仍在 `build_import_assets`（单张上传与整册导入共用同一处，这是它的原话） |
| 68 | 三级降级落在 `application/identity/seed_identity.py` | 落在 `infrastructure/models/face_adapter.py` 的 `detect_identity_photo` | 后两级要**解码 / 缩放 / 重编码图片**（`application-purity` 契约禁掉 PIL），第三级还要**识别 session**（那是适配器私有状态）。1.0 能把它写在 `app.py` 里，是因为 `pipeline.face.identity_adapter` 当时是全局可达的；2.0 没有那个全局。用例（`SeedIdentity`）只负责「拿这些脸去建什么」 |
| 69 | 未提 `domain/identity/ports.py` | 新增 `FaceDetector` 端口 | 容器字段**必须按 domain 端口声明**（`test_container_wiring.py` 强制），而人脸适配器此前没有任何端口。两个入口（`detect` / `detect_identity_photo`）的差别写在端口 docstring 里：**不是两种能力，是一套能力的两档召回策略** |
| 70 | 未提 `test_face_embedding.py` | 三级降级的六条用例加在**适配器自己的**用例文件里 | 它们是适配器的行为，不是用例的行为。放进 `test_import_identity.py` 就要在那里重建一整套假件（StubFace / StubAnalysis / 假 session），而 `test_face_embedding.py` 里已经有现成的、且**判据表本身就是用它验的** |

### 第 71 条 · 一条杀不掉的变异：`SeedIdentity` 选出的「最好的那张脸」在终态上不可观测

红灯 B 的第三次变异（`max(chosen, key=…)` → `min(chosen, key=…)`，即代表向量改取**质量最低**
的那张）**没有变红**。按纪律先怀疑「变异没落上去」与「用例没走到边界」，逐条排查：

- 变异**确实落上去了**（`apply()` 的 `assert count == 1` 通过，且事后 `diff` 确认文件被改写）；
- 用例**确实跑到了**那一行（临时插 `print` 实测，两条路径都执行）；
- 排除了 `__pycache__` 陈旧的假说（删掉 `__pycache__` 重跑，结果不变）。

**根因**：`FaceRepository.refresh_prototypes` 在重算原型时**顺带把代表向量也重写了**
（`repositories/faces.py:389-400`，取当前实例里质量最高的那一张）。而 `SeedIdentity` 落实例时
写的 `quality` 就是它选代表时的那个键——**两条路径用的是同一条规则，所以终态永远一致**，
`create_cluster` 那一刻传进去的向量在两行之后就被覆盖了。

**判决：照搬 1.0，不改，如实登记。** 1.0 的 `seed_person_identity` 同样是「先按质量选出代表
向量写进簇，再 `_refresh_face_prototypes`」；去掉前一步会让 2.0 与 1.0 在「簇建出来到刷新
原型之间」有一个可观测的中间态差异，而那个窗口在单线程用例里根本不存在。
**代价如实记**：这条选脸逻辑没有任何门禁用例能钉住它，它是一行**等价但不可观测**的代码。

### 第 72 条 · 预置出来的簇 `revision` 是 1，1.0 是 0

1.0 用一条 `INSERT ... status='confirmed'` 直接把簇建成已确认（`revision` 取 DDL 默认 0）。
2.0 走 `create_cluster` → `confirm_cluster`，后者带 `revision = revision + 1`。
**终态的语义一致**（都是「一个已确认的、挂着人的簇」），差的是那一个计数器。
不为了对齐它去改 `confirm_cluster`——那个 `+1` 是簇审阅路径（Task 73）真实需要的修订号，
为预置这一条路径把它去掉，等于让审阅路径少一次修订。

### 第 73 条 · 预置实例的 `validity` 写死 `uncertain`

1.0 的 `seed_person_identity` 那条 INSERT **不含 `validity` 列**，落的是 DDL 默认值
`'uncertain'`（`db.py:1238` 的 `_ensure_columns`）。2.0 的仓储 `add_instance` 要求显式传，
所以这里**显式传 `uncertain`**——不是为了「更安全」，是为了**与 1.0 的落库结果一致**：
预置走的是「用户说这是谁」，不是 SCRFD 复核，两种证据强度不该在同一个字段上抹平。
**这一条有用例钉住**（`test_seeded_instances_carry_the_uncertain_validity`）。

### 第 74 条 · 导入路径的对照用例断言的是「内容」不是「调用序」

`test_a_matching_face_is_already_a_mention_when_the_event_is_decided` 里，事件归并那一步
**去库里查 `entity_mentions`**，断言查到 1 行——而不是断言「`faces` 排在 `events` 前面」。
两种写法在正常实现下都绿，但把顺序倒过来时：前者红在「这一刻还没有提及」（这正是
`person_score` 恒 0 的成因），后者只红在「列表顺序不对」。**要钉的是后果，不是顺序本身**。
（顺序也顺带断了一条 `order == ["faces", "events"]`，作为失败时的第一手线索。）

---

## 3. 这份文档没有做的事（如实记录）

1. **没有跑 1.0 的测试套件。** 所有 1.0 的行为都是从**源码 + 行号**读出来的，
   只有第 3 条（兜底画像）做了实跑验证。S5 执行时每个任务的门禁才真正把这些行为钉住。
2. **没有查运行中进程的实际环境**（`/proc/<pid>/environ` 在这一轮里读不出内容，
   `sudo` 需要密码）。所以 `SENTRIX_PERSON_INSIGHT_SCOPES` 的「默认关闭」是**从代码默认值推断的**，
   不是从 153 的部署实况确认的。这一条在第 20 号证据里已经标明。
3. **Task 71–83 尚未实施完。** 阶段 3–5（逐任务红/绿、跨任务回归、每 commit `make ci`）
   到目前为止对 **Task 69 与 Task 70** 执行完毕，其余按计划继续进行；每个落地任务的
   红灯 / 绿灯 / `make ci` 原始输出都在计划文档的验证记录里。**没有实施的任务不写推测的
   2.0 代码块**——S2 的教训是「13–14% 的错误率全部发生在计划生成这一步」。
4. **没有碰 1.0 仓库的任何文件**，没有重启 8091 / 8100 / 8101 / 8500 / 11434 / 8191 / 4274。
5. **没有决定任何一条「待裁决」**——7 条都原样列在计划文档里。

---

## 4. 与 1.0 逐条对回的位置索引

| 2.0 落点 | 1.0 来源 |
|---|---|
| `domain/identity/faces.py` | `backend/face_clustering.py:12-147`、`backend/face_embeddings.py:19-24,224-244` |
| `domain/identity/models.py` | `backend/db.py:560-699` 的表语义 |
| `domain/identity/appearance.py` | `backend/person_appearance.py:4-25` |
| `domain/identity/portrait.py` | `backend/person_portraits.py:14-181` |
| `domain/identity/insights.py` | `backend/person_insights.py:31-111` |
| `domain/spaces/models.py` / `ports.py` | 1.0 无（设计 §2.3） |
| `application/identity/enroll_face.py` | `backend/db.py:4815-4911` |
| `application/identity/review_cluster.py` | `backend/db.py:4776-4797,5152-5245,5290-5341,5579-5589` |
| `application/identity/name_person.py` | `backend/db.py:4399-4466` |
| `application/identity/seed_identity.py` | `backend/app.py:2253-2310`、`backend/db.py:5343-5396` |
| `application/identity/person_directory.py` | `backend/agent_runtime/tools.py` 的人物工具（1.0 无独立端口） |
| `application/identity/run_insights.py` | `backend/person_insights.py:159-253` |
| `application/memories/assign_events.py` | `backend/db.py:2093-2095,2241-2260` |
| `application/memories/maintain_facts.py` | `backend/db.py:1494-1503,3433-3447`、`backend/pipeline.py:348` |
| `application/retrieval/retrieve_entity.py` | `backend/retrieval/entity.py:1-60` |
| `domain/retrieval/lexical.py`（person_bridge） | `backend/retrieval_indexes.py:20,180` |
| `infrastructure/models/face_detector.py` | `backend/face_detector.py:1-181` |
| `infrastructure/models/face_adapter.py` | `backend/model_clients.py:1616-1938` |
| `infrastructure/models/onnx_runtime.py` | `backend/onnx_runtime.py:1-90` |
| `infrastructure/persistence/migrations/0006_identity.sql` | `backend/db.py:400-1400` 的 DDL 段 |
| `infrastructure/persistence/repositories/faces.py` | `backend/db.py:4467-4955` |
| `infrastructure/persistence/repositories/entities.py` | `backend/db.py:3679-3760,4041-4070,4400-4760,5590-5610` |
| `infrastructure/persistence/repositories/participants.py` | `backend/db.py:2486,3287,5549-5578` |
| `infrastructure/persistence/repositories/appearance.py` | `backend/db.py:4482-4560` |
| `api/routes/identity.py` | `backend/app.py:1969-2100,1498-1800,2205-2420,2508-2533` |

---

## 5. 环境事实（规划时实测）

| 项 | 实测值 |
|---|---|
| 隔离副本 | `/home/asus/s5verify/repo`（`git clone --local`，18 MB），分支 `s5-tdd` |
| 基线 `make ci` | lint / format / mypy（111 files）/ import-linter（5 kept）/ check-docs 全绿；`pytest --cov` 被 **SIGKILL**（`EXIT=2`） |
| 不带 cov 的全量测试 | 660 passed / 25 skipped / 0 failed（第一次）；第二次跑出现 2 条 e2e 抖动（`e2e/test_assistant_turn.py`），单文件重跑 `6 passed` |
| 内存 | 60 GB 总 / 39 GB 已用 / 19 GB 可用（`free -g`） |
| GPU | 13124 MiB / 24576 MiB 已用（`nvidia-smi`） |
| 2.0 venv 人脸依赖 | **全缺**（无 onnxruntime / insightface / opencv） |
| 1.0 venv 人脸依赖 | `insightface 1.0.1` / `onnxruntime-gpu 1.23.2` / `opencv-contrib-python 4.10.0.84` |
| AdaFace 权重 | 700,286,703 B（`/home/asus/models/AdaFace/pretrained/adaface_ir50_ms1mv2.ckpt`） |
| 不稳定的根因 | 同机在跑 1.0 的 8091（RSS 11.7 GB）、vLLM 8500、photobench runner、2.0 的 8191/4274 |

---

## 2j. Task 75 / 76 / 80 实施期实测（含裁决③的落地）

三个任务逐任务提交：`272ea20`（Task 75）、`fef1da5`（Task 76）、`a1e29c7`（Task 80）。
用例总数从 936（920 passed + 16 skipped）涨到 **991 collected**。

### 裁决③的落点：领域错误带码、路由只映射

**做法**：新增 `apps/api/src/sentrix/domain/errors.py` 的 `DomainError`（一个 `code` 字符串
+ 一个 `status_code` 整数），`api/errors.py` 只留四个 api 层自己的子类与**唯一**那个处理器。
用例抛带码的错误，路由原样放行。

**为什么基类必须落在 domain**：`application-purity` 契约不许 application import api，
而 api 可以 import domain——「两边都看得见」的那一层只有 domain。这不是新设计，
是裁决③那句话在现有分层下的唯一落点。

**动到的 S3 代码（两处，都是裁决点名的）**：

1. `application/assistant/run_turn.py` 的 `TurnNotFoundError(LookupError)` →
   `TurnNotFoundError(DomainError, LookupError)`，带 `code="not_found"` / `status_code=404`；
   构造时的实参从「会话 id / 回合 id」改成固定文案（`conversation not found` /
   `turn not found`）——**id 从响应里消失了**，这是行为变更，如实登记。
2. `api/routes/assistant.py` 的 `start_turn` 去掉 `try/except TurnNotFoundError` 与那句
   硬编码 404，改由统一处理器出。

**连带改掉的断言**：`apps/api/tests/contract/test_assistant_routes.py` 断言的是
`response.json()["detail"] == "conversation not found"`（`HTTPException` 的形状）。
统一之后响应体是 `{"code", "message", "detail"}`，断言改成
`["code"] == "not_found"` + `["message"] == "conversation not found"`。
**状态码与文案没变，变的是「谁决定它」。**

**没动的 HTTPException**：`media.py` / `timeline.py` / `search.py` / `assistant.py` 里还有
若干路由**自己在边界上**发现的问题（找不到 asset、目录不存在、result set 越界）仍用
`HTTPException`。它们是「请求在这里就没法继续」，不是用例的结论；把它们一起换掉会改动
四条既有契约的响应体，属于另一次机械变更。**这是一条未结的账**，不是遗漏。

### 第 64 条 · 身份路由的实际范围比计划窄

计划写的是移植 `app.py:1512-1800` 的「`/api/people` 系列」。实际搬的是**身份层能回答的
那几个**：列表 / 详情 / 改名 / 批量确认 / 预置 / 人脸裁剪 + 簇列表与四审阅。
`profile` / `claims` / `event_memory` / `patterns` / `portrait` 五块读的是
`semantic_claims` / `person_event_memory` / `person_portraits` 那几张表，**它们是 S5c
（Task 85/86）的东西**——现在搬等于照抄一个查不到数据的投影。

`/people/{id}` 的详情同理：1.0 的 `get_entity_detail` 还带 relationships / facts /
entity_observations，2.0 只回**实体 + 别名 + 头像脸 + 簇（带样本）**。少的那三块不是
这一层的事实来源（关系与事实各有自己的读侧），登记为**未搬**。

### 第 65 条 · 新增三个读方法与一个端口方法（计划清单里没有）

| 新增 | 为什么绕不开 |
|---|---|
| `EntityRepository.list_people` | `/api/people` 要列人物，而端口上只有 `get`。空间规则（`include_in_people = 0` 的不出现）与 `list_clusters` 同一条，写在这一层只写一次 |
| `FaceRepository.list_clusters_for_entity` | 人物详情要「这个人的簇」。`list_clusters` 是「进人物视图的全部簇」，按实体过滤它做不了 |
| `PreviewEncoder.crop_jpeg` | 裁剪要 PIL，而 `api-transport-only` 契约不许 api 直接 import PIL。端口是唯一合法的落点（与 `encode_jpeg` 同一条理由：同一张图的两种取景） |
| `EntityRepository.confirmed_people_for_observation` / `_for_event` | Task 80 的两个来源（见第 67 条） |

### 第 66 条 · 删掉了 S3 的占位实现

`infrastructure/assistant/identity.py`（`NoIdentityDirectory`）**删除**，不是留着不用。
留着的代价是 `check_reachability` 会把它报成「零生产调用者的模块」——那正是这条检查要抓的
形状。计划清单里没写这一条删除，但它是不变量推出来的必然后果。

capability-map 里那条指向它的记录同步改成新实现（`person_directory.py`），
否则「登记的落点」会指向一个已删除的文件。

### 第 67 条 · Task 80 的两处偏离：装配点与「删掉默认值」

1. **装配点不在 `bootstrap/container.py`，在 `api/routes/media.py` 的
   `build_import_assets`。** 那是 `AssignEvents` 在全仓**唯一**的构造点（该函数的注释
   自己写着「只在这里接一次」），container 里根本没有 `assign_events` 字段。计划把这一行
   记在 container 是记错了位置，不是记错了事。

2. **`confirmed_people` / `confirmed_event_people` 删掉了默认值。**
   S4 给它们留的是 `lambda _: frozenset()`，理由是「那时没有 `entity_mentions`」。
   代价是**「忘了接线」与「真的没有人」在类型上完全一样**，而 `person_score` 恒为 0
   不报错、只让事件分错——与本仓库一路在防的那类静默同形。删掉默认值之后，装配点少写
   一个参数就是 `TypeError`。**这是一次行为面收紧**：任何未更新的构造点会立刻红。

3. **`EventAnchorSet.people` 原来是写死的 `frozenset()`。** 计划只写了移植两个
   `_confirmed_entity_ids_for_*`，但**只接观察侧那一半是没有用的**：`_person_score` 算的
   是 `anchor.people & event.people`，事件侧恒空 → 交集恒空 → 分数恒 0。
   事件侧那一半必须一起接。这条是实施期才看清的，不是计划里的。

### 第 68 条 · 一条没变红的「不变量」被自己的对照臂抓出来

`test_the_person_component_moves_the_total_by_exactly_its_weight` 第一版用**同一对 5 分钟
间隔的照片**跑两条臂，断言 `总分差 == WEIGHTS['person'] * 0.8`。实测**差值恒为 0**：
那对照片的原始总分超过 1.0，被 `max(0.0, min(1.0, total))` 截断，两条臂都等于 1.0。

它没有报错、也没有变红——因为 0.0 != 0.16 会红，它**确实红了**，但红的原因不是「实现错了」
而是「这对输入饱和了」。改法是两臂都用 4 小时的时间差（仍在 6 小时窗内），并在断言前
**显式加一条 `total < 1.0` 的前置**——饱和时先报「换一对不饱和的输入」，而不是报一个
看起来像权重算错的差值。

同族的前一例是第 60 条（变异的 `<=` 没走到边界）。**两次都是「用例看起来在钉一个数，
其实那个数在这组输入下是常数」。**

### 第 69 条 · `split_guard` 一旦触发会把 `total` 直接置 0

`domain/memories/events.py:352` 的 `if split_guard: total = 0.0`。所以「有共同确认人物时
不再拆事件」在**产品上**的表现不是「分数从 0.65 涨到 0.81」，而是**从「另起一个事件」变成
「归并进已有事件」**。Task 80 的那条对照用例因此断言的是 `created`，不是分数——
分数只是中间量。S4 实测的「2017-11-05 被拆成 4 个事件」走的正是 `created=True` 这条路。

### 第 70 条 · 簇样本的排序与 1.0 不同（登记，未改）

1.0 `list_face_clusters` 的样本是 `ORDER BY fi.created_at DESC LIMIT 12`；2.0 走
`FaceRepository.list_instances`（`quality DESC, created_at ASC`）再取前 12。
**同一批样本、不同顺序**，界面上是「先看到最清楚的」还是「先看到最新的」的差别。
改它要动 Task 71 已交付并被门禁钉住的排序契约，本轮不动。

### 本轮**没有做**的

> **两条都已由下一轮（§2k 的 E 节）收掉**：定点变异做了四个任务、`make ci` 逐 commit
> 各跑一次。下面两段是当时的状态，保留原样以便追溯。

- **定点变异（红灯 B）这一轮没做。** 前两轮每个任务挑三条要害改一行、验证红灯指向具体
  断言；本轮三个任务的用例里有对照臂（Task 80 的六条全是），但没有做「改掉实现那一行
  看门禁红不红」的动作。**这是一条未结的账**，下一轮补。
- **`make ci` 的逐 commit 复核这一轮有缺口。** Task 75 提交后启动的那次 `make ci` 在
  Task 76 的改动落地之后才跑到 pytest（`CI_OK`，日志
  `logs/ci-20260923-204812-run0.log`），所以它验证的是 75+76 的合并树而不是 75 单独一棵。
  76/80 之后的完整 `make ci` 见下一节。**如实登记，不当作「每个 commit 都跑过」。**

---

## 2k. Task 81 / 82 / 83 / 84 实施期实测

上一轮 §2j 末尾那两条未结的账，本轮的处理写在本节末尾。

### A. Task 81（事件参与者投影 + 确认后重分段）的偏离

#### 第 75 条 · `captured_by` 那一路没有搬，因为 2.0 没有它的数据来源

1.0 `_refresh_event_participants`（`db.py:5567-5574`）写两种角色：`visible_subject`
（0.75）与 `captured_by`（取 `asset.source_confidence`，缺省 0.5）。后者的取值链是
`assets.source_owner_id` → `entities` 里那个已确认的人。

**2.0 的 `assets` 表没有 `source_owner_id` / `source_confidence` 两列**，也没有任何
写入者：1.0 那两个值来自导入侧的 metadata（`db.py:1467-1473` 的
`metadata.get("source_owner_id")`），而 2.0 的 `ImportRequest` 没有 metadata 通道。
**本轮不建这两列**——建出来就是两个零写入者的列，而 `check_reachability.py` 只覆盖模块、
看不见列（这条不对称上一轮已经登记在第 44 条）。计划的「必须钉住的行为」里写着
`captured_by` 的取值口径，那条口径在 2.0 暂时无处可施；**等哪条导入路径真的带来
「谁拍的」这个信息时再连列一起补。**

#### 第 76 条 · 关系候选（`_maintain_event_cooccurrence_candidates`）没有搬

1.0 `upsert_event_participant` 的最后一句是
`self._maintain_event_cooccurrence_candidates(event_id)`（`db.py:2508-2525`）：同一个
事件里两个 `visible_subject` 会往 `relationships` 写一条 `共同出现` 的 **pending** 关系。

2.0 里**没有任何地方读 `relationships`**（人物详情与家庭图谱是 S6）。写进去就是一张
只有写入者、没有读者的表——S4 已在「零调用的能力」上栽过两次。搬它的那一天，
`create_relationship` 也要一起搬，否则这条候选连落库都做不到。

#### 第 77 条 · `refresh_event_summary` 的两处缩减与一次合并

参与者的摘要重写（1.0 `db.py:2585-2610`）在 2.0 少了三样东西：

1. **地点**直接用 `event.place`，不再走 `_resolved_event_place` 的反查表：2.0 的事件在
   归属时就把 `asset_places` 那一行写进 `place` 了，再解析一次等于把同一条规则实现两遍。
2. **GPS 前缀**（`_gps_prefix_for_event`）没有搬：2.0 的 `events` 表没有 GPS 列。
3. 1.0 那条 `source_type != 'video_scene'` 的摘要豁免在 2.0 **恒真**——`events` 表没有
   `source_type` 列，也就没有视频事件。等视频事件进来时要连列一起补。

另外，1.0 在 `_refresh_event_participants` 里发两条 UPDATE（先 `participants_json`、再
`refresh_event_summary`），**事件 `revision` 因此跳两格**。2.0 没有 `participants_json`
这一列（参与者就是 `event_participants` 表），第一条 UPDATE 的剩余效果只有 revision，
所以合成一次 `apply_summary`：**每个受影响事件恰好 +1**，与计划的判据一致。

#### 第 78 条 · 参与者的 `revision` 是**逐观察**递增的（实测 3，不是 2）

1.0 的循环是 `for observation_id in event_observation_ids: ... upsert_event_participant(...)`
（`db.py:5569-5571`），**每条观察各调一次 upsert**。所以一个事件里同一个人的两条证据
会让参与者行的 `revision` 从 1 涨到 3，而不是 2。用例的第一版断言写的是 2，实测 3——
实现是对的，**断言写错了**，按 1.0 的实际行为改正并把这个数写进注释。

#### 第 79 条 · `event_entities` 的 upsert 没有公开方法，但换家那一段照样搬

计划的移植清单里有 `upsert_event_entity`（`db.py:3287`）。2.0 现在**没有任何一处写
`event_entities`**（1.0 的写入者是 agent runtime 与实体抽取，属 S6），搬一个零调用者的
公开方法正是本仓库一路在防的那个形状。但 `rehome` **仍然搬这张表的行**：它的
`event_id` 有指向 `events(id)` 的外键，而连接上开着 `PRAGMA foreign_keys`，
留着就会挡住 `events.merge` 的 `DELETE`。合并语义（同键合并证据、抬置信度）以私有形态
存在，等 S6 有了写入者再开口。

#### 第 80 条 · 两处 2.0 新增的端口方法（计划里没有）

- `EventRepository.events_for_observations`：1.0 是**逐条观察**查一次再在 Python 里分组
  （`db.py:5550-5554`），2.0 一次 `IN` 查完。行为一样，往返次数从 N 降到 1。
- `EventParticipantRepository` 整个端口，以及 `ConfirmedPersonEvents` 这个用例类落在
  `application/memories/assign_events.py` 里（计划把 `participants.py`【新】记在
  infrastructure，application 侧只写了 `assign_events.py`【改】——按计划的文件清单落）。

### B. Task 82（facts 门禁 + 候选来源）的偏离

#### 第 81 条 · `facts_json` 新开 0007，不并进 0006

计划把「加进 0006 或新开 0007」留给了执行时判断。**取 0007**：0006 已经作为一次提交
发出去过，而迁移器只记版本号、不记内容——任何跑过 0006 的库都不会再执行一遍，
把它改宽只会得到「老库少一列、新库多一列」，那种差异**不报错**，只在写事实时炸。

#### 第 82 条 · `FactCandidate` 被 `ObservationFact` 取代（删掉一个重复类型）

`application/memories/maintain_facts.py` 原本有一个 `FactCandidate`（四个字段与
模型自报的 facts 元素完全一样）。本轮把形状收到 `domain/media/models.py` 的
`ObservationFact`——它**是观察的一部分**（跟着 `ObservationSemantics` 从观察器出来、
跟着 `Observation` 落库），而 `domain/memories/events.py` 反过来要 import
`domain/media/models.py` 的 `GpsPoint`，把形状定义在 memories 会成环。
`maintain_facts.py` 的 `FactCandidate` 与 `DEFAULT_FACT_CONFIDENCE` 一并删除。

**顺带登记一处 1.0 的死兜底**：`pipeline.py:350` 的
`normalize_confidence(fact.get("confidence"), result.get("confidence", 0.5))` 那个 0.5
在 1.0 就到不了——`analyze_image` 已经用 0.65 归一过一遍（`model_clients.py:1190`）。
2.0 的 `ObservationFact.confidence` 默认值因此是 **0.65**。

#### 第 83 条 · `MaintainFacts.confirmed_people` 删掉了默认值

S4 给它留的是 `lambda _scope_id: frozenset()`。代价与 Task 80 删掉 `AssignEvents` 那两个
默认值时完全一样：**「忘了接线」与「真的没有已确认人物」在类型上长得一模一样**，
而事实恒空不报错、只是永远查不到。删掉之后装配点少写一个参数就是 `TypeError`。

#### 第 84 条 · `purge_unanchored` 在 S5 没有生产调用者

1.0 `db.py:3433-3447` 的 `purge_unanchored_facts` 唯一调用点是
`scripts/maintenance/rebuild_memory.py:128`，2.0 不复刻脚本。本轮把它做成
`FactRepository.purge_unanchored` + `MaintainFacts.purge_unanchored` 并有两条用例
（正例 / 反例），**但不接任何用例**——接了就是自己发明一个触发时机（1.0 的
cluster reject 与 rename 都不调它）。**如实登记为「有能力、无调用者」**，
等 S6 的维护入口出现再接。

#### 第 85 条 · 一条**同构装配**的用例证明不了生产装配（本轮最有价值的一条实测）

Task 82 的端到端用例第一版是自己拼一个 `MaintainFacts`（「同构装配」），
把生产 `api/routes/media.py` 里那句 `candidates=lambda observation: observation.facts`
换成 S4 的恒空元组 —— **它照样全绿**。用例验的是一个自己拼出来的装配。

改法是加一条**走 `build_import_assets(container, observe=True)`** 的用例。加上之后，
同一个变异立刻变红（红点落在 `test_the_production_wiring_writes_the_observed_fact`）。
**这条教训适用于所有「装配点 + 用例各自拼一遍」的地方**：同构不等于同一条路径。

### C. Task 83（`person_bridge` + 实体检索通道）的偏离

#### 第 86 条 · 1.0 的 `person_bridge` 投影写的是 `str(dict)`，2.0 写名字

1.0 `retrieval_indexes.py:74-80` 的 `_terms_from_field` 对列表元素一律
`_normalize(item)`，而 `observations.people` 的元素是
`{"entity_id":…, "name":…, "status":…}` 这样的 dict —— `str(dict)` 写进
`person_bridge` 之后，`WHERE normalized_value = '妈妈'` **永远查不中**。
1.0 里真正干活的是 `EntityRetriever._resolve_asset_ids_for_person` 那条
「投影查不到就退回直接读观察」的兜底（`entity.py:50-59`）。

**2.0 的值源是名字**（`Observation.people` 已经是名字元组）。投影因此真的能命中；
兜底那条路照搬，但它现在管的是「投影还没建起来的库」而不是「投影建坏了」。
测试 `test_confirming_a_person_projects_the_name_not_the_raw_snapshot` 钉的是
`normalized_value` 的**值**——把名字换回 `str(dict)`，它变红。

#### 第 87 条 · `EvidenceRequest.person` 是 2.0 新增字段（计划文件清单里没有）

计划的 Task 83 文件清单只有 `retrieve_entity.py`【新】+ `lexical.py` / `ports.py` /
`search_terms.py`【改】。但 entity 通道**只在有人物条件时才给召回信号**：不把
`retrieve_evidence.py` 的 `EvidenceRequest` 加一格 `person`，那条通道在类型上就没法被
喂过——它会永远返回空，而不是「被接上了」。这与 S4 给 `metadata` 通道加
`media_types` / `time_bounds` / `place` 是同一个处境（那几个字段的 `http` 入口同样要等
S6 的查询解析）。所以 `retrieve_evidence.py` 也在本轮改了，登记在此。

#### 第 88 条 · 兜底那条路不支持跨空间查询

`RetrieveEntity` 的兜底读 `ObservationRepository.list_scope(scope_id, limit)`，而它要求
一个具体的 scope；1.0 的 `list_observations(limit=100_000)` 可以不带。`scope_id` 为空时
兜底**不跑**（投影那条路照跑）。2.0 目前没有跨空间的人名查询调用方
（`EvidenceRequest` 总有 scope），所以这里是「不支持」而不是「随便挑一个空间」。

#### 第 89 条 · 兜底的扫描上限 100_000 写在常量里

1.0 `entity.py:52` 的 `list_observations(limit=100_000)`。写成
`FALLBACK_SCAN_LIMIT` 而不是一个看起来无穷大的默认值：**超过它的照片会静默召回不到**，
这个数必须可见。

### D. Task 84（全局重聚类维护入口）的偏离

#### 第 90 条 · 仓储补了 **6 个**方法（计划只列了 application + route + 测试）

计划的任务文件清单里，Task 84 只有 `recluster.py`【新】、`routes/maintenance.py`【新】、
`main.py` / `container.py`【改】+ 两个测试。**实际至少要给仓储补方法**（上一轮已经预告）：

| 端口 | 新方法 | 1.0 对应 |
|---|---|---|
| `FaceRepository` | `list_verified_instances` | `db.py:4961-4967` 那条 SELECT |
| `FaceRepository` | `assign_instances`（批量改归属） | `db.py:4993-4996` 的 `executemany` |
| `FaceRepository` | `set_cluster_state`（状态 + member_count） | `db.py:4997-5001` |
| `FaceRepository` | `list_uncertain_unclustered` | `db.py:5054-5060` |
| `FaceRepository` | `list_confirmed_clusters`（认领目标） | `db.py:5085-5088`（`member_count >= 2`） |
| `EntityRepository` | `retire_unclustered_candidates` | `db.py:5118-5132`（`_sync_face_entity_statuses`） |
| `VectorRepository` | `update_metadata` | `db.py:5028-5038`（改向量 metadata 的 `cluster_id`） |

合计 **7 个**。计划的清单低估了这条路径的读写面，这一行是如实登记。

#### 第 91 条 · `_sync_face_entity_statuses` 写成一条 SQL 而不是逐实体循环

1.0 是「列出所有 person 实体 → 逐个查活跃簇数 → 逐个 UPDATE」。2.0 写成**一条带
`NOT EXISTS` 的 UPDATE**：同一件事，但少了 N 次往返，而且判据只有一处。
唯一的语义差别是「已确认的人不动」从 Python 的 `if` 变成 SQL 的 `status != 'confirmed'`——
两条用例（孤儿被撤 / 已确认的人不动）分别钉住两侧。

#### 第 92 条 · 认领阈值 0.75 不读环境变量

1.0 读 `os.getenv("FACE_ATTACH_THRESHOLD", "0.75")`。2.0 写成常数：`domain-purity`
禁止 domain 碰 `os`，而且这类阈值一旦能从环境改，**同一个库在两台机器上会聚出不同的
结果，差别只藏在部署脚本里**。同组的另外两个数（0.08 的领先余量、`>= 2` 的最小成员数）
1.0 本来就是硬编码的。

#### 第 93 条 · 新增 `api/schemas/maintenance.py`（计划只列了 routes）

与其余端点同构：请求 / 响应模型放 `api/schemas/`，路由只做映射。
`threshold` / `minimum_quality` 的默认值取 `FaceClusterer` 的类默认（`0.30` / `0.30`），
放在**请求模型**上是为了让默认值出现在 OpenAPI 契约里——调用方看得见自己不传时会按
什么口径跑。**不取** 1.0 两个 benchmark 调用方的 `0.30` / `0.55`（后者与类默认不一致，
已登记在证据 39）。

#### 第 94 条 · `bootstrap/container.py` 没有改（计划列了它）

计划的文件清单里有 `container.py`【改】。实际不需要：维护路由与身份路由一样，
在路由里从已有的 `container.*` 字段构造用例（那两处是「用例装配点」，
`container.py` 的职责是**适配器**装配）。为一条不需要新适配器的路由加一个 container
字段，只会让「谁是装配点」多一处。

#### 第 95 条 · 认领时的同分排序改成确定性

1.0 `ranked.sort(reverse=True)` 只按分数排，同分时名次由簇 id 的降序决定——
同一份数据换个插入顺序就换一个答案，而这里**挂错人是会留痕的**。
2.0 用 `(-score, cluster_id)`；0.08 的领先余量本来就会挡掉近似同分，
这条是为了让「真的同分」也确定。

### E. 上一轮两条未结的账，本轮的处理

- **70c（定点变异）本轮补上了。** 四个任务各挑了要害改一行验证红灯指向具体断言：
  Task 81——把 `confirm` 里那两句接线注释掉（红灯精确落在
  `test_confirming_a_cluster_merges_the_two_events_its_photos_created`）；
  Task 82——①候选来源退回恒空、②门禁退回恒空、③解析器丢掉 facts，
  三次都红且红在对应的断言上；Task 83——①通道顺序排到 lexical 之后（分数从
  `1.0 + 2/61` 掉到 `1/61`）、②退回 S4 的不建 `person_bridge`、③把名字换成
  `str(dict)`（重现 1.0 的缺陷），三次都红。
- **70d（每个 commit 单独一次完整 `make ci`）本轮每棵树各跑一次**，日志见交付记录；
  本轮不再有「验证的是合并树」的缺口。

#### 第 96 条 · 1.0 只撤「有实例、但实例被并走」的簇；一张脸都不挂的簇不会被清

`recluster_faces` 的 `stale_ids` 是 `{row["cluster_id"] for row in instances ...} - active`
（`db.py:5019-5021`）——它的来源是**实例表**，所以一个 `member_count = 0`、一张脸都不挂
的簇**根本不在那次重算的视野里**，状态保持原样。2.0 照抄了这个行为
（`test_a_cluster_with_no_instances_at_all_is_not_retired` 把它钉住）。

真正的清理落在实体那一侧：那种簇对应的候选人物会在
`retire_unclustered_candidates` 里因为没有活着的簇被转成 rejected。所以「空簇」这件事
在 2.0 里是**一半被清理、一半留着**——留着的那个是历史审计，转掉的那个是人物列表的
显示面。这不是我们的设计选择，是 1.0 的行为；登记在此，免得后来人把它当成 bug 顺手
删掉一个簇。
