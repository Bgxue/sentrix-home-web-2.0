# 1.0 行为基线

基线提交 `2d2cee7cbf1023f5019b9aaa9061af9c4cf45afa`。
2.0 重写时必须保持这些数值与规则不变，除非明确决策要改。

## 人脸

| 项 | 值 | 位置 |
|---|---|---|
| 在线匹配余弦阈值 | 0.30（硬编码，无 env 可配） | `backend/db.py:4815` |
| 全局重聚类阈值 | 0.30 | `backend/face_clustering.py:67` |
| 质量阈值 | 0.30 | `backend/face_clustering.py:67` |
| **身份向量模型** | **buffalo_l / `w600k_r50.onnx`，112×112，`(x−127.5)/128.0`，512 维** | `backend/model_clients.py:1690-1716`、`:1772-1787`、`:1799-1840` |
| 人脸检测 | RetinaFace r50 ONNX，BGR + mean 104/117/123，priors `STEPS=[8,16,32]`、NMS 0.4、置信筛 0.05；**两趟**（粗 1024/128，空了才细 640/256 且阈值降到 0.1） | `backend/face_detector.py:1-181` |
| SCRFD 复核闸门 | `FACE_MIN_SIZE=64`、`FACE_VERIFIED_SCRFD_SCORE=0.7`、`FACE_VERIFIED_MIN_AREA=0.003`、`FACE_IDENTITY_MIN_QUALITY=0.55`；**只有 verified 才准建簇** | `backend/model_clients.py:1718-1840` |
| 贪心多视角聚类的六个默认参数 | `threshold=0.30` / `prototype_threshold=0.30` / `prototype_limit=6` / `model_identity="all"` / `max_prototypes_per_view=3` / `minimum_quality=0.1` | `backend/face_clustering.py:59-66` |

> **2026-09-23 实测补齐（S5 Task 69）**。上一版这张表只记了三个阈值，而实施时逐行读
> `face_clustering.py` / `face_embeddings.py` 并做变异检查（32 处变异，31 处被测试杀掉）
> 之后，实测出下面九条**表里没写、但不写就会做错**的行为。前八条是 1.0 的真实语义，
> 第九条是 1.0 的缺陷，移植时照抄并单独登记。
>
> **0. 身份向量是 buffalo_l，不是 AdaFace——这一条是更正。**
> 本文件从写下第一版起就没有出现过 AdaFace（`git log -p --all -- docs/refactor/behaviour-baseline.md`
> 对 `adaface` 零命中），但项目里一直流传着「AdaFace 是人脸身份向量」的说法
> （`scripts/runtime/start_sentrix_api_8091.sh` 里导出 `FACE_EMBEDDING_MODE=adaface`
> 与四个 `ADAFACE_*`，环境的 brief 也照抄了它）。**实测那是死配置**：
> `face_embedding_is_adaface()` 全仓零调用；`FaceAdapter.__init__` 把 `identity_adapter`
> 设为 `None`（`model_clients.py:1629`）之后再无第二处赋值，唯一调用点 `app.py:2299`
> 必抛 `AttributeError` 并被上一行的裸 `except Exception: return []` 吞掉；
> 1.0 自己的测试把它标成
> `@unittest.skip("legacy identity_adapter contract removed; identity is fixed to buffalo_l")`
> （`tests/test_face_clustering.py:126`）。真正写出 `embedding_model` 的是
> `_detect_retina_tiled_unlocked` 的两个分支，**都写死 `"buffalo_l"`**。所以这张表现在
> 有了一行确切的值，而那一行不是 AdaFace。见 `s5-deviations.md` 证据 1/2/3。
>
> 1. **`continue` 与 `break` 等价。** `_best_compatible_cluster` 的候选簇已按
>    `(-score, cluster.id)` 降序排过，第一个低于阈值的后面必然都低于阈值。1.0 用的是
>    `continue`，照抄。**这条写不成测试**——把 `continue` 改成 `break` 没有任何用例会红，
>    变异检查里它是 32 处变异中唯一存活的那处。只能靠读代码，如实登记为「未被门禁覆盖」。
> 2. **「同姿态桶为空 → 直接命中」不是无门槛的。** 阈值检查（`cluster.best_score` 对
>    **原型**取最大）发生在 `_same_view_members` **之前**；空桶分支绕过的只是 `_matches`
>    （逐成员比对），不是阈值本身。
> 3. **承 2，后果比想象的强。** 一个 `profile_left` 新样本只要与簇里任一**原型**够像，
>    就能无条件并入，**哪怕它与簇里某些成员的余弦是 −0.64**。1.0 自带的
>    `test_frontal_and_profile_views_share_multi_view_cluster` 正是靠这条把侧脸并进只有
>    正脸的簇。这同时划定了「`unknown` 必须匹配全部成员」这条防线的**真实适用范围**：
>    它只对 `pose_bucket == "unknown"` 的样本生效；姿态桶确实存在、只是不等于新样本姿态的
>    样本，走的是另一条路。**这是本表里最容易做错的一条**——按「相似度必须过人脸级阈值」
>    去重写，会把多视角簇拆成两个。
> 4. **`noise=True` ⟺ 单例 + 低于质量门。** 低于质量门的样本进不了匹配，而 noise 簇又被
>    排除出候选。它仍然会进 `result.clusters` 与 `labels`，**不是被丢掉**。
> 5. **`_update_prototypes` 的排序 / 截断只在追加新桶时跑。** 同桶原地替换直接返回、不重排
>    （替换只在 quality 变大时发生，而排序键首项就是 `-quality`，所以行为等价）。
> 6. **`p10` 的退化条件是 `len(sims) <= 2`**——两个成员也退化，不是 `< 2`。
> 7. **模型身份与 `protected_cluster` 都是整簇否决**，不是按成员过滤：签名对不上就整簇跳过。
> 8. **1.0 的四个纯函数里一行 `os` 都没有。** `os.getenv("ADAFACE_*")` 全在被判死的
>    适配器里，不在 `normalize_embedding` / `pose_bucket` / `compute_face_quality` /
>    `FaceClusterer` 里。所以移植这四个函数**不会产生任何 env 派生常量**。
> 9. **`compute_face_quality` 最后那次 `_clamp` 是恒等。** 四项都非负、权重和为 1.0，
>    上界恰好 1.0——那次 clamp **不可达**。照抄保留（改动它等于改掉一个不可达分支，
>    收益为零而差异真实存在）。
> 10. **`recluster_faces(threshold=0.30, minimum_quality=0.55)` 的 `minimum_quality`
>     与 `FaceClusterer` 自身默认的 `0.30` 不一致。** 1.0 的「两个 0.30」之外还有第三个
>     数值 0.55，只出现在那个 benchmark-only 的函数里，别把它当成人脸质量门的值。
>
> **移植时唯一一处字节级偏离**：2.0 的 `cosine` 给 `zip(...)` 加了 `strict=True`
> （ruff 的 B905 要求；长度已在上一步判断过，行为不变）。

## 检索融合

| 项 | 值 | 位置 |
|---|---|---|
| 默认策略 | `weighted_rrf` | `configs/retrieval/defaults.json` |
| RRF_K | 60 | `backend/retrieval/fusion.py:21` |
| anchor 加成 | +1.0 | `backend/retrieval/fusion.py:22` |
| 通道权重 | visual 2.5 / lexical 1.0 / metadata 1.0 / entity 1.0 / text 0.5 / adjacency 0.5 | `backend/retrieval/fusion.py:37` |
| adjacency | `kind="expander"`，默认关闭，`retrieve()` 恒返回空 | `backend/retrieval/adjacency.py:34`、`:47` |

> **2026-09-22 核对（S4c Task 64）**：接线时把这张表逐条对回 1.0，**没有发现不符**——
> 七个权重、`RRF_K=60`、anchor `+1.0` 与 `backend/retrieval/fusion.py:21-22`、`:37-44`
> 逐值相同，2.0 的 `domain/retrieval/fusion.py` 只是把 `enumerate` 换成 `hit.rank`、
> 同分按 `asset_id` 稳定排序（两处都已在文件注释里记明）。补记三条这张表原先没写、
> 但接线必须照搬的：
>
> - **通道进融合的顺序**是 `metadata → entity → lexical → visual_ann → text_ann`
>   （`backend/retrieval/__init__.py:79`）。顺序是语义：`fusion.fuse` 在候选第一次
>   出现时按那个通道定 `evidence_class`（`backend/retrieval/fusion.py:86-95`），
>   metadata 不是第一个就会丢掉 anchor 的 `+1.0`。
> - **adjacency 不进并行召回**：`_retrieve_multi` 的召回循环跳过
>   `kind == "expander"` 的通道（`backend/evidence_retrieval.py:198-201`），
>   Kernel 拿 primary 融合结果的 exact/strong 当种子再 `expand`
>   （`:246-273`），扩展结果 append 在主结果之后。默认配置里它连实例都不建
>   （`configs/retrieval/defaults.json:10` 的 `"adjacency": false`）。
> - **缺通道的形态是「不在列表里」**（`backend/retrieval/__init__.py:79-84` 只对启用
>   的通道调工厂），不是「构造一个恒空的通道」。`channel_hits` 里没有那个键。
> - **每条通道的召回条数**抬到 `max(requested_limit, authorized_count)`
>   （`backend/evidence_retrieval.py:176-181`），融合前不按交付上限裁
>   （理由写在 `:171-176`）。2.0 的等价物见 `s4-deviations.md` §8。

## 助手

| 项 | 值 | 位置 |
|---|---|---|
| 默认 profile | `goal_driven_candidate` | `backend/agent_runtime/profile.py` |
| profile 预算 | 8 步 / 6 次调用 / 180s | `backend/agent_runtime/profile.py` |
| `max_search_calls` 硬收敛 | 1 | `backend/agent_runtime/runtime.py:1895` |
| 本地模型 max_tokens 上限 | min(1501, 4400 - prompt_tokens)，下限 64 | `backend/app.py:2680-2692` |

## 事件

> **2026-09-22 修正**：本节的「信号」一行原文写的是**四个**信号（时间 / GPS 2km /
> 视觉相似 / 人物）。实测 `backend/db.py:2065-2070` 的加权和是**八项**：

```
0.25·time + 0.25·location + 0.15·visual_place + 0.05·event_type
+ 0.20·activity + 0.05·objects + 0.20·person + 0.05·visual_boost
+ spatio_temporal_bonus（时空几乎重合时 +0.20）
```

按四个信号去实现会丢掉 `visual_place` / `event_type` / `activity` / `objects`
四路共 **0.45** 的权重，另加 `visual_boost`（0.05）与时空加成（0.20）——
**合计 0.70**。八项权重之和是 1.20，不是 1.0；`total` 最后靠
`max(0.0, min(1.0, total))` 收口。照「权重必须归一」去改会把饱和区的形状改掉。

| 项 | 值 | 位置 |
|---|---|---|
| 信号（八项） | 时间 0.25 / 位置 0.25 / 视觉地点 0.15 / 事件类型 0.05 / 活动 0.20 / 物件 0.05 / 人物 0.20 / 视觉加成 0.05 | `backend/db.py:2065-2070` |
| 时空加成 | 时间与位置都 ≥ 0.90 时 +0.20 | `backend/db.py:2078` |
| 时间衰减 | 线性，360 分钟到底 | `backend/db.py:2004` |
| 位置衰减 | GPS 线性，**2 公里**到底（`max(0, 1 - km/2)`） | `backend/db.py:2015` |
| 视觉加成 | 相似度 0.70 以下不给分，1.00 给满 | `backend/db.py:2071-2074` |
| 拆事件护栏 | 语义冲突 + 无物件/人物佐证 + 视觉 < 0.45 → `semantic_visual_conflict`，总分归零 | `backend/db.py:2055-2063` |
| 候选时间窗 | 6 小时（硬窗，与 360 分钟衰减不是一回事） | `backend/db.py:1967` |
| 候选阈值 | `total >= 0.50` | `backend/db.py:1969` |
| 活动匹配 | 相同或互为子串 → 0.9（措辞不同不惩罚） | `backend/db.py:2030-2033` |
| 物件/人物/事件类型匹配 | 0.8 / 0.8 / 0.8 | `backend/db.py:2043,2047,2027` |
| 归并时机 | 导入时同步 | `backend/db.py:2287` |
| 全局两两归并 | `consolidate_events`，GPS 线性 **1000 米**衰减 + 时间 ≥ 0.70 + 三条 merge 分支 | `backend/db.py:2192-2220` |
| 全局归并的调用点 | **只在 4 个 benchmark / maintenance 脚本里**；生产导入路径只调候选打分 | `scripts/benchmarks/ingest_album3_v2.py:64` 等 |

> **2.0 的取舍**：候选打分（2km 那套）移植；`_event_pair_score`（1000 米那套）
> **不移植**。两个函数对同一个距离给两个答案，而 1.0 的生产路径只用前者。
> 见 `docs/refactor/s4-deviations.md` §2 第 2 项。

## 地点

| 项 | 值 | 位置 |
|---|---|---|
| 国内精度 | 区县级（PyGeoCN，面） | `backend/geocoding.py:103-140` |
| PyGeoCN confidence | 0.90（1.0 记作 `source="tianditu"`；2.0 改名 `pygeocn`） | `backend/geocoding.py:134` |
| GeoNames confidence | 0.70 | `backend/geocoding.py:190` |
| `precision` | `"district" if district else "city"`（国内）；国外恒 `"city"` | `backend/geocoding.py:121,172` |
| 两个来源的关系 | **二选一**：PyGeoCN 有结果就返回，`reverse_geocoder` 根本不会被调用 | `backend/geocoding.py:196-216` |
| 距离 | GeoNames 那一路算了 `distance_km`，但**从不读取**（既不展示也不参与判定） | `backend/geocoding.py:186` |
| 地点匹配 | 三条确定性规则：整串子串 / 行政区去后缀双向子串 / 中英别名 | `backend/geocoding.py:269-308` |
| 行政区后缀 | `省 市 区 县 地区 自治州 盟 特别行政区`，按序取第一个命中，**只砍一次** | `backend/geocoding.py:222-230` |
| `_PLACE_ALIASES` | 32 条中英桥；其中 `"赵州桥"` / `"三峡坝址"` 两条是 benchmark 泄漏，见 `frozen.md` | `backend/geocoding.py:234-258` |

> **2.0 的扩展（NEW-2.0）**：国内精度从「区县」下探到「乡镇 + 村庄/居民点」，
> 数据源 GeoNames `ADM4`（11,450 条）+ `PPL`（860,831 条），最近邻点索引。
> 1.0 零参考。见 `docs/refactor/s4-deviations.md` §1 第 1 项。

## 外观证据 / 人物画像 / 画像流水线（S5c）

| 项 | 值 | 位置 |
|---|---|---|
| 头肩裁剪的三个松紧系数 | `HEAD_SCALE=1.35` / `BODY_HEIGHT_SCALE=4.5` / `FACE_TOP_EXPANSION=0.35` | `backend/person_appearance.py:4,21,23` |
| 裁剪尺寸下限 | 宽 `<24` 或高 `<48` → **抛错，不回退整图** | `backend/person_appearance.py:23` |
| 外观证据唯一键 | `(person_id, face_instance_id, model_name)`——**换模型是另一条证据** | `backend/db.py:4482` |
| 外观置信度兜底 | `0.55` | `backend/model_clients.py:1352` |
| 画像长度 | 汉字数 80–500 | `backend/person_portraits.py`（提示词原文即写死） |
| 画像主题数 | 2–6 | 同上 |
| 证据包抽样 | `MAX_MOMENTS=12` / 每事件 `PER_EVENT=2` / 每主题引用 `MAX_REFS_PER_THEME=2` / `TOP_ACTIONS=3` | 同上 |
| 未确认角色的措辞 | 必须含 `似乎/可能/从照片看/看起来/像是` 之一 | 同上（`_mentions_role`） |
| 核心人物排序六项权重 | 日期 `0.30` / 事件 `0.20` / 覆盖 `0.15` / 同伴 `0.10` / 场景 `0.10` / 质量 `0.15`（**和恰好 1.0**） | `backend/person_insights.py:31-57` |
| 排序的满值档 | `DATE_FULL=8.0` / `EVENT_FULL=6.0` / `COVERAGE_FULL=20.0` / `CO_PERSON_FULL=4.0` / `SCENE_FULL=4.0` | 同上 |
| 确认加成 / 单日惩罚 | `CONFIRMED_BOOST=0.20` / `SINGLE_DAY_PENALTY=0.35` | 同上 |
| 核心人物门槛 | 上限 `10`，且跨够 `2` 天（`CORE_MIN_DATE_COUNT`） | 同上 |
| 代表照 | 上限 `12` / 每事件 `2` / 去重阈值 `0.94` | 同上 |
| 提示词版本 | `person-moment-v1`（`person_moments.py:186`）/ `person-portrait-v1`（`person_insights.py:251`） | 两者都是 upsert 键或画像行的组成部分 |

> **2026-09-29 补记（S5c / Task 87 时才发现缺）**。这一节是 T87 验收时才补进来的——
> T85a（裁剪几何）、T86c（画像三层）、T86d（排序与代表照）落地时都**没有回来更新这份
> 基线**。值本身没错（都是从 2.0 已落地的模块里取的，那些模块的 docstring 逐个标了
> 1.0 行号），但**漏记这件事本身是个缺陷**：基线的用途是「下一次有人要动这些数时，
> 能查到它们属于 1.0 的行为、动它们就是偏离」，而查不到就会按自己的判断改掉。
>
> 三条与「值」同等重要的行为，也一并记在这里：
>
> 1. **`select_representatives` 的余弦去重分支在生产上从不执行。** 1.0 唯一的调用点
>    传的是字面量 `{}`（`person_insights.py:271`），所以「与已选中的任一张余弦 ≥ 0.94
>    就跳过」那条永远走不到。**2.0 照搬了这个空表**（`run_insights._select`），
>    把发现记在 `s5-deviations.md` 第 137 条。**动这一条等于改产品行为**，不是修 bug。
> 2. **`person_portrait_revisions` 的 `user_locked` 是写入口的判据**，不是界面上的
>    禁用按钮：当前版本被锁住时 `create` 直接返回、不写新行（`db.py:6380`）。
> 3. **外观证据的三重守卫**（人已确认 / 实例存在 / 该实例经 `entity_mentions` 确认给了
>    这个人）。1.0 的调用点把守卫的错误连同媒体错误一起 `except` 掉了，所以**第三重
>    在 1.0 里从来没浮出来过**；2.0 只捕获媒体失败，守卫的错误往外抛
>    （`s5-deviations.md` 第 131 条）。

## 模型与嵌入

| 项 | 值 | 位置 |
|---|---|---|
| 视觉嵌入 | chinese-clip-ViT-L-14，768 维，进程内 GPU | `backend/embeddings/chinese_clip_visual.py` |
| 文本嵌入 | BAAI/bge-m3，1024 维，HTTP sidecar `127.0.0.1:8101`（CPU） | `backend/embeddings/bge_text.py` |
| 方案冻结 | `embeddings/scheme.py` 已钉死，不读 env | `backend/embeddings/scheme.py` |
| VLM 端点 | `http://127.0.0.1:8100/v1`（llama.cpp，OpenAI 兼容） | `backend/model_clients.py:537` |
