-- S4 时间线：地点结构、事件、事实、词法投影。
-- 字段语义取自 1.0 backend/db.py 的 assets.metadata_json.reverse_geocode / events /
-- facts / observation_search_terms / observation_search_fts，按设计 §3.2 做三处结构改进：
--   1. 地点从 metadata_json 的扁平 dict 拆成 asset_places 的正式列（见该表注释）；
--   2. 观察的语义字段提升为列，词法索引才不用去解析 JSON blob；
--   3. 词法投影是**可重建的派生表**，主键与来源版本号一并落库。

-- 地点解析结果。一行一个 asset（GPS 在 asset 上，不在 observation 上）。
--
-- 为什么是独立表而不是 assets 上的 8 个列：独立表让「没解析过」与「解析了但没结果」
-- 成为两个可区分状态——前者没有行，后者有行且 precision/source 为空。
-- 压在 assets 的列上时这两件事都是空字符串，与 1.0 把 {} 和缺失混为一谈是同一个毛病。
--
-- admin_* 来自面（PyGeoCN 行政边界），可以做硬条件过滤；
-- nearest_* 来自点（GeoNames 最近邻），只能说「附近」。两栏语义不同，不可互相顶替。
CREATE TABLE asset_places (
    asset_id TEXT PRIMARY KEY REFERENCES assets(id),
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    admin_province TEXT NOT NULL DEFAULT '',
    admin_city TEXT NOT NULL DEFAULT '',
    admin_district TEXT NOT NULL DEFAULT '',
    nearest_township TEXT NOT NULL DEFAULT '',
    nearest_locality TEXT NOT NULL DEFAULT '',
    nearest_distance_m REAL,
    precision TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    gps_latitude REAL,
    gps_longitude REAL,
    resolved_at TEXT NOT NULL
);

CREATE INDEX idx_asset_places_district ON asset_places(scope_id, admin_district);
CREATE INDEX idx_asset_places_scope ON asset_places(scope_id);

-- 观察的语义字段。observations 在 0002 已有 caption / ocr_text / detail_json；
-- 这里补上词法投影与事件打分真正要读的那几列。
-- 1.0 把它们散在 observations 的多个 *_json 列里且没有 revision，词法投影因此
-- 无法判断自己是不是过期——revision 是 refresh 幂等的依据。
ALTER TABLE observations ADD COLUMN place TEXT NOT NULL DEFAULT '';
ALTER TABLE observations ADD COLUMN activity TEXT NOT NULL DEFAULT '';
ALTER TABLE observations ADD COLUMN event_type TEXT NOT NULL DEFAULT '';
ALTER TABLE observations ADD COLUMN objects_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE observations ADD COLUMN clothing_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE observations ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;

CREATE INDEX idx_observations_revision ON observations(asset_id, revision);

-- 事件。1.0 有一张 events + event_revisions + event_participants + event_entities，
-- 2.0 本切片只保留 events 与 event_observations：参与者与实体都依赖 S5 的 entities，
-- 那两张表随 S5 一起建，不在这里留空壳。
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    title TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    time_start TEXT,
    time_end TEXT,
    place TEXT NOT NULL DEFAULT '',
    admin_district TEXT NOT NULL DEFAULT '',
    activity TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    aggregation_score REAL NOT NULL DEFAULT 0,
    aggregation_breakdown_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_events_scope_time ON events(scope_id, time_start);
CREATE INDEX idx_events_status ON events(status);
CREATE INDEX idx_events_district ON events(scope_id, admin_district);

CREATE TABLE event_observations (
    event_id TEXT NOT NULL REFERENCES events(id),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (event_id, observation_id)
);

CREATE INDEX idx_event_observations_observation ON event_observations(observation_id);

-- 事实。1.0 backend/db.py:3397 maintain_fact 的表语义原样保留：
-- 同一 (scope_id, subject, predicate) 至多一条 active，改口产生 pending 新行并记
-- supersedes_fact_id。
CREATE TABLE facts (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 0,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    supersedes_fact_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_facts_subject ON facts(scope_id, subject, predicate, status);
CREATE INDEX idx_facts_status ON facts(scope_id, status);

-- 词法投影。派生表：可整表重建，每一行都能追回某条 observation 的某个 revision。
-- 1.0 的对应表是 observation_search_terms + observation_search_fts
-- （backend/retrieval_indexes.py:112-158）。
CREATE TABLE observation_search_terms (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    field_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL DEFAULT 'observation',
    source_revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_search_terms_scope_field ON observation_search_terms(scope_id, field_type);
CREATE INDEX idx_search_terms_observation ON observation_search_terms(observation_id);
CREATE INDEX idx_search_terms_asset ON observation_search_terms(asset_id);
CREATE INDEX idx_search_terms_value ON observation_search_terms(asset_id, normalized_value);

-- 预分词 FTS5 投影：只索引 tokens 一列，其余四列 UNINDEXED 只做取回。
-- 与 1.0 一致（retrieval_indexes.py:139-149）——排序分数在 Python 侧算，
-- FTS5 只回答「哪些 asset 含这个 token」。
CREATE VIRTUAL TABLE observation_search_fts USING fts5(
    tokens,
    scope_id UNINDEXED,
    field_type UNINDEXED,
    asset_id UNINDEXED,
    observation_id UNINDEXED
);
