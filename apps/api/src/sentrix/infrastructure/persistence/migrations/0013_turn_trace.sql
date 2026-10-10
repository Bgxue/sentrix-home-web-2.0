-- 回合级的「这一轮到底怎么走的」：模型调用、规划、账本、门禁、预算。
--
-- **为什么不塞进 agent_turn_events。** 那张表是 `public_progress` 的落库，而
-- `public_progress` 同时是**终端用户看到的进度条**——`apps/web` 的 `AssistantPanel`
-- 把每一条都渲染成一行（只用 `text`）。往里加事件 = 用户每一步多看到一行噪音。
-- 这一格是**评测读的回合级事实**，与 `candidates_json` 走同一条路。
--
-- 形状由 `run_turn` 组装、由 PhotoBench 投影成 1.0 的 `model_call_metrics` /
-- `agent2_trace` / `debug_trace`：那三处 UI 面板此前整块不出现或恒显示「未记录」。
ALTER TABLE agent_turns ADD COLUMN trace_json TEXT NOT NULL DEFAULT '{}';
