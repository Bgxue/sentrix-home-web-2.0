/**
 * 语义实体组详情。对应 1.0 `src/app.js:870-882` 的 `semanticGroupCard` 点开后的弹层。
 *
 * 数据只来自 `GET /entity-groups/{id}`：组本身、成员详情、去重后的观察与事件。
 * **没有关系那一块**——2.0 的这个端点不合并成员关系，前端不补空壳。
 */

import { useEffect, useState } from "react";

import {
  assetPreviewUrl,
  getEntity,
  getEntityGroup,
  type AssetDetailResponse,
  type EntityDetailFullResponse,
  type EntityGroupDetailResponse,
  type EntitySummaryPayload,
  type ObservationPayload,
} from "../../shared/api/client";
import { AssetDetail } from "../library/AssetDetail";
import { assetFromObservation } from "./observationAsset";
import { EntityDetailView } from "./EntityDetailView";
import "./knowledge.css";

function memberSummary(member: EntitySummaryPayload): string {
  return `${member.evidence_count} 条证据 · 置信度 ${Math.round(member.confidence * 100)}%`;
}

export interface EntityGroupDetailProps {
  groupId: string;
  scopeId: string;
  onClose: () => void;
}

export function EntityGroupDetail({ groupId, scopeId, onClose }: EntityGroupDetailProps) {
  const [detail, setDetail] = useState<EntityGroupDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [entity, setEntity] = useState<EntityDetailFullResponse | null>(null);
  const [asset, setAsset] = useState<{
    asset: AssetDetailResponse;
    observations: ObservationPayload[];
  } | null>(null);
  const [busyMember, setBusyMember] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getEntityGroup(groupId, scopeId)
      .then((response) => !cancelled && setDetail(response))
      .catch((cause: Error) => !cancelled && setError(cause.message));
    return () => {
      cancelled = true;
    };
  }, [groupId, scopeId]);

  async function openMember(member: EntitySummaryPayload) {
    setBusyMember(member.id);
    setError(null);
    try {
      setEntity(await getEntity(member.id));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusyMember(null);
    }
  }

  function openObservation(observation: ObservationPayload) {
    setAsset({
      asset: assetFromObservation(observation),
      observations: (detail?.observations ?? []).filter(
        (item) => item.asset_id === observation.asset_id,
      ),
    });
  }

  return (
    <div className="entity-dialog" role="dialog" aria-modal="true" aria-label="语义实体组详情">
      <button type="button" className="entity-close" onClick={onClose}>×</button>
      <div className="entity-dialog-panel">
        {error !== null ? <p className="error">{error}</p> : null}
        {detail === null && error === null ? <p className="entity-empty">正在读取实体组…</p> : null}
        {detail !== null ? (
          <>
            <p className="section-kicker">SEMANTIC ENTITY GROUP</p>
            <h2>{detail.group.canonical_name}</h2>
            <p className="entity-lead">
              这些原始图片中可以观察到与 {detail.group.canonical_name} 有关的
              {detail.group.entity_type === "object" ? "物品" : "画面氛围"}特征。
            </p>
            <div className="entity-facts">
              <span>成员 · {detail.group.members.length}</span>
              <span>证据 · {detail.group.evidence_count}</span>
              <span>置信度 · {Math.round(detail.group.confidence * 100)}%</span>
              <span>
                归并策略 · {detail.group.rationale.strategy} / {detail.group.rationale.normalized_key}
              </span>
            </div>
            {detail.group.semantic_details.length > 0 ? (
              <div className="entity-tags">
                {detail.group.semantic_details.map((item) => (
                  <span key={item}>{item}</span>
                ))}
              </div>
            ) : null}

            <section className="entity-section">
              <h3>成员实体 · {detail.group.members.length} 项</h3>
              <ul className="entity-member-list">
                {detail.group.members.map((member) => (
                  <li key={member.id}>
                    <button
                      type="button"
                      onClick={() => void openMember(member)}
                      disabled={busyMember === member.id}
                    >
                      {member.preview_asset_id !== null && member.preview_media_type === "image" ? (
                        <img
                          src={assetPreviewUrl(member.preview_asset_id)}
                          alt={member.preview_file_name ?? member.canonical_name}
                          loading="lazy"
                        />
                      ) : (
                        <span className="entity-member-mark" aria-hidden="true">
                          {member.canonical_name.slice(0, 1)}
                        </span>
                      )}
                      <span>
                        <strong>{member.canonical_name}</strong>
                        <small>{memberSummary(member)}</small>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>

            <section className="entity-section">
              <h3>原始证据 · {detail.observations.length} 条观察</h3>
              {detail.observations.length === 0 ? (
                <p className="entity-empty">该组尚未关联可查看的 Observation。</p>
              ) : (
                <ul className="entity-observation-list">
                  {detail.observations.map((observation) => (
                    <li key={observation.id}>
                      <button type="button" onClick={() => openObservation(observation)}>
                        <strong>{observation.caption || "原始观察证据"}</strong>
                        <small>
                          {observation.captured_at ?? "未标注时间"} · {observation.source_type}
                        </small>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="entity-section">
              <h3>关联事件 · {detail.events.length} 个</h3>
              {detail.events.length === 0 ? (
                <p className="entity-empty">暂无关联事件。</p>
              ) : (
                <ul className="entity-event-list">
                  {detail.events.map((event) => (
                    <li key={event.id}>
                      <strong>{event.title}</strong>
                      <small>
                        {event.time_start ?? "未标注时间"} · {event.place || "未标注地点"}
                      </small>
                      {event.activity || event.summary ? <p>{event.activity || event.summary}</p> : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        ) : null}
      </div>

      {entity !== null ? (
        <EntityDetailView detail={entity} onClose={() => setEntity(null)} />
      ) : null}
      {asset !== null ? (
        <AssetDetail
          asset={asset.asset}
          observations={asset.observations}
          onClose={() => setAsset(null)}
        />
      ) : null}
    </div>
  );
}
