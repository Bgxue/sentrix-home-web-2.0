/**
 * 模态框。直译 1.0 `src/app.js:1123` 的 `.modal-backdrop` / `.modal-panel` 结构：
 * 遮罩 + 面板 + 右上角关闭。
 *
 * 只保留 2.0 用得到的两个行为：
 * 1. **Esc 关闭**；2. 点遮罩关闭（点面板内部不关）。
 * 1.0 还有一个「返回上一页」的 `.modal-back`，那是它用模态历史模拟页面栈的产物；
 * 2.0 已经是 URL 路由，面板里再放一个返回键会让人以为在页面栈里。命令面板与帮助
 * 都是单层模态，不需要它。
 *
 * `role="dialog"` / `aria-modal` 不只是无障碍标记：`useAutoRefresh` 靠
 * `[role="dialog"]` 判断「用户正在操作模态」，据此暂停后台刷新。
 */

import { useEffect, type ReactNode } from "react";

import "./Modal.css";

interface Props {
  readonly kicker?: string;
  readonly title: string;
  readonly onClose: () => void;
  readonly children: ReactNode;
}

export function Modal({ kicker, title, onClose, children }: Props) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        // 只有按在遮罩本身才关；从面板里拖选文字滑到遮罩上松手不该关。
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal-panel" role="dialog" aria-modal="true" aria-label={title}>
        <button type="button" className="modal-close" aria-label="关闭" onClick={onClose}>
          ×
        </button>
        {kicker ? <div className="modal-kicker">{kicker}</div> : null}
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}
