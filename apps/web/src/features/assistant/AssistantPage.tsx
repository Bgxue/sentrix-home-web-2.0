/** 家庭记忆助手。对应 1.0 `src/app.js:619` 的 `searchView()`。 */

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { AssistantPanel } from "./AssistantPanel";

export function AssistantPage() {
  const { scopeId } = useScope();
  return (
    <>
      <PageHeader
        kicker="记忆组织 / 对话"
        title="你想找回哪一段记忆？"
        description="从人物、时间、地点、物体或一句描述开始，答案会带回原始证据。"
      />
      <AssistantPanel scopeId={scopeId} />
    </>
  );
}
