# S8 违背 / 偏离审计（音频与视频）

审计对象：`docs/refactor/2026-10-04-s8-implementation-plan.md`。
基线：设计文档 `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md:513`
对 S8 的定义，与 1.0 的实现。

**这份文档现在的形态是「开工前的偏离登记」**（S3/S4/S5 那三份是收尾审计）。
S8 施工中若再有偏离，追加到 §3。

---

## 0. 一句话摘要

S8 的设计文档定义是「**FunASR 音频、关键帧/场景切分、派生帧可回溯原视频秒数**」。
本切片**去掉 FunASR 转写**（用户 2026-10-04 裁决），其余照搬 1.0 的机制。
另有 **1 条修 1.0 的缺陷**（血缘索引覆盖面）与 **3 条 1.0 的历史包袱不移植**。

**没有一条偏离是凭喜好的**——每条都指到 1.0 的具体行，或指到本仓库的一条既有契约。

---

## 1. 与设计文档的偏离

### 1.1 去掉 FunASR 音频转写（**唯一一条实质偏离**）

| 项 | 内容 |
|---|---|
| **出处** | 设计文档 `:513`：`S8 \| 音频与视频 \| FunASR 音频、关键帧/场景切分、派生帧可回溯原视频秒数 \| 导入视频，场景成型、能跳回原片` |
| **1.0 怎么做** | `backend/model_clients.py:1462` 的 `FunASRClient`，`paraformer-zh`，**进程内 import 进主进程**（无 sidecar、无独立端口）。`backend/pipeline.py:880-887` 的 `_audio_observation` 把转写写进 `observations.transcript`；`:350` 把它拼进文本嵌入。失败无 try/except，整条资产标 `failed` |
| **2.0 改成什么** | **不做转写。** 音频只做：入库、ffprobe 元数据（时长/码率/采样率）、播放、按元数据与文件名检索。`observations` **不加 `transcript` 列**；`compose_text` 保持七字段（1.0 是八字段含 transcript） |
| **为什么** | 用户 2026-10-04 裁决「不要 FunASR」。选型理由是**依赖与形态**：FunASR 1.4.1 在 1.0 的 venv 里，2.0 没有；而它要拖进 `torch` / `torchaudio` / `transformers` / `pytorch-lightning`（`backend/requirements.txt:10-15`），与设计 §2.1「2.0 不管理模型进程生命周期」以及 W1 刚立的**重模型一律侧车**先例都不合。走侧车则要新增一个端口与一套运维件，而这一块在 1.0 里本身很薄（无重试、无测试） |
| **代价** | 「音频里说了什么」这类问题答不了。但**验收里本来就没有这一条**——S8 的验收是「导入视频，场景成型、能跳回原片」，是视频中心的 |
| **可逆性** | **高。** 不加列、不引依赖，将来要转写是纯新增：加列 + 加适配器 + 改 `compose_text` |
| **证据强度** | 用户裁决，且与机器现状一致（153 上除 FunASR 外无任何 ASR：whisper 系无、`~/models` 无 ASR 模型、8100 的 qwen35 能力标的是 `completion`+`multimodal` 不含音频） |

---

## 2. 修 1.0 的缺陷（不是偏离，是**不把缺陷带过来**）

| # | 1.0 的缺陷 | 2.0 怎么修 |
|---|---|---|
| 1 | **唯一血缘索引只覆盖一种 `derived_kind`**：`db.py:1054-1056` 是 `WHERE derived_kind = 'video_keyframe'`，而 1.0 的**默认**算法产出的是 `video_keyframe_webp` → **默认路径没有 DB 级幂等保证**，重复导入只能靠 `cleanup_video_derivatives` 兜底 | S8a 的 T501 把索引做成**覆盖所有 `derived_kind`**。2.0 只保留一条抽帧路径（§3），所以一个覆盖全部的索引就够 |
| 2 | **视频场景摘要不刷新**：`db.py:5576` 对 `source_type='video_scene'` 跳过重建，摘要只在导入时算一次，后续补齐无法更新 | S8b 登记为待定：2.0 的事件摘要若走 `refresh_event_summary`，视频场景**不豁免**。具体在 T512 落地时定 |
| 3 | **前端关键帧计数恒 0**：`src/app.js:742` 用 `derived_kind === "video_keyframe"` 统计，而默认算法产出 `video_keyframe_webp` | 2.0 前端见 §3 第 3 条 |

---

## 3. 1.0 的历史包袱，**不移植**

| # | 包袱 | 为什么不 |
|---|---|---|
| 1 | **双抽帧算法并存**：`worldmm`（legacy）与 `hybrid_webp`（现用），两套 `derived_kind`（`video_keyframe` / `video_keyframe_webp`）、两套输出（`memory.json` / `frame_map.json`）、`list_derived_assets` 要兼容两者 | 用户裁决①「先做简化闭环」。**只保留一条路径**，`derived_kind` 只有一个取值 |
| 2 | **WorldMM 的 160 帧预算与挑选逻辑**（`SENTRIX_VIDEO_MAX_KEYFRAMES`、`summary_keyframes.json`、「每场景先保最高分帧再按时间序补齐」） | 随第 1 条一起去掉 |
| 3 | **1.0 前端那套场景 UI 的统计口径**（`worldmm_scene_count` 等） | 2.0 前端整体改版待用户主导（见 F7 一节），S8 只保证**接口给得出** `source_timestamp_sec` 与场景区间 |

---

## 4. 施工中的新增偏离

（施工时追加）
