/**
 * 空状态。直译 1.0 `src/app.js:203` 的 `emptyState(title, description, action)`。
 *
 * 三类文案各有分工（1.0 的用法）：标题说**发生了什么**、说明说**为什么**、
 * 动作给**下一步**。缺了动作就成了死胡同，所以它虽是可选参数，界面上要尽量给。
 */

import type { ReactNode } from "react";

interface Props {
  readonly symbol?: string;
  readonly title: string;
  readonly description: string;
  readonly action?: ReactNode;
}

export function EmptyState({ symbol = "▤", title, description, action }: Props) {
  return (
    <section className="empty-search">
      <div className="empty-symbol" aria-hidden="true">{symbol}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div style={{ marginTop: 22 }}>{action}</div> : null}
    </section>
  );
}
