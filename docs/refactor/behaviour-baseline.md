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

> **2026-10-03 W1 追记（换 wemm 之后）**：上面这组通道权重（`visual_ann` 2.5 / `lexical` 1.0 / `text_ann` 0.5）来自 **chinese-clip 时代**的 R7 消融（当时视觉 r10=0.887、词法 0.373、文本 0.158）。**换成 wemm 后没有重跑那次消融**——1.0 自己也
> 没重跑（`backend/retrieval/fusion.py:33-42` 开了 wemm 后权重一字未改，连 R7 的注释都
> 原样留着）。所以这里照搬权重 = 1.0 现状 = **用户验证过的那个配置**；但要清楚它是
> **继承来的未验证假设**，不是「在 wemm 下重新验过的结论」。真要让权重与 wemm 匹配，
> 得单跑一次消融——那是独立的一件事，不在 W1 范围内。改任何一个数字前先改这里。

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
| 视觉嵌入 | tencent/WeMM-Embedding-2B，2048 维，HTTP sidecar `127.0.0.1:8102`（视觉 / 文本**统一空间**） | `backend/embeddings/wemm_sidecar.py` |
| 文本嵌入 | 同上——同一个模型、同一个实例（统一空间，两个槽共用一份 client）；1.0 的 wemm 是**半接线**（只有查询侧跟随方案），2.0 由 W1 把写入侧一并接上 | `backend/embeddings/wemm_sidecar.py` |
| 方案冻结 | `embeddings/scheme.py` 已钉死，不读 env | `backend/embeddings/scheme.py` |
| VLM 端点 | `http://127.0.0.1:8100/v1`（llama.cpp，OpenAI 兼容） | `backend/model_clients.py:537` |

## 照片复核 / 读字（S6）

`inspect_photo` 与 `read_photo_text` 的提示词与取值口径。1.0 的调用量分别是
10,721 次与 2,867 次，是助手的主要取证手段（设计文档 §1.3）。

| 项 | 值 | 位置 |
|---|---|---|
| `inspect_photo` 提示词 | 两个槽 `{target_instruction}` / `{question}`，固定要 `observation` / `certainty` / `target_face_id` 三个字段 | `backend/agent_runtime/tools.py:2616-2622` |
| `certainty` 的取值 | **只有 `supported` / `uncertain` 两档**；模型没给时默认 `supported`。**没有「确定没有」那一档** | `:2619`、`:2255` |
| 目标人物那句 | 只在**指名了目标人物**时才填；与裁剪图做没做出来无关（裁剪失败照样填，因为那句话说「（如有）」） | `:2196-2198`、`:2186-2200` |
| 人脸清单 | 拼在提示词**之后**（不是槽）；未确认的人**不填 `person_name`**，模型据此不许猜姓名 | `:2204-2208`、`:2343-2355` |
| 发几张图 | 指名目标人物且定位到裁剪件时发**两张**：裁剪件在前、原图在后 | `:2192` |
| 问题缺席时的默认问法 | `请描述这张照片` | `:2125` |
| **`read_photo_text` 的第一条路** | **任何 OCR 后端之前**先读导入期已落库的 `observations.ocr_text`，命中即返回 `source: ingestion_ocr` / `certainty: supported` / `persisted: true`（零推理开销） | `backend/agent_runtime/ocr_tool.py:544-559` |
| `read_photo_text` 的后端顺序 | PaddleOCR 小模型优先（CPU、零显存、进程内），读不到回退 VLM；`paddleocr` 装不上时自己退化成 VLM-only | `ocr_tool.py:3`、`:134-142` |
| 密集文字的两条处理 | 3×3 切块放大（`rows=3, cols=3, scale=2.0`）、按 y 聚行（`y_tol=26`） | `ocr_tool.py` 的 `_tile_images` / `_small_row_cluster` |

> **`paddleocr` 从来没进过 2.0。** 它不是「移植时丢了」，是那个可选后端一开始就没加：
> 2.0 的唯一 OCR 来源是 `OpenAIVlmObserver` 写入的 `ocr_text`。**所以 2.0 现在所处的状态，
> 正是 1.0 在 `paddleocr` 装不上的环境里自己退化到的那个状态。**
>
> **2026-09-29 实测（153 的 8100，qwen3.5-4B）**：这个模型**答得比问的多**——问
> 「这张图里画的是什么形状？只回答形状名称。」，它回「这张照片里画的是一个红色圆和一个
> 蓝色正方形」。这正是 1.0 在 `read_photo_text` 的工具描述里专门警告模型不要拿它去统计
> 「OCR 金额、桌数」的那件事的两面：**这个工具的用途是「读出真的写着什么」，而 VLM
> 会读出图上没有的字、且不会自报**。T124 的工具描述必须按裁决如实收窄。

## 照片人物取证（S6 · T122）

`query_photo_people`——1.0 调用 887 次。它只回答「**这张照片里有谁**」，
不做跨照片的人名搬运。

| 项 | 值 | 位置 |
|---|---|---|
| 工具描述原文 | 「读取一张已检索照片自己的已确认人物和未确认同行者。asset_handle 必须来自当前 search_memories preview；不会把其他候选照片的人名移植过来。」 | `backend/agent_runtime/tools.py:2723-2724` |
| 声明的证据类型 | `photo_identity` + `confirmed_identity`（**两个都是**公共类型） | `tools.py:2727`、`evidence_contract.py:13-14` |
| 已绑定人脸的判据 | `face_instances ⋈ face_clusters ⋈ entities`，唯一那条过滤是 `e.entity_type = 'person'`。**没有任何状态过滤** | `tools.py:2281-2300` |
| 该查询的排序 | `quality DESC, detection_confidence DESC`（只有两个键，同分时取到哪行由 SQLite 定） | 同上 |
| 未确认同行者 | `people_json` 里**没有 `entity_id`** 的条目数 **减去**已绑定的脸数，下界 0 | `tools.py:2582-2588` |
| 人名快照取哪一条 | `observations` 里 `ORDER BY updated_at DESC LIMIT 1` | `tools.py:2578-2580` |
| 空结果那句话 | `"没有已确认姓名"`（不是空串——空串会让模型以为字段坏了） | `tools.py:2602` |
| 两个失败码 | `bad_handle`（handle 不在结果集）与 `scope_mismatch`（结果集不属于当前相册）分开 | `tools.py:2566-2576` |

> **三处「名字/直觉与代码不一致」，都是 1.0 的，照搬不修。**
>
> 1. **`_confirmed_photo_identities` 判的是「绑没绑」，不是「确认没确认」。** 函数名与
>    docstring 都写 *confirmed*，而 SQL 两个 JOIN 都是 INNER、一个状态过滤都没有。
>    2.0 里簇可以带 pending 实体（`enroll_face` 就是「建簇带 pending 实体」）。
>    **顺手补一个 `status = 'confirmed'` 是在改产品行为。**
> 2. **`people` 是照着人脸实例逐行生成的，同一个人有两张脸就出现两次。**
>    摘要会念成「爸爸、爸爸」（`tools.py:2589-2593`）。**去重会同时改掉 `people` 的长度**，
>    而那个长度是证据。
> 3. **`membership` 在 1.0 来自家庭图谱**（`get_effective_family_membership`，
>    `tools.py:2295`），**不是实体上的字段**。2.0 只有 `entities.family_role`，
>    所以工具描述里如实写明「尚无关系图谱」——不要据此推断两个人之间的亲属关系。
>
> **另外：那条 SQL 有三重互相覆盖的过滤**（簇 INNER、实体 INNER、`entity_type='person'`），
> 单独把任一 INNER 换成 LEFT JOIN 都观察不到行为差别——`NULL = 'person'` 不为真，
> WHERE 那一句自己就把未绑定的行挡掉了。2026-09-30 用定点变异实测确认过。


> **2026-09-30 补：`read_photo_text` 的 VLM 回退是死代码。** 上面那张表里
> 「后端顺序」与「密集文字的两条处理」两行描述的是 **PaddleOCR 那一路**，而模块注释
> 承诺的「读不到回退 VLM」**在 1.0 里没有实现**：`_OCR_PROMPT` / `_OCR_PROMPT_FULL`
> 全仓从未被引用，`ocr_tool.py` 整条链上一次模型调用都没有。
> 1.0 生产 venv 里装着 `paddleocr`，所以 2,867 次调用跑的都是小模型。
> **2.0 的 `read_photo_text` 因此只有一条路：读导入期已落库的 `observations.ocr_text`**
> （那是 1.0 的第一条路，见上表最后一行）。裁决与理由记在 S6 计划 §8.8。

## 实体目录（S6 · T128a，2026-09-30）

`GET /api/entities`（1.0 `app.py:1858-1868`）是**全部实体**的目录；`/api/people` 是
**家庭成员**，两个端点。下面这些数值、字面量与边界照搬，不改。

| 项 | 值 | 位置 |
|---|---|---|
| `includePeople` 默认 | `false`（不给人物） | `backend/app.py:1859` |
| pending 人物在**这个**端点上的名字 | `待命名成员`（**不带序号**） | `backend/app.py:1865` |
| pending 人物在 `/api/people` 上的名字 | `待命名成员 1`、`待命名成员 2`（按列表顺序递增） | `backend/app.py:1517-1519` |
| pending 人物的接口说明 | `由人脸聚类生成，等待用户确认` | `backend/app.py:1867` |
| 同一件事在**库里**的原文 | `由 buffalo_l embedding 生成，等待用户确认` | `backend/db.py:4639` |
| `status` 为空时排除的取值 | `rejected` / `superseded` | `backend/db.py:4047` |
| 空间规则 | 恒有 `scope_id NOT IN (SELECT id FROM memory_spaces WHERE include_in_people = 0)` | `backend/db.py:4043-4045` |
| 排序与上限 | `updated_at DESC`，**无 LIMIT** | `backend/db.py:4052` |
| `cluster_count` | `face_clusters` 计数，**没有状态过滤**（被拒的簇也算） | `backend/db.py:4060-4062` |
| `photo_count` | `COUNT(DISTINCT a.id)` 且 `fc.status != 'rejected'` | `backend/db.py:4067-4072` |
| `mention_count` / `evidence_count` | `entity_mentions` / `entity_observations` 的行数——**两张不同的表** | `backend/db.py:4078-4086` |
| `relationship_count` | subject 侧 + object 侧两轮相加，`status != 'retracted'`；**2.0 留 S7，不出现在响应里** | `backend/db.py:4088-4100` |
| `avatar_face_instance_id` | 非 rejected 簇里按 `detection_confidence DESC, created_at ASC` 取第一 | `backend/db.py:4102-4110` |
| `preview_asset_id` / `preview_file_name` / `preview_media_type` | `entity_observations ⋈ observations ⋈ assets`，按 `captured_at DESC, o.id DESC` 取第一 | `backend/db.py:4111-4120` |
| `reviewable`（person） | `status == 'confirmed'` 或 存在 `status != 'rejected'` 且 `member_count > 0` 的簇 | `backend/db.py:4128-4131` |
| `single_sample`（person） | `status != 'confirmed'` 且 所有簇 `member_count <= 1` | `backend/db.py:4132-4133` |
| `reviewable`（非 person） | `evidence_count > 0`——与簇无关，**也不看 status** | `backend/db.py:4135` |
| 私密地点 | 仅 `place`：`private_flag` **`is True`** 时名字换成 `alias`（缺省 `私密地点`）并给 `private: true` | `backend/db.py:4674-4685` |
| 私密地点的两个属性键 | `private_flag` 与 **`alias`（单数）**——人物别名用的是 `aliases`（复数），差一个字母 | `backend/db.py:4679-4681` |

> **三处反直觉的边界，都不要「修好」。**
>
> 1. **`private_flag` 判的是 `is True`，不是真值。** 属性值是从 JSON 解出来的任意类型，
>    整数 `1` 与字符串 `"true"` 都能通过真值判断，而 1.0 只认真正的布尔。
> 2. **`single_sample` 不看簇的 `status`。** 一个**被拒**的、有两张脸的簇照样让它不是
>    孤证——而 `reviewable` 是看 status 的。两处规则不同，都是 1.0 的原样。
> 3. **空簇列表下 `all()` 为真**，所以一个还没聚出任何簇的 pending 人物判为
>    `single_sample`。
>
> **一条死检查，别当字段搬过来。** 1.0 前端 `src/app.js:695` 有
> `if (ent?.is_self) return`——`entities` 表**没有 `is_self` 列**（`SELECT *` 的列里没有，
> `_decorate_entities` 也不补），这个 `if` 永远不成立。
>
> **一条在生产里是死路径的默认值。** `includePeople` 默认 `false`，但 1.0 前端
> **永远传 `true`**（`src/api.js:42` 把它写死在请求里）。默认值照搬——它是 API 契约——
> 但没有任何 1.0 的界面场景走那条默认路径，**不要为它编造消费场景**。
>
> **装饰字段在前端是承重的**（这是 128a 不做减法、全量搬的理由）：
> `reviewable` 是 `src/app.js:675` 实体网格的入口过滤谓词；`avatar_face_instance_id` 与
> `mention_count` / `cluster_count` / `relationship_count` 在 `:678` 的实体卡上；
> `evidence_count` 在 `:876` / `:910` / `:929`；`photo_count` / `single_sample` 驱动
> `:939` / `:2112` 的批量确认。
> 例外：`preview_*` 在**实体列表上**产出了但前端当前没有读者——它被读的地方是
> `entityGroups`（`:785-786`，T129 的投影）。如实记下，不夸大成承重。
>
> **一处已存在的 2.0 与 1.0 的差异（不在 T128a 范围内，未改）：** 1.0 的 `/api/people`
> 给 pending 人物的是**带序号**的 `待命名成员 {n}`（`backend/app.py:1517-1519`，按列表
> 位置递增），2.0 的 `_person_payload` 用的是不带序号的 `PENDING_CLUSTER_NAME`。
> 本次只把两边的 **summary** 对齐了（那是同一个改写块里漏掉的一条），序号这件事需要
> 单独裁决：序号是**列表位置**的函数，放进 `_person_payload` 意味着那个函数要知道
> 自己在列表里的下标。

## 实体详情（S6 · T128b，2026-09-30）

`GET /api/entities/{id}`（1.0 `app.py:1948-1956` + `db.py:4570-4627`）。**是十二个键，
不是计划里写的「九个子查询」**；2.0 搬九个，三个不进响应（各有理由，见
`application/identity/read_entity_detail.py` 的模块 docstring）。

| 项 | 值 | 位置 |
|---|---|---|
| 实体不存在 | 返回 `None` → 404 `entity not found` | `backend/db.py:4571-4573`、`app.py:1951-1952` |
| pending 人物的改写 | **只换 `canonical_name` 成 `待命名成员`**——`family_role` 与 `summary` **不改** | `backend/app.py:1954-1955` |
| 对照：`/api/people/{id}/profile` | 同一件事**三件都改**（名字 + `family_role=None` + `summary`） | `backend/app.py:1545-1549` |
| 对照：`/api/people/{id}/evidence` | 同上，三件都改 | `backend/app.py:1561-1565` |
| `facts` 的匹配方式 | 按 **`canonical_name` 字符串**匹配 `subject` 或 `object`，**不按 id** | `backend/db.py:4588` |
| `facts` 的取数顺序 | **先取 1000 条（全库、无 scope 过滤）再过滤**——两步的顺序是行为的一部分 | `backend/db.py:4588` |
| `facts` / `relationships` 的状态过滤 | **都没有**（`retracted` 的关系也会返回）；而 `properties` **有** | `backend/db.py:4588` / `:5590` 对 `:4667` |
| `clusters` 的状态过滤 | **没有**——被拒的簇也在详情里 | `backend/db.py:4580` |
| `avatar_face_instance_id` 的状态过滤 | **有**——`fc.status != 'rejected'` | `backend/db.py:4576` |
| 每簇样本数 | `LIMIT 12` | `backend/db.py:4583` |
| `observations` 的取数 | `captured_at DESC LIMIT 100`，**没有第二排序键** | `backend/db.py:4590-4594` |
| `evidence_count`（详情） | **取回来的观察条数**（上限 100） | `backend/db.py:4601` |
| `evidence_count`（列表） | **`entity_observations` 的原始行数** | `backend/db.py:4083` |
| `evidence_files` 的合并 | 观察先、外观后，**按 id 去重且后写覆盖先写**（所以撞车时外观那条赢） | `backend/db.py:4602-4613` |
| `evidence_files` 的 kind | `observation` / `appearance` | `backend/db.py:4607` / `:4612` |
| `file_name` 的兜底 | 取不到就用 `asset_id` | `backend/db.py:4606` / `:4611` |

> **两处「同名不同义」不要统一。** ① `evidence_count` 在**列表**里是
> `entity_observations` 的原始行数、在**详情**里是取回来的观察条数（上限 100）——
> 1.0 就是两个数（`db.py:4083` 对 `:4601`）。② 同一个响应里 `properties` 排
> `superseded`、`clusters` 不排 `rejected`，两种相反的规矩并存。
>
> **`clusters` 的样本排序在 2.0 与 1.0 不同**（已存在的差异，不在 T128b 范围内）：
> 1.0 是 `ORDER BY fi.created_at DESC`（`db.py:4584`），2.0 的
> `FaceRepository.list_instances` 是 `quality DESC, created_at ASC`。
> 两处都会给出「最多 12 张」，但**取到哪 12 张可能不同**。
>
> **`transcript` 不在响应里**：1.0 的 `observations` 表有这一列，2.0 没有——
> 视频/音频的转写属于 **S8**（`application/retrieval/index_observation_text.py` 的
> docstring 已写明「不是遗漏，是那一侧还没做」）。1.0 前端拿它当 caption 的兜底，
> 所以少一个键的后果是那句话落到常量，不会渲染出错。

## 家庭图谱（S7b1 · T301–T306，2026-10-02）

六张表与五条写路由里的常量与判据，逐条指着 1.0。**这些值只存在于 1.0 的代码里、无文档**，
而它们中的每一个都决定了「用户改过的会不会被模型覆盖」。

| 项 | 值 | 出处 |
|---|---|---|
| membership 合法值 | `family` / `friend` / `unknown`——**`unknown` 是合法值不是缺失** | `backend/db.py:5617` |
| 来源合法值 | `model` / `user_override`，**只有两个** | `backend/db.py:5618` |
| `state` 取值 | `effective` / `superseded` / `retracted`（**`retracted` 只出现在关系侧**） | 六张表 DDL（`db.py:702-781`） |
| `locked` | `1 if source == "user_override" else 0`；**block 表恒 1，且那是它的默认值** | `db.py:5658` / `:5766` |
| 用户写死的两个值 | `source="user_override"`、`confidence=1.0`（**五个写路由全部如此**） | `app.py:1668` / `:1684` / `:1700` |
| 关系标签白名单 | **31 条**；一对多是常态（`父亲` 的反向是 `{儿子, 女儿}`，由孩子的性别决定用哪个） | `family_graph.py:1-34` |
| 校验话术 | `关系标签不能为空` / `关系标签不匹配: X -> Y`——两句不同的话 | `family_graph.py:38-41` |
| 一条边 | **落两行**（正 + 反），同 timestamp、同 source、同证据 | `db.py:5793-5805` |
| 关系新行的 `family_graph_id` / `supersedes_id` | **恒 NULL** | `db.py:5797` |
| membership 取当前 | `ORDER BY locked DESC, revision DESC, updated_at DESC LIMIT 1` | `db.py:5636` |
| portrait 取当前 | `ORDER BY created_at DESC LIMIT 1` | `db.py:5913` |
| 头像排序 | `fi.quality DESC, fi.detection_confidence DESC, fi.created_at ASC LIMIT 1` | `db.py:5659` |
| 代表照 | 走**人脸**（`face_instances⋈face_clusters⋈assets`），`ORDER BY a.id DESC LIMIT 3` | `db.py:5662-5669` |
| `appearance_count` | `COUNT(*)` 人脸实例行数——**不是不同资产数** | `db.py:5671-5675` |
| pending 人物的占位 | **「待命名人物」** | `db.py:5677` |

| 触发去抖 | **event watermark**：`COUNT(*) FROM events WHERE scope_id = ?` 与上一次 run 的 `stats['event_watermark']` 比，**没涨就不跑**；`latest is None` 时**不跳过** | `backend/pipeline.py:746-749` |
| `trigger_type` | 手工路由写 `user_rerun`、批末触发写 `ingest`；`input_mode` 恒 `semantic_text_only` | `app.py:1719` / `pipeline.py:750` |
| run 初始态 | `status='queued'`、`current_stage='queued'`、`stats={}` | `db.py:5869` |
| run 完成 | `status='completed'` 时额外写 `completed_at` | `db.py:5895` |
| run 失败 | 只写 `error`，**`stats` 保持空** → watermark 缺失 → 下次一定重跑 | `family_graph_service.py:43` |
| 模型截断（送进去的） | 人 `[:8]`（按 observation 数降序、同数按 id 升序）、每人 observation_ids `[:6]`、descriptions `[:2]` 每条 `[:300]`、共现 `[:30]` 且**只留两端都在 top-8 内的对** | `family_graph_service.py:56/63/64/66-67` |
| 证据里的描述上限 | 每人 `[:20]`（**模型只看前 2 条，两道闸看这 20 条**） | `family_graph_service.py:214` |
| `_has_explicit_relation_evidence` 的词表 | **只覆盖 13 个谓词**；表外的退化成「字面匹配谓词自己」 | `family_graph_service.py:132-145` |

> **三处「1.0 就是这样」不要统一。** ① `get_active_family_portrait` 用 `created_at DESC`，
> 而 `get_effective_family_membership` 用三个键——同一批「取当前有效行」的语义，两个排序。
> ② `set_family_relationship` 的新行 `supersedes_id` **恒 NULL**（历史链断在关系侧），
> 而 `set_family_membership` **会回填** `supersedes_id`。③ `write_family_portrait`
> **不校验 `source`**，另两个写方法都校验 `FAMILY_SOURCES`。
>
> **三条「静默跳过」，都不抛异常——调用方分不出「刚写成功」与「被跳过」：**
> 用户写下的行模型不许覆盖（membership / portrait 直接返回当前行）；撤回过的边在
> `family_relationship_blocks` 里挡着模型；用户重新建边时**删掉**那一行 block。
>
> **`GET /family-graph` 的 `run` 键 b1 不返回。** 1.0 的响应里有它，但 b1 阶段**没有任何
> 东西会创建 run**（自动触发与手动路由都归 S7b2）——恒 `null` 读起来像「确实没跑过」，
> 而真相是「b1 还没有会跑的路径」。S7b2 连创建路径一起补。


### 助手引用家庭图谱（S7c · 2026-10-02）

助手工具 `get_person_profile` 从「只回 `family_role`」扩到家庭图谱那三张表的实际内容。
**下面这些值同样只存在于 1.0 的代码里、无文档。**

| 项 | 值 | 出处 |
|---|---|---|
| 工具新增的四个数据键 | `membership` / `membership_source` / `summary` / `relationships` | 1.0 `db.py:2875-2881` |
| `membership` 缺省 | `"unknown"`——**没有归属行与「归属不明」是同一件事** | 1.0 `db.py:2875` 的 `or "unknown"` |
| `membership_source` 缺省 | `""` | 1.0 `db.py:2876` |
| `summary` 来自 | `family_person_portraits.portrait_text`（1.0 那格叫 `summary_zh`） | 1.0 `db.py:2877` |
| 关系的三个键 | `predicate` / `other_name` / `source`，一个不多一个不少 | 1.0 `db.py:2855-2859` |
| `other_name` 兜底 | `未命名成员` | 1.0 `db.py:2857` |
| 关系方向 | **只收此人为 `subject` 的边**——`predicate` 才是「此人是对方的什么」 | 1.0 `db.py:2852-2858` |
| **`family_role` 不再出现在工具结果里** | 1.0 的 digest **全文没有这个键**；它是 2.0 在 S3 的占位。`entities.family_role` 列保留，`GET /people/{id}` 照旧返回 | 1.0 `db.py:2840-2889` |

**1.0 的方向过滤是残的，2.0 因为双向存储反而不残。** 1.0 一段关系只按一个方向存，
配上「只收出边」之后，查「女儿」的档案**拿不到父亲**——而 1.0 的测试恰好只测了
父为 subject 的方向，把这个缺口盖住了。2.0 的 `set_relationship` 一次写正向 + 反向
两行，所以同一句过滤拿到的是**完整**的关系集。契约测试
`test_a_relationship_appears_on_both_ends_with_its_own_predicate` 钉的就是这件事：
同一条边，妈妈看到 `母亲`、小明看到 `儿子`，各恰好一条。

**「没有关系」不是「查不到这个人」。** `relationships` 为空数组时 `found` 仍为真。
实测那份 53 张照片的相册就是 8 个人 0 条关系——把空关系当成查不到，助手会对着一个
明明存在的人说找不到。

**这一格与 `entities.family_role` 是两条不同的轴。** 1.0 真实库里 298 对
「`family_role` × `membership`」的配对**一致率 0**：一条是自由文本角色，一条是三值分类。

## 视频与音频（S8a · T501–T506，2026-10-04）

| 项 | 值 | 出处 |
|---|---|---|
| 浏览器可直接播的编码 | **只有** `h264` / `avc1`（判 `codec_name`，不看后缀） | 1.0 `video/processor.py:59` |
| 预览转码参数 | `libx264` / `-preset veryfast` / `-crf 23` / `-pix_fmt yuv420p` / `-c:a aac` / `-movflags +faststart` | 1.0 `video/processor.py:62-67` |
| 转码映射 | `-map 0:v:0` 与 **可选** `-map 0:a:0?`（无音轨不该让整次转码失败） | 同上 |
| 转码超时 | 3600 秒（读元数据的 ffprobe 超时是 60 秒，两件事不共用一个上限） | 1.0 `video/processor.py:68` / `metadata.py:43` |
| 拍摄时间优先级 | `com.apple.quicktime.creationdate` → `format.creation_time` → `stream.creation_time`（`creation_source` 记下用的是哪个） | 1.0 `video/metadata.py:60-63` |
| GPS 来源 | `com.apple.quicktime.location.ISO6709`，退 `location`；**ISO6709 格式**（`+22.4846+114.5438+005.469/`） | 1.0 `video/metadata.py:9,31-36` |
| 时区写法 | 紧凑写法 `+0800` 补成 `+08:00`；`Z` 换成 `+00:00` | 1.0 `video/metadata.py:10,44-45` |
| 旋转角 | 先 `side_data_list[].rotation`，退流标签 `rotate`；都没有是 0 | 1.0 `video/metadata.py:66-74` |
| 探测结果的落库键 | `assets.metadata_json` 的 `video_metadata` / `browser_preview_path` / `browser_preview_error`，2.0 另加 `video_probe_error` | 1.0 `video/processor.py:118-121` |
| `/assets/{id}/file` | 视频且非 `?original=1` 且转码件在 → `preview.mp4`；否则原片。**图片那条路一直交原文件**（预览在 `/preview`） | 1.0 `app.py:2448-2478`（2.0 的 `/file` 行为见 `routes/media.py` 注释） |
| 场景事件的类型标记 | `events.source_type = 'video_scene'`；场景列表只取 `status = 'active'`、按 `source_scene_index` 升序 | 1.0 `db.py:2427-2433` |
| 派生物的清场顺序 | 先删 `event_observations` / `observations`，再删 `events` / `assets`（外键开着） | 1.0 `db.py:2435-2448`（2.0 的顺序约束写在仓储 docstring） |
| `reprocess` 的返回 | `202` + `{accepted: true, asset_id, status: "video-queued"}`；资产行状态回到 `queued` | 1.0 `app.py:1472-1481` |
| 整册导入认的视频后缀 | `.mp4` / `.mov` / `.m4v` / `.avi` / `.mkv`（与 `MediaType.from_file_name` 同一组） | 1.0 `app.py` 批量目录扫描（2.0 原先只有 `IMAGE_SUFFIXES`） |

**音频/视频的转写不在 S8a**，也不在 S8 的任何一段：用户 2026-10-04 裁决「不要 FunASR」，
`observations` 不加 `transcript` 列，`compose_text` 保持七字段。理由与代价逐条登记在
`docs/refactor/s8-deviations.md` §1.1。音频的入库/元数据/播放属 S8c（T521–T523）。

**抽帧参数（间隔、质量门）不在本表**：那是 S8b 的 T511，落地时才登记。

## 视频抽帧与场景（S8b · T511–T514，2026-10-04）

**前四条是 2.0 新定的值，1.0 没有对应物**——1.0 的抽帧是 YOLO/Pose 预筛 + Katna 质量门
+ WorldMM 聚类，没有「固定时长窗」这个概念（用户 2026-10-04 裁决①「先做简化闭环」，
见 `s8-deviations.md` §3 第 1 条）。登记在这里是为了让它们可被质疑，而不是当成 1.0 的口径。

| 项 | 值 | 出处 |
|---|---|---|
| **场景 = 固定时长的时间窗** | `SCENE_WINDOW_SEC = 60.0` 秒 | **不是 1.0 的值**（S8b 新定） |
| **每个场景抽几帧** | 一帧，取**窗口中点**那一秒 | **不是 1.0 的值**（S8b 新定） |
| **总场景数封顶** | `MAX_SCENES = 40`；超过时**按比例放大窗口**，不是无限抽帧 | **不是 1.0 的值**（S8b 新定） |
| **`derived_kind` 取值** | 只有一个：`video_keyframe` | 用户裁决⑧（不搬双算法并存）；1.0 有两个（`video_keyframe` / `video_keyframe_webp`） |
| 抽帧的 ffmpeg 参数 | `-ss <输入侧> -frames:v 1 -q:v 2`，产物 `scenes/{n:03d}/kf_0001.jpg` | **不是 1.0 的值**（1.0 出的是 webp/jpg 混合） |
| 场景事件的标题 / 摘要 | `视频场景 N` / `视频场景 · {start:.1f}s~{end:.1f}s` | 1.0 `video/processor.py:203` |
| 场景事件的时间区间 | `source_start_sec` / `source_end_sec` 是**原片秒数**；`time_start` / `time_end` 是拍摄时间 + 偏移 | 1.0 `processor.py:183-184` |
| 关键帧的拍摄时间 | 源片 `captured_at` + `source_timestamp_sec` | 1.0 `processor.py:186` |
| 关键帧的事件归属 | **强制**挂到它那一秒的场景事件（不走候选打分） | 1.0 `pipeline.py:333` |
| 幂等键 | `(parent_asset_id, source_frame_index)`；**不是 timestamp** | 迁移 0010 表头；1.0 那条按 timestamp 的索引在默认路径上从未生效 |
| 场景摘要是否豁免刷新 | **不豁免**（1.0 `db.py:5576` 豁免 `video_scene`，是缺陷） | `s8-deviations.md` §2 第 2 条 |
| 关键帧是否付 VLM 观察 | **付**（走完整图片流水线，含观察） | 用户裁决④「派生帧走图片流水线」 |
| `POST /videos/{id}/reprocess` 的 `status` | `processed`（同步跑完后的真实状态） | **S8b 变更**：S8a 曾按 1.0 回 `video-queued`，那时确实只是排队；S8b 起抽取链同步跑，回那个词是假话 |

**封顶的代价与理由**：每个关键帧要走完整图片流水线（VLM 观察 + wemm 嵌入 + 人脸），
**帧数就是 VLM 调用数**；10 分钟视频 ≈ 10 次，不封顶的话长视频会把 GPU 打满。

**音频/视频的转写不在 S8b**（用户裁决②「不要 FunASR」）。音频属 S8c。
