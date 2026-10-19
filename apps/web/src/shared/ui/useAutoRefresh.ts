/**
 * 5 秒静默刷新。语义逐条对齐 1.0 `src/app.js:2253-2256`：
 *
 *     window.setInterval(() => {
 *       if (document.visibilityState === "hidden" || isUserEditing()) return;
 *       refreshData({ silent: true });
 *     }, 5000);
 *
 * `isUserEditing()` 在 1.0 `:1134-1137` 的定义是三条：有模态、焦点在输入控件、
 * 焦点在某个表单里。2.0 没有全局 `state.modal`，用 `[role="dialog"]` 代替——
 * 我们的 `Modal` 会带上这个属性。
 *
 * **静默**指不切 loading / 不清空旧数据；这一条由调用方保证，hook 只负责节拍。
 */

import { useEffect, useRef } from "react";

export const AUTO_REFRESH_MS = 5000;

/** localStorage 在这些输入上可能正被编辑，刷新时不能把它们顶掉。 */
export function isUserEditing(doc: Document = document): boolean {
  const active = doc.activeElement;
  if (active instanceof HTMLElement) {
    if (active.matches("input, textarea, select")) return true;
    if (active.closest("form")) return true;
  }
  return doc.querySelector('[role="dialog"]') !== null;
}

/** 页面不可见或用户正在编辑时都不刷新。 */
export function shouldAutoRefresh(doc: Document = document): boolean {
  return doc.visibilityState === "visible" && !isUserEditing(doc);
}

export function useAutoRefresh(
  callback: () => void,
  intervalMs: number = AUTO_REFRESH_MS,
  enabled = true,
): void {
  // 每次渲染都把最新回调放进 ref：调用方常写成 `useCallback` 依赖 `scopeId`，
  // 若把 callback 直接放进 effect 依赖，换相册会重装计时器——那一刻本来也该重置，
  // 但数据刷新与计时器不该耦合，这里只重置节拍。
  //
  // 写入放在 effect 里（而不是渲染期间）：渲染阶段改 ref 会被 React 的严格检查
  // 判为副作用；这个 effect 没有依赖，因此每次提交后都会同步一次。
  const latest = useRef(callback);
  useEffect(() => {
    latest.current = callback;
  });

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => {
      if (!shouldAutoRefresh()) return;
      latest.current();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [enabled, intervalMs]);
}
