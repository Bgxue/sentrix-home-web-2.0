/**
 * 实体与知识。对应 1.0 `src/app.js:870` 的 `semanticKnowledgeView()`（pageHeader 在 `:882`）。
 *
 * 1.0 这一页有实体组、行程候选、地理地点三块，2.0 的对应端点都还没有。**只做已确认
 * 人物那一块**（`/people` 真实可用），其余三块给空状态——它们是 S6–S11 的可见进度表。
 */

import { useEffect, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { StatCard } from "../../shared/ui/StatCard";
import { listPeople } from "../../shared/api/client";

export function KnowledgePage() {
  const { scopeId } = useScope();
  const [peopleCount, setPeopleCount] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    listPeople(scopeId)
      .then((people) => !cancelled && setPeopleCount(people.length))
      .catch(() => !cancelled && setPeopleCount(null));
    return () => {
      cancelled = true;
    };
  }, [scopeId]);

  return (
    <>
      <PageHeader
        kicker="语义记忆 / 实体目录"
        title="人物、地点与细节共同组成回忆。"
        description="相近描述会自动归到同一语义实体组；成员实体、事件和照片始终保留为可追溯的组成部分。"
      />
      <section className="stats-grid">
        <StatCard label="已确认人物" value={peopleCount ?? "—"} note="来自 /people" />
        <StatCard label="语义实体组" value="—" note="端点未实现" />
        <StatCard label="地理地点" value="—" note="端点未实现" />
        <StatCard label="行程候选" value="—" note="端点未实现" />
      </section>
      <Section kicker="行程候选" title="跨事件的长线回忆">
        <EmptyState
          symbol="◇"
          title="暂无行程候选"
          description="只有跨日或跨地点的连续事件才会成为待确认行程，而 trips 端点尚未实现。"
        />
      </Section>
    </>
  );
}
