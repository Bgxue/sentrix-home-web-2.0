-- S8a T501：视频与音频的血缘列。
--
-- 1.0 在 `db.py:1019-1060` 用 `_ensure_columns` 加这一组（它没有版本化迁移）。
-- **列名与类型逐字照抄**，理由与 0008/0009 相同：抄错一个列名要等到第一次写入时才炸，
-- 而那时已经过了几层。
--
-- 这一组列只解决一件事：**视频派生物怎么指回原片**。
--
--   assets 上：`parent_asset_id` 指回源视频；`derived_kind` 标这是派生物；
--              `source_timestamp_sec` 是**在原片里的第几秒**——S8 验收里
--              「跳回原片」靠的就是它；`source_frame_index` / `source_scene_index`
--              是它在抽取序列与场景里的序号。
--   events 上：`source_type = 'video_scene'` 标记这是场景事件而不是普通事件；
--              `source_asset_id` 指回源视频；`source_start_sec` / `source_end_sec`
--              是这个场景在原片里的区间。
--
-- **派生物是 `media_type = 'image'` 的普通 Asset**（1.0 `video/processor.py:188`
-- 的做法），靠上面这几个列与普通照片区分。这样关键帧直接走既有图片流水线，
-- 白得 observation / 向量 / 人脸 / 事件归并——这是整条设计里最省力的一环。
--
-- 视频的时长/码率/分辨率**不在这里**：它们进 `assets.metadata_json.video_metadata`
-- （1.0 `video/contracts.py:10-27` 的 `VideoMetadata`），与 EXIF 同一套口径——
-- 每类媒体自己的元数据形状不同，摊成列会让列数随媒体类型膨胀。

ALTER TABLE assets ADD COLUMN parent_asset_id TEXT REFERENCES assets(id);
ALTER TABLE assets ADD COLUMN derived_kind TEXT;
ALTER TABLE assets ADD COLUMN source_timestamp_sec REAL;
ALTER TABLE assets ADD COLUMN source_frame_index INTEGER;
ALTER TABLE assets ADD COLUMN source_scene_index INTEGER;

ALTER TABLE events ADD COLUMN source_type TEXT;
ALTER TABLE events ADD COLUMN source_asset_id TEXT REFERENCES assets(id);
ALTER TABLE events ADD COLUMN source_scene_index INTEGER;
ALTER TABLE events ADD COLUMN source_start_sec REAL;
ALTER TABLE events ADD COLUMN source_end_sec REAL;
ALTER TABLE events ADD COLUMN source_metadata_json TEXT NOT NULL DEFAULT '{}';

-- 派生帧的幂等键：**同一源片、同一帧号只允许一行**。
--
-- **这里与 1.0 有一处刻意的不同，理由是 1.0 那个索引根本没生效过。**
-- 1.0 建的是 `(parent_asset_id, source_scene_index, source_timestamp_sec)`
-- 且带 `WHERE derived_kind = 'video_keyframe'`（`db.py:1054-1056`），
-- 而它当时**默认**的抽帧算法产出的却是 `'video_keyframe_webp'` —— 不匹配，
-- 所以那条索引在默认路径上从未约束过任何一行，重复导入实际靠
-- `cleanup_video_derivatives` 兜底（见 `s8-deviations.md` §2 第 1 条）。
--
-- 既然没有可沿用的既有行为，就选一个不会自己绊倒自己的键：
-- **`source_frame_index` 是抽取序列里的序号，天然唯一**；而 `source_timestamp_sec`
-- 是 REAL，同一场景里两帧落在同一个秒上是有可能的（场景检测尤其如此），
-- 拿它做唯一键会造出偶发的插入失败。2.0 只保留一条抽帧路径
-- （`s8-deviations.md` §3），所以一个覆盖全部 `derived_kind` 的索引就够。
CREATE UNIQUE INDEX idx_assets_derived_lineage
    ON assets(parent_asset_id, source_frame_index)
    WHERE derived_kind IS NOT NULL;

-- 场景事件的幂等键，与 1.0 同形：同一源片的同一场景序号只有一条事件。
CREATE UNIQUE INDEX idx_events_video_scene_lineage
    ON events(source_asset_id, source_scene_index)
    WHERE source_type = 'video_scene';

-- 反查用：按源片找它的派生物 / 场景。
CREATE INDEX idx_assets_parent ON assets(parent_asset_id);
CREATE INDEX idx_events_source_asset ON events(source_asset_id);
