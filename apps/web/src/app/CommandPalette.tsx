/**
 * 命令面板。对应 1.0 `src/app.js:1059-1060`（模板）与 `:1800-1805`（提交分支）。
 *
 * 1.0 的顶栏 ⌘ **只是一个按钮**，没有任何全局快捷键监听。2.0 照做——不自己发明
 * `keydown` 全局绑定：浏览器/系统级快捷键的冲突面太大，而 1.0 的入口本来就只靠点击。
 *
 * 提交行为也照 1.0：
 * 1. 输入命中某个导航项（标签或 id）→ 跳到那一页；
 * 2. 否则当作问题 → **直接进家庭记忆助手**（1.0 是进 search 视图后 `submitSearch`，
 *    2.0 的助手就是那条问答链，用 `?q=` 交接）。
 */

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { NAV_ITEMS, SYSTEM_ITEMS, type NavItem } from "./nav";
import { Modal } from "../shared/ui/Modal";

export type CommandResolution =
  | { readonly kind: "nav"; readonly item: NavItem }
  | { readonly kind: "question"; readonly query: string }
  | { readonly kind: "empty" };

/**
 * 1.0 的判据是 `command.includes(item.label) || command.toLowerCase().includes(item.id)`。
 * 原样保留，但加上「精确等于标签/id」优先——否则 `"时间线"` 会先撞上别名更短的项。
 */
export function resolveCommand(
  command: string,
  items: readonly NavItem[] = [...NAV_ITEMS, ...SYSTEM_ITEMS],
): CommandResolution {
  const trimmed = command.trim();
  if (trimmed === "") return { kind: "empty" };
  const lowered = trimmed.toLowerCase();
  const exact = items.find((item) => item.label === trimmed || item.id === lowered);
  if (exact) return { kind: "nav", item: exact };
  // 1.0 的两条：输入包含完整标签、输入包含 id。加一条「标签包含输入」是为了
  // 让 2.0 更长的标签仍能被短词命中（`时间线` → `事件时间线`）——1.0 的占位文案
  // 举的例子就是「时间线」，而 2.0 的标签加了「事件」两个字。
  const partial = items.find(
    (item) =>
      trimmed.includes(item.label) ||
      item.label.includes(trimmed) ||
      lowered.includes(item.id),
  );
  if (partial) return { kind: "nav", item: partial };
  return { kind: "question", query: trimmed };
}

export function CommandPalette({ onClose }: { readonly onClose: () => void }) {
  const navigate = useNavigate();
  const [command, setCommand] = useState("");

  function go(item: NavItem) {
    navigate(item.path);
    onClose();
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const resolution = resolveCommand(command);
    if (resolution.kind === "empty") return;
    if (resolution.kind === "nav") {
      go(resolution.item);
      return;
    }
    navigate(`/assistant?q=${encodeURIComponent(resolution.query)}`);
    onClose();
  }

  return (
    <Modal kicker="COMMAND" title="打开一个工作区" onClose={onClose}>
      <form onSubmit={submit}>
        <label>
          输入页面或问题
          <input
            autoFocus
            aria-label="命令"
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            placeholder="例如：时间线、资料库、搜索冰箱"
          />
        </label>
        <div className="command-links">
          {[...NAV_ITEMS, ...SYSTEM_ITEMS].map((item) => (
            <button
              key={item.id}
              type="button"
              className="button ghost"
              onClick={() => go(item)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <p className="command-hint">
          没命中页面时，回车会把这句话直接带进「家庭记忆助手」。
        </p>
        <div className="modal-actions">
          <button type="submit" className="button primary">执行</button>
        </div>
      </form>
    </Modal>
  );
}
