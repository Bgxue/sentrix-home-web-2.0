-- 记忆空间（相册域）。字段语义取自 1.0 backend/db.py:403 的 memory_spaces。
-- 2.0 是空库起步，不迁移 1.0 数据。
CREATE TABLE memory_spaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'household',
    status TEXT NOT NULL DEFAULT 'active',
    include_in_people INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_memory_spaces_status ON memory_spaces(status);
