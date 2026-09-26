/**
 * 页头。直译 1.0 `src/app.js:191` 的 `pageHeader(kicker, title, description, action)`。
 *
 * 三段文字与一个动作槽，四个参数一个不少——1.0 的每一页都用同一个版式，2.0 照搬，
 * F6 的截图对拍才对得上。
 */

import type { ReactNode } from "react";

interface Props {
  readonly kicker: string;
  readonly title: string;
  readonly description: string;
  readonly action?: ReactNode;
}

export function PageHeader({ kicker, title, description, action }: Props) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{kicker}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {action}
    </div>
  );
}
