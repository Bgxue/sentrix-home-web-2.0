/**
 * 人物与关系。对应 1.0 `src/app.js:648` 的 `peopleView()`（pageHeader 在 `:670`）。
 *
 * 1.0 这一页有两个 tab（关系图谱 / 待整理人物）。**2.0 只做后者**——关系图谱要的
 * `relationships` 与 `family-graph` 端点都还没有。这里不放假图谱，直接说明。
 */

import { useScope } from "../../app/ScopeProvider";
import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { PeopleView } from "./PeopleView";

export function PeoplePage() {
  const { scopeId } = useScope();
  return (
    <>
      <PageHeader
        kicker="家庭人物"
        title="把相册里的人，连成清楚的关系。"
        description="关系默认来自模型判断；你的修改会直接写入数据库，并永久优先于后续推断。"
      />
      <Section kicker="人物聚类 / 待确认" title="先确认身份，再进入长期记忆">
        <PeopleView scopeId={scopeId} />
      </Section>
      <Section kicker="关系图谱" title="尚未开放">
        <EmptyState
          symbol="◎"
          title="关系图谱还没有数据源"
          description="它要的 relationships 与 family-graph 端点尚未实现。确认人物之后，关系会在这里成图。"
        />
      </Section>
    </>
  );
}
