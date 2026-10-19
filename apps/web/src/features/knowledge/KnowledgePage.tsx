/**
 * 实体与知识。对应 1.0 `src/app.js:870` 的 `semanticKnowledgeView()`（pageHeader 在 `:882`）。
 *
 * 1.0 这一页有实体组、行程候选、地理地点三块。2.0 能接上的只有：
 *
 * - **语义实体组**：`GET /entity-groups` + `GET /entity-groups/{id}`（物品 / 氛围）；
 * - **实体详情**：`GET /entities/{id}`，地点属性编辑走
 *   `PUT /entities/{id}/properties/{key}`；
 * - **语义声明 / 人物**：`GET /knowledge` 的 `claims` 与 `/people`。
 *
 * **没接的两块，在界面上如实说明缺什么**：
 * - 地理地点：`/knowledge` 没有 `places`（城市 → 区县 → 照片墙）字段；
 * - 行程候选：后端没有 trips 端点。
 *
 * 前端不从 GPS 自行反查行政区，也不伪造这两个端点——那会把「缺字段」变成一张
 * 看起来像真的空白地图。
 */

import { useEffect, useMemo, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { EmptyState } from "../../shared/ui/EmptyState";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { StatCard } from "../../shared/ui/StatCard";
import {
  assetPreviewUrl,
  fetchKnowledge,
  listEntityGroups,
  listPeople,
  type EntityGroupPayload,
  type KnowledgeResponse,
  type PersonPayload,
} from "../../shared/api/client";
import { EntityGroupDetail } from "./EntityGroupDetail";
import "./knowledge.css";

function groupSummary(group: EntityGroupPayload): string {
  if (group.entity_type === "object") {
    return `这些原始图片中可以观察到与 ${group.canonical_name} 有关的物品特征。`;
  }
  if (group.entity_type === "place") {
    return `这些原始图片中可以观察到与 ${group.canonical_name} 有关的场景特征。`;
  }
  return `这些原始图片中可以观察到与 ${group.canonical_name} 有关的画面氛围。`;
}

function EntityGroupGrid({
  groups,
  emptyTitle,
  emptyDescription,
  onOpen,
}: {
  groups: readonly EntityGroupPayload[];
  emptyTitle: string;
  emptyDescription: string;
  onOpen: (groupId: string) => void;
}) {
  if (groups.length === 0) {
    return <EmptyState symbol="◇" title={emptyTitle} description={emptyDescription} />;
  }
  return (
    <div className="entity-grid">
      {groups.map((group) => (
        <button
          key={group.id}
          type="button"
          className="entity-card semantic-group-card"
          onClick={() => onOpen(group.id)}
        >
          {group.preview_asset_id !== null && group.preview_media_type === "image" ? (
            <img
              className="semantic-group-thumb"
              src={assetPreviewUrl(group.preview_asset_id)}
              alt={`${group.canonical_name} 的原始证据`}
              loading="lazy"
            />
          ) : (
            <span className="semantic-group-thumb semantic-group-fallback" aria-hidden="true">
              {group.canonical_name.slice(0, 1)}
            </span>
          )}
          <span className="semantic-group-copy">
            <span className="semantic-type-label">
              {group.members.length > 1 ? "相近场景" : "语义场景"} · {group.evidence_count} 条证据
            </span>
            <strong>{group.canonical_name}</strong>
            <span className="semantic-detail-tags">
              {(group.semantic_details.length > 0 ? group.semantic_details : ["由图片观察维护"]).map(
                (detail) => (
                  <span key={detail}>{detail}</span>
                ),
              )}
            </span>
            <small>{groupSummary(group)}</small>
            <span className="semantic-open-link">查看相关证据 →</span>
          </span>
        </button>
      ))}
    </div>
  );
}

export function KnowledgePage() {
  const { scopeId } = useScope();
  const [knowledge, setKnowledge] = useState<KnowledgeResponse | null>(null);
  const [groups, setGroups] = useState<readonly EntityGroupPayload[]>([]);
  const [people, setPeople] = useState<readonly PersonPayload[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openGroupId, setOpenGroupId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fail = (cause: Error) => !cancelled && setError(cause.message);

    fetchKnowledge(scopeId)
      .then((response) => !cancelled && setKnowledge(response))
      .catch(fail);
    listEntityGroups(scopeId)
      .then((items) => !cancelled && setGroups(items))
      .catch(fail);
    listPeople(scopeId)
      .then((items) => !cancelled && setPeople(items))
      .catch(fail);

    return () => {
      cancelled = true;
    };
  }, [scopeId]);

  const confirmedPeople = useMemo(
    () => people.filter((person) => person.confirmed).length,
    [people],
  );
  const currentClaims = useMemo(
    () => (knowledge?.claims ?? []).filter((claim) => claim.status !== "superseded").length,
    [knowledge],
  );
  const objectGroups = useMemo(
    () => groups.filter((group) => group.entity_type === "object" && group.evidence_count > 0),
    [groups],
  );
  const atmosphereGroups = useMemo(
    () => groups.filter((group) => group.entity_type === "atmosphere" && group.evidence_count > 0),
    [groups],
  );

  return (
    <>
      <PageHeader
        kicker="语义记忆 / 实体目录"
        title="人物、地点与细节共同组成回忆。"
        description="相近描述会自动归到同一语义实体组；成员实体、事件和照片始终保留为可追溯的组成部分。"
      />
      {error !== null ? <p className="error">{error}</p> : null}
      <section className="stats-grid">
        <StatCard label="已确认人物" value={confirmedPeople} note="来自 /people" />
        <StatCard label="语义实体组" value={groups.length} note="来自 /entity-groups" />
        <StatCard label="语义声明" value={currentClaims} note="来自 /knowledge 的 claims" />
        <StatCard label="地理地点" value="—" note="/knowledge 没有地点分组字段" />
      </section>

      <Section kicker="物品" title="物品语义">
        <EntityGroupGrid
          groups={objectGroups}
          emptyTitle="尚未形成物品实体"
          emptyDescription="等待带有可回溯观察证据的资料。"
          onOpen={setOpenGroupId}
        />
      </Section>

      <Section kicker="氛围" title="画面氛围">
        <EntityGroupGrid
          groups={atmosphereGroups}
          emptyTitle="尚未形成氛围实体"
          emptyDescription="等待带有可回溯观察证据的资料。"
          onOpen={setOpenGroupId}
        />
      </Section>

      <Section kicker="GPS 地理位置" title="按拍摄地点整理的回忆">
        <EmptyState
          symbol="◈"
          title="地点聚合暂不可用"
          description={
            "GET /knowledge 目前只返回 profiles / claims / spaces，没有地点分组。" +
            "要做城市 → 区县 → 照片墙，后端需要提供 places 数组，且每项至少包含 " +
            "name / level / province / count，children[].photos[] 里带 asset_id、file_name、" +
            "caption、semantic_place。前端不自行从 GPS 反查行政区。"
          }
        />
      </Section>

      <Section kicker="行程候选" title="跨事件的长线回忆">
        <EmptyState
          symbol="◇"
          title="暂无行程候选"
          description="只有跨日或跨地点的连续事件才会成为待确认行程；2.0 目前没有 trips 端点。"
        />
      </Section>

      {openGroupId !== null ? (
        <EntityGroupDetail
          groupId={openGroupId}
          scopeId={scopeId}
          onClose={() => setOpenGroupId(null)}
        />
      ) : null}
    </>
  );
}
