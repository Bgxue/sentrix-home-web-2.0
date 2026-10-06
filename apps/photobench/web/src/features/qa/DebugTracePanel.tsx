/**
 * 完整运行时轨迹。移植 1.0 `App.vue:3638-3663`
 * （`runtimeDebugTurns(item).length` 才挂）。`runtimeDebugTurns` 在 `lib/trace.ts`。
 */
import type { RunItemDetail } from "../../api/types.ts";
import { esc } from "../../lib/format.ts";
import { runtimeDebugTurns } from "../../lib/trace.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

export interface DebugTracePanelProps {
  detail: RunItemDetail;
}

export function DebugTracePanel({ detail }: DebugTracePanelProps) {
  const turns = runtimeDebugTurns(detail);
  if (!turns.length) return null;

  return (
    <details className="call-node debug-trace-node">
      <summary>
        <strong>调试详情：完整运行时轨迹</strong>
        <span>{turns.length} 轮用户问答 · 提示词 / 回答 / 工具输入输出 / 评判</span>
      </summary>
      <div className="debug-trace-body">
        {turns.map((turn, turnIndex) => {
          const steps = (Array.isArray(turn.debug_trace) ? turn.debug_trace : []) as Dict[];
          return (
            <details key={turnIndex} className="debug-turn">
              <summary>
                <span className="debug-turn-index">第 {Number(turn.index) + 1} 轮</span>
                <strong>{esc(turn.message) || "问答"}</strong>
                <span className="debug-turn-meta">{steps.length} 个步骤</span>
              </summary>
              <div className="debug-steps">
                {steps.map((step, stepIndex) => {
                  const type = String(step.type ?? "");
                  const label =
                    type === "model" ? "模型步骤" : type === "tool" ? "工具步骤" : type === "judge" ? "评判步骤" : type;
                  const debug = dict(step.debug);
                  return (
                    <details key={stepIndex} className={`debug-step debug-step-${String(step.type || "step")}`}>
                      <summary>
                        <span className="debug-step-index">{stepIndex + 1}</span>
                        <strong>{label}</strong>
                        <span className="debug-step-status">{esc(step.status) || "已记录"}</span>
                      </summary>
                      {type === "model" ? (
                        <div>
                          <p className="muted small">提示词</p>
                          <pre>{JSON.stringify(step.prompt, null, 2)}</pre>
                          <p className="muted small">模型回答</p>
                          <pre>{esc(step.raw_full) || esc(step.raw)}</pre>
                        </div>
                      ) : type === "tool" ? (
                        <div>
                          <p className="muted small">工具输入</p>
                          <pre>{JSON.stringify(step.arguments, null, 2)}</pre>
                          <p className="muted small">工具输出</p>
                          <pre>{JSON.stringify(step.observation, null, 2)}</pre>
                        </div>
                      ) : type === "judge" ? (
                        <div>
                          <p className="muted small">评判结论</p>
                          <pre>{JSON.stringify({ faithful: step.faithful, problems: step.problems }, null, 2)}</pre>
                          {step.debug ? (
                            <div>
                              <p className="muted small">评判提示词</p>
                              <pre>{JSON.stringify(debug.prompt, null, 2)}</pre>
                              <p className="muted small">评判回答</p>
                              <pre>{esc(debug.raw)}</pre>
                            </div>
                          ) : null}
                        </div>
                      ) : (
                        <div>
                          <pre>{JSON.stringify(step, null, 2)}</pre>
                        </div>
                      )}
                    </details>
                  );
                })}
              </div>
            </details>
          );
        })}
      </div>
    </details>
  );
}
