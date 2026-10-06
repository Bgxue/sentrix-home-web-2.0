/**
 * JUDGE 模型原始输入弹窗。移植 1.0 `App.vue:3796` 的
 * `<Teleport to="body">` + `judgeInput()`(`:719-723`) + `openJudgeInput/closeJudgeInput`(`:726-727`)。
 *
 * React 用 `createPortal(..., document.body)` 对应 `Teleport to="body"`：弹窗节点挂在
 * `body` 下、**不在组件树内**，所以 `.judge-modal-backdrop` 的 `position:fixed` 不会被
 * 任何祖先的 `transform` / `overflow` 影响（`photobench.css` 就是按 body 层级写的）。
 */
import { createPortal } from "react-dom";

import type { RunItemDetail } from "../../api/types.ts";

type Dict = Record<string, unknown>;

/** 1.0 `:719-723`：`complete` = `judge.input.messages` 是数组。 */
function judgeInput(item: RunItemDetail): { complete: boolean; rawJson: string } {
  const judge = (item.judge ?? {}) as Dict;
  const input = (judge.input ?? {}) as Dict;
  return {
    complete: Array.isArray(input.messages),
    rawJson: Array.isArray(input.messages) ? JSON.stringify(input, null, 2) : "",
  };
}

export interface JudgeInputModalProps {
  item: RunItemDetail;
  onClose: () => void;
}

export function JudgeInputModal({ item, onClose }: JudgeInputModalProps) {
  const { complete, rawJson } = judgeInput(item);
  return createPortal(
    <div
      className="judge-modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="judge-modal" role="dialog" aria-modal="true" aria-label="Judge 模型原始输入">
        <header>
          <div>
            <h3>JUDGE 模型原始输入</h3>
            <span className="muted small">{item.qa_id ?? ""}</span>
          </div>
          <button className="judge-modal-close" type="button" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </header>
        {complete ? (
          <pre>{rawJson}</pre>
        ) : (
          <p className="judge-input-note">该历史结果在运行时未保存 Judge 原始请求 JSON，无法恢复。</p>
        )}
      </section>
    </div>,
    document.body,
  );
}
