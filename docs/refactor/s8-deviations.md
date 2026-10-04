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

---

## 4. 施工中的新增偏离（S8a · T501–T506，2026-10-04）

S8a 交付「元数据 + 血缘列能用 + 能播 + 路由通」，不建场景、不抽帧（那是 S8b）。
下面每条都指到具体的一处代码或一件 2.0 没有的东西，**没有一条是凭喜好**。

### 4.1 `VideoMetadata` 不带 1.0 的 `raw`

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `backend/video/contracts.py` 的 `VideoMetadata` 有一个 `raw: dict` 字段，装的是整包 ffprobe JSON（`metadata.py` 的 `raw=payload`）；`processor.py` 把 `metadata.as_dict()`（含 `raw`）整份写进 `assets.metadata_json.video_metadata` |
| **2.0 改成什么** | `domain/media/models.py` 的 `VideoMetadata` 保留其余 12 个字段，**去掉 `raw`** |
| **为什么** | `raw` 在 1.0 **没有任何读侧**（全仓 grep 只命中写入处与 dataclass 定义），而它把每条流的完整标签树都带进库里——一个 4K 视频的 ffprobe payload 是几十 KB，每次导入写一份没人读。2.0 的 `metadata_json` 只放真正要被读的东西 |
| **可逆性** | 高。要原始 payload 时加回一个字段、适配器多传一个键即可 |
| **证据强度** | 全仓 grep：`raw` 只出现在 `contracts.py` 的定义与 `metadata.py` 的赋值处 |

### 4.2 `video_scenes` 的响应只回 2.0 拿得到的那几块

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `GET /api/video-scenes/{id}` 返回 `get_event_detail`（`db.py:3273`）= `{event, observations, facts, entities, event_revisions}`，其中 `event` 被 `get_event` 顺手挂上 `keyframe_assets` / `source_video`（`db.py:2345-2367`） |
| **2.0 改成什么** | 只回 `{event, keyframe_assets, source_video}`——**六个键去掉三个**（`observations` / `facts` / `entities` / `event_revisions` 里的四个），且 `keyframe_assets` / `source_video` 放在 `event` **旁边**而不是里面 |
| **为什么** | 2.0 没有通用的事件详情读侧（`GET /events/{id}` 只回 `TimelineEvent`），而 `event_revisions` 这张表**在 2.0 根本不存在**（0005_timeline.sql 的表头注释：「2.0 本切片只保留 events 与 event_observations」）。按 1.0 的形状填四个恒空的键，正是 `read_entity_detail` 明确拒绝过的那种「恒空占位键」。摊平而不是嵌套，是为了让列表端点（`/videos/{id}/scenes`）复用同一个 `VideoScenePayload` 时不背上两个它永远为空的字段 |
| **可逆性** | 高。三个键的读路径将来落地时，加回去是纯新增 |
| **证据强度** | 迁移 0005 的表头注释 + `GET /events/{id}` 的现存响应形状 |

### 4.3 `reprocess` 在 S8a 只清场 + 重置状态

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `app.py:1472-1481`：清派生数据 → 状态置 `video-queued` → `background_tasks.add_task(process_asset, asset_id)` 真跑一遍抽取链 |
| **2.0 改成什么** | 前两步照做，**第三步没有**——`cleanup_derivatives` + `update_status(QUEUED)`，返回 `202 {accepted, asset_id, status: "video-queued"}` |
| **为什么** | 抽取链是 S8b（T511–T514）。2.0 **没有任务队列**，起一个查无此人的后台任务只会把「跑到哪了」藏起来——与 `routes/timeline.py` 的 `import_album` 选同步是同一条纪律（那里写着「2.0 没有任务队列，装作异步只会把『跑到哪了』藏起来」） |
| **代价** | S8a 里 `queued` 没有消费者，重跑之后资产停在 `queued`。**这是登记在案的已知状态，S8b 的 T511–T514 接走它**；S8a 是切片、没有前端调用方 |
| **可逆性** | 高。S8b 往 `update_status` 之后接上抽取链即可，路由不动 |
| **证据强度** | 计划书 T504 原文「可以只做「清派生数据 + 重置状态」，把真正重跑留到 S8b 接上，并在注释里写明」 |

### 4.4 三处 2.0 新加的可观测面（1.0 没有）

| 项 | 1.0 | 2.0 为什么加 |
|---|---|---|
| `metadata_json.video_probe_error` | 没有。ffprobe 失败 → 整条资产标 `failed`（`processor.py` 的 `except` 把 stage 写成 `video-failed`） | 2.0 选择「导入照收、原因留在资产上」（与 EXIF 读不出来不阻断同一条既有口径），那就得有地方放原因；没有它，失败又变成不可见 |
| `ImportResponse.video_probed` | 没有。接口上看不出视频有没有被处理过 | 正是 S8a 要修的失败模式：「视频收下了但什么都没发生」在响应里完全不可见。与 `indexed` / `placed` 同一个位置 |
| `VideoPayload.metadata` | 1.0 把整行 asset（含整个 `metadata_json`）交出去 | 2.0 只列出视频真正有的那几位 + 探测结果，形状固定、不随 metadata_json 里塞了什么都变 |

### 4.5 预览转码的时机与落点

| 项 | 1.0 | 2.0 |
|---|---|---|
| 时机 | 后台 worker（`process_asset`）里转 | **导入请求内同步转**（`ImportAssets._probe_video`） |
| 落点 | `{SENTRIX_DATA_DIR}/derived/video/{asset_id}/preview.mp4` | `{SENTRIX_DATA_DIR}/media/derived/video/{asset_id}/preview.mp4` |

**时机**：2.0 的导入本来就是同步的（整册导入甚至在同一请求里跑完整趟画像流水线），
没有 worker 可以挂。代价是导入一个长视频要等一次转码——S8a 取「能播」优先。
**落点**：2.0 的媒体文件统一在 `media_dir` 下，派生件跟它同一个根，换数据目录就整体换
（与 `Settings.preview_dir` / `qdrant_dir` 都由 `data_dir` 派生是同一条理由）。

### 4.6 `cleanup_derivatives` 的级联范围

| 项 | 内容 |
|---|---|
| **1.0 怎么做** | `cleanup_video_derivatives` → 对每个派生物再调 `cleanup_asset_derivatives`，后者递归清掉那张图名下的观察 / 向量 / 人脸 / 提及等一大堆行 |
| **2.0 S8a 做多少** | 只清：场景事件、事件的 `event_observations`、派生物本身、以及挂在派生物上的 `observations`。**人脸 / 实体提及 / 地点 / 向量那几张表的级联没做** |
| **为什么** | 那几行是 S8b 的 T513（派生帧送进图片流水线）造出来的，而 T514 才是「幂等与清理」。照着还没写的写侧去猜表结构、多删一张表都可能是错的。**S8a 里这些行根本不存在**，所以不做不会漏 |
| **可逆性** | 高。S8b 的 T514 在同一个方法里往下接 |
| **证据强度** | 计划书 §2 的 S8b 任务表（T513 落派生物、T514 幂等与清理） |
