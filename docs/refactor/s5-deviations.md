# S5 违背 / 偏离审计（人脸与身份层 · 收尾与画像）

审计对象：`docs/refactor/2026-09-23-s5-implementation-plan.md`。
基线：1.0 提交 `2d2cee7c`（`behaviour-baseline.md` 钉的那一个），
实读的是工作树 HEAD `fba87be9`（两者在人脸与身份层无差异——`git log` 显示这两条提交
都只动 photobench 遥测）。2.0 起点提交 `f663522`。

**这份文档是本轮规划的产物，不是实施记录。** Task 69 已在隔离副本落地（见计划文档的验证记录），
Task 70–86 尚未实施，所以下面每一条的「代价 / 现状」写的是**计划形态**，实施时会按 S4 的
惯例在文末追加 `§N 实施期新增的偏离`。

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
| 4 | **全局重聚类没有生产调用者** | `recluster_faces`（`db.py:4956`）的两个调用点：`scripts/benchmarks/ingest_album3_v2.py:67`、`scripts/benchmarks/ingest_household_face_benchmark.py:57`。生产导入路径（`pipeline.py:316-320` / `:443-470`）只调 `add_face_instance`。 | **不移植**（见第 4 条裁决） |
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
| 29 | 全局重聚类 + uncertain 认领 + 实体状态同步 | `db.py:4956/5048/5118` | 见证据 4/5：调用点全在 `scripts/benchmarks/`。与 S4 不移植 `_event_pair_score` 同判据 |
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
| 37 | **`docs/architecture/module-map.md` 基线里有一行重复**：`sentrix/application/retrieval/retrieve_evidence.py` 在表里出现了**两次**（`git show f663522:` 里该路径命中 10 次）。用整表重写脚本生成后变成 9 次。`check_module_map.py` 两种状态都报 0 问题。 | **保留修正**（生成器的必然结果；若要零无关改动可以加回去，但下次再跑生成器还会掉） |
| 38 | **`continue` vs `break` 是一条杀不掉的变异** —— 32 处变异里 31 处被杀，存活的就是它（候选簇已降序排过，两者等价）。 | **登记为「未被门禁覆盖」**，不写测试硬凑 |
| 39 | **`recluster_faces` 的口径与 `FaceClusterer` 默认值不一致**：`db.py:4956` 的 `minimum_quality=0.55`，而 `face_clustering.py:67` 的类默认是 `0.30`。Task 69 只搬了 domain 的默认值，那个 `0.55` 属于被判死的 benchmark-only 调用方。 | **登记**；若第 4 条裁决改判「要全局重聚类」，这个 0.55 要一起搬 |

**同时实测确认了两件事**（不影响判决，只加证据）：
`pairwise_metrics` 与 `AdaFaceAdapter` / `MagFaceAdapter` 在全仓**确实没有生产调用者**（证据 1/33 复核通过）；
1.0 的四个纯函数里**没有任何 `os.getenv`**，所以「读 env 的落成模块级常量」这条预设
在这个范围里不适用（那些读取全在被判死的适配器里）。

---

## 3. 这份文档没有做的事（如实记录）

1. **没有跑 1.0 的测试套件。** 所有 1.0 的行为都是从**源码 + 行号**读出来的，
   只有第 3 条（兜底画像）做了实跑验证。S5 执行时每个任务的门禁才真正把这些行为钉住。
2. **没有查运行中进程的实际环境**（`/proc/<pid>/environ` 在这一轮里读不出内容，
   `sudo` 需要密码）。所以 `SENTRIX_PERSON_INSIGHT_SCOPES` 的「默认关闭」是**从代码默认值推断的**，
   不是从 153 的部署实况确认的。这一条在第 20 号证据里已经标明。
3. **没有实施 Task 70–86。** 阶段 3–5（逐任务红/绿、跨任务回归、每 commit `make ci`）
   只对 Task 69 执行（见计划文档）。
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
