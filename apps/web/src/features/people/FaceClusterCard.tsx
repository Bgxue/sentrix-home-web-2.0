import { useState, type FormEvent } from "react";

import type { FaceClusterSummary } from "../../shared/api/client";
import { clusterDisplayName } from "./reviewErrors";

/** 1.0 确认框里的那组家庭角色（``app.js:956`` 的 ``<select>``），原样保留。 */
const FAMILY_ROLES = ["母亲", "父亲", "孩子", "祖父母", "其他家庭成员"];

export interface FaceClusterCardProps {
  cluster: FaceClusterSummary;
  selectedForMerge: boolean;
  busy: boolean;
  onToggleMerge: (clusterId: string) => void;
  onConfirm: (clusterId: string, name: string, familyRole: string | null) => Promise<boolean>;
  onReject: (clusterId: string) => void;
  onSplit: (clusterId: string, faceInstanceId: string) => void;
}

/**
 * 一簇待审的脸。
 *
 * 三处「能不能点」全部读 ``cluster.reviewable`` / ``cluster.single_sample`` 这两个
 * **服务端算好的派生布尔**，前端不重算「几张脸算孤证」「什么状态算可审」。
 *
 * 拆分按产品要求是**在这一簇里点某一张脸**：点中哪张，动作就作用在哪张，
 * 不存在「拆哪张由前端猜一个」的路径。
 */
export function FaceClusterCard({
  cluster,
  selectedForMerge,
  busy,
  onToggleMerge,
  onConfirm,
  onReject,
  onSplit,
}: FaceClusterCardProps) {
  const [splitFace, setSplitFace] = useState<string | null>(null);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [familyRole, setFamilyRole] = useState("");

  const label = clusterDisplayName(cluster);
  const locked = busy || !cluster.reviewable;

  async function submitConfirm(event: FormEvent) {
    event.preventDefault();
    // 空名字交给后端判（那是用例的判据，400），前端只负责把结论原样显示出来。
    const ok = await onConfirm(cluster.id, name, familyRole === "" ? null : familyRole);
    if (ok) {
      setNaming(false);
      setName("");
      setFamilyRole("");
    }
  }

  return (
    <article className="cluster-card" data-cluster-id={cluster.id}>
      <header className="cluster-head">
        <div>
          <p className="section-kicker">人物识别</p>
          <strong>{label}</strong>
        </div>
        <label className="cluster-pick">
          <input
            type="checkbox"
            checked={selectedForMerge}
            onChange={() => onToggleMerge(cluster.id)}
            aria-label={`选中 ${label} 用于合并`}
          />
          选中用于合并
        </label>
      </header>

      <ul className="cluster-samples">
        {cluster.samples.map((sample, index) => (
          <li key={sample.face_instance_id}>
            <button
              type="button"
              className="cluster-sample"
              aria-pressed={splitFace === sample.face_instance_id}
              aria-label={`${label} 的第 ${index + 1} 张人脸`}
              disabled={locked}
              onClick={() =>
                setSplitFace(splitFace === sample.face_instance_id ? null : sample.face_instance_id)
              }
            >
              <img
                src={sample.crop_url}
                alt={`${label} 的人脸样本 ${index + 1}`}
                loading="lazy"
                title={`质量 ${Math.round(sample.quality * 100)}%`}
              />
            </button>
          </li>
        ))}
      </ul>

      <p className="cluster-meta">
        聚类置信度 {Math.round(cluster.confidence * 100)}% ·{" "}
        {cluster.entity_status === "confirmed" ? "已绑定人物" : "待确认身份"} ·{" "}
        {cluster.member_count} 张样本
        {cluster.samples.length < cluster.member_count
          ? `（显示 ${cluster.samples.length} 张）`
          : ""}
        {cluster.single_sample ? " · 孤证：只有一张脸，谨慎确认" : ""}
      </p>

      {splitFace === null ? null : (
        <div className="cluster-split">
          <span>已在这簇里点中 1 张脸</span>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              onSplit(cluster.id, splitFace);
              setSplitFace(null);
            }}
          >
            拆分为新簇
          </button>
          <button type="button" onClick={() => setSplitFace(null)}>
            取消
          </button>
        </div>
      )}

      {naming ? (
        <form className="cluster-naming" onSubmit={submitConfirm}>
          <label>
            姓名或称呼
            <input
              name="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：妈妈"
            />
          </label>
          <label>
            家庭角色
            <select
              name="family_role"
              value={familyRole}
              onChange={(event) => setFamilyRole(event.target.value)}
            >
              <option value="">暂不确认</option>
              {FAMILY_ROLES.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>
          <div className="cluster-actions">
            <button type="submit" className="primary" disabled={busy}>
              确认并写入
            </button>
            <button type="button" onClick={() => setNaming(false)}>
              取消
            </button>
          </div>
        </form>
      ) : (
        <div className="cluster-actions">
          <button
            type="button"
            className="primary"
            disabled={locked}
            onClick={() => setNaming(true)}
          >
            确认身份
          </button>
          <button type="button" disabled={locked} onClick={() => onReject(cluster.id)}>
            不是人物
          </button>
        </div>
      )}
    </article>
  );
}
