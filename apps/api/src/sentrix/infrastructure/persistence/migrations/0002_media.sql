-- assets / observations / ingest_batches。
-- 字段语义取自 1.0 backend/db.py:422/:444/:413，但做了两处结构改进（设计 §3.2）：
--   1. 1.0 把 EXIF/GPS/反地理/派生数据全塞进 metadata_json，检索只能 LIKE '%…%' 全表扫。
--      2.0 把需要检索的字段提升为正式列：gps_latitude / gps_longitude / device。
--   2. 1.0 的 video 派生字段（parent_asset_id 等）本切片用不到，先不建。
CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    batch_id TEXT,
    file_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    content_sha256 TEXT,
    captured_at TEXT,
    gps_latitude REAL,
    gps_longitude REAL,
    device TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_assets_scope ON assets(scope_id);
CREATE INDEX idx_assets_content_sha256 ON assets(content_sha256);
CREATE INDEX idx_assets_batch ON assets(batch_id);
CREATE INDEX idx_assets_captured_at ON assets(captured_at);

CREATE TABLE observations (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    asset_id TEXT NOT NULL REFERENCES assets(id),
    source_type TEXT NOT NULL,
    captured_at TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    caption TEXT,
    ocr_text TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_observations_asset ON observations(asset_id);
CREATE INDEX idx_observations_scope ON observations(scope_id);

CREATE TABLE ingest_batches (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    status TEXT NOT NULL DEFAULT 'open',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
