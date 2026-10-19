/**
 * 故事工作室。对应 1.0 `src/app.js:690-709` 的 `storiesView()`（pageHeader 在 `:706`）。
 *
 * **这一页在 2.0 是冻结的**（`docs/refactor/frozen.md`）：故事生成不在本期范围。
 * 导航入口保留（裁决①：全导航 + 冻结项空状态），页面据实说明。
 *
 * **冻结的直接原因是后端没有端点。** 1.0 的完整 CRUD + 生成 + 重试 + 报告视图依赖
 * `GET/POST/PATCH/DELETE /api/stories`，而 2.0 的
 * `apps/api/src/sentrix/api/routes/` 里没有 stories 路由（`grep -rn "stories"` 为零）。
 * 前端不能拿事件列表拼一个「看起来能生成」的假界面：那会让用户以为故事写进了库，
 * 刷新后才发现什么都没有。
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
        title="后端故事端点尚未实现"
        description="故事工作室在 2.0 保持冻结：1.0 的 CRUD、生成、重试与报告视图依赖 /api/stories，而 2.0 后端目前没有 stories 路由。前端不伪造可用的生成界面，等端点落地后再接。"
      />
    </>
  );
}
