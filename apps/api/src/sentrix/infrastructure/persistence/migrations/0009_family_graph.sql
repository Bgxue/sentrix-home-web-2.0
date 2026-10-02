-- S7b1 T301：家庭图谱的六张表。
--
-- 1.0 在 `db.py:702-781` 建这六张（`CREATE TABLE IF NOT EXISTS`，因为它没有版本化迁移）。
-- **列集、类型、默认值、外键、索引逐字照抄**——理由与 `0008_semantic_entities.sql` 相同：
-- 抄错一个列名要等到第一次写入时才炸，而那时已经过了几层。
--
-- 六张表是两组：
--   - **用户/模型都能写的图**：`family_person_memberships`（成员归属）、
--     `family_relationships`（亲属边）、`family_person_portraits`（家庭画像）；
--   - **为图服务的元数据**：`family_relationship_blocks`（用户撤回过的边，挡住模型恢复）、
--     `family_analysis_runs`（推断运行）、`family_scope_groups`（把多个 scope 并成一张图）。
--
-- **三处 1.0 的列语义与直觉不符，照搬并在此记明：**
--
-- 1. `state` 取 `effective` / `superseded` / `retracted` 三个值，**不是布尔**；
--    每张图表都靠它 + `supersedes_id` 组成版本链，读侧一律先按 `state = 'effective'` 过滤。
-- 2. `locked` 的含义是「用户写下的，模型不许覆盖」——`source = 'user_override'` 的行
--    恒为 1，`source = 'model'` 的行恒为 0。`family_relationship_blocks.locked` 是
--    唯一一个**默认值就是 1** 的。
-- 3. **`family_relationships.family_graph_id` 恒为 NULL、`supersedes_id` 也恒为 NULL。**
--    1.0 的 `set_family_relationship`（`db.py:5797`）两条 INSERT 都写死
--    `family_graph_id = NULL` 与 `supersedes_id = NULL`，且**没有任何地方回填它们**；
--    对比 `family_person_memberships` 的新行**会**回填 `supersedes_id`。
--    这不是 2.0 的省略，是 1.0 原样——**关系侧的历史链是断的**，别顺手补上
--    （补上会产出一批 1.0 没有的版本指针，而「谁 supersede 了谁」是要读的）。
--    两列都留着：schema 是 1.0 的，去掉列会让新老库对不上。
CREATE TABLE family_person_memberships (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    membership TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    inference_run_id TEXT,
    state TEXT NOT NULL DEFAULT 'effective',
    locked INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    supersedes_id TEXT REFERENCES family_person_memberships(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_family_membership_effective
    ON family_person_memberships(scope_id, entity_id, state);

CREATE TABLE family_relationships (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    family_graph_id TEXT,
    subject_entity_id TEXT NOT NULL REFERENCES entities(id),
    predicate TEXT NOT NULL,
    object_entity_id TEXT NOT NULL REFERENCES entities(id),
    inverse_predicate TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    inference_run_id TEXT,
    state TEXT NOT NULL DEFAULT 'effective',
    locked INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    supersedes_id TEXT REFERENCES family_relationships(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_family_relationship_effective
    ON family_relationships(scope_id, subject_entity_id, object_entity_id, state);

-- 用户撤回一条边时**同时**在这里落一行：`left`/`right` 是 `tuple(sorted(...))`
-- 规范化过的无序对，所以同一对人只有一行。模型推断遇到 `locked = 1` 的 block 会跳过
-- （`set_family_relationship` 返回空），用户重新建边时则**删掉**这一行。
CREATE TABLE family_relationship_blocks (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    left_entity_id TEXT NOT NULL REFERENCES entities(id),
    right_entity_id TEXT NOT NULL REFERENCES entities(id),
    source TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_id, left_entity_id, right_entity_id)
);

CREATE INDEX idx_family_relationship_blocks_pair
    ON family_relationship_blocks(scope_id, left_entity_id, right_entity_id);

-- 一次推断运行。**`stats_json` 里那个 `event_watermark` 是触发去抖的唯一依据**
-- （S7b2 用）：完成时写入当时的 `COUNT(*) FROM events WHERE scope_id = ?`，
-- 下次触发时事件数没有增长就不跑。失败时 stats 保持 `{}`，于是 watermark 缺失，
-- 下次一定重跑——这是有意的。
CREATE TABLE family_analysis_runs (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES memory_spaces(id),
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage TEXT NOT NULL DEFAULT 'queued',
    config_json TEXT NOT NULL DEFAULT '{}',
    stats_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX idx_family_analysis_runs_scope
    ON family_analysis_runs(scope_id, created_at DESC);

CREATE TABLE family_person_portraits (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    portrait_text TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL,
    inference_run_id TEXT,
    state TEXT NOT NULL DEFAULT 'effective',
    locked INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_family_portrait_active
    ON family_person_portraits(scope_id, entity_id, state);

-- 把若干 scope 并成一张图。`source` 默认 `user_override` 且 UPSERT 时**不更新它**
-- （1.0 `db.py:5937` 的 `ON CONFLICT DO UPDATE` 只改 `graph_id` 与 `updated_at`）。
CREATE TABLE family_scope_groups (
    scope_id TEXT PRIMARY KEY REFERENCES memory_spaces(id),
    graph_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user_override',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_family_scope_groups_graph ON family_scope_groups(graph_id);
