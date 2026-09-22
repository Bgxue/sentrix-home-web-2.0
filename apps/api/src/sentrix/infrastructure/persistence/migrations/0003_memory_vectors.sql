-- 向量事实源。语义取自 1.0 backend/db.py:782 的 memory_vectors，两处结构改进：
--   1. 新增 dimension 列。1.0 只能把 vector_json 解析出来才知道维度，
--      「已写入的向量维度是否与当前模型一致」这件事在 SQL 层看不见。
--   2. 索引改成 (space, model_name) 与 (space, source_type, source_id)：
--      1.0 只索引了 space 单列，带模型名的查询用不上索引。
-- Qdrant 是派生索引、可重建；SQLite 才是权威。删库只需重跑一次重嵌。
CREATE TABLE memory_vectors (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    space TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(space, source_type, source_id, model_name)
);

CREATE INDEX idx_memory_vectors_space_model ON memory_vectors(space, model_name);
CREATE INDEX idx_memory_vectors_source ON memory_vectors(space, source_type, source_id);
CREATE INDEX idx_memory_vectors_scope ON memory_vectors(scope_id);
