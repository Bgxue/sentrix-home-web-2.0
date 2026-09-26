/**
 * 故事工作室。对应 1.0 `src/app.js:690` 的 `storiesView()`（pageHeader 在 `:706`）。
 *
 * **这一页在 2.0 是冻结的**（`docs/refactor/frozen.md`）：故事生成不在本期范围。
 * 导航入口保留（裁决①：全导航 + 冻结项空状态），页面据实说明。
 */

import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";

export function StoriesPage() {
  return (
    <>
      <PageHeader
        kicker="家庭表达 / 故事工作室"
        title="把真实事件整理成家人愿意一起看的故事。"
        description="故事只引用你选择的事件和证据；标题、章节和内容保存为本地草稿。"
      />
      <EmptyState
        symbol="▶"
        title="故事工作室尚未开放"
        description="这一页在 2.0 里是冻结的：故事生成不在本期范围。导航保留是为了让结构与 1.0 一致。"
      />
    </>
  );
}
