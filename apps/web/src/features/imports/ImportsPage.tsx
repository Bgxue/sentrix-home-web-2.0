/** 导入队列。对应 1.0 `src/app.js:733` 的 `importsView()`（pageHeader 在 `:745`）。 */

import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";

export function ImportsPage() {
  return (
    <>
      <PageHeader
        kicker="资料入口 / 本地导入"
        title="把资料带回家，剩下的交给本地 AI。"
        description="选择相册文件夹后，图片和视频会一起上传，并自动读取拍摄时间与地点。"
      />
      <EmptyState
        symbol="↓"
        title="导入界面尚未接线"
        description="后端的 POST /api/v1/media/import 已经可用，这一页的拖放与队列在 F2 落地。"
      />
    </>
  );
}
