# S6 实施计划（草案 · 2026-09-29）

## 0. 本文档怎么来的，以及一次修正

### 0.1 第一版跑偏了，错在哪

我先做了一次覆盖审计（把 1.0 的 119 条路由按四域逐条对拍），然后**基于自己的审计结果**
写了一个「清理收口」的 S6。**而路线图里 S6 早有定义**——设计文档
`docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md` §8「切片与里程碑」：

| # | 切片 | 内容 | 验收 |
|---|---|---|---|
| **S6** | **检索补全** | 补齐六路召回 + 硬条件（时间/人物/地点）+ 证据包 + trace；加 `inspect_photo`(VLM) / `read_photo_text`(OCR) / `query_memory_facts` / `query_photo_people` | 证据包和 trace 能展开 |
| S7 | 家庭关系 | family graph + runs + relationships + membership + 关系确认 | 能建关系，助手能引用 |
| S8 | 音频与视频 | FunASR 音频、关键帧/场景切分、派生帧可回溯原视频秒数 | 导入视频，场景成型、能跳回原片 |

S3 计划里也留了明确的交接（`2026-09-22-s3-implementation-plan.md:6145`）：

> 留给后续切片的：**双层防伪的语义评审层（S6）**、**恢复作答的四级阶梯（S6/S7）**、
> **六路取证的补齐（S6）**。

**错在哪**：我把「审计发现了什么」直接当成了「下一步该做什么」，中间少了一步
**查已定义的切片划分**。这个项目从 1.0 逐行移植、每一步都要有出处，而我在写计划这一步
偏偏没查出处。**审计的结论不是计划，是计划的输入。**

**保留的部分**：审计本身有效且已复核。它回答的是「1.0 与 2.0 的差在哪」，
而 S6 要回答的是「差集里哪一块是这一步该做的」——两者不是一回事，但前者是后者的前提。
§1 是前者的结论，§2 起是后者。

### 0.2 证据来源

- 1.0 的 119 条路由（`grep '@app\.\(get\|post\|put\|patch\|delete\)' backend/*.py`）按四域逐条对拍；
- 2.0 运行中实例 8191 的 `openapi.json`（39 条端点）；
- 结构实测：五条 import-linter 契约、`scripts/check_*.py` 的判定逻辑、迁移建表与
  `git grep` 的引用比对、线上库 `data/sentrix.db` 行数；
- 路线图：设计文档 §8、S3 计划的交接段、`frozen.md`、`capability-map.jsonl`。

每条结论带 `文件:行`，均可在当前工作树复核。

---

## 1. 覆盖审计的三个结论（Q1 / Q2 / Q3）

### 1.1 框架是否足够清晰？——**骨架清晰，但自检有一条粒度断层**

**正面（实测）**

| 项 | 实测值 |
|---|---|
| 五条分层契约 | 全 KEPT；**`ignore_imports` 例外 0 条** |
| 各层规模 | domain 6.6k / application 5.7k / infrastructure 8.0k / api 2.3k / **bootstrap 仅 258 行** |
| 应用层分解 | 一个用例一个文件、动词开头（`enroll_face` / `extract_moments` / `retrieve_visual`…） |
| 生产可达性 | `check_reachability.py` 报 0 问题 |
| 文档诚实度 | 设计 §2.3 自己删掉一个空头承诺（原写「CI 打印例外条数」，实为 import-linter 不提供该输出） |

**断层：`check_reachability.py` 的判定单位是模块，不是符号**

`scripts/check_reachability.py:178-192` 取 `name`（**模块名**）去问谁用过它：

```python
callers = {other for other in reachable
           if other != path and any(target == name and local in used[other]
                                    for local, target in bound[other].items())}
wired = bool(callers)
```

于是 `repositories/facts.py` 因为 `container.py` 构造了 `SqliteFactRepository` 就算「已接线」——
**它里面 `list_facts` 有没有人调，这个问题从没被问过。**

S4 栽过两次「代码在、没人调」，这条门禁把那个教训变成了可机械判定的东西。
**但它修的是模块级，同一个失效在方法级复发了**，而且三个域同时中招（见 §1.2 ①）。

**另外两条**

- **没有任何门禁盯 schema**：`ingest_batches` 表建在 `migrations/0002_media.sql:48`、
  `assets.batch_id` 列建在 `:9`，而全 `src/` 唯一提到它的是
  `application/media/import_album.py:18` 的一句**注释**；线上库该表 0 行、该列全 NULL。
- **`check_capability_map.py` 是单向校验**（已在 `s5-deviations.md` 第 41 条登记）：
  能抓「声称验过、其实没有」，**抓不到「其实验过、却标着 pending」**——
  `person_moments` 与 `person_portrait_revisions` 就卡在 `pending`，而 S5c 已交付它们
  （2026-09-29 实测：8 行瞬间 / 2 篇画像 / `model_name: qwen35-q4km`）。

### 1.2 1.0 是否已经完全实现？——**没有**

119 条路由的三分归属：

| 域 | 路由数 | 已实现 | 已冻结（有记录） | **缺失（从没被决定过）** |
|---|---:|---:|---:|---:|
| 媒体 / 导入 / 视频 / 观察 | 19 | 7 | 0 | **13** |
| 记忆 / 事实 / 实体 / 地点 | 15 | 3 | 0 | **12** |
| 身份 / 人物 / 关系 | 44 | ~22 | ~11 | **~11** |
| 助手 / 配置 / 运维 | 41 | ~27 | ~9 | **~13** |
| **合计** | **119** | **~59** | **~20** | **~49** |

**那 49 条之所以要紧，是因为它们从没被决定过要不要做。** 三个值得注意的形状：

**① 代码写好了、没人调**

| 位置 | 状态 |
|---|---|
| `repositories/facts.py:145 list_facts` / `:103 set_status` / `:113 supersede_others` | 全仓 **0 调用者** |
| `repositories/appearance.py:176 list_for`（外观证据读） | **0 调用者**（唯一使用点是 `observe_appearance.record` 的写） |
| `repositories/relationships.py:113 create` / `:182 confirm` / `:209 retract` | **0 调用者** |

其中 **facts 是一条功能缺陷，不只是缺接口**：写链路完整（`MaintainFacts` + `review_cluster` 的
家庭角色事实），表有 `status TEXT NOT NULL DEFAULT 'active'`（`0005_timeline.sql:94`），
`FactStatus` 四值齐全（`domain/memories/facts.py:38-41`）。但 `decide_fact_write` 在「值变了」时
会插一条 `pending`——**而它永远升不了级、删不掉、也看不见**。

> 精度声明：线上 `album1` 的 `facts` 表 **0 行**（该库导入于 S4/S5b 接线之前），
> 所以这是**代码可证、数据未显**的潜伏缺陷，不是观察到的现象。

**② 后端有、前端不接**：`GET /assistant/conversations/{id}` 返回 `messages` 与 `turns`
（`api/routes/assistant.py:137,149-159`），但前端 `shared/api/client.ts` **没有 `getConversation`**
（全前端 grep 零命中）。`AssistantPanel.tsx:39-43` 明写「重新进入时由上层决定要不要回放」，上层没做。
**结果：重进一个会话是空白。**

**③ 枚举有、生产者没有**：`TerminationReason.CANCELLED` 在 `run_turn.py:632` 做了映射，
但全仓**没有任何地方产生它**，`useTurnStream.ts:107` 对 `"cancelled"` 的判断因此是死代码。
回合跑在 daemon 线程（`assistant.py:225`），前端断开 SSE 只让事件生成器 `return`，
**worker 照跑到预算耗尽为止**。

### 1.3 缺的是否必要？——**按归属分，不按大小分**

第一版我按「必要性」分了 A/B/C/D 四类，这是错的分类轴——它把「该现在做」「该 S7 做」
「不建议做」混在了一格里。正确的轴是**归属**：

| 归属 | 内容 |
|---|---|
| **S6（检索补全）** | 六路里的 `text_ann` / `adjacency`、硬条件（时间/人物/地点）、证据包与 trace 可展开、语义评审层、恢复作答阶梯、四个工具 |
| **S6 的前置依赖** | `query_memory_facts` 依赖 facts 读路径（§1.2 ①）；`query_photo_people` 依赖人物检索（S5b 已给 `retrieve_entity`） |
| **S7（家庭关系）** | family-graph 7 条、relationships 5 条、`person_role_hypotheses`、`find_graph_violations`（`s5-deviations.md` 第 15/35 条已标「随 S7」） |
| **S8（音频与视频）** | 视频 4 条路由 + 整条抽取/场景链、FunASR |
| **S9/S10（PhotoBench）** | 评测后端/前端重构 |
| **S11（服务与部署）** | model-manager 收敛；**`hardware` / `model-profiles` / `runtime-providers` 9 条都归这里**——设计文档 §2.1 明写「2.0 不管理模型进程生命周期」，且 1.0 那 8 条本身只是 8500 的薄代理 |
| **S6（补，见 §3 第六组）** | **语义实体 / entity-groups / knowledge**——2026-09-29 裁决归 S6。它确实是检索补全的一部分：2.0 的 `retrieve_entity` 目前只服务人物，非人物语义实体是 entity 通道缺的那一半 |
| **S7（补）** | **恢复作答的四级阶梯**——2026-09-29 裁决归 S7（S3 计划原写「S6/S7」，现定 S7） |
| **未归属（需裁决）** | 鉴权；observations 原始列表；OCR settings / telemetry / query-gaps；`/api/dashboard`；**回合取消**（语义已裁决「丢弃部分结果」，但它不属检索补全，本切片不排）；**会话历史回放**（后端已有、前端不接，一行 client 函数的事，但同样不属 S6） |

---

## 2. S6 的真实起点

逐条对设计文档 §8 给 S6 的四项内容：

| S6 要求 | 2.0 现状 | 证据 |
|---|---|---|
| **六路召回补齐** | **5/6 才是真缺口**（2026-09-29 核查后修正）。缺 `text_ann`；**`adjacency` 不是缺口**——见下 | 1.0 有 `retrieval/{visual_ann,text_ann,lexical,metadata,entity,adjacency}.py` 六个，但 `configs/retrieval/defaults.json:10` 写着 `"adjacency": false`，而 `build_default_retrievers`（`retrieval/__init__.py:80`）对关掉的通道**直接 `continue`，连实例都不建**。`AdjacencyRetriever` 只被自己的模块、工厂和 `test_adjacency_retriever.py` 引用，`.expand(` 唯一的生产调用点在 Kernel 里——而 Kernel 只有在它被构建时才跑。**它在 1.0 是死代码**，与 `agent_contracts.py`（392 行、静态不可达 → 不搬）同一类。2.0 的 `retrieve_evidence.py:22` 早就把这件事写进了注释，而我在写第一版计划时没核到 |
| **`text_ann` 的真实体量** | **不是「加一路召回器」** | 1.0 的 `text_ann` 要：查询文本 → `TextQueryEmbedder` → semantic/episodic ANN → 把 observation 命中映回 Asset。而 2.0 **整条文本嵌入侧都不存在**：`scheme.py` 自己写着「TEXT 一侧（BAAI/bge-m3，1024 维，HTTP sidecar）留给 S4」而 S4 没做；`config.py:27` 的 `text_embedder_url` 全仓只有它自己那一行；线上库 `memory_vectors` **只有 `visual` 空间**（53 asset + 117 face_instance），一条文本向量都没有。所以这是一条**多段任务**：端口 + adapter + 写入 + 召回 + 存量回填 |
| **硬条件（时间/人物/地点）** | **0** | `grep -iE 'hard_filter\|time_range\|person_filter\|place_filter'` 在 `application/retrieval/` 与 `domain/retrieval/` **零命中** |
| **四个工具** | **4 个全缺** | 2.0 注册的是 `search_memories` / `get_result_page` / `get_original_photos` / `get_person_profile`（`domain/assistant/tools.py:54,81,98,121`）；`inspect_photo` / `read_photo_text` / `query_memory_facts` / `query_photo_people` 全仓零命中 |
| **证据包 + trace 能展开** | **部分** | `domain/assistant/evidence_ledger.py` 在，`application/retrieval/retrieve_evidence.py` 在（融合 + 缺口传递到 HTTP 响应）；**trace 的展开面未核** |
| 语义评审层（S3 交接） | **部分** | `domain/assistant/judge.py` 203 行 + `application/assistant/judge.py` 53 行；「双层防伪」的第二层缺否未核 |
| 恢复作答的四级阶梯（S3 交接） | **移出 S6 → S7**（2026-09-29 裁决） | 仍未核形态：1.0 有 `agent_runtime/runtime.py:93 _ocr_recovery_instruction`、`:274` `system_recovery`、`:436` 的确定性恢复；**「四级阶梯」在 1.0 里我没找到独立形态**（`grep '四级'` 只命中「不确定性用自然语言四级」，那是措辞分级，不是恢复阶梯）。S7 动工前必须先核，不能猜 |
| 语义实体 / entity-groups / knowledge | **0（并入 S6）** | 2026-09-29 裁决归 S6。`semantic_claims` / `semantic_profiles` 表在 2.0 的 7 个迁移里**根本不存在**；`repositories/entities.py:180` 的列表查询**硬编码 `entity_type = 'person'`**，非人物实体抽取（1.0 `db.py:3840-4032`）没移植 |

**1.0 的调用量给优先级**（25,112 条轨迹全量，设计文档 §1.3）：

| 工具 | 1.0 调用次数 |
|---|---:|
| `search_memories` | 17,718 |
| **`inspect_photo`** | **10,721** |
| **`read_photo_text`** | **2,867** |
| `get_result_page` | 1,649 |
| **`query_memory_facts`** | **1,603** |
| **`query_photo_people`** | **887** |
| `get_original_photos` | 4 |

**S6 要补的四个工具在 1.0 的真实调用量是 16,078 次**——不是锦上添花，是助手的主要取证手段。
没有它们，2.0 的助手只能靠 `search_memories` 一条腿走路。

---

## 3. S6 任务表

任务编号续前端（终于 112），从 113 起。

### 第一组 · 前置（不做，后面的工具接不上）

| Task | 内容 | 为什么是前置 |
|---|---|---|
| **113** | facts 读路径接线：`list_facts` 接上 + `GET /facts` | **`query_memory_facts` 没有它就没有数据源**。仓储方法已写好、零调用者，是「接线」不是「新做」 |
| **114** | facts 状态机闭合：`confirm` / `reject` 两条用例 + 路由，`set_status` / `supersede_others` 接线 | 只接读不接写，`pending` 仍然永久卡死；而 `query_memory_facts` 读到的会是永远升不了级的行 |

### 第二组 · 六路补齐

| Task | 内容 |
|---|---|
| **115a** | **文本嵌入侧**：`TextEmbedder` 端口 + bge-m3 HTTP adapter（8101 sidecar 已在跑、已实测 `{"status":"ok","model":"BAAI/bge-m3","dimension":1024}`）+ `scheme.py` 的 TEXT 常量（`scheme.py` 自己注明这一侧「留给 S4」而 S4 没做） |
| **115b** | **写入侧**：导入时把 observation 的语义文本写进 `semantic` 空间（1.0 `pipeline.upsert_vector` 的 `source_type=observation`） |
| **115c** | **召回器**：`RetrieveTextAnn`（1.0 `retrieval/text_ann.py`），把命中映回 Asset |
| **115d** | **存量回填**：线上 53 张 asset 已有 text 语义但没有文本向量。回填脚本（体例照 T85 那次人脸回填） |
| ~~116~~ | **不实现 `adjacency`**（2026-09-29 核查：1.0 默认关着，是死代码）。按 `agent_contracts.py` 的先例处置，理由记入 `frozen.md` |
| **117** | 通道补齐的**对照验收**：同一 query 在 `text_ann` 接上前后各跑一次，断言 `channels` 里出现了 `text_ann` 键且**名次不是垫底**（垫底意味着它其实没贡献） |

### 第三组 · 硬条件

**2026-09-29 核查后重写。** 原计划写成「时间 / 人物 / 地点三个独立任务」，那把机制看小了。
核 1.0 之后：**它们是同一件事——条件判定（condition pass）**，而 2.0 整个缺这一步。

1.0 有**两套**机制，别混：

| 机制 | 作用 | 谁在用 | 2.0 现状 |
|---|---|---|---|
| `HardFilterContext` → 各通道的**召回前筛** | 影响「召回什么」，不保证结果满足条件 | 只有 `metadata` 通道真用（`metadata.py:33` 的早退）；`visual_ann` / `lexical` / `entity` / `text_ann` 只从它取 scope | ✅ 已有（`EvidenceRequest` 的 time_bounds / place / person / media_types） |
| `spec.constraints` 里 `strictness == HARD` → **融合后过滤** | 保证「结果真的满足条件」 | `_evaluate`（`evidence_retrieval.py:414-425`），`_condition` 对 time / media / place / person 四维返回 matched/contradicted | ❌ **完全没有** |

`retrieve_evidence.py` 的模块 docstring 自己写着「S4 只搬前两步（召回 + 融合）。
后两步要种子分级与 `EvidencePacket`，属于 S6」——第三条就是**条件证据判定**。

| Task | 内容 |
|---|---|
| **118** | **条件判定的纯规则**：`_condition` 的四维（time / media / place / person）→ 领域层纯函数，各自返回 matched / contradicted / unknown |
| **119** | **融合后的排除**：HARD 约束不满足即排除（negated 反过来），并把 `excluded_count` 传出来——1.0 的 `packet.excluded_count` 就是这个 |
| **120** | **HTTP 面**：`/search` 接受 `time_range` / `person` / `place` / `media_type`，接进 `EvidenceRequest` |

**为什么原先拆成三个是错的**：三个条件走的是同一个判定与同一个排除动作，
拆开会让「排除逻辑」写三遍——而 1.0 那份是**一张表**（`_condition` 按 dimension 分派）。
按条件拆任务，等于把一张表切成三张。

**判据**：每个维度都要有「过滤前有、过滤后没有」的用例——那是这一条唯一的可见效果。
`person` 那一维特别注意：1.0 在人物不匹配时返回 **`unknown` 而不是 `contradicted`**
（`evidence_retrieval.py:479`），而 HARD 的判据是 `status != "matched"`——
所以 unknown 一样被排除。这个区别只在 `SEMANTIC` 档上才有意义（那里只排除 contradicted）。

### 第四组 · 四个工具

| Task | 内容 | 依赖 |
|---|---|---|
| **121** | **`query_memory_facts`——依赖核查后重估为一条子系统，不是一个工具**（2026-09-29 实测）。体量：`structured_memory.py` 311 行（`StructuredMemoryExecutor` + 16 个方法）+ 工具侧 `_query_memory_facts` 与四个 helper（`_draft_from_filters` / `_spec_for` / `_resolve_entity` / `_fact_samples`）约 120 行 + **`QuerySpec` / `Constraint` / draft 这套查询契约**（1.0 `query_contracts.py` 406 行，2.0 只移植了其中的 `parse_time_expression`）。三套枚举：`operation` {count, exists, first, last, group} × `subject` {asset, person_appearance, processing} × `filters` {time, media, place, person}。**建议拆成 121a 查询契约 / 121b executor / 121c 工具与注册**。前置 113/114（facts 读路径）仍然成立 |
| **122** | `query_photo_people` | S5b 的 `retrieve_entity` |
| **123** | `inspect_photo`（VLM，1.0 调用 10,721 次） | 8100 上的视觉端点（当前 qwen3.5-4B + mmproj，已实测可用） |
| **124** | `read_photo_text`（OCR，1.0 调用 2,867 次） | **动工前核查**：1.0 的 `ocr_tool.py`（638 行）用的是哪个 OCR 后端，2.0 有没有 |

> **T121b 是「重写」不是「移植」**（2026-09-29 二核）。`structured_memory.py` 里 5 个方法
> **直接拼 SQL**（`_base_query` / `_place_clause` / `_count` / `_group_by` / `_list`），
> 而那份 SQL 写死在 1.0 的 schema 上：
>
> | 1.0 | 2.0 |
> |---|---|
> | `json_extract(a.metadata_json, '$.reverse_geocode.{label,name,province,city,district}')` | **没有 `reverse_geocode`**——地点权威来源是 `asset_places` 表里的 `Place` |
> | `a.captured_location` 当文本 `LIKE` | `GpsPoint` 对象（`as_text()`） |
> | `a.derived_kind` | 待核 |
> | `store.connection` / `store.list_assets` | `Database.connect()` + 仓储 |
> | `geocoding.place_alias_names` | `PlaceAliases` |
>
> 而且按 2.0 的分层，**这些 SQL 该落进 infrastructure 的仓储**，不是 application 用例。
> 所以 T121b 的实际定义是：把 5 处 SQL 逐条翻译到 2.0 的 schema 并搬进仓储层，
> 其余 11 个方法（纯编排）才谈得上移植。**翻译 SQL 是静默出错的高发区**
> （`LIKE` 该不该是 `instr`、少了 `derived_kind` 过滤、alias 展开漏一支），
> 每条都要有「同一份数据、两条路径给出同一个数」的对照用例。

> **一条方法论，由两个正好相反的案例得出**（2026-09-29）：
>
> | 工具 | 注释说 | 代码说 | 真相 |
> |---|---|---|---|
> | `adjacency` | 配置里 `"adjacency": false` | 类存在、工厂会跳过它、`.expand(` 无生产调用者 | **是死的**——注释对 |
> | `query_memory_facts` | `tools.py` 模块 docstring：「**全量聚合工具已删除**」 | `tools.py:2690` **就注册着**；`runtime.py:604/654` 还在特判它；轨迹里 1,603 次调用 | **是活的**——注释过期 |
>
> **两次都不能只信一边。** 只信「1.0 有哪些 .py 文件」会把 adjacency 当缺口（第一版计划就这么错了）；
> 只信注释会把 query_memory_facts 当成已删。**判一个能力在不在，要看它有没有注册、有没有生产调用者**——
> 那正好就是 `check_reachability` / `check_method_reachability` 这两条门禁在 2.0 里问的问题。

### 第五组 · 收口

| Task | 内容 |
|---|---|
| **125** | 证据包 + trace 的可展开面（§8 给 S6 的验收条件就是这一条） |
| **126** | 语义评审层的第二层（「双层防伪」——**动工前核查 1.0 的第二层是什么**） |

### 第六组 · 语义实体（2026-09-29 裁决并入 S6）

非人物语义实体是 entity 通道缺的那一半：2.0 的 `retrieve_entity` 目前只服务人物
（`repositories/entities.py:180` 的列表查询**硬编码 `entity_type = 'person'`**）。
这一组做完，`GET /entities` / `entity-groups` / `knowledge` 三条 1.0 路由才算有落点。

| Task | 内容 | 动工前须核 |
|---|---|---|
| **127** | 迁移 0008：`semantic_claims` / `semantic_profiles` 表族 + 非人物实体抽取（1.0 `db.py:3840-4032`） | 1.0 那两张表的**实际列与被引用处**——它们 2.0 一张都没建，抄错列名要等到写入时才炸 |
| **128** | `GET /entities` + `GET /entities/{id}` + `PUT /entities/{id}/properties/{key}`（接 `repositories/entities.py` 的 `list_people` 放开 person 过滤、`set_property`/`maintain_property`/`list_properties` 三个零调用方法） | `maintain_entity_property_values`（多值合并）在 1.0 只被语义抽取调用，`s5-deviations.md` 第 46 条记过它「本切片没有调用者」——**这就是那个调用者** |
| **129** | `GET /entity-groups` + `/{id}`（1.0 是 `db.py:4194` 现算的投影，不落表） | 投影的**分组键**是什么 |
| **130** | `GET /knowledge`（1.0 返回 profiles + claims + spaces 三块） | — |
| **131** | 把非人物实体接通 `retrieve_entity`，让 entity 通道对语义实体也生效 | 与 119（人物硬条件）的边界：人物是硬条件，语义实体是召回通道，**两者在 1.0 里不是一回事** |

### 收口

| Task | 内容 |
|---|---|
| **132** | 收口：`capability-map.jsonl` 补本切片新能力；把 §1.1 的三条自检缺口**或补或正式记入不做** |

### 伴随任务（低成本的防腐，与上面并行）

| Task | 内容 | 为什么现在做 |
|---|---|---|
| **133** | **方法级可达性**：新增一条检查，对已判定可达的模块看其公开**方法**有否调用者。§1.2 ① 的三个死方法是现成红灯样本 | 本切片要接大量方法线；接线时正是这条门禁最有用的时候——它会把「还剩几个零调用方法」变成可数的 |

**三条顺序约束**（单独看都对、顺序错了就出错）：

1. **133 要在 113–131 之前或同时**。否则接线全程没有任何东西提醒还剩哪些零调用方法——
   而「让红色消失」本身是这条门禁最好的一次验证。
2. **113/114 必须在 121 之前**。`query_memory_facts` 没有 facts 读路径就没有数据源，
   而直接写工具会得到一个「永远返回空」的工具，读起来像「这个人没有事实」。
3. **115a–115b 在 115c/117 之前**——没有写入侧就没有向量，召回器接上去只会永远返回空，而「空」与「没接」在读数上分不开。117 是对照验收，没有前面几个无从对照。

---

## 4. 逐任务要点（只写不显然的地方）

### Task 114 · facts 状态机的闭合

形状由 1.0 定：`confirm_fact` 是把新值升 `active` **并且**把旧值标 `superseded`，
两件事在同一事务里（`supersede_others` 就是为它准备的）。2.0 的 `set_status` 与
`supersede_others` 已写好、签名也对，**只是没有调用者**——先确认这一点再写用例。

回归用例钉那个具体病症：造一条 `pending`，走确认，断言它变成 `active` **且旧值变成 `superseded`**。
定点变异：去掉 `supersede_others` 的调用，用例必须变红——否则它只验了「状态改了」，
没验「旧值被作废」，而后者才是「改口」这个动作的全部意义。

### Task 117 / 123 / 124 · 三个「对照验收」

这三个的共同形状是：**新增的能力必须证明它比原来多拿到了东西**，而不是「不报错」。

- 117：同一 query 补齐前后各跑一次，断言 `channels` 里出现了新键、且**名次不是垫底**
  （垫底意味着它其实没贡献）；
- 123/124：拿一张**已知内容的照片**跑，断言返回的观察/文字与人工标注一致——
  1.0 的 PhotoBench 有现成的题可以借；
- 三个都要有「关掉新通道 / 不接工具」的对照臂，否则「有输出」与「输出是对的」分不开。

### Task 121b · 结构化查询的 SQL 是重写，不是移植

1.0 的 `StructuredMemoryExecutor` 把「筛什么」与「怎么筛」写在同一个类里（311 行）。
2.0 拆成两半：`domain/retrieval/structured_query.py` 的 `derive_filter`（纯规则，
可穷举）与 `infrastructure/persistence/repositories/structured.py` 的 `build_base`
加四个聚合方法（SQL，要对真库）。**拆开的收益在验证方式上**：前半穷举单测，
后半用「同一份数据、两条路径同一个数」对照——`place_text_matches`（融合后判定）
与 `place_clause`（召回时 WHERE）必须给出同一个判定，否则同一个问题走统计路径与
走语义路径会得到两个数。

**三处与 1.0 的差异，每条都有理由**（模块 docstring 有全文）：

| 差异 | 依据 |
|---|---|
| `COALESCE(a.derived_kind,'') = ''` 整条删掉 | 2.0 的 `assets` 表既没有 `derived_kind` 也没有 `parent_asset_id`（实测 `PRAGMA table_info`），**根本不建派生资产**；照抄会直接报 no such column |
| `a.captured_location LIKE ?` 删掉 | 2.0 那一列是 `GpsPoint`，拿 `30.274100,120.155100` 去 `LIKE '%杭州%'` 永远匹配不上——`test_conditions.py:295` 早就为这件事写过用例 |
| `place` 分组的键改成确定性的 | 1.0 直接读左连接上来的 `o.place`，一个资产有多条观察时取到哪一行由 SQLite 的扫描顺序决定，同一个问题问两次可能给出不同的分组名 |

**时间边界用 `isoformat()` 不用 `str(datetime)`**：`captured_at` 存的是 ISO 串，
而 `str()` 用空格分隔日期与时间，两者的字典序不同，`>=` 会静默给出错误的边界。
同理，`derive_filter` 把时间在**派生阶段**就解析成 `(start, end)`，而不是留到拼 SQL
时现解析——那样「解析失败」会以「拿到 None」的形式悄悄过去。

**定点变异 8 条全部变红，但过程里抓出两条假绿，比变异本身更值得记：**

1. 时间边界那条最初用 `tzinfo=UTC` 造数据，落库成 `2023-11-01T00:00:00+00:00`，
   比上界 `2023-11-01T00:00:00` 多一个尾巴、字典序更大——把 `<` 改成 `<=` 之后
   它照样被排除，用例照样绿。**它绿的原因不是边界开闭，是那个时区后缀。**
   真实库里的值是 naive 的（EXIF 直读），用例改成 naive 之后才真的守住右开；
2. 通配符那条最初拿 `AB` 去对模式 `%A_B%`，而那个模式要三个字符才匹配得上，
   `instr` 与 `LIKE` 给出同一个答案，用例对这两种实现**完全没有区分力**。
   存储值改成 `AXB` 才把它劈开。

**两处需要后续处理，先记在这里：**

- **同一列里 naive 与带偏移量的值并存是一个已知的坑**
  （`test_time_bounds_compare_as_strings_so_offsets_are_a_hazard` 钉住了它）。
  时间区间是拿**字符串**比大小的，同一列只要全是同一个偏移量，字典序就等于时间序——
  今天成立。一条带非 UTC 偏移的记录进来就不成立了（`2023-10-31T20:00:00-05:00`
  排在 `2023-11-01T00:00:00` 前面）。修法在**写入侧**（落库前归一成一个偏移量），
  不在读取侧——读取侧替它决定「naive 的值算哪个时区」是猜。
- **`strip_admin_suffix` 的后缀顺序有个既有缺陷**：`区` 排在 `地区` 前面，
  于是 `阿里地区` 去掉的是 `区` 不是 `地区`，得到 `阿里地`（1.0 的 `replace` 连招
  给的是 `阿里`）。也就是说用户说「阿里」时规则 2 在 2.0 里匹配不上。这个顺序在
  `domain/places/aliases.py`，不在 SQL 那一侧；改它要连带改 `place_text_matches`，
  单列一条。

**动工 T121c 前读 1.0 的工具层，读回一个自己在 T121b 写错的判断。**
`derive_filter` 一度写成「只有 HARD 档的地点条件才进 WHERE」，理由是「SEMANTIC 档不该
排除候选」。1.0 的 `structured_memory.py:_place` **全程没看过 `strictness`**——它遍历
`constraints_for("place")` 返回第一个非空值；而 1.0 的工具层
（`agent_runtime/tools.py:_draft_from_filters`）把地点条件构造成 `semantic_required`。

两句合起来：按严格度筛的话，**每一次带地点的统计都会悄悄退化成整个相册的统计**，
而返回值看起来完全正常（`count=53` 不是错误，只是答非所问）。两档严格度的差别作用在
**融合之后逐候选判定**上（`conditions.excludes`），不作用在召回/统计的 WHERE 上——对
count/first/last 这类问题，`filters.place` 就是问题本身。

原用例把这个错误判断钉住了（`test_a_semantic_place_constraint_does_not`），所以变异
不会红。改成断言正确行为之后，把 HARD 判断植回去立刻红一条。**这是「一条绿的用例
可以钉住一个错的结论」的第二个例子**，前两个假绿在同一节的上面。

同一次核对还改掉一处：时间与地点都取**第一个**可用约束，不是「后面覆盖前面」。
1.0 遍历时碰到第一个就返回，写成覆盖的话，两条约束并存时谁生效取决于 spec 的构造顺序。

**门禁这边有一条盲区被它自己暴露出来**：`check_reachability.py` 的粒度是**模块**，
判据是「有没有被 import 过」。端口 `domain/retrieval/ports.py` 引用了
`StructuredFilter`，而 `ports.py` 从 `container.py` 可达，于是
`structured_query.py` 整个模块就算「接上了」——**模块内的规则函数
（`derive_filter` / `hard_conditions` / `normalize_group_by`）其实至今没有生产调用者。**
已在 `check_reachability.py` 的 `PENDING_WIRING` 旁边写明，T121c 是它们的第一个真调用者。

### Task 127 · 恢复作答的阶梯

**动工前必须先核查**（照 S5c 的 T86g 体例：先核依赖，核出缺口再写任务定义）。
要核的是：1.0 的「四级阶梯」到底是哪四级、在哪个文件、由谁触发。
我这次只 `grep` 到措辞分级（确定→较可能→不确定→无依据），**那不是恢复阶梯**。
如果核查发现它其实是 S7 的事（S3 计划写的是「S6/S7」），本任务移出 S6。

---

## 5. 不做（按 2026-09-29 裁决与路线图归属）

| 项 | 归属 / 理由 |
|---|---|
| **`adjacency` 召回器** | **1.0 默认关着**（`configs/retrieval/defaults.json:10` 的 `"adjacency": false`，`build_default_retrievers` 连实例都不建），只被自己的测试跑过。实现它等于**加一个 1.0 从没跑过的东西**。按 `agent_contracts.py`（392 行、静态不可达 → 不搬）的先例：不实现，理由记入 `frozen.md`。**注意 `fusion.py` 的权重表与 `EXPANDER_CHANNELS` 里还留着它的位置——那是照抄 1.0 的权重表，留着无妨，但不要因为「表里有」就去建召回器** |
| 视频（4 条路由 + 抽取链） | **S8**；按裁决**单独排**，本切片不做 |
| family-graph 7 条、relationships CRUD、角色假设 | **S7**（设计 §8）；`s5-deviations.md` 第 15/35 条已标「随 S7」 |
| `hardware` / `model-profiles` / `runtime-providers` 9 条 | **S11**；设计 §2.1「2.0 不管理模型进程生命周期」 |
| PhotoBench 后端 / 前端 | **S9 / S10** |
| **鉴权** | 裁决：**不着急**。核实过的事实记在此：`api/routes/` 零鉴权依赖，8191/8091 绑 `0.0.0.0`，局域网内任何人可读全部家庭照片、改身份数据。**它不是「缺功能」而是「缺一个设计决定」**，等排期时单独定 |
| 语义实体 / entity-groups / knowledge | **未归属**：`repositories/entities.py:8-13` 注释推给 S7，但 S7 的定义（设计 §8）里没有它。**列进 §6 待裁决** |
| observations 原始列表、OCR settings / telemetry / query-gaps、`/api/dashboard` | 未归属，价值判断见 §6 |

**这份「不做」清单要进 `frozen.md`**（或有明确归属的记进对应切片计划），
否则下一次审计还会把它们当「缺失」重报一遍——本轮审计就已经重报了一批 S7/S11 的东西。

---

## 6. 完成定义

1. §3 的 113–133 全部落地，或逐条写明移出本切片的原因；
2. 设计 §8 给 S6 的验收条件成立：**证据包和 trace 能展开**；
3. 六路召回 6/6，且 117 的对照臂证明新通道有实际贡献（名次非垫底）；
4. 三个硬条件各有「过滤前有、过滤后没有」的用例；
5. 四个工具在真数据上各跑通一次，且有对照臂（关掉工具时结果确实变差）；
6. 133 的新门禁**有定点变异证明会红**，且 §1.2 ① 的三个死方法在接线后从红灯变绿；
7. `capability-map.jsonl` 不含「已实现却标 pending」的条目（`person_moments` /
   `person_portrait_revisions` 是现成的两条）；
8. **CI 结果可解释**：当前 `test_the_answer_never_leaks_internal_identifiers` 因
   qwen3.5-4B 会在正文写 `photo_N` 而常红（裁决：判为模型问题、不修）。
   本切片不改变这一点，完成定义要说清「CI 红 ≠ 本切片未完成」，并说明是哪一条。

---

## 7. 待裁决

| # | 问题 | 状态 |
|---|---|---|
**2026-09-29 全部裁决完毕**（含本文件第一版新提的三条）：

| # | 问题 | 裁决 |
|---|---|---|
| ① | 建/删相册 | **加入删除逻辑** |
| ② | S7 是否存在 | 存在，定义在设计文档 §8；本文件第一版误判为不存在 |
| ③ | 鉴权 | **不着急** |
| ④ | 视频 | **单独排**（对应路线图的 S8） |
| ⑤ | 回合取消后部分结果 | **丢弃** |
| ⑥ | 记忆空间删除的连带范围 | **删干净**：空间行 + 该 scope 的 assets/observations/events/entities/face_*/facts/…（按 `scope_id` 级联）+ 对应 Qdrant collection + `data/media` 下的实体文件。理由：留半份数据比删两份更糟——剩下的行会以孤儿形态出现在检索里，而**没有任何界面能让人发现它们** |
| ⑦ | 语义实体 / entity-groups / knowledge | **归 S6**（§3 第六组） |
| ⑧ | 恢复作答的阶梯 | **归 S7** |

**① + ⑥ 的落点**：建/删相册不属检索补全，**不在 S6 任务表内**——它进 §1.3 的「未归属」栏，
但**语义已定，不需要再问**：实现时按 ⑥ 的级联范围做，且删除必须是显式确认的不可逆操作。

**仍然没有归属的**（本切片不排，也不需要现在决定）：鉴权、observations 原始列表、
OCR settings / telemetry / query-gaps、`/api/dashboard`、回合取消、会话历史回放。
