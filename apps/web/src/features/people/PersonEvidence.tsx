/**
 * 人物证据页（1.0 `app.js:950-955` 的 `person-evidence` 弹窗）。
 *
 * 两块取数：
 * - **人脸样本**来自 `GET /people/{id}` 的 `clusters[].samples[]`，每张样本自带服务端
 *   拼好的 `crop_url`；原图用 `assetFileUrl(asset_id)` 打开。
 * - **关联事件**来自 `GET /entities/{id}` 的 `events`。`GET /people/{id}` **不返回事件**
 *   （它只给人物与簇），所以这里多取一次实体详情，而不是让页面假装有这块数据。
 *
 * 只读：证据页不提供写操作，改名/关系/画像都在详情侧栏各自的位置。
 */

import { useEffect, useState } from "react";

import {
  assetFileUrl,
  getEntityDetail,
  getPerson,
  type EntityDetailResponse,
  type FaceCropRef,
  type PersonDetailResponse,
} from "../../shared/api/client";
import { describeReviewError } from "./reviewErrors";

interface Props {
  readonly personId: string;
  readonly personName: string;
  readonly onClose: () => void;
}

function formatTime(value: string | null): string {
  return value === null ? "时间未知" : value.slice(0, 16).replace("T", " ");
}

/** 一簇最多带回若干张样本，跨簇可能重复同一张脸；按 face_instance_id 去重后展示。 */
function faceSamples(detail: PersonDetailResponse): FaceCropRef[] {
  const seen = new Set<string>();
  const samples: FaceCropRef[] = [];
  for (const cluster of detail.clusters) {
    // 被拒绝的簇仍是这个人的历史簇；它不是「身份证据」，不该出现在证据页。
    if (cluster.status === "rejected") continue;
    for (const sample of cluster.samples) {
      if (seen.has(sample.face_instance_id)) continue;
      seen.add(sample.face_instance_id);
      samples.push(sample);
    }
  }
  return samples;
}

export function PersonEvidence({ personId, personName, onClose }: Props) {
  const [person, setPerson] = useState<PersonDetailResponse | null>(null);
  const [entity, setEntity] = useState<EntityDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getPerson(personId), getEntityDetail(personId)])
      .then(([personDetail, entityDetail]) => {
        if (cancelled) return;
        setPerson(personDetail);
        setEntity(entityDetail);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(describeReviewError(cause));
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [personId]);

  const samples = person === null ? [] : faceSamples(person);
  const events = entity?.events ?? [];

  return (
    <aside
      className="person-detail"
      role="dialog"
      aria-modal="true"
      aria-label={`${personName} 的人脸证据`}
    >
      <header className="detail-head">
        <div>
          <p className="section-kicker">人物证据</p>
          <strong>{personName}</strong>
          <small>
            {person === null ? "" : `${samples.length} 张人脸样本 · ${events.length} 个关联事件`}
          </small>
        </div>
        <button type="button" className="detail-close" aria-label="关闭证据页" onClick={onClose}>
          ×
        </button>
      </header>

      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {!loaded ? <p className="empty">正在读取证据…</p> : null}

      <section className="detail-section">
        <h3>人脸样本</h3>
        {loaded && samples.length === 0 ? (
          <p className="detail-empty">还没有可回看的人脸样本。</p>
        ) : (
          <ul className="detail-media">
            {samples.map((sample) => (
              <li key={sample.face_instance_id}>
                <img
                  src={sample.crop_url}
                  alt={`${personName} 的人脸样本`}
                  loading="lazy"
                  title={`检测置信度 ${Math.round(sample.detection_confidence * 100)}%`}
                />
                <a
                  className="text-button"
                  href={assetFileUrl(sample.asset_id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看原图
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="detail-section">
        <h3>关联事件</h3>
        {loaded && events.length === 0 ? (
          <p className="detail-empty">还没有关联事件。确认后，新的事件观察会继续维护。</p>
        ) : (
          <ul className="detail-relations">
            {events.map((event) => (
              <li key={event.id}>
                <span>
                  <b>{event.title || "未命名事件"}</b>
                  <small>
                    {formatTime(event.time_start)} · {event.place || "未标注地点"}
                  </small>
                  {event.summary ? <small>{event.summary}</small> : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
