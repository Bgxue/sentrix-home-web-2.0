/**
 * 区块。对应 1.0 的 `.content-section` + `.section-head`（`styles.css:110-112`）。
 * `kicker` 与 `title` 的搭配与页头一致，1.0 每一屏都是这个版式。
 */

import type { ReactNode } from "react";

interface Props {
  readonly kicker?: string;
  readonly title: string;
  readonly action?: ReactNode;
  readonly children: ReactNode;
}

export function Section({ kicker, title, action, children }: Props) {
  return (
    <section className="content-section">
      <div className="section-head">
        <div>
          {kicker ? <p className="section-kicker">{kicker}</p> : null}
          <h2>{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
