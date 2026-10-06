/**
 * 重新 Judge 评分卡。逐行照搬 1.0
 * `services/photobench/frontend/src/App.vue:3059-3096`（任务表给的是 `:3059-3089`，
 * 实测 `<section class="rejudge-card">` 到 `</section>` 收在 `:3096`）。
 *
 * **两处必须由上层注入的接缝**（1.0 直接闭包读，2.0 拆到别的 hook）：
 *   - `judgeModel`：1.0 读 `judgeModel.value`，归配置切片（`hooks/useConfig.ts`），
 *     这里做成可选 prop，缺省 `-`（与 1.0 的 `judgeModel || '-'` 一致）。
 *   - `onSavePrompt`：1.0 的 `saveJudgePrompt()` 在 `useConfig` 侧（`useConfig.ts:167`），
 *     `useRuns`/`useRunDetail` 不提供。做成可选 prop；未接线时按钮可点但无副作用。
 *
 * 1.0 的 `startRejudge` 内含 `window.confirm`；`useRunDetail` 把它拆成
 * `requestRejudge()`（置 `rejudgeConfirmation`）/`confirmRejudge()`/`cancelRejudge()`。
 * 本组件用 effect 在 `rejudgeConfirmation` 置位时弹原生 confirm——保持 1.0 的交互，
 * 不改成一版内联确认条。
 */
import { useEffect, useRef } from "react";

import type { RejudgeTask } from "../../api/types.ts";
import type { PromptKindMeta } from "../../hooks/useRunDetail.ts";
import { statusLabel } from "../../lib/format.ts";

/**
 * 1.0 `App.vue:2465` 的三类提示词中文名。2.0 在 `hooks/useConfig.ts:69` 是私有的
 * （未 export），本切片不能改 hooks，照搬一份只读副本。
 */
const PROMPT_KIND_LABELS: Record<string, string> = {
  answer_quality: "回答质量",
  task_decision: "任务判断",
  evidence: "证据核验",
};

export interface RejudgeCardProps {
  activeRejudge: RejudgeTask | null;
  judgePromptKinds: PromptKindMeta[];
  activePromptKind: string;
  onSwitchPromptKind: (kind: string) => void;
  rejudgePrompt: string;
  onRejudgePromptChange: (value: string) => void;
  /** 归配置切片；缺省与 1.0 `judgeModel || '-'` 同形。 */
  judgeModel?: string;
  /** 归配置切片；未接线时可点但无副作用。 */
  onSavePrompt?: () => void;
  onResetPrompt: () => void;
  canRejudge: boolean;
  rejudgeSubmitting: boolean;
  rejudgeConfirmation: { message: string } | null;
  onRequestRejudge: () => boolean;
  onConfirmRejudge: () => void;
  onCancelRejudge: () => void;
  rejudgePercent: number;
}

export function RejudgeCard({
  activeRejudge,
  judgePromptKinds,
  activePromptKind,
  onSwitchPromptKind,
  rejudgePrompt,
  onRejudgePromptChange,
  judgeModel,
  onSavePrompt,
  onResetPrompt,
  canRejudge,
  rejudgeSubmitting,
  rejudgeConfirmation,
  onRequestRejudge,
  onConfirmRejudge,
  onCancelRejudge,
  rejudgePercent,
}: RejudgeCardProps) {
  const running = activeRejudge?.status === "running";

  // requestRejudge 只置位 rejudgeConfirmation，真正弹窗与执行在这里。用 ref 保证同一
  // 待确认对象只弹一次（回调 identity 变化也不会重复弹，StrictMode 双调也安全）。
  const handledConfirmation = useRef<{ message: string } | null>(null);
  useEffect(() => {
    if (!rejudgeConfirmation || handledConfirmation.current === rejudgeConfirmation) return;
    handledConfirmation.current = rejudgeConfirmation;
    if (window.confirm(rejudgeConfirmation.message)) onConfirmRejudge();
    else onCancelRejudge();
  }, [rejudgeConfirmation, onConfirmRejudge, onCancelRejudge]);

  return (
    <section className="rejudge-card">
      <div className="rejudge-head">
        <div>
          <h3>重新 Judge 评分</h3>
          <p>只复用本次运行已有的题目、标准答案和 Agent 回答，不重新执行相册处理、模型切换或 Agent 问答。</p>
        </div>
        {activeRejudge ? (
          <span className={"phase-status " + (activeRejudge.status || "")}>{statusLabel(activeRejudge.status)}</span>
        ) : null}
      </div>
      <div className="prompt-kind-tabs">
        {judgePromptKinds.map((meta) => {
          const kind = meta.kind || "";
          return (
            <button
              key={kind}
              type="button"
              className={"btn ghost compact prompt-kind-tab" + (activePromptKind === kind ? " active" : "")}
              onClick={() => onSwitchPromptKind(kind)}
            >
              {PROMPT_KIND_LABELS[kind] || kind}
              {meta.custom ? <span className="custom-badge">已自定义</span> : null}
            </button>
          );
        })}
      </div>
      <label className="rejudge-prompt">
        Judge System Prompt（{PROMPT_KIND_LABELS[activePromptKind] || activePromptKind}）
        <textarea
          value={rejudgePrompt}
          disabled={running}
          rows={8}
          spellCheck={false}
          onChange={(event) => onRejudgePromptChange(event.target.value)}
        />
      </label>
      <div className="rejudge-toolbar">
        <span className="muted small">
          {rejudgePrompt.length} 字符 · Judge {judgeModel || "-"}
        </span>
        <div className="rejudge-actions">
          <button className="btn ghost compact" disabled={running} onClick={() => onSavePrompt?.()}>
            保存提示词
          </button>
          <button className="btn ghost compact" disabled={running} onClick={onResetPrompt}>
            恢复默认
          </button>
          <button className="btn compact" disabled={!canRejudge || rejudgeSubmitting} onClick={() => onRequestRejudge()}>
            {running ? "重新评分中…" : "重新评分全部 QA"}
          </button>
        </div>
      </div>
      {activeRejudge ? (
        <div className="rejudge-progress">
          <div className="rejudge-progress-meta">
            <span>
              {activeRejudge.completed || 0}/{activeRejudge.total || 0} 题
            </span>
            <span>
              失败 {Number(activeRejudge.failed || 0)} · {rejudgePercent}%
            </span>
          </div>
          <div className="phase-bar">
            <div className="phase-bar-fill" style={{ width: `${rejudgePercent}%` }} />
          </div>
          {activeRejudge.error ? <p className="error">{String(activeRejudge.error)}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
