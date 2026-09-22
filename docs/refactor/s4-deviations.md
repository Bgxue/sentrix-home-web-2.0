# S4 偏离审计（时间线：地点 / 事件 / 词法 / 元数据）

基线：`/home/asus/Github/Sentrix-Home-Web` @ `2d2cee7cbf1023f5019b9aaa9061af9c4cf45afa`（只读）。
2.0 侧：隔离副本 `/home/asus/s4verify/repo`（分支 `psh2.0`），基线 `28b5ed9`（S3 结束时
`make ci` 全绿、444 passed）。

本文档记的是 **S4 相对 1.0 的每一处有意偏离**。格式对齐 `s3-deviations.md`：
每一条写清 **1.0 怎么做 / 2.0 怎么做 / 依据是什么**，依据分两类——
**用户裁决**（明确的新决定）与**实测证据**（能摆出来的东西）。

「迁移项目不是重设计项目」是本项目的硬约束，所以：

- 凡是 1.0 做过、2.0 照搬的，不进这张表；
- 凡是 1.0 没做过的新东西，一律标 **NEW-2.0** 并写明 1.0 的对应物是什么（通常是「没有」）；
- 凡是偏离，依据栏必须能指向一条裁决或一组实测数据。**没有依据的偏离不该存在**。

---

## 0. 一句话摘要

| 类别 | 条数 | 说明 |
|---|---:|---|
| 用户裁决 | 5 | 用户对规划阶段 5 处阻塞的明确决定 |
| 实测证据 | 6 | 读 1.0 源码与数据库得出、有具体行号/计数的偏离 |
| 实现期新发现 | 11 | 写代码时才发现、规划阶段没看到的偏离（本文档新增） |
| **合计** | **22** | 其中 7 条是 1.0 有缺陷、2.0 修掉；15 条是 NEW-2.0 或结构改进 |

**7 处修 1.0 的缺陷**：`_commit_semantic_image` 不写事实、词法投影写了但没人调、
`_event_pair_score` 与候选打分对同一距离给两个答案、`merge` 标 merged 不删、
`confirm_fact` 跨 scope 作废、metadata 的 place 预筛因 JSON 字符串而整条失效、
scoped rebuild 清空整张 FTS。

---

## 1. 审计表

### 1.1 用户裁决（5 条）

| # | 1.0 怎么做 | 2.0 怎么做 | 依据 |
|---|---|---|---|
| 1 | **没有乡镇/村庄**。国内只到区县（PyGeoCN 的天地图边界，面）；国外只到城市（`reverse_geocoder` 自带数据） | 新增 `NearestResolver` 端口 + `GeoNamesNearestResolver`：`ADM4`（乡镇/街道，11,450 条）+ `PPL`（村庄/居民点，860,831 条）+ 国外 `PPL*`，三维单位向量 cKDTree 最近邻。**NEW-2.0** | **用户裁决 #1**：「乡镇/村庄，S4 就加上。这是明确的新特性（1.0 零参考、要下载 GeoNames allCountries 402MB、建 KD-tree）」 |
| 2 | 地点是一个**扁平 dict**：`{"source","precision","label","province","city","district","admin1","admin2","country","latitude","longitude","confidence","distance_km"}`，直接存进 `assets.metadata_json.reverse_geocode` | `Place(admin, nearest, precision, source)`：`admin` 是面（权威归属，可硬条件过滤），`nearest` 是点（只能说「附近」）。**NEW-2.0** | **用户裁决 #2**：「按设计文档新造 `Place(admin, nearest, precision, source)`。标为 NEW-2.0，并写清 1.0 的扁平 dict 长什么样、2.0 怎么映射」 |
| 3 | **两条写事实的路径行为不一致**：`pipeline.process()`（`pipeline.py:346-350`）调 `maintain_fact` 并过 `is_confirmed_person_name` 门；`pipeline._commit_semantic_image()`（`pipeline.py:570-635`）**一次都不调**。同一张照片走哪条路径取决于它什么时候导进来 | 两条路径共用同一个写入决定点 `decide_fact_write`；`is_writable_fact` 抽成纯函数。**记录为「修 1.0 内部矛盾」** | **用户裁决 #3**：「接进去。理由：`pipeline.process()` 调 `maintain_fact`、`commit_semantic_image` 不调——同一件事两条路径不一致，是 1.0 的缺陷」 |
| 4 | `RetrievalIndex.refresh_from_observation` **存在但生产零调用**。调用点只有：`scripts/maintenance/ensure_visual_vectors.py:190`、`backend/scope_finalize.py:142`、`backend/retrieval/lexical.py:46`（懒补建）、两个测试。日常导入路径不写 | 挂到导入路径上**逐条写**（写一条 Observation 就刷新它自己的词法投影）。**记录为「相对 1.0 的行为增强」** | **用户裁决 #4**：「挂到导入路径上逐条写（1.0 的 `refresh_from_observation` 写了但生产零调用）」 |
| 5 | `backend/geocoding.py:234-258` 的 `_PLACE_ALIASES` 硬编码 **32 条**，其中 `"赵州桥": ["赵县"]`、`"三峡坝址": ["夷陵区"]` 是 benchmark 泄漏 | 搬**通用 30 条**到数据文件 `apps/api/src/sentrix/infrastructure/places/data/aliases.json`；删掉泄漏的 2 条。**`frozen.md` 的条目改成「已硬编码泄漏条目」而不是整表** | **用户裁决 #5**：「搬通用 30 条…删 `赵州桥`/`三峡坝址` 两条泄漏。`frozen.md` 的条目改成『已硬编码泄漏条目』而不是整表」 |

### 1.2 实测证据（6 条）

| # | 1.0 怎么做 | 2.0 怎么做 | 依据 |
|---|---|---|---|
| 6 | 事件候选打分的加权和是**八项**（`db.py:2065-2070`）：`0.25·time + 0.25·location + 0.15·visual_place + 0.05·event_type + 0.20·activity + 0.05·objects + 0.20·person + 0.05·visual_boost + bonus` | 八项一并不变，写成 `domain/memories/events.py` 的 `WEIGHTS` + `score_event_candidate`。**同步修 `behaviour-baseline.md`**——它原文写的四个信号会丢 **0.70** 权重 | **实测**：`backend/db.py:2065-2070` 原文；`db.py:2078` 的 `spatio_temporal_bonus`；`db.py:2071-2074` 的 `visual_boost`。八项权重之和 **1.20** 而非 1.0，最后靠 `min(1.0, total)` 收口 |
| 7 | 有两套距离衰减：候选打分用 **2 km**（`db.py:2015`），事件间的全局两两归并用 **1000 米**（`db.py:2205`） | **只移植候选打分那套**。`_event_pair_score` / `consolidate_events` 的 1000 米分支不移植 | **实测**：`consolidate_events` 的全部 6 个调用点都在 `scripts/benchmarks/` 与 `scripts/maintenance/`（`ingest_album3_v2.py:64`、`person_insight_fixture.py:106`、`evaluate_household_end_to_end.py:58`、`rebuild_memory.py:126`），生产导入路径只调候选打分 |
| 8 | `pipeline.process()` 把 `upsert_vector("visual", …)` 排在 `merge_observation_into_event` **之前**（`pipeline.py:315-325`），并留了注释「Event selection needs the image vector before an event exists」 | 同样的顺序，在 Task 56 钉死；`test_assign_events.py` 用两条对照用例把「向量没写 → `visual_available=False`、boost 0」与「向量先写 → boost 1.0」分别断言 | **实测**：`_event_visual_similarity`（`db.py:2082-2110`）读 `memory_vectors` 的 `space='visual' & source_type='asset'`；顺序写反时它返回 `(0.0, False)`，**不报错**，只是 `visual_boost` 恒 0、`split_guard` 因 `not visual_available` 更易命中 |
| 9 | 词法召回**不是 BM25**：FTS5 只当倒排索引用（只索引 `tokens` 一列），分数在 Python 侧算——每个 query token 独立匹配，按命中的**不同 token 数**累加，命中数恰为 1 且整串完全相等时 `exact_boost = 2.0` | 同规则，落在 `domain/retrieval/lexical.py` 的 `score_token_hits`。**不准引入 BM25** | **实测**：`retrieval_indexes.py:296-334` 的 `search_fts` 全文；整个 1.0 仓库里 `bm25(` **零次调用** |
| 10 | `SENTRIX_METADATA_RECALL_LIMIT` 环境变量，默认 `"200"`（`backend/retrieval/metadata.py:36-37`） | 落成**模块级常量** `METADATA_RECALL_LIMIT = 200`，理由写在该常量旁 | **实测**：1.0 的配置纪律是「~126 个 env flag 的值互相矛盾」（2.0 `config.py` 的开头注释就是为这件事写的）。这个值没有任何生产改动记录，做成 env 只会多一个没人动过的开关 |
| 11 | 见 §1.1 第 5 项（同一条） | 见 §1.1 第 5 项 | 用户裁决 #5 |

### 1.3 实现期新发现（11 条，规划阶段没看到）

| # | 1.0 怎么做 | 2.0 怎么做 | 依据 |
|---|---|---|---|
| 12 | `precision` 只有 `"district"` 与 `"city"` 两个值：`_lookup_pygeo` 是 `"district" if district else "city"`（`geocoding.py:121`），`_lookup_reverse_geocoder` 恒 `"city"`（`:172`） | 设计 §3.2 的枚举写的是 `"district" \| "township" \| "locality"` **三级**，漏掉了 `city`。2.0 补成四级 `DISTRICT / CITY / TOWNSHIP / LOCALITY` | **实测**：把「只解析到市」硬塞进 `district` 会让下游以为拿到了区县级归属——而这正是 admin / nearest 分槽要防的那种谎。1.0 本来就有这一级 |
| 13 | `merge_observation_into_event` 新建事件的 `event_type` 恒为 `"待判断"`、`title` 恒为 `"待总结事件"`（`db.py:2310-2325`）；`_event_display_place` 在视觉地点缺失时回退 `"其他或不确定"` | 三个占位值照搬，提成 `DEFAULT_EVENT_TITLE` / `DEFAULT_EVENT_TYPE` / `DEFAULT_EVENT_PLACE` 常量 | **实测**：`db.py` 原文。提常量只是让「这是个占位符而不是内容」在类型与命名上看得见 |
| 14 | `_merge_events`（`db.py:5482`）把 source 事件标成 `merged` 而**不删**。于是同一批照片同时挂在一个 `active` 事件和一个 `merged` 事件上；`list_events` 只查 active 所以表面正常，但任何按 `event_id` 直接取数的地方会拿到半份 | `EventRepository.merge` 把 source 的关联搬到 target 后**删掉 source 行**。`test_event_merge_moves_observations_and_deletes_source` 钉住 | **实测**：1.0 的 `list_events` 带 `status='active'` 过滤，`get_event(id)` 不带——同一个库在两处给出不同答案。孤儿 `merged` 行没有任何清理路径 |
| 15 | `confirm_fact` 的作废 UPDATE **不带 scope**（`db.py:3434-3438`）：`UPDATE facts SET status='superseded' WHERE subject=? AND predicate=? AND status='active' AND id!=?` | `FactRepository.supersede_others` 加 `scope_id = ?`。`test_supersede_others_is_scoped` 断言另一个相册的同名事实不动 | **实测**：2.0 的 scope 隔离是硬约束（`0002_media.sql` 的 `assets.scope_id`、按 scope 去重）。1.0 那条 UPDATE 会让「album1 里确认了妈妈的去向」把「album2 里妈妈的去向」一起作废 |
| 16 | `MetadataRetriever` 的 place 预筛（`metadata.py:59-67`）：拿到 `asset.metadata_json` 后只处理 dict，而 `MemoryStore` 返回的是 **JSON 字符串**，于是 `geo` 恒为 falsy → 「没有 geocode」→ **放行**。结果是纯地点查询把整个 scope 灌进 metadata 通道，按插入顺序挤掉真正精确的地点 | `place_text_matches(value, place, aliases)` 在 `place is None` 时返回 `False`，并在 docstring 里写明「没解析出地点」与「地点不匹配」必须在调用方分开表达 | **实测**：1.0 自己的注释（`metadata.py:59-64`）就是在记这个 bug 的修复。2.0 连结构一起改掉——`asset_places` 是独立表，「没解析过」没有行、「解析过但没结果」有行 |
| 17 | `rebuild_all(scope_id=…)` 无条件 `DELETE FROM observation_search_fts`，然后只补这一个 scope 的行（`retrieval_indexes.py:271-295`）。1.0 已在自己的注释里记了这个 bug 的修复，但删范围仍按整表 | `rebuild_scope` 只动本 scope；`test_lexical_rebuild_scope_does_not_wipe_other_scopes` 断言重建 album1 之后 album2 的行还在 | **实测**：`retrieval_indexes.py:278-284` 的注释原文 |
| 18 | `search_fts` 用 `scored.sort(key=…, reverse=True)` 只排分数，同分顺序取决于 `token_hits` 这个 dict 的插入序 | `score_token_hits` 的排序键是 `(-score, asset_id)`。`test_score_is_stable_on_ties_by_asset_id` 断言正反两种插入序给出同一个结果 | **实测**：同一份数据换个 `DETAIL`/`ORDER BY` 就换一套名次。这是「同一输入两次跑结果不同」的来源，而 RRF 的下游按名次算分，会把它放大 |
| 19 | GeoNames 那一路算了 `distance_km` 但**从不读取**（`geocoding.py:186` 写入，全仓无 SELECT） | `NearestPlace.distance_m` 保留并在展示层可见；**并加一条 50 km 半径上限**（`MAX_NEAREST_RADIUS_M`）。**NEW-2.0，这一条需要用户确认** | **实测**：1.0 对太平洋正中的坐标会返回 `"Honolulu, Hawaii, US"` 并把 `distance_km = 3712` 一起写进 dict 然后丢掉。2.0 的展示形式是「`大石桥村`附近」，3000 公里的「附近」是假话。**这是本文档里唯一一条我主动加了 1.0 没有的保护的条目**——若认为应当与 1.0 一样不加限，删掉 `MAX_NEAREST_RADIUS_M` 即可，其余逻辑不受影响 |
| 20 | **不存在这一层**（1.0 的国外回退 `reverse_geocoder` 自带数据只有城市级、单一池，且国内根本走不到它——`lookup()` 是先到先得） | 最近邻点分**三个池**：乡镇（`ADM4`）、国内居民点（`country == "CN"`）、国外居民点；**先国内池，国内有命中就不看国外池**。`HOME_COUNTRY = "CN"` | **实测**：只用 `cities15000.zip` 时坐标为萧山的照片解析出 `浙江省杭州市萧山区 · Xiaoshan附近`——`cities15000` 里中国城市的主名是**罗马字**。设计 §3.3 的表本来就分开写了（国内 → `ADM4`/`PPL`，国外 → `allCountries` 的 `PPL*`），分池只是把那张表落成代码。**混合池会产出半英半中的展示**，而展示层那句「附近」正是给用户看的。合成数据用例：一个美国点与查询点**完全重合**（距离 0）时仍必须输给中文村庄 |
| 21 | **不存在这一层** | `_pick_member`：从 zip 里挑数据文件时**同名成员优先**（`CN.zip` → `CN.txt`），否则取最大的 `.txt` | **实测（真数据才露出来的 bug）**：官方包里每个 dump 都带一个 `readme.txt`。`CN.zip` 的两个成员是 `readme.txt`（8,843 B）与 `CN.txt`（132,055,747 B），而 `namelist()` 里 readme **排在前面**。按顺序取第一个会解析出 **0 个点**、不报错，只是整个国内最近邻静默失效（`counts` 是 `(0, 2117, 31975)`，那 2,117 全来自 `cities15000`）。合成数据的 zip 里没有 readme，所以单元测试发现不了——**只有拿真数据端到端跑一次才看得见** |
| 22 | **不存在这一层** | `_display_name`：`name` 列没有汉字时，从 `alternatenames` 列取**第一个含汉字的**备选名 | **实测**：GeoNames 中国记录的 `name` 列绝大多数是罗马字，中文名在 `alternatenames`。三行原始数据：`name='Mei Xian'` / `alts='Mei Hsien,Mei Xian,Meixian Shi,mei xian,梅县'`；`name='Nyêngbo'` / `alts='…,宁布'`；`name='Changshousi'` / `alts='…,长寿寺'`。设计 §3.2 给的展示形式是 `浙江省杭州市萧山区 · 赵州镇大石桥村附近`——不取中文名会得到 `ZhaozhouShitacun附近`，展示层那句「附近」正是给用户看的 |


---

## 2. 逐条证据

### 2.1 第 6 项：八分量 vs 四个信号

`behaviour-baseline.md` 原文（修正前）：

```
| 信号 | 时间 / GPS 距离线性评分（2km 衰减）/ 视觉相似 / 人物 | `backend/db.py:2000` 附近 |
```

`backend/db.py:2065-2070` 的原文：

```python
total = (
    0.25 * time_score + 0.25 * location_score + 0.15 * visual_place_score
    + 0.05 * event_type_score + 0.20 * activity_score
    + 0.05 * object_score + 0.20 * person_score + 0.05 * visual_boost
    + spatio_temporal_bonus
)
```

缺的四路与两处加成：

| 分量 | 权重 | 基线文档漏了会怎样 |
|---|---:|---|
| `visual_place` | 0.15 | 「同一场活动换了个机位」不再被认出来 |
| `event_type` | 0.05 | 语义类型冲突不再降分 |
| `activity` | 0.20 | **最大的一路**，活动描述相同不再加分 |
| `objects` | 0.05 | 共同物件不再做桥（且 `split_guard` 的 `corroborated` 会恒假） |
| `visual_boost` | 0.05 | 视觉相似度完全不影响事件归并 |
| `spatio_temporal_bonus` | 0.20 | 时空几乎重合的强先验消失 |
| **合计** | **0.70** | |

其中 `objects` 那一项还会**连带**影响 `split_guard`：`corroborated = bool(object_score or person_score)`，
`object_score` 恒 0 时只要语义冲突且视觉 < 0.45 就会拆事件——**同一场家庭活动的不同瞬间
会被拆成十几个事件**。这不是「少 5% 分数」，是事件数整体失真。

已同步修正 `docs/refactor/behaviour-baseline.md`。

### 2.2 第 7 项：两套距离衰减

| | 候选打分 | 全局两两归并 |
|---|---|---|
| 函数 | `_event_candidate_score`（`db.py:2000`） | `_event_pair_score`（`db.py:2192`） |
| GPS 衰减 | `max(0, 1 - km/2.0)`，**2 km** | `max(0, 1 - m/1000.0)`，**1000 米** |
| 触发 | 每导入一条观察 | `consolidate_events` 手动调 |
| 生产调用点 | `pipeline.process()` → `merge_observation_into_event` | **仅 4 个 benchmark / maintenance 脚本** |

`grep -rn "consolidate_events"` 的全部结果：

```
scripts/benchmarks/ingest_album3_v2.py:64
scripts/benchmarks/person_insight_fixture.py:106
scripts/benchmarks/evaluate_household_end_to_end.py:58
scripts/maintenance/rebuild_memory.py:126
backend/db.py:2221                      ← 定义
```

生产导入路径（`pipeline.py`）里**一次都没有**。两个函数对同一个距离给出两个答案
（1.5 km 时前者给 0.25、后者给 0），而只有前者在生产上跑。移植两个等于把一个
benchmark 专用的口径搬进产品，并让「事件该不该合并」有两个互斥的答案。

**结论：只移植候选打分那套。** 若将来要做「整册导入后的全局归并」，应当作为独立
切片重新设计（1.0 那套的阈值是为 `album3-v2` 那个特定数据集调的，没有通用性证据）。

### 2.3 第 8 项：归并顺序

`pipeline.py:315-325` 原文（含注释）：

```python
observation = self.store.add_observation(asset_id, result)
# Event selection needs the image vector before an event exists.
self.store.upsert_vector("visual", "asset", asset_id, result.get("clip_embedding"), ...)
...
event = (self.store.attach_observation_to_event(forced_event_id, observation["id"])
         if forced_event_id else self.store.merge_observation_into_event(observation))
```

依赖链：`merge_observation_into_event` → `_event_candidate_score` →
`_event_visual_similarity` → `SELECT … FROM memory_vectors WHERE space='visual' AND
source_type='asset' AND source_id = <asset_id>`。

**顺序写反的失败形态**（已在 `test_assign_events.py` 里做成对照用例）：

| 顺序 | `visual_available` | `visual_boost` | 报错 |
|---|---|---|---|
| 向量先写 | `True` | 1.0（同一向量） | 无 |
| 向量后写 | `False` | 0.0 | **无** |

两行都是绿的——这就是为什么它必须被一条测试钉死，而不是靠注释。Task 56 钉。

### 2.4 第 9 项：不是 BM25

`retrieval_indexes.py:296-334` 的 `search_fts`：

```python
tokens = pre_tokenize(query)
whole = tokens[0]
token_hits: dict[str, int] = {}
for token in tokens:
    rows = self.connection.execute(
        'SELECT asset_id FROM observation_search_fts WHERE observation_search_fts MATCH ?' ...,
        (f'"{token}"',),
    ).fetchall()
    for row in rows:
        token_hits[row[0]] = token_hits.get(row[0], 0) + 1
scored = []
for asset_id, count in token_hits.items():
    exact_boost = 2.0 if count == 1 and whole and self._whole_is_exact(asset_id, whole, scope_id) else 0.0
    scored.append({"asset_id": asset_id, "score": count + exact_boost, "token_hits": count})
scored.sort(key=lambda item: item["score"], reverse=True)
```

全仓 `bm25(` 命中 **0 次**。FTS5 在这里的角色是**倒排索引**：`observation_search_fts`
的建表语句只索引 `tokens` 一列，`scope_id` / `field_type` / `asset_id` / `observation_id`
全是 `UNINDEXED`，排序完全在 Python 侧做。

2.0 引入 BM25 会换掉一套在真实相册上校准过的排序（R7/R8 消融：视觉 r10=0.887、
词法 0.373），而**校准数据不在 2.0 里**——没有任何证据支持新排序更好。

### 2.5 第 10 项：`METADATA_RECALL_LIMIT`

1.0 `backend/retrieval/metadata.py:36-37`：

```python
internal_limit = max(int(limit or 20),
                     int(os.environ.get("SENTRIX_METADATA_RECALL_LIMIT", "200")))
```

2.0 `domain/retrieval/...` → `application/retrieval/retrieve_metadata.py`：

```python
# 1.0 把召回上限做成了 SENTRIX_METADATA_RECALL_LIMIT 环境变量（默认 "200"）。
# 2.0 落成常量：这个值没有任何生产改动记录，而 1.0 的配置纪律是「~126 个 env flag
# 的值互相矛盾」（见 2.0 config.py 的开头注释）。做成 env 只会多一个没人动过的开关。
# 它同时是「纯地点查询最多返回多少张」的唯一上限，改它等于改 metadata 通道的强度，
# 应当连同证据一起改，而不是在部署时改。
METADATA_RECALL_LIMIT = 200
```

### 2.6 第 19 项：最近邻半径上限（**需要用户确认**）

1.0 `geocoding.py:181-190` 算了 `distance_km`：

```python
try:
    match_lat = float(match.get("lat"))
    match_lon = float(match.get("lon"))
    distance = round(_distance_km(latitude, longitude, match_lat, match_lon), 3)
except (TypeError, ValueError):
    ...
return {..., "distance_km": distance}
```

全仓没有任何地方 SELECT / 读取这个键。所以 1.0 对太平洋正中的坐标会返回
`"Honolulu, Hawaii, US"`——`label` 里写着「夏威夷」，而照片在 3700 公里外的公海。

2.0 的展示形式（设计 §3.2）是 `浙江省杭州市萧山区 · 赵州镇大石桥村附近`，
「附近」这个词必须配得上它修饰的距离。因此加了 `MAX_NEAREST_RADIUS_M = 50_000`。

**实测**（真数据，`cities15000`，34,092 个点）：

```
清迈     -> NearestPlace(township='', locality='Chiang Mai', distance_m=240.3)
东京     -> NearestPlace(township='', locality='Eifuku',      distance_m=940.8)
太平洋中部 -> None          ← 上限生效
```

**这一条我主动加了 1.0 没有的保护，属于超出「照搬」的动作，需要裁决。**
若要回退到 1.0 行为，删掉 `MAX_NEAREST_RADIUS_M` 的检查即可（改一处）。

### 2.7 第 13 项：占位值

`db.py:2310-2325` 的新建事件 INSERT 里，`title` 与 `event_type` 是**字面量**：

```python
title = "待总结事件"
event_type = "待判断"
```

`_event_display_place`（`db.py:2273-2286`）在视觉地点为空或退化成坐标串时回退 `"其他或不确定"`：

```python
display_place = self.store._event_display_place(observation)
event_place = self.store._event_display_place(observation) or "其他或不确定"
```

三个值原样保留成常量。**注意**：`"待判断"` 与 `"其他或不确定"` 是会**落库**并出现在
接口响应里的可见值，不是内部标识——改它们会改变前端显示与既有数据的可读性，
所以照搬而不是「顺手改成语义更好的词」。

---

## 3. 结构与命名上的偏离（无行为影响，但需登记）

| # | 1.0 | 2.0 | 依据 |
|---|---|---|---|
| A | `source: "tianditu"`（`geocoding.py:120`） | `PlaceSource.PYGEOCN = "pygeocn"` | 设计 §3.3 与 `frozen.md` 都按数据源命名（`pygeocn` / `geonames`）；`tianditu` 是 PyGeoCN 内置数据的来源方，不是 2.0 直接调用的服务 |
| B | 别名表在 `backend/geocoding.py` 模块级常量 | 数据文件放 `apps/api/src/sentrix/infrastructure/places/data/aliases.json`（**包内**），不是设计 §3.4 举例的 `data/places/aliases.json`（仓库根） | **实测**：仓库根 `.gitignore` 第 12 行是 `/data/`——那是运行期数据目录（`sentrix.db` / media / previews / qdrant 都派生自它），整条被忽略。审查表的第一件事是「表在版本控制里」，放进去「可审查、可替换、可测试」三条一条都做不到 |
| C | `_event_candidate_score` 返回一个 dict，键名混用（`objects` 对外、`confirmed_people` 落库） | `EventCandidateScore` frozen dataclass + `breakdown()` 方法，**落库键名与 1.0 完全一致**（`confirmed_people` / `visual_similarity` / `split_guard` …） | 读旧 `aggregation_breakdown_json` 的人不必换字典键。`test_breakdown_keeps_1_0_field_names` 钉住 |
| D | `EventAnchorSet` 里 `people` 来自 `entity_mentions` 的 confirmed person（`db.py:2093-2099`） | 同一个槽位，但 S4 阶段实现恒返回空集——`entity_mentions` 属于 S5 | **见 §4** |

---

## 4. 已知缺口（如实记录，不削足适履）

| 缺口 | 影响 | 记在哪 |
|---|---|---|
| `person_score`（权重 **0.20**）在 S5 之前恒为 0 | 事件归并少掉一整路最强信号之一；靠时间/GPS/活动/物件仍然工作，但「同一个人出现在两场不同活动」这条桥暂时没有 | `AssignEvents.__init__` 的 `confirmed_people` 参数默认 `frozenset()`，注释写明这是 S5 的输入而不是编出来的假来源 |
| `_terms_from_detail`（`visible_details` / `regions` / `text_blocks`）不建 | 词法召回少一路 `detail` 字段。S4 的 `Observation` 没有 `detail`，它属于 S6 的 OCR/VLM 精读 | `search_terms.py` 的 `_observation_terms` docstring |
| `person_bridge` 字段类型保留在 `FIELD_SOURCES` 但不写 | 与 1.0 的字段清单保持逐字对齐，读代码的人不会以为「2.0 忘了这一路」 | `search_terms.py` 的 `_ACTIVE_FIELD_SOURCES` |
| `reverse_geocoder`（1.0 的国外回退）**不引入** | 国外城市由 GeoNames `PPL*` 覆盖。1.0 的 `reverse_geocoder` 自带数据是 `cities1000` 的子集，与 GeoNames 同源，两套并存只会给出两个答案 | 本表；`geonames.py` 的模块 docstring |
| 事件封面 `select_event_cover` / 参与者表 `event_participants` / `event_entities` **不建** | 本切片不做封面选图与参与者投影；参与者强依赖 S5 的 `entities` | `0005_timeline.sql` 的 `events` 表注释 |

---

## 5. 与 1.0 逐条对回的位置索引

| 2.0 文件 | 1.0 来源 |
|---|---|
| `domain/places/models.py` | `backend/geocoding.py:103-190`（扁平 dict 的两个分支）+ 设计 §3.2（新结构） |
| `domain/places/aliases.py` | `backend/geocoding.py:222-270` 的 `_PLACE_ADMIN_SUFFIXES` / `_strip_admin_suffix` |
| `domain/places/matching.py` | `backend/geocoding.py:269-308` 的 `place_text_matches` / `place_alias_names` |
| `infrastructure/places/pygeocn.py` | `backend/geocoding.py:103-140` 的 `_lookup_pygeo` |
| `infrastructure/places/geonames.py` | **无**（NEW-2.0；1.0 的 `_lookup_reverse_geocoder` 只到城市） |
| `infrastructure/places/composite.py` | `backend/geocoding.py:196-216` 的 `lookup()`，结构改成互补 |
| `infrastructure/places/aliases.py` | `backend/geocoding.py:234-258` 的 `_PLACE_ALIASES`（去 2 条泄漏） |
| `domain/memories/events.py` | `backend/db.py:1962-1990`（候选）/ `:2000-2080`（打分）/ `:2016-2040`（split_guard） |
| `domain/memories/facts.py` | `backend/db.py:3397-3460`（maintain/confirm/reject/purge） |
| `domain/retrieval/lexical.py` | `backend/retrieval_indexes.py:31-80`（分词）/ `:296-334`（计分）/ `:225-245`（锚点） |
| `infrastructure/persistence/repositories/asset_places.py` | `assets.metadata_json["reverse_geocode"]`（拆列，设计 §3.2②） |
| `infrastructure/persistence/repositories/events.py` | `backend/db.py:2287-2330` / `:3343` / `:5482` |
| `infrastructure/persistence/repositories/facts.py` | `backend/db.py:3397-3460` |
| `infrastructure/persistence/repositories/search_terms.py` | `backend/retrieval_indexes.py:105-345` |
| `application/memories/assign_events.py` | `backend/db.py:1962-1990` + `:2287-2330` + `:2082-2110` |
| `infrastructure/persistence/migrations/0005_timeline.sql` | `backend/db.py` 的 `events` / `facts` / `observation_search_terms` / `observation_search_fts` 建表 |

---

## 6. 这份文档没有做的事

- **没有**逐行核对 1.0 的 `db.py` 里 S4 范围之外的函数（`trips` / `stories` /
  `relationships` / `face_clusters` 等），它们属于 S5 / S7，在各自切片里审计。
- **没有**验证 1.0 那 30 条通用别名在真实召回上的效果——`_PLACE_ALIASES` 在 1.0 的
  生产库里实际命中次数没有统计（`place_alias_names` 的调用点只有 `place_text_matches`，
  而它只在 metadata 通道里跑，而那个通道的 place 预筛是坏的，见第 16 项）。
  2.0 保留这 30 条是**因为它们是通用双语知识且用户已裁决**，不是因为有召回数据支撑。
- **没有**测过 `allCountries`（402MB）全量索引的内存与建树时间。见 §7。

---

## 7. 环境事实（实测）

| 项 | 实测值 | 备注 |
|---|---|---|
| FTS5 | **可用**（`sqlite3` 自带） | `.venv/bin/python -c "CREATE VIRTUAL TABLE … USING fts5(x)"` 通过 |
| `pygeo_cn` | 0.1.5，装在 2.0 的 `.venv` 里 | 连带 `geopandas 1.1.4` / `pandas 3.0.6` / `shapely 2.1.2` / `pyogrio 0.13.0` |
| PyGeoCN 单次耗时 | **约 0.30 秒**（每次 `geopandas.read_file` 三份 GeoJSON） | 已加按坐标的 LRU；每个**新**坐标仍要付一次 |
| PyGeoCN 实测返回 | `(37.75, 114.77)` → `河北省 / 石家庄市 / 赵县` | 与 1.0 一致 |
| `scipy` | 1.17.1（新装，供 `cKDTree`） | 1.0 venv 里本来就有 scipy 1.15.3 |
| 153 → `download.geonames.org` 带宽 | **27–34 KB/s** | `allCountries.zip`（402 MB）按此速率需 **约 3.7 小时** |
| `cities15000.zip` | 3,359,366 B，实测解出 **34,092** 个可用点 | 国外城市级已可用 |
| `CN.zip` | 32,195,246 B（约 96 万条中国区记录） | 见下 |
| 磁盘 | `/home/asus` 可用 1.1 T | 充足 |

**关于 `allCountries`（402 MB）**：用户裁决 #1 提到它，实测带宽下需要约 3.7 小时。
153 上**目前没有任何 GeoNames 数据**（`/home/asus/Github/Sentrix-Home-Web/data/geo/`
不存在）。本轮的验证用 `CN.zip` + `cities15000.zip`（合计 35 MB）覆盖设计 §3.3 声明的
能力边界（国内到区县 + 乡镇/村庄，国外到城市/乡镇）。`allCountries` 与
`CN.zip + cities15000.zip` 的中国区记录**是同一批数据**（`CN.zip` 就是
`allCountries` 按 `country_code = CN` 切出来的），差别只在国外记录的密度
（`allCountries` 到村，`cities15000` 到人口 ≥15000 的城镇）。要做「国外到村庄」才需要全量。


---

## §7 补做 S4a 与实施 S4b 时新增的偏离（第 23–38 条）

前 22 条见上面的 §1–§6。这一节只记本轮新增的，格式同上：**1.0 怎么做 / 2.0 怎么做 / 依据**。

### 7.1 S4a 补做时被 commit 切分逼出来的（4 条）

**第 23 条 · `asset_places` 的「解析了但没结果」写不进去。**
1.0 把 `{}` 与缺失混为一谈（都是空字符串）。2.0 的迁移注释写的是「后者有行且
precision/source 为空」，但 `save()` 的入参是 `Place`，而两槽皆空的 `Place` 在
`__post_init__` 里直接抛——**这一态其实写不进去**。
**2.0 的实际做法**：不写行；第三态由「asset 上有 GPS」+「`asset_places` 里没有行」
两件事共同表达，`RetrieveMetadata` 的 `recognize_place` 回调正是按这个判据剔除的。
依据：`Place` 的两槽皆空是构造期就拦住的不变量，为一个写不进去的状态在迁移里留列名
注释是过度承诺。

**第 24 条 · 任务边界与文件边界不一致（四处）。**
`GpsPoint.distance_km` 的测试在 Task 46、实现原定在 Task 49；`domain/memories/ports.py`
的两个仓储跨 Task 50/52；`domain/retrieval/ports.py` 的 `VectorRepository.get`（51）与
词法端口（53）同文件；`test_timeline_repositories.py` 跨 49/51/52/53 四个任务。
2.0 按**任务**切，不按文件切：实现随它的测试走，端口分两次落，跨任务的测试文件按节
拆成四个版本（终版与快照逐字节一致）。依据：连续实现掩盖了「谁先谁后」的约束，
而 mypy/ruff 会在切分的那一刻把它们指出来（见计划文档的四处错位表）。

**第 25 条 · 三处原本没有门禁的地方补了测试。**
Task 47 只有一个 `python -c` 片段；`composite.py` 零测试；Task 49 的门禁
`test_migrations.py` 验的是迁移执行器、在 0005 存在之前就是绿的（恒真门禁）。
2.0 补 `test_pygeocn_resolver.py`（5 条）、`test_offline_place_resolver.py`（5 条）、
`test_timeline_schema.py`（4 条）。依据：用户的三条硬约束里「禁止恒真测试」是其中之一，
而 `python -c` 片段不是门禁。

**第 26 条 · `capability-map.jsonl` 的 S4a 12 条是这一轮才补的。**
上一轮的计划文档写了「本切片新增 12 条（全表第 50–61 条）」，但文件一个字节都没动。
2.0 按任务逐条补进对应的 commit（每条带 1.0 的确切来源），`check_capability_map.py` 在
每个 commit 上都是 0 问题。依据：能力登记是 `make ci` 的一部分，不能只写在计划里。

### 7.2 S4b 的实现偏离（11 条）

**第 27 条 · 导入路径多了一步「语义富集」。**
1.0 有两段提交：`_commit_fast_image`（落盘 + 向量）与 `_commit_semantic_image`
（VLM 语义 + 事件 + 事实 + 词法）。2.0 把顺序钉成
`存储 → EXIF/GPS → 地点 → 视觉向量 → 观察 → 语义富集 → 事件归属 → 词法投影`，
**语义富集排在事件归属之前**：事件的 `activity` / 语义字段直接取 Observation，
晚于归并就等于用空语义打分。依据：`s4-deviations.md` §2.3 的归并顺序硬约束。

**第 28 条 · 去重命中时只重跑幂等的两步。**
1.0 的重复导入只重算向量。2.0 重跑**视觉向量 + 地点**（两者都是 asset 的属性，
就地覆盖），**不重跑事件归属**——它不幂等，对同一条 Observation 再跑一次可能开出第二个
事件。依据：使用者唯一的补救动作必须有效，但「有效」只包括重算属性。

**第 29 条 · 单张上传走快路径，整册导入才接观察器。**
1.0 的 fast/semantic 分界在导入路径上是隐式的（取决于哪条 `_commit_*` 被调用）。
2.0 做成显式参数 `build_import_assets(container, observe=...)`：`POST /media/import`
不调模型（上传要立刻返回），`POST /imports` 才接。依据：契约测试不能打真模型，
而单张上传的响应时间不该由一次 VLM 推理决定。

**第 30 条 · 视觉观察只落 `ObservationSemantics` 的七个字段。**
1.0 的 `analyze_image` 还要 semantic 受控词表（地点/物品/氛围三个主类体系）、
`detail`（visible_details / regions / text_blocks / uncertainties）与模型自报的 `facts`。
2.0 全部不搬。依据：`_terms_from_detail` 属于 S6 的 OCR/VLM 精读（S4 的 Observation
没有 `detail` 字段）；事实由 `maintain_fact` 规则决定，不由模型自报（S5）。

**第 31 条 · 事件总结不写 `episodic` 向量。**
1.0 的 `_persist_event_summary` 在同一个事务里写一条事件文本向量（走 8101）。
2.0 的检索通道是 visual_ann + lexical + metadata 三路，**没有任何一路读 `episodic`**。
依据：写一条没人消费的向量只是让「事件语义检索」看起来已经做了。等 S6 接上再搬。

**第 32 条 · 事件总结不写回 confidence。**
1.0 的 `_persist_event_summary` 的 UPDATE 只有四列（title / event_type / activity /
summary），确定性摘要那 0.45 从不落库。2.0 一样。依据：逐列对齐 1.0 的 UPDATE。

**第 33 条 · `EventRecord` 补 `created_at`，`EventRepository` 补三个方法。**
按日期分组时 `time_start` 缺失要退回 `created_at`（1.0 让客户端各自回退，同一份数据在
两个前端上分到不同的天）；`count_by_scope` 让分页能说出「还有多少」（1.0 只能靠
「返回条数少于请求条数」猜）；`apply_summary` 是事件总结的落点；`place` 过滤参数让
地点筛选发生在 SQL 里而不是取回来再过滤。依据：这些是「后端是权威」的直接后果。

**第 34 条 · 时间线按天分组放在后端。**
1.0 由客户端分组。2.0 在后端分。依据：日期属于事件的语义（含 `created_at` 回退），
而分组必须在分页**之后**做，页才是「事件的页」。

**第 35 条 · 整册导入同步执行。**
1.0 的批量导入是 CLI/脚本。2.0 的 `POST /imports` 同步跑完再返回。依据：2.0 没有任务
队列，装作异步只会把「跑到哪了」藏起来。代价写进了 runbook 的相邻章节：客户端超时要
自己调大。

**第 36 条 · Web 的落点是 `App.tsx` 而不是 `router.tsx`。**
计划写的是「Modify: `router.tsx`」。2.0 Web 里**没有 router**（S2/S3 的界面切换是
`App.tsx` 里的条件渲染）。依据：按实际落点改，不为一个计划里的文件名发明一层路由。

**第 37 条 · e2e 的模型部分用确定性假件。**
计划 Task 62 写的是「真相册、真模型」。2.0 用真照片 + 真 EXIF + 真 GeoNames 数据 +
真 PyGeoCN，但 VLM 观察器与视觉嵌入器换成假件。依据：8100 的 llama-server 是 1.0 的
服务、生命周期不归 2.0 管；chinese-clip 是 1.6GB 权重 + GPU 推理。两者都不属于 `make ci`。
「真数据」这条要求被完整满足——本轮抓到的两条发现（国外坐标、罗马字 ADM4）都来自真数据。

**第 38 条 · ADM4 记录可能只有罗马字名，展示层不强行翻译。**
真数据实测：`geonameid 7577037` 的 `alternatenames` 列为空，展示成
`河北省邯郸市永年区 · Nanlvgu西大屯附近`；同类还有 `Wusong Qu`、`Xisu`、`Hang Dong`。
1.0 没有这一级。2.0 的 `_display_name` 只在备选名里**找得到汉字**时才替换，找不到就用
原值——**宁可半中半英，也不拿拼音去猜一个中文名**。依据：这是数据缺失，不是实现缺陷。


---

## §8 S4c（检索融合接线）新增的偏离（第 39–47 条）

S4c 补的是 S4b 的两处遗留：检索融合没接线（Task 64）与词法投影的 asset 锚点没有写侧
（Task 65）。这一节的格式与 §7 相同：**1.0 怎么做 / 2.0 怎么做 / 依据**。

**第 39 条 · 编排的落点从「一个 Kernel 类」挪到 application 层。**
1.0 的 `EvidenceRetrievalKernel`（`backend/evidence_retrieval.py:92`）是个六百行的类，
同时管召回、融合、条件判定与证据包。2.0 按
`2026-09-21-sentrix-home-web-2-refactor-plan.md` 第 34 行的目标表，落到
`application/retrieval/retrieve_evidence.py`，**只搬前两段**（多通道召回 + 加权 RRF）。
依据：四层架构里没有「Kernel」这个位置；S4 没有 `QuerySpec` / 条件判定，先搬能搬的，
S6 在同一文件上加后两段。

**第 40 条 · 视觉命中的名次从融合结果里还原，而不是给 `RetrieveVisual` 加一个原始命中出口。**
`RetrieveVisual` 是 S2 的单通道用例，`execute()` 内部已经 `fuse` 过一次。编排层从
`channels["visual_ann"]` / `raw_scores` 把它还原成 `CandidateHit`（`_visual_candidates`）。
依据：另一条路是把 `execute` 拆成 `recall` + `execute`，但那会让 `execute` 失去生产
调用方（只剩测试在用），或者要重写它那 301 行单测。融合只消费 `rank`，而 `rank` 原样
记在命中里，所以还原是无损的，分数由编排层按名次重算。

**第 41 条 · 每条通道的召回条数是 4 倍过取，不是整册回灌。**
1.0 把每条通道的 `recall_limit` 抬到 `max(requested_limit, authorized_count)`，即**整个
已授权 scope**（`backend/evidence_retrieval.py:176-181`），理由写在 `:171-176`：不能让
一个相关资产因为「另一条通道把它排在了某个任意头部之外」而消失。2.0 用
`RECALL_OVERSAMPLE_FACTOR = 4`（与 `retrieve_visual.py` 的 `OVERSAMPLE_FACTOR` 同值）：
融合前不裁到交付上限，融合后才按 `limit` 交付。依据：1.0 的写法是一次查询把全 scope
拉进内存，2.0 的 `/search` 有明确的交付上限，4 倍过取买到了同一条性质而成本有界。
`test_a_candidate_outside_every_channels_head_is_still_ranked_by_the_fusion` 钉住它。

**第 42 条 · metadata 通道在 S4 的端点上恒静默。**
1.0 的 `MetadataRetriever` 只在有正向结构化条件（时间窗 / 媒体类型 / 地点）时才给召回
信号（`backend/retrieval/metadata.py:28-31`），而那些条件来自查询解析器。2.0 的
`GET /api/v1/search` 只有 `q` / `scope_id` / `limit`，硬条件属于 S6（设计文档 §8 的 S6 行：
「补齐六路召回 + 硬条件（时间/人物/地点）」）。所以这条通道**接进了融合，但在端点上
恒返回空**——这与 1.0 对纯内容词查询的行为一致，不是「没接上」。`EvidenceRequest` 已经
留好 `time_bounds` / `media_types` / `negated_media` / `place` 四格，由 S6 来填。

**第 43 条 · 助手入口与 HTTP 入口共用一次融合。**
1.0 的 HTTP 侧与 `agent_runtime/tools.py` 的 `search_memories` 走的是同一个
`EvidenceRetrievalKernel`。2.0 原来两处各 new 一个 `RetrieveVisual`，于是「网页能搜到、
助手搜不到」是默认行为。现在两个入口都调 `api/routes/search.py` 的
`build_retrieve_evidence(container)`（`routes/assistant.py` 从 `routes/search.py` import
这个工厂，与它已经从 `routes/media.py` import `deliver_asset` 是同一个形状）。

**第 44 条 · `SqliteLexicalIndex` 多了两个必填协作方。**
锚点列的值来自 **asset**（`file_name` / `captured_at`）与它解析出的 `Place`，而
`refresh_from_observation(observation)` 的入参只有 Observation。2.0 给适配器加了
`assets` / `places` 两个必填关键字参数（1.0 的 `RetrievalIndex` 拿着整个 `store`，
不存在这个问题）。依据：做成可选或从 observation 反查都会让「锚点没写」重新变成静默
失败，而这一版修的就是它。

**第 45 条 · 锚点行的 `source_revision` 写 `observation.revision`，不是 1.0 的硬编码 1。**
1.0 的 `_asset_anchor_rows` 每条都返回 `source_revision = 1`（`retrieval_indexes.py:236` 等）。
2.0 写的是当前 Observation 的 revision。依据：2.0 里这个字段的含义是「这行是为该观察的
哪个 revision 写的」，而锚点行与 observation 行是先删后插、同批重写的；没有任何消费者
拿它做过期判断。行为无差异，记在这里是因为数值确实不同。

**第 46 条 · `geo` 的取值是 `Place.searchable_text()` 的分段，不是 1.0 的 `label`。**
1.0 写四个值：`label`（`浙江省杭州市萧山区` 整串）、`province`、`city`、`district`
（`retrieval_indexes.py:230-235`）。2.0 写 `searchable_text()` 的每一段（省 / 市 / 区县 /
乡镇 / 村庄，不含 GPS 坐标）。依据：1.0 的 `label` 在 PyGeoCN 那条路上**可能退化成坐标
串**（它自己用 `DISPLAY_COORDINATE_RE` 挡过这件事），而 2.0 的 `Place` 把 admin（面）与
nearest（点）分成两个槽位，分段写正好把它们都覆盖到。整串查询仍然能命中——FTS5 的
bigram 会把 `浙江省杭州市萧山区` 拆开去撞 `浙江省` / `杭州市` / `萧山区` 三段。

**第 47 条 · 第 31 条（事件总结不写 `episodic` 向量）补上 1.0 侧的实证。**
原条目只说「没有任何一路读 `episodic`」。接线时对回 1.0：**消费它的是 `text_ann` 通道**
——`TextAnnRetriever` 的 `spaces=("semantic", "episodic")`
（`backend/retrieval/text_ann.py:30`），两个 space 的命中按余弦取大后**合并成同一条通道**
再进融合，不是两条通道。所以 `episodic` 既不该单独进 `fuse()`，也不该在 S4 提前写；
等 S6 落地 `text_ann` 时连同两个 space 一起搬。`summarize_event.py` 的模块注释已按这条
实证改写。
