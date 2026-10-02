/**
 * 人物详情侧栏：身份 / 关系 / 证据三段（计划书 §4）。
 *
 * **写操作全部走 client，成功后调 `onChanged()` 重拉**——不做本地乐观更新：改名的
 * 别名历史、成员身份的 `locked`/`revision`、关系的正反两行都是服务端算的，本地先改
 * 一份就是又抄一遍规则，抄错时用户看到的和库里的是两回事。
 *
 * 详情要显示「对方是谁」，所以除契约里的四个 prop 外多收一个 `people`（同一份名录，
 * 由页面传进来，避免在这里再拉一遍接口）。
 */

import { useState, type FormEvent } from "react";

import {
  deleteFamilyRelationship,
  deliveredPhotoUrl,
  putFamilyRelationship,
  renamePerson,
  setFamilyMembership,
  setFamilyPortrait,
  type FamilyMembershipValue,
  type FamilyPerson,
  type FamilyRelationship,
} from "../../../shared/api/client";
import { describeReviewError } from "../reviewErrors";
import { dedupeRelationships } from "./graphLayout";
import {
  membershipLabel,
  membershipOf,
  personDisplayName,
  relationChoices,
  MEMBERSHIP_VALUES,
} from "./membership";
import { PersonAvatar } from "./PersonAvatar";

const CHOICES = relationChoices();

interface Props {
  readonly person: FamilyPerson;
  readonly people: FamilyPerson[];
  readonly relationships: FamilyRelationship[];
  readonly onClose: () => void;
  readonly onChanged: () => Promise<void>;
  /** 图上拖拽落到人身上时预选的对方；普通点开是 null。 */
  readonly relateTarget?: string | null;
}

export function PersonDetail({
  person,
  people,
  relationships,
  onClose,
  onChanged,
  relateTarget = null,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState(person.display_name);
  const [portrait, setPortrait] = useState(person.portrait?.portrait_text ?? "");
  const [predicateIndex, setPredicateIndex] = useState(0);
  const [targetId, setTargetId] = useState(relateTarget ?? "");

  const others = people.filter((item) => item.id !== person.id);

  // 一对人只留一条边（与服务端给的正反两行、多种关系归并成同一形状），再只挑碰这个人的。
  const edges = dedupeRelationships(relationships)
    .filter(
      (edge) => edge.subject_entity_id === person.id || edge.object_entity_id === person.id,
    )
    .map((edge) => {
      const outgoing = edge.subject_entity_id === person.id;
      return {
        subject: edge.subject_entity_id,
        object: edge.object_entity_id,
        otherId: outgoing ? edge.object_entity_id : edge.subject_entity_id,
        label: outgoing ? edge.predicate : edge.inverse_predicate,
      };
    });

  async function run(action: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (cause) {
      setError(describeReviewError(cause));
    } finally {
      setBusy(false);
    }
  }

  function submitRename(event: FormEvent) {
    event.preventDefault();
    void run(() => renamePerson(person.id, name));
  }

  function submitPortrait(event: FormEvent) {
    event.preventDefault();
    void run(() => setFamilyPortrait(person.id, person.scope_id, portrait));
  }

  function submitRelation(event: FormEvent) {
    event.preventDefault();
    if (targetId === "") return;
    const choice = CHOICES[predicateIndex] ?? CHOICES[0];
    void run(() =>
      putFamilyRelationship({
        scopeId: person.scope_id,
        subjectEntityId: person.id,
        predicate: choice.predicate,
        objectEntityId: targetId,
        inversePredicate: choice.inversePredicate,
      }),
    );
  }

  const currentMembership: FamilyMembershipValue = membershipOf(person);

  return (
    <aside className="person-detail" role="complementary" aria-label={`${personDisplayName(person)} 的详情`}>
      <header className="detail-head">
        <PersonAvatar person={person} size="lg" />
        <div>
          <strong>{personDisplayName(person)}</strong>
          <small>
            {membershipLabel(currentMembership)} · 出现 {person.appearance_count} 次
            {person.membership?.locked === true ? " · 已锁定" : ""}
          </small>
        </div>
        <button type="button" className="detail-close" aria-label="关闭详情" onClick={onClose}>
          ×
        </button>
      </header>

      {error === null ? null : (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <section className="detail-section">
        <h3>身份</h3>
        <form className="detail-form" onSubmit={submitRename}>
          <label>
            姓名
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <button type="submit" className="button ghost small" disabled={busy}>
            保存姓名
          </button>
        </form>
        <label className="detail-field">
          家庭归属
          <select
            value={currentMembership}
            disabled={busy}
            onChange={(event) => {
              const next = event.target.value as FamilyMembershipValue;
              void run(() => setFamilyMembership(person.id, person.scope_id, next));
            }}
          >
            {MEMBERSHIP_VALUES.map((value) => (
              <option key={value} value={value}>
                {membershipLabel(value)}
              </option>
            ))}
          </select>
        </label>
        <form className="detail-form" onSubmit={submitPortrait}>
          <label>
            家庭画像
            <textarea
              rows={3}
              value={portrait}
              placeholder="这个人在家庭里的角色、习惯、常在一起的场景…"
              onChange={(event) => setPortrait(event.target.value)}
            />
          </label>
          <button type="submit" className="button ghost small" disabled={busy}>
            保存画像
          </button>
        </form>
      </section>

      <section className="detail-section">
        <h3>关系</h3>
        {edges.length === 0 ? (
          <p className="detail-empty">还没有已记录的关系。</p>
        ) : (
          <ul className="detail-relations">
            {edges.map((edge) => (
              <li key={`${edge.subject}-${edge.object}`}>
                <span>
                  <b>{edge.label}</b> {personDisplayName(people.find((item) => item.id === edge.otherId) ?? person)}
                </span>
                <button
                  type="button"
                  className="text-button"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      deleteFamilyRelationship({
                        scopeId: person.scope_id,
                        subjectEntityId: edge.subject,
                        objectEntityId: edge.object,
                      }),
                    )
                  }
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}

        {others.length === 0 ? null : (
          <form className="detail-form" onSubmit={submitRelation}>
            <div className="detail-relation-row">
              <select
                aria-label="关系"
                value={predicateIndex}
                onChange={(event) => setPredicateIndex(Number(event.target.value))}
              >
                {CHOICES.map((choice, index) => (
                  <option key={choice.label} value={index}>
                    {choice.label}
                  </option>
                ))}
              </select>
              <select
                aria-label="对方"
                value={targetId}
                onChange={(event) => setTargetId(event.target.value)}
              >
                <option value="">选择对方…</option>
                {others.map((item) => (
                  <option key={item.id} value={item.id}>
                    {personDisplayName(item)}
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="button primary small" disabled={busy || targetId === ""}>
              建立关系
            </button>
          </form>
        )}
      </section>

      <section className="detail-section">
        <h3>证据</h3>
        {person.representative_media.length === 0 ? (
          <p className="detail-empty">还没有代表照片。</p>
        ) : (
          <ul className="detail-media">
            {person.representative_media.map((media) => (
              <li key={media.id}>
                <img src={deliveredPhotoUrl(media.id, media.file_name)} alt={media.file_name} loading="lazy" />
                <small>{media.file_name}</small>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
