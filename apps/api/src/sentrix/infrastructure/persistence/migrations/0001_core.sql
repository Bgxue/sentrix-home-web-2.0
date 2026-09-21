-- 记忆空间（相册域）。字段语义取自 1.0 backend/db.py:403 的 memory_spaces。
-- 2.0 是空库起步，不迁移 1.0 数据。
--
-- 少了 1.0 的 source_path 列，是有意的：它在 1.0 只被写入（db.py:1392 的 INSERT /
-- UPSERT），全仓没有任何 SELECT 读它，属于只写不读的死列；而且导入来源本就不该进记忆
-- 模型（设计 §1.1 的硬约束「导入源不得写入记忆」）。
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
