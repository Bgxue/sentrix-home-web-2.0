/** 事件时间线。对应 1.0 `src/app.js:626` 的 `timelineView()`（pageHeader 在 `:645`）。 */

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Timeline } from "./Timeline";

export function TimelinePage() {
  const { scopeId } = useScope();
  return (
    <>
      <PageHeader
        kicker="记忆组织 / 事件"
        title="家里的时间线，不只是文件列表。"
        description="照片和视频片段按真实拍摄时间统一整理。"
      />
      <Timeline scopeId={scopeId} />
    </>
  );
}
