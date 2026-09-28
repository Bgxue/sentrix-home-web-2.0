# S5c 实施计划（外观证据 / 人物画像 / e2e）

> **【2026-09-26 已搬回主仓库】** 本文档写在隔离副本 `/home/asus/s5verify/repo`
> （分支 `s5-tdd`）里。2026-09-26 把 S5c **已完成**的三个纯规则模块搬进主仓库
> `/home/asus/Github/sentrix-home-web2.0`（分支 `psh2.0`）：
>
> ```
> domain/identity/appearance.py    85 行   + tests/unit/test_person_appearance.py  112 行
> domain/identity/moments.py      170 行   + tests/unit/test_person_moments.py     179 行
> domain/identity/portrait.py     366 行   + tests/unit/test_portrait.py           525 行
> domain/identity/models.py                + 140 行纯新增（4 个 Record 与取值常量）
> ```
>
> **未完成的部分（T85b–T87）原样保留，仍是未开始**——本文档第 2 节的任务表就是现状。
> 三个新模块因此没有生产调用者，`check_reachability` 报 3 个问题。**搬回时经用户裁决
> 写进了 `scripts/check_reachability.py` 的 `PENDING_WIRING`**（逐条注明接线任务
> T85c/T85d、T86e/T86f、T86g），门禁恢复绿——按该表的反向规则，接线完成后**必须**
> 删掉对应条目，否则检查会报红要求删。
>
> **随附的重编号**（两边分叉后同号不同事，搬回时整段重编号）：
> 本文档条目号 `109–119` → `s5-deviations.md` 的 `第 119–129 条`；
> 段号 `## 2l` → `## 2q`。

**日期**：2026-09-24 　**分支**：`s5-tdd`（隔离副本 `/home/asus/s5verify/repo`）
**上游**：`docs/refactor/2026-09-23-s5-implementation-plan.md`（S5a/S5b 已落地），
`docs/refactor/s5-deviations.md`

---

## 0. 本轮的起点：四条用户裁决

| # | 问题 | 裁决 | 落实位置 |
|---|---|---|---|
| ① | `infer_graph` 两阶段无落点 | **不移植，只做 5 阶段**（rank / select / moments / compile / write）；连带 `person_graph.py` 整个不搬 | `domain/identity/portrait.py`、`domain/identity/insights.py`、`application/identity/run_insights.py` |
| ② | `relationships` 无读方法 | **补** `list_person_relationships`（1.0 `db.py:5601-5610`） | 关系仓储 |
| ③ | `apply_relationship_threshold` 是死代码 | **不搬**（同 S4 不搬 `_event_pair_score`） | — |
| ④ | 画像的自动触发入口 | **导入后自动跑** | 导入路由批末 |

**裁决④ 的记账（必须写清）。** 这是 **1.0 从未实际发生过的行为**：1.0 的
`SENTRIX_PERSON_INSIGHT_SCOPES` allowlist 从没生效过，画像在 1.0 里**没有**自动触发过。
所以这一条**不是按证据走出来的**，是按产品意图做的决定，代价照写：

- **每次导入触发画像生成**，而 `write_portraits` 对**每个核心人物**至少一次模型调用
  （writer），校验不过还要 repair，再不过才走确定性兜底——即**每个人物最多 2 次模型
  调用**，核心人物上限 10（`rank_core_people` 的硬上限），所以单次导入的模型调用上限
  是 **20 次**（外加 moments 阶段每人每资产一次）。
- 导入本身已经是「串行走模型调用、会跑很久」的同步请求（`routes/timeline.py` 的
  `import_album` docstring 自己写了这一点）。再加一段同等量级的调用，请求时长会成倍
  增长，而 2.0 没有任务队列（同处 docstring 已说明）。**这是本轮明确接受的代价。**

---

## 1. 裁决① 的依赖连带（先核清再动手）

### 1.1 `ROLE_OPTIONS` 必须重新找家

`person_portraits.py:11` 是 `from .person_graph import ROLE_OPTIONS`。裁决① 判死
`person_graph.py`，所以这个词表要重新落点。**查过全部消费方之后**的决定：

| 1.0 位置 | 用途 | 2.0 处置 |
|---|---|---|
| `person_portraits.py:35` `_mentions_role` | 未确认角色的模糊措辞判据 | **搬** → 落 `domain/identity/portrait.py` |
| `db.py:6471-6474` `confirm_role_hypothesis` | 角色假设确认时校验词表 | **不搬**：整个函数属角色假设功能（`person_role_hypotheses` 表 0006 未建、写入者是 `infer_graph`），随裁决① 一起判死 |
| `person_graph.py:121` `normalize_person_graph` | 同上 | 随 person_graph 判死 |
| `tests/test_person_graph.py` | 1.0 测试 | 不搬 |

结论：2.0 里这个词表的**唯一**消费方就是画像那条判据，所以它住在 `portrait.py` 里。

### 1.2 **计划缺口：`moments` 阶段没有归属**

裁决① 保留的 5 阶段里有一个 `moments`（1.0 的 `extract_moments`），但
`2026-09-23-s5-implementation-plan.md` 的移植清单（A 表 26 条 + B 表）**从头到尾没有
给 `person_moments.py` 的 `PersonMomentExtractor` 派过任何任务**：第 176 行把它与另外
三张表一起列进「冻结」那一格，而裁决② 只解冻了**表**，没有解冻**写入者**。

实测确认这确实是缺口：

- `grep -rn "moment" apps/api/src/` 在 2.0 只命中 `repositories/entities.py` 与
  `application/memories/read_timeline.py` 两处**无关**的命中；
- 2.0 没有任何模型端口方法对应 1.0 `model_clients.py:1316` 的 `analyze_person_moments`。

所以 S5c 的 `moments` 阶段是**净新增**三块：纯词表（已落）、带编号预览渲染（要 PIL，
落 infrastructure）、抽取用例（要 `store` + 模型调用，落 application）。**这三块是本轮
相对原计划多出来的工作量**，登记在此，不假装计划里本来就有。

### 1.3 `suggested_relationships` 在 2.0 恒为空

1.0 证据包里有 `suggested_relationships`，来源是 `list_relationship_hypotheses`，写入者
是 `infer_graph`。裁决① 判死它，且 0006 **没有建** `relationship_hypotheses` 表
（已核对：0006 的 14 张表里没有它）。所以这个键在 2.0 恒为 `[]`。

**处置：整个键从证据包里去掉**，而不是留一个恒空的占位——留空键会让读提示词的人以为
那条链路是通的。连带：关系假设读方法**不补**（无表、无写入者，补了就是死代码，
与裁决③ 判 `apply_relationship_threshold` 死代码的判据同形）。

---

## 2. 任务拆分（沿用计划预留的 85–87，扩充为 11 条子任务）

**编号纪律**：计划里 85–87 本来就是预留给 S5c 的（外观证据 / 人物画像 / e2e），
本轮**沿用并扩充**，不另起 88+ 造成孤儿号。原先拆分里的 **T89（`infer_graph` 支线）
随裁决① 取消**。

| # | 任务 | 依赖 | 状态 |
|---|---|---|---|
| **T85a** | `domain/identity/appearance.py` 裁剪几何（`expanded_person_crop`） | — | ✅ **完成** |
| T85b | `domain/identity/ports.py` + `repositories/appearance.py`（外观证据读写） | T85a | ✅ **完成** |
| T85c | `infrastructure/media/person_vision.py` 人物裁剪取样适配器（PIL 边界） | T85a | ✅ **完成** |
| T85d | `application/identity/observe_appearance.py`（每人每事件取一张 → 模型 → 落库）+ 接进 `review_cluster.confirm` | T85b/T85c | ✅ **完成** |
| **T86a** | `domain/identity/moments.py` 词表 + 归一化 + 提示词 | — | ✅ **完成** |
| T86b | `domain/identity/ports.py` + `repositories/moments.py`、`relationships.py`（含裁决② 的 `list_person_relationships`）、`portraits.py` | — | ✅ **完成** |
| **T86c** | `domain/identity/portrait.py` 证据包 / 强制校验 / 确定性兜底 | T86a | ✅ **完成** |
| T86d | `domain/identity/insights.py` 核心人物排序 / 代表照选择 | — | ✅ **完成** |
| T86e | `infrastructure/media/person_vision.py` 带编号预览渲染 + 模型端口（moments / appearance / portrait 三个方法） | T86a | ✅ **完成** |
| T86f | `application/identity/extract_moments.py`（`moments` 阶段） | T86a/T86b/T86e | ✅ **完成** |
| T86g | `application/identity/run_insights.py` 5 阶段编排（含缺陷 3 的日志落点：2.0 没有 run 表） | T86c/T86d/T86f | ✅ **完成** |
| T86h | `api/routes/insights.py` + `main.py` + 容器装配 | T86g | ⬜ 未开始 |
| T86i | **裁决④**：导入路由批末自动触发画像 | T86h | ⬜ 未开始 |
| T87 | `tests/e2e/test_identity_album.py`（断言内容不只数量） | 全部 | ⬜ 未开始 |

**状态口径（重要，不要把「已写」读成「已验证」）：**

| 模块 | 写了 | 自己的用例 | 定点变异 | 被谁覆盖 |
|---|---|---|---|---|
| `domain/identity/appearance.py` | ✅ | ✅ 8 条 | ✅ 9/9 变红 | 全绿 |
| `domain/identity/portrait.py` | ✅ | ✅ 21 条 | ✅ 19/19 变红 | 全绿 |
| `domain/identity/moments.py` | ✅ | ✅ 13 条 | ✅ 12/12 变红 | 全绿 |

**三条已完成子任务的合计**：42 条用例（8 + 21 + 13）全绿；
定点变异 **40 条（9 + 19 + 12）全部变红**。

`moments.py` 的用例是后补的，补的过程本身撞出两条记录（都进了 `s5-deviations.md`）：
`None` 会被 `str()` 成字符串 `"None"`（实测 1.0 亦然，照搬）；词表粒度粗
（拦「生病」不拦「糖尿病」），已用一条用例把边界钉住，**不是当缺陷修**。

同理，`domain/identity/models.py` 新增的四个 Record 里，只有 `PersonMomentRecord` 与
`RelationshipRecord` 被 `test_portrait.py` 用到；`PortraitRevisionRecord` 与
`AppearanceEvidenceRecord` 目前**零用例、零消费者**，它们的字段形状只对着 0006 的建表
语句核过一遍。

---

## 3. 验证记录（已完成部分的原始输出）

### T85a · 外观裁剪几何

```
$ .venv/bin/pytest apps/api/tests/unit/test_person_appearance.py -q -p no:warnings
.......                                                                  [100%]
```

定点变异（9 条，**全部变红**）：

```
基线绿：apps/api/tests/unit/test_person_appearance.py
[1] HEAD_SCALE = 1.35 -> 1.30                                        变红 ✓
[2] FACE_TOP_EXPANSION = 0.35 -> 0.30                                变红 ✓
[3] BODY_HEIGHT_SCALE = 4.5 -> 4.0                                   变红 ✓
[4] MIN_CROP_WIDTH = 24 -> 20                                        变红 ✓
[5] MIN_CROP_HEIGHT = 48 -> 40                                       变红 ✓
[6] if face_width < 2 or face_height < 2: -> < 1 and < 1             变红 ✓
[7] crop_left 去掉 max(0, ...) 钳位                                   变红 ✓
[8] crop_bottom 的 body_height_scale -> 0.0                          变红 ✓
[9] len(bbox) != 4 -> != 3                                           变红 ✓
变异 9 条，没变红 0 条
```

### T86c · 人物画像

```
$ .venv/bin/pytest apps/api/tests/unit/test_portrait.py -q -p no:warnings
.....................                                                    [100%]
```

定点变异（19 条，**全部变红**）——其中三条直接对着三条缺陷：

```
[1] deterministic = (source == DETERMINISTIC) -> False               变红 ✓   （缺陷 2）
[2] deterministic = (source == DETERMINISTIC) -> True                变红 ✓   （缺陷 2 的反向门禁）
[3] relationship 引用分支 return any(...) -> return True             变红 ✓   （缺陷 1：后门复现）
[4] MIN_HANZI 80 -> 70                                               变红 ✓
[5] MAX_HANZI 500 -> 400                                             变红 ✓
[6] MIN_THEMES 2 -> 1                                                变红 ✓
[7] MAX_THEMES 6 -> 7                                                变红 ✓
[8] MAX_MOMENTS 12 -> 11                                             变红 ✓
[9] PER_EVENT 2 -> 3                                                 变红 ✓
[10] TOP_ACTIONS 3 -> 2                                              变红 ✓
[11] MAX_REFS_PER_THEME 2 -> 1                                       变红 ✓
[12] 抽样排序 -confidence -> +confidence                             变红 ✓
[13] normalize 引用守卫去掉 ref.get("id")                             变红 ✓
[14] _contains_sensitive -> False                                    变红 ✓
[15] if role in _UNDECIDED -> if False                               变红 ✓
[16] 去掉 not confirmed_role 那一半                                    变红 ✓
[17] 去掉 hedge 判据                                                   变红 ✓
```

### 静态门禁（全部通过）

```
$ .venv/bin/ruff check apps/api/src apps/api/tests scripts
All checks passed!
$ .venv/bin/ruff format --check apps/api/src apps/api/tests scripts
227 files already formatted
$ .venv/bin/mypy
Success: no issues found in 140 source files
$ .venv/bin/lint-imports
domain-purity KEPT / application-purity KEPT / api-transport-only KEPT
infrastructure-purity KEPT / layers KEPT
Contracts: 5 kept, 0 broken.
$ .venv/bin/python scripts/check_reachability.py
sentrix.domain.identity.appearance: 没有任何生产调用者
sentrix.domain.identity.moments: 没有任何生产调用者
sentrix.domain.identity.portrait: 没有任何生产调用者
reachability: 3 个问题
```

**`check_reachability` 现在是红的，这是真实状态，不是待办。** 三个新模块的调用者
（T86g/T86h 的编排与路由）本轮没有落地。**刻意没有写进 `PENDING_WIRING`**：
那张表是给「本切片内先落地、随后接线」用的，而本切片停在了中间——由同一个停下来的
agent 写进去，它与一块永久墓碑长得一模一样。下一位接手的人要么把线接上、要么再决定。

---

## 4. 本轮**没有**完成的部分

- 上面任务表的 11 条子任务（T85b–T87）。
- **隔离副本的完整 `make ci` 没有跑**：`ci.sh` 的门禁要求 `load1 <= 2.0 且
  MemAvailable >= 12GB`，实测 `load1 = 30.23`（真实仓库那个 `make ci`，PID 1515611，
  09:24 起跑，仍在 pytest 阶段）。按纪律**等窗口、不绕过**，而本轮没有等到。
- **真实仓库执行（阶段 8）未开始**：前提是那个 `make ci` 跑完（`ps -p 1515611` 查不到），
  届时才谈得上对拍、取补丁、逐 commit 跑 CI。
- **真数据端到端（阶段 9）未开始**：没有任何画像被真实生成过，所以「兜底占比」
  「缺陷 1 拦截数」这两个本轮最想要的数字**都还没有**。

---

## 5. T86g 动工前的依赖核查（2026-09-28 核出，**动手前先读这一段**）

T85c / T85d / T86f 落地后核了一遍 `PersonInsightService._run_stage`
（1.0 ``person_insights.py:198-255``）的五个阶段，2.0 缺的东西比任务表上一行字看起来多：

| 阶段 | 1.0 用的东西 | 2.0 现状 |
|---|---|---|
| `rank_people` | `store.person_candidate_features(scope_id)` | **不存在**。`grep -rn person_candidate_features apps/api/src` 零命中——这个查询要新写一个仓储方法（它要拼的列就是 `insights.core_person_score` 读的那几个，形状见 1.0 ``db.py`` 里那个方法） |
| `select_representatives` | 一段内联 SQL：`face_instances` ⋈ `face_clusters` ⋈ `observations` + LEFT JOIN `event_observations`，按 `c.entity_id = ?` | **不存在**。同样要新写一个仓储方法。注意它带 `LEFT JOIN`——没有事件的照片也要进来（那正是 `per_event` 抽样要处理的） |
| `extract_moments` | `PersonMomentExtractor` | ✅ T86f 已落地 |
| `compile_portraits` | `compile_portrait_evidence(store, person_id)` | ✅ T86c 已落地，但 2.0 的签名是 `(person, moments, relationships)`——**材料由调用方取好递进来**，不是收一个 store。所以 T86g 还要负责取这三样 |
| `write_portraits` | writer → validate → repair → validate → 确定性兜底 → validate → `create_portrait_revision` | 纯函数（`validate_portrait` / `deterministic_portrait`）与仓储（`PortraitRepository.create`）都在，**编排本身没写** |

**另外两处与 1.0 的结构差异**（都是已定的决定，不是缺口）：

1. **没有 run 表**。1.0 的 `run()` 绕着 `person_insight_runs` 转（状态机、`current_stage`
   断点续跑、`stats["moment_failures"]` 落点）。0006 没建那张表，所以 2.0 的 `RunInsights`
   是一个**同步跑完五个阶段的函数**——没有 run_id、没有续跑、失败数由
   `MomentExtraction.failures` 直接返回给调用方。这是计划里写的「缺陷 3 的日志落点」
   那件事，T86g 必须自己决定这些数字去哪（最省事且不撒谎的做法：作为返回值给调用方，
   由 T86h 的路由决定是否回给前端）。
2. **`infer_graph` 整个不搬**（裁决①），所以 `_run_stage` 里那一支与 `_infer_graph`
   那一大段都不移植。连带 `state["graph"]` 没有消费者。

**动工顺序建议**：两个仓储方法 → `RunInsights` 编排 → 用例（每个阶段各钉一条 +
一条端到端的阶段顺序）。阶段顺序本身值得钉：1.0 是**固定顺序**（`STAGES` 元组），
而 `select` 必须排在 `moments` 之前、`compile` 必须排在 `write` 之前——这些依赖是
数据流的，不是风格的。
