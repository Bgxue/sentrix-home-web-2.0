# S8 实施计划：音频与视频

> 路线图出处：设计文档 `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md:513`
> ——「**S8 | 音频与视频 | FunASR 音频、关键帧/场景切分、派生帧可回溯原视频秒数 |
> 导入视频，场景成型、能跳回原片**」。它是 **M3（S8–S11）** 的第一段。
>
> 前置：S0–S7 全部完成；W1（检索换 wemm 统一嵌入）完成。

---

## 0. 动工前双侧核查（2026-10-04）

### 0.1 1.0 里 S8 是四块，**体量极不均匀**

| 块 | 内容 | 体量 |
|---|---|---|
| ① 视频元数据 + 入库 | ffprobe、浏览器预览转码、拍摄时间/GPS 继承、状态机 | 中 |
| ② **抽帧 + 场景切分** | YOLO/Pose 预筛 → 事件合并 → Katna 质量门 → 每场景落代表帧 | **最大** |
| ③ **场景入库与回溯** | `video_scene` 事件 + 派生帧资产 + `source_timestamp_sec` 血缘 + 前端 seek | 中，**验收核心** |
| ④ FunASR 音频转写 | 进程内 paraformer-zh → `observations.transcript` → 拼进文本向量 | 很薄 |

**主导设计（也是最省力的复用路径）：派生关键帧是 `media_type="image"` 的新 Asset，
直接喂进现有图片流水线**（1.0 `video/processor.py:188,401`）。于是白得
Observation、向量、人脸、事件归并。**推论：wemm 不需要 `embed_video`**——关键帧是图片。

### 0.2 2.0 现状：视频**能进库但完全静默空转**

- `MediaType` 已有 `VIDEO` / `AUDIO`，分类是通的（`domain/media/models.py:15-43`）；
- `POST /media/import` **没有任何 media_type 白名单**：视频被收下、落盘、建
  Asset + Observation（`application/media/import_assets.py:161-213`），
  **但不读时长、不抽帧、不转写**，`Observation` 语义全空；
- `GET /assets/{id}/preview` 对视频 **500**——`PillowPreviewEncoder` 打不开视频；
- 只有 IMAGE 才读 EXIF（`import_assets.py:179-180`），视频拿 `(None,None,None)`；
- 整册导入更早就过滤掉了视频（`import_album.py:42` 的 `IMAGE_SUFFIXES`）；
- **2.0 源码里 `keyframe` / `ffmpeg` / `FunASR` 零命中**，没有任何视频代码。

**这是与 W1 那次同类的状态**：看着正常（能导入、能列出），实际什么都没发生，且不报错。

### 0.3 环境（153 实测）

| 项 | 状态 |
|---|---|
| `ffmpeg` / `ffprobe` | ✅ 4.4.2（`/usr/bin`） |
| `funasr` | 1.4.1，在 **1.0 的 venv** 里；**2.0 的 `.venv` 没有** |
| 别的 ASR | **没有**（whisper 系无、`~/models` 无 ASR 模型、8100 的 qwen35 不含音频） |

### 0.4 1.0 的已知缺陷（**都不照搬**）

1. **前端关键帧计数漏算**：导入页统计用 `derived_kind === "video_keyframe"`，而默认算法
   产出的是 `video_keyframe_webp` → 那个面板**永远显示 0**（`src/app.js:742`）。
2. **唯一血缘索引只覆盖 legacy 一种 `derived_kind`**（`db.py:1054-1056` 的
   `WHERE derived_kind='video_keyframe'`）→ 当前默认算法**没有 DB 级幂等保证**。
3. **视频场景摘要不刷新**（`db.py:5576` 对 `source_type='video_scene'` 跳过重建）。
4. **双抽帧算法并存**（`worldmm` legacy vs `hybrid_webp` 现用）、两套 `derived_kind`、
   两套输出格式、一堆兼容代码。

---

## 1. 裁决

### 用户 2026-10-04 定的三条

| # | 问题 | 裁决 |
|---|---|---|
| ① | 抽帧那整套怎么移 | **先做简化闭环**——用 ffmpeg 抽帧 + 按时间间隔切场景，把「场景成型 + 能跳回原片」这条验收链跑通。**血缘三件套与「派生帧走图片流水线」照搬**，日后换更好的抽帧算法时不动其他部分 |
| ② | FunASR 在 2.0 跑在哪 | **不要 FunASR**。进一步确认：**音频不转写，只做入库与播放** |
| ③ | S8 怎么切 | **切三段**：S8a 血缘 → S8b 抽帧 → S8c 音频 |

### ②产生的偏离（**必须登记**）

设计文档 §8 把 S8 定义为「**FunASR 音频**、关键帧/场景切分、派生帧可回溯原视频秒数」。
本计划**去掉转写**：

- `observations` **不加 `transcript` 列**；
- 文本向量拼接（`index_observation_text.compose_text`）**不含转写**——
  1.0 那一版是八字段含 `transcript`，2.0 保持七字段；
- 音频的价值面收窄为：能导入、能读元数据（时长等）、能播、能按元数据/文件名检索。

**登记在 `docs/refactor/s8-deviations.md`**（与 s3/s4/s5 同体例），并在
`behaviour-baseline.md` 的 S8 段写明「转写不在本切片」。

### 我按证据定的（沿用 1.0 的设计，不另问）

| # | 决定 | 依据 |
|---|---|---|
| ④ | **派生帧做成 `media_type="image"` 的新 Asset，走现有图片流水线** | 1.0 就是这么做的（`processor.py:188,401`）。这是视频白得人物/OCR/向量/事件能力的唯一原因，也是最省力的路径 |
| ⑤ | **血缘三件套照搬**：`assets.source_timestamp_sec` + `parent_asset_id` + `derived_kind`，配 `events.source_start_sec` / `source_end_sec` + `(source_asset_id, source_scene_index)` 唯一约束 | 这是「派生帧可回溯原视频秒数」的唯一实现，前端 seek 与助手交付原片都靠它 |
| ⑥ | **唯一血缘索引要覆盖所有 `derived_kind`** | 1.0 那个索引只覆盖 legacy 一种（§0.4 缺陷 2），默认算法实际没有幂等保证。这是**修缺陷不是偏离** |
| ⑦ | `/api/assets/{id}/file` 的 **preview 分流**照搬 | H.264 直出、其余 ffmpeg 转码（1.0 `app.py:2448-2478`）。浏览器能播的前提 |
| ⑧ | **只保留一条抽帧路径** | 不要 worldmm/hybrid 双算法并存的包袱（§0.4 缺陷 4） |
| ⑨ | **wemm 不加 `embed_video`** | 关键帧是图片，走 `embed_image` 即可（④的直接推论） |

---

## 2. 三段

### S8a 视频入库与血缘

**验收**：导入一个真实视频，能读到时元数据、能在浏览器里播、`/videos/{id}` 返回得出来。

| # | 内容 |
|---|---|
| T501 | 迁移 `0010_video.sql`：`assets` 加 `parent_asset_id` / `derived_kind` / `source_timestamp_sec` / `source_frame_index` / `source_scene_index`；`events` 加 `source_type` / `source_asset_id` / `source_scene_index` / `source_start_sec` / `source_end_sec`；**唯一血缘索引覆盖所有 `derived_kind`**（裁决⑥） |
| T502 | ffprobe 元数据适配器（`infrastructure/media/video_metadata.py`）。**落 infrastructure**：`application-purity` 契约禁止 application import 子进程/PIL 那类 |
| T503 | 浏览器预览转码（非 H.264 才转，libx264/crf23/+faststart），与 `/assets/{id}/file` 的 preview 分流（裁决⑦） |
| T504 | 视频 4 条读路由 + `reprocess`：`GET /videos/{id}`、`GET /videos/{id}/scenes`、`GET /video-scenes/{id}`、`POST /videos/{id}/reprocess`。**照 1.0 的响应形状**（`{video, scenes}` / `{video_asset_id, scenes}` / 事件详情 / `{accepted, asset_id, status}`） |
| T505 | `POST /media/import` 遇到视频**不再静默空转**：至少读元数据 + 进状态机。同时修掉 `GET /assets/{id}/preview` 对视频 500 那条（走 T503 的分流） |
| T506 | 导入页/整册导入放行视频（`import_album.py` 的 `IMAGE_SUFFIXES`） |

### S8b 抽帧与场景

**验收**：导入视频后**场景成型**，派生关键帧能在时间线/检索里出现，**每张能定位回原片秒数**。

| # | 内容 |
|---|---|
| T511 | 关键帧抽取（**简化闭环**，裁决①）：ffmpeg 按固定间隔或场景检测抽帧。抽帧参数登记进 `behaviour-baseline.md` |
| T512 | 场景切分：先按时间间隔切，每个场景建一条 `source_type='video_scene'` 的事件，区间写 `source_start_sec` / `source_end_sec` |
| T513 | 派生帧落库为 `image` 类型的 Asset（带 `parent_asset_id` / `derived_kind` / `source_timestamp_sec` / `source_scene_index`），**送进现有图片流水线**（裁决④） |
| T514 | 幂等与清理：重复导入靠唯一血缘索引 + `cleanup_video_derivatives`；**索引覆盖所有 kind**（裁决⑥） |
| T515 | 视频与音频的媒体类型过滤在检索侧已通（`search.py:127`、`query_contracts.py:321-324`、`fact_query.py:47`）——只做验证，不改 |

### S8c 音频

**验收**：导入一个音频文件，能读时长、能在浏览器里播、能按文件名/元数据检索到。

| # | 内容 |
|---|---|
| T521 | 音频走同一条导入链：ffprobe 读元数据（时长/码率/采样率）、预览与播放分流 |
| T522 | 确认检索侧 `media=audio` 过滤可用；**不引入转写**（裁决②） |
| T523 | 收口：capability-map / module-map / behaviour-baseline / `s8-deviations.md` / openapi |

---

## 3. 门禁与交付物

每个新 `.py` 都要登 `docs/architecture/module-map.md`；新能力登
`docs/refactor/capability-map.jsonl`（现在 150 行，**零条视频/音频**）；新常量登
`behaviour-baseline.md`；加了路由必须重跑 `export_openapi.py`。

**不能破的契约**：`domain-purity` 禁 PIL/httpx/子进程 —— **视频解码与 ffmpeg 只能落
infrastructure**；`application-purity` 同理。

---

## 4. 施工记录

（施工时逐条填）
