-- S5 人脸与身份层：实体、人脸簇/实例/原型、提及桥、事件参与者投影。
-- 字段语义取自 1.0 backend/db.py 的 DDL 段，对应关系写在 s5 实施计划的任务表里：
--   entities :560-570（+ :1240-1243 运行期 ALTER 追加的三列）
--   entity_revisions :572-580 / entity_properties :582-594
--   face_clusters :612-622 / face_instances :624-641（+ :1238 的 validity）
--   face_prototypes :643-654 / person_appearance_evidence :656-669
--   entity_mentions :671-678 / entity_observations :680-686
--   relationships :688-699（+ :1244-1246 的 inverse_predicate）
--   event_participants :503-511 / event_entities :487-501
--   person_moments :1145-1172 / person_portrait_revisions :1195-1219（S5c 才写，见下）
--
-- 1.0 是 CREATE TABLE IF NOT EXISTS + 运行期 _ensure_columns 打补丁，2.0 一次建对：
-- entities 的 identity_state/role_state/name_state、face_instances 的 validity、
-- relationships 的 inverse_predicate 三处 ALTER 在 1.0 是事后补的列，这里直接写进建表语句。
-- face_instances.cluster_id 可空是**必须一次建对**的：1.0 为它写过一次整表重建
-- （db.py:1257-1311 _migrate_face_instances_cluster_nullable），因为 SQLite 不能就地
-- 去掉 NOT NULL。低置信度人脸要能作为证据落库而不被塞进某个簇，这个可空性就是那条路径。
--
-- 0005 的注释说「参与者与实体都依赖 S5 的 entities，那两张表随 S5 一起建，不在这里留空壳」——
-- 那一次的推迟是**被依赖逼出来的**（entities 当时不存在）。到 0006，entities 就在本文件里，
-- 全家的外键目标都闭合了，所以整族一次建齐。

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    family_role TEXT,
    summary TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    identity_state TEXT NOT NULL DEFAULT 'clustered',
    role_state TEXT NOT NULL DEFAULT 'unknown',
    name_state TEXT NOT NULL DEFAULT 'anonymous',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_entities_scope ON entities(scope_id);
CREATE INDEX idx_entities_name ON entities(scope_id, entity_type, status, canonical_name);

-- 审计表。1.0 的簇审阅四件（confirm / reject / merge / split）与改名都往这里写，
-- 是「每一次身份变更都留痕」这个产品硬约束的落点，所以它属于 S5a 而不是可选项。
CREATE TABLE entity_revisions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_entity_revisions_entity ON entity_revisions(entity_id, created_at DESC);

-- 实体属性。source='user' 的当前值永不被 derived 覆盖（db.py:4695-4727），
-- derived 写出不同值时落 status='pending' 等确认。
CREATE TABLE entity_properties (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    property_key TEXT NOT NULL,
    value_json TEXT NOT NULL DEFAULT 'null',
    source TEXT NOT NULL DEFAULT 'derived',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    supersedes_property_id TEXT REFERENCES entity_properties(id),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_entity_properties_entity_key ON entity_properties(entity_id, property_key, updated_at);
CREATE INDEX idx_entity_properties_status ON entity_properties(status);

CREATE TABLE face_clusters (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    status TEXT NOT NULL DEFAULT 'pending',
    entity_id TEXT REFERENCES entities(id),
    representative_embedding_json TEXT NOT NULL DEFAULT '[]',
    member_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_face_clusters_scope ON face_clusters(scope_id);

CREATE TABLE face_instances (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    cluster_id TEXT REFERENCES face_clusters(id),
    bbox_json TEXT NOT NULL DEFAULT '[]',
    embedding_json TEXT NOT NULL DEFAULT '[]',
    detection_confidence REAL NOT NULL DEFAULT 0,
    quality REAL NOT NULL DEFAULT 0,
    pose_json TEXT NOT NULL DEFAULT '[]',
    area_ratio REAL NOT NULL DEFAULT 0,
    sharpness REAL NOT NULL DEFAULT 0,
    pose_bucket TEXT NOT NULL DEFAULT 'unknown',
    embedding_model TEXT NOT NULL DEFAULT 'unknown',
    embedding_version TEXT NOT NULL DEFAULT 'unknown',
    embedding_quality_signal REAL NOT NULL DEFAULT 0,
    validity TEXT NOT NULL DEFAULT 'uncertain',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_face_instances_cluster ON face_instances(cluster_id);
CREATE INDEX idx_face_instances_asset ON face_instances(asset_id);
CREATE INDEX idx_face_instances_observation ON face_instances(observation_id);

-- 每个姿态桶一条。UNIQUE 是「同桶只留最好的那一张」在 SQL 层的凭证，
-- 不是可以由写入方自己保证的约定。
CREATE TABLE face_prototypes (
    id TEXT PRIMARY KEY,
    cluster_id TEXT NOT NULL REFERENCES face_clusters(id),
    face_instance_id TEXT NOT NULL REFERENCES face_instances(id),
    pose_bucket TEXT NOT NULL DEFAULT 'unknown',
    embedding_json TEXT NOT NULL DEFAULT '[]',
    quality REAL NOT NULL DEFAULT 0,
    model_name TEXT NOT NULL DEFAULT 'unknown',
    model_version TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(cluster_id, pose_bucket)
);

CREATE INDEX idx_face_prototypes_cluster ON face_prototypes(cluster_id);

CREATE TABLE person_appearance_evidence (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES entities(id),
    face_instance_id TEXT NOT NULL REFERENCES face_instances(id),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    crop_bbox_json TEXT NOT NULL DEFAULT '[]',
    clothing_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    model_name TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(person_id, face_instance_id, model_name)
);

CREATE INDEX idx_person_appearance_person ON person_appearance_evidence(person_id);
CREATE INDEX idx_person_appearance_face ON person_appearance_evidence(face_instance_id);

-- 提及桥。UNIQUE 里的 face_instance_id 可空——SQLite 的 UNIQUE 允许多个 NULL，
-- 所以「没有对应人脸实例的提及」不会互相顶掉，这是有意的。
CREATE TABLE entity_mentions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    face_instance_id TEXT REFERENCES face_instances(id),
    confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, observation_id, face_instance_id)
);

CREATE INDEX idx_entity_mentions_observation ON entity_mentions(observation_id);
CREATE INDEX idx_entity_mentions_entity ON entity_mentions(entity_id);

CREATE TABLE entity_observations (
    entity_id TEXT NOT NULL REFERENCES entities(id),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    confidence REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'observation_extraction',
    created_at TEXT NOT NULL,
    PRIMARY KEY(entity_id, observation_id)
);

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    subject_entity_id TEXT NOT NULL REFERENCES entities(id),
    predicate TEXT NOT NULL,
    object_entity_id TEXT NOT NULL REFERENCES entities(id),
    inverse_predicate TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    confidence REAL NOT NULL DEFAULT 0,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    supersedes_relationship_id TEXT REFERENCES relationships(id),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_relationship_subject_object ON relationships(subject_entity_id, object_entity_id);
CREATE INDEX idx_relationships_scope ON relationships(scope_id);

-- 事件参与者与实体投影（0005 明确留给 S5 的两张）。
-- event_participants.person_id 指向 entities：这就是 0005 当时不能建它的原因。
CREATE TABLE event_participants (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    person_id TEXT NOT NULL REFERENCES entities(id),
    role TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id, person_id, role)
);

CREATE INDEX idx_event_participants_event ON event_participants(event_id);
CREATE INDEX idx_event_participants_person ON event_participants(person_id);

CREATE TABLE event_entities (
    event_id TEXT NOT NULL REFERENCES events(id),
    entity_id TEXT NOT NULL REFERENCES entities(id),
    relation TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(event_id, entity_id, relation)
);

CREATE INDEX idx_event_entities_event ON event_entities(event_id);
CREATE INDEX idx_event_entities_entity ON event_entities(entity_id);

-- 观察上的「已确认人物」快照。1.0 是 observations.people_json（db.py:1239 的
-- _ensure_columns 追加），2.0 的 0005 没建。事件八分量的 person_score 与
-- person_bridge 词法投影都读它，所以它属于 S5a。
ALTER TABLE observations ADD COLUMN people_json TEXT NOT NULL DEFAULT '[]';

-- 人物瞬间与画像修订：2026-09-23 用户裁决解冻这两张（person_insight_runs 仍冻结，
-- 所以本文件**没有** person_insight_runs，person_moments.run_id 也就不存在——
-- 1.0 的 run_id 是指向那张冻结表的外键，本切片不建那张表，这一列随之取消）。
-- 它们在 S5c（Task 84/85）才有写入者；建在这里的理由与代价写在
-- docs/refactor/s5-deviations.md 的裁决②条目里。
CREATE TABLE person_moments (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    person_id TEXT NOT NULL REFERENCES entities(id),
    cluster_id TEXT NOT NULL REFERENCES face_clusters(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    observation_id TEXT NOT NULL REFERENCES observations(id),
    asset_id TEXT NOT NULL REFERENCES assets(id),
    face_instance_id TEXT NOT NULL REFERENCES face_instances(id),
    action_text TEXT NOT NULL DEFAULT '',
    interaction_target_ids_json TEXT NOT NULL DEFAULT '[]',
    interaction_text TEXT NOT NULL DEFAULT '',
    participation_style TEXT NOT NULL DEFAULT '',
    visible_affect TEXT NOT NULL DEFAULT '',
    social_role_cues_json TEXT NOT NULL DEFAULT '[]',
    narrative_note TEXT NOT NULL DEFAULT '',
    selection_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    model_name TEXT NOT NULL DEFAULT 'unknown',
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(person_id, observation_id, prompt_version)
);

CREATE INDEX idx_person_moments_person ON person_moments(person_id, status, created_at DESC);
CREATE INDEX idx_person_moments_event ON person_moments(event_id, person_id);

CREATE TABLE person_portrait_revisions (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    person_id TEXT NOT NULL REFERENCES entities(id),
    revision INTEGER NOT NULL,
    portrait_text TEXT NOT NULL,
    themes_json TEXT NOT NULL DEFAULT '[]',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    trigger_type TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT 'unknown',
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    user_feedback TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(person_id, revision)
);

CREATE INDEX idx_person_portraits_person ON person_portrait_revisions(person_id, revision DESC);
