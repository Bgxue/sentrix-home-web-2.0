-- S6 T127：非人物实体的语义层——`semantic_profiles` 与 `semantic_claims` 两张表。
--
-- 1.0 在 `db.py:811-849` 建这两张表，2.0 到 S6 才搬。**列集逐字照抄**，理由是计划
-- §4 Task 127 那条：它们 2.0 一张都没建，抄错列名要等到写入时才炸。
--
-- **不搬 `db.py:1073-1084` 的 `_ensure_columns` 补丁。** 1.0 那是给老库打补丁用的
-- （它先有 CREATE、后有这几列），核过一遍：补丁里那几列**已经在 CREATE 里**了
-- （`scope_id` / `event_memory_json` / `patterns_json` / `confidence_source` /
-- `normalized_value_text` / `normalization_version` / `support_count`）。2.0 的迁移
-- 是一次建对，与 `0006_identity.sql:12` 同一条规矩。
--
-- **一处要写进注释的名字与判据不一致：`person_id`。** 两列都叫 `person_id`、
-- 约束都是 `REFERENCES entities(id)`，而 1.0 的抽取**是给非人物实体写 profile 与 claim 的**
-- （place / object / atmosphere / time 四类，见 `_find_or_create_entities_batch`）。
-- 也就是说这一列装的是「这条记录关于哪个实体」，与「人」无关。
-- **照搬列名（忠实），改名的理由不足以抵消偏离 1.0 的代价**；与
-- `_confirmed_photo_identities`（名字说 confirmed、SQL 不查状态）是同一类，
-- 两处都在 docstring 里点明。
--
-- **不建 `memory_feedback.target_claim_id` 那条外键**：它引用 `semantic_claims`
-- （1.0 `db.py:906`），但 `memory_feedback` 在 2.0 的冻结清单里（`frozen.md`「反馈」），
-- 那张表本身不建。
CREATE TABLE semantic_profiles (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    person_id TEXT NOT NULL UNIQUE REFERENCES entities(id),
    summary_zh TEXT NOT NULL DEFAULT '',
    activity_summary_zh TEXT NOT NULL DEFAULT '',
    place_summary_zh TEXT NOT NULL DEFAULT '',
    appearance_summary_zh TEXT NOT NULL DEFAULT '',
    preference_summary_zh TEXT NOT NULL DEFAULT '',
    event_memory_json TEXT NOT NULL DEFAULT '[]',
    patterns_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE semantic_claims (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    person_id TEXT NOT NULL REFERENCES entities(id),
    dimension TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_text TEXT NOT NULL,
    normalized_value_text TEXT,
    normalization_version TEXT NOT NULL DEFAULT 'zh-v1',
    support_count INTEGER NOT NULL DEFAULT 1,
    value_entity_id TEXT REFERENCES entities(id),
    valid_from TEXT,
    valid_to TEXT,
    supporting_event_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 0,
    confidence_source TEXT NOT NULL DEFAULT 'derived',
    supersedes_claim_id TEXT REFERENCES semantic_claims(id),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 1.0 `db.py:1113` 的那条。按实体取 claim 是这条链路上唯一的读法
-- （`list_semantic_claims(person_id)`、`get_entity_detail` 都走它）。
CREATE INDEX idx_semantic_claims_person ON semantic_claims(person_id);
