/**
 * 执行结果面板。移植 1.0 `App.vue:3620-3623`（`guardSummary(item).recorded` 才挂）。
 * `guardSummary` 在 `lib/details.ts`（1.0 `:2092-2106` 的移植），这里只做渲染。
 */
import type { RunItemDetail } from "../../api/types.ts";
import { guardSummary } from "../../lib/details.ts";
import { conversationTurns, itemParseRate } from "../../lib/evidence.ts";
import { completionLabel, esc, executionStateClass } from "../../lib/format.ts";

import { terminationDisplayLabel } from "./qaShared.ts";

type Dict = Record<string, unknown>;

export interface ExecutionStatusPanelProps {
  detail: RunItemDetail;
}

export function ExecutionStatusPanel({ detail }: ExecutionStatusPanelProps) {
  const guard = guardSummary(detail);
  if (!guard.recorded) return null;

  const multiTurn = conversationTurns(detail).length > 1;
  return (
    <section className={`execution-status-panel ${executionStateClass(detail)}`}>
      <div className="execution-status-head">
        <div>
          <small>执行结果</small>
          <strong>{multiTurn ? "最终一轮状态汇总" : "Agent 结束状态"}</strong>
        </div>
        <span className="status-pill">{completionLabel(detail)}</span>
      </div>
      <div className="execution-status-grid">
        <span>
          <small>运行状态</small>
          <b>{guard.status}</b>
        </span>
        <span>
          <small>终止原因</small>
          <b>{terminationDisplayLabel(detail)}</b>
        </span>
        <span>
          <small>恢复次数</small>
          <b>{esc(guard.recoveries)} 次</b>
        </span>
        <span>
          <small>JSON 解析</small>
          <b>{itemParseRate(detail)}</b>
        </span>
        <span className="execution-status-wide">
          <small>运行说明</small>
          <b>{esc((detail as Dict).agent_reason) || "本轮 Agent 已按正常流程结束"}</b>
        </span>
      </div>
    </section>
  );
}
