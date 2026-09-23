import { useCallback, useEffect, useState } from "react";

import {
  confirmFaceCluster,
  listFaceClusters,
  listPeople,
  mergeFaceClusters,
  rejectFaceCluster,
  renamePerson,
  splitFaceCluster,
  type FaceClusterSummary,
  type PersonPayload,
} from "../../shared/api/client";
import { FaceClusterList } from "./FaceClusterList";
import { PersonList } from "./PersonList";
import { describeReviewError } from "./reviewErrors";

interface PeopleData {
  clusters: FaceClusterSummary[];
  people: PersonPayload[];
}

/**
 * 拉一次列表。**只取数据，不写 state**——写进哪里由调用方决定。
 *
 * 审阅页只管还没决定的那些簇；已确认的人走 ``/people``。两边的 scope 都由调用方给。
 */
async function fetchPeopleData(scopeId: string): Promise<PeopleData> {
  const [clusters, people] = await Promise.all([
    listFaceClusters(scopeId, "pending"),
    listPeople(scopeId, "confirmed"),
  ]);
  return { clusters, people };
}

/**
 * 人脸簇审阅页——S5 的产品硬约束（确认 / 拒绝 / 合并 / 拆分 / 命名）。
 *
 * 这个组件只做三件事：把服务端的判断摆出来、把用户的选择收起来、把请求发出去。
 * **不重算任何后端规则**：谁可审、是不是孤证都读 ``FaceClusterSummary`` 上的派生布尔；
 * 合并方向由用户在多选里指定，前端不做「哪个更像谁」的推断。
 */
export function PeopleView({ scopeId }: { scopeId: string }) {
  const [clusters, setClusters] = useState<FaceClusterSummary[]>([]);
  const [people, setPeople] = useState<PersonPayload[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [targetChoice, setTargetChoice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const apply = useCallback((data: PeopleData) => {
    setClusters(data.clusters);
    setPeople(data.people);
  }, []);

  useEffect(() => {
    fetchPeopleData(scopeId)
      .then(apply)
      .catch((cause: unknown) => setError(describeReviewError(cause)))
      .finally(() => setLoaded(true));
  }, [scopeId, apply]);

  /**
   * 每个动作都走这一条：先清错误 → 跑 → 成功后重新拉一遍列表 → 失败把后端的 ``code``
   * 翻成中文摆到页面上。
   *
   * **重新拉而不是本地改状态**：member_count / revision / samples 都是服务端算的，
   * 本地改一份就是又抄了一遍规则，抄错的时候用户看到的簇和库里的是两回事。
   */
  async function run(action: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await action();
      apply(await fetchPeopleData(scopeId));
      return true;
    } catch (cause) {
      setError(describeReviewError(cause));
      return false;
    } finally {
      setBusy(false);
    }
  }

  function toggleMerge(clusterId: string) {
    setSelected((current) => {
      if (current.includes(clusterId)) {
        return current.filter((id) => id !== clusterId);
      }
      // 最多两个：再选第三个时顶掉最早选的那个，而不是让点击静默失效。
      return [...current, clusterId].slice(-2);
    });
  }

  // 目标没显式选过（或选的那个已被取消勾选）时，默认取先点的那个。
  const mergeTarget =
    selected.length === 2
      ? selected.includes(targetChoice ?? "")
        ? (targetChoice as string)
        : selected[0]
      : null;
  const mergeSource =
    mergeTarget === null ? null : (selected.find((id) => id !== mergeTarget) ?? null);

  function handleMerge() {
    if (mergeTarget === null || mergeSource === null) {
      return;
    }
    const target = mergeTarget;
    const source = mergeSource;
    void run(async () => {
      await mergeFaceClusters(target, source);
      setSelected([]);
      setTargetChoice(null);
    });
  }

  return (
    <section className="people">
      <header className="people-head">
        <div>
          <p className="section-kicker">人物</p>
          <h2>人脸簇审阅</h2>
        </div>
        <span className="result-count">{clusters.length} 个待确认簇</span>
      </header>
      <p className="people-lead">
        这些簇是本机人脸模型自动聚出来的。确认会给它一个身份，拒绝只是不再把它当人看——
        两种处理都不动原始照片。
      </p>

      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {!loaded ? (
        <p className="empty">正在读取待确认的簇…</p>
      ) : (
        <>
          <FaceClusterList
            clusters={clusters}
            selectedIds={selected}
            mergeTargetId={mergeTarget}
            busy={busy}
            onToggleMerge={toggleMerge}
            onChooseMergeTarget={setTargetChoice}
            onMerge={handleMerge}
            onConfirm={(clusterId: string, name: string, familyRole: string | null) =>
              run(() => confirmFaceCluster(clusterId, { name, family_role: familyRole }))
            }
            onReject={(clusterId: string) => {
              void run(() => rejectFaceCluster(clusterId));
            }}
            onSplit={(clusterId: string, faceInstanceId: string) => {
              void run(() => splitFaceCluster(clusterId, faceInstanceId));
            }}
          />

          <section className="people-confirmed">
            <h3>已确认的人物</h3>
            <PersonList
              people={people}
              busy={busy}
              onRename={(personId: string, name: string) => run(() => renamePerson(personId, name))}
            />
          </section>
        </>
      )}
    </section>
  );
}
