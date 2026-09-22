-- 助手闭环的持久化：会话、消息、回合、回合进度事件、结果集。
-- 字段语义取自 1.0 backend/db.py:918-979，四处结构改进：
--   1. 1.0 没有 agent_turn_events 表——进度只存在 app.py 的进程内 dict `_TURN_JOBS`
--      里，永不清理、重启即丢、多 worker 直接 404。2.0 把每条进度事件落库，
--      SSE 的断线续传才有东西可续。
--   2. 1.0 的 agent_conversation_messages.content_json 装的是
--      {text, intent, presentation} 一整个投影；2.0 只存正文文本。展示层的字段属于
--      前端，不该混进对话记录。
--   3. 1.0 的 FTS 表没有触发器、靠手工 INSERT 同步，而且只索引 content_json 里的
--      text 字段。对话检索（search_conversation_history）是 S7 的事，S3 不建 FTS——
--      建一张没人同步的表，比不建更糟。
--   4. agent_trajectories 在 1.0 没有 conversation_id 索引，而 list_trajectories 恰恰
--      按它排序查询。这里建上。
CREATE TABLE agent_conversations (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    title TEXT NOT NULL DEFAULT '新对话',
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_message_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_agent_conversations_scope ON agent_conversations(scope_id, updated_at);

CREATE TABLE agent_conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id),
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    turn_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_agent_conversation_messages_cid
    ON agent_conversation_messages(conversation_id, created_at);

CREATE TABLE agent_turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES agent_conversations(id),
    scope_id TEXT NOT NULL DEFAULT 'home-default',
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    termination_reason TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    requirements_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_agent_turns_conversation ON agent_turns(conversation_id, created_at);

CREATE TABLE agent_turn_events (
    turn_id TEXT NOT NULL REFERENCES agent_turns(id),
    step_index INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (turn_id, step_index)
);

CREATE TABLE agent_result_sets (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    asset_ids_json TEXT NOT NULL DEFAULT '[]',
    total INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_agent_result_sets_scope ON agent_result_sets(scope_id);
