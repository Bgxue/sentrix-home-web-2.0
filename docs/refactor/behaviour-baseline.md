# 1.0 行为基线

基线提交 `2d2cee7cbf1023f5019b9aaa9061af9c4cf45afa`。
2.0 重写时必须保持这些数值与规则不变，除非明确决策要改。

## 人脸

| 项 | 值 | 位置 |
|---|---|---|
| 在线匹配余弦阈值 | 0.30（硬编码，无 env 可配） | `backend/db.py:4815` |
| 全局重聚类阈值 | 0.30 | `backend/face_clustering.py:67` |
| 质量阈值 | 0.30 | `backend/face_clustering.py:67` |

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

## 模型与嵌入

| 项 | 值 | 位置 |
|---|---|---|
| 视觉嵌入 | chinese-clip-ViT-L-14，768 维，进程内 GPU | `backend/embeddings/chinese_clip_visual.py` |
| 文本嵌入 | BAAI/bge-m3，1024 维，HTTP sidecar `127.0.0.1:8101`（CPU） | `backend/embeddings/bge_text.py` |
| 方案冻结 | `embeddings/scheme.py` 已钉死，不读 env | `backend/embeddings/scheme.py` |
| VLM 端点 | `http://127.0.0.1:8100/v1`（llama.cpp，OpenAI 兼容） | `backend/model_clients.py:537` |
