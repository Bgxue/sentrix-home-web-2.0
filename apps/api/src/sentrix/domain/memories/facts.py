"""事实（fact）的写入与状态迁移规则。纯规则，零 IO。

对应 1.0 ``backend/db.py:3397-3430`` 的 ``maintain_fact``、``:3432`` 的
``confirm_fact``、``:3455`` 的 ``reject_fact``、``:3439`` 的 ``purge_unanchored_facts``。

**所谓「两阶段」**：1.0 有两条写事实的路径，而它们行为不一致——

- ``pipeline.process()``（``pipeline.py:346-350``）在写观察之后**会**调
  ``maintain_fact``，并先过 ``is_confirmed_person_name`` 门；
- ``pipeline._commit_semantic_image()``（``pipeline.py:570-635``，即快速路径导入 +
  延迟语义富集）**不调**，整段没有一处 ``maintain_fact``。

同一张照片走哪条路径取决于它是什么时候导进来的，于是「事实有没有被记下来」变成了
导入批次的历史属性。这不是设计选择，是 1.0 的内部矛盾。2.0 把「哪些字段算一条可写
事实」和「写入动作是什么」抽成纯规则，两条路径共用同一个决定点。见
``s4-deviations.md`` §1 第 3 项。

写入语义（原样保留）：

- 同一个 ``(scope, subject, predicate)`` 只有一条 ``active``；
- 新值与当前 ``active`` 的 ``object`` **相同** → 不新建，只合并证据 + 取
  ``MAX(confidence)``；
- 不同 → 新建一条：当前没有 ``active`` 时新条就是 ``active``，有则新条是
  ``pending``（等人确认），并把 ``active`` 记进 ``supersedes_fact_id``。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class FactStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class FactWriteAction(StrEnum):
    """一次 ``maintain_fact`` 落到数据库上是什么动作。"""

    REINFORCE = "reinforce"  # 值没变：合并证据、抬 confidence
    INSERT = "insert"  # 值变了或本来就没有：插入一条新事实


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    scope_id: str
    subject: str
    predicate: str
    object: str
    status: FactStatus
    confidence: float
    evidence_ids: tuple[str, ...]
    revision: int
    supersedes_fact_id: str | None = None


@dataclass(frozen=True, slots=True)
class FactWrite:
    """一次写入的决定。仓储照着执行，不再自己判断。"""

    action: FactWriteAction
    status: FactStatus
    revision: int
    evidence_ids: tuple[str, ...]
    confidence: float
    supersedes_fact_id: str | None


def is_writable_fact(subject: str | None, predicate: str | None, object_value: str | None) -> bool:
    """1.0 ``pipeline.py:348`` 的 ``all(fact.get(k) for k in ("subject","predicate","object"))``。

    三个字段都要**非空字符串**。1.0 的写法是 ``all(...)``，``0``、``[]`` 也会被判掉——
    这里显式写成 strip 后的真值判断，把「字段缺失」与「字段是空串」合并成同一件事，
    与 1.0 行为一致。
    """
    return all(str(value or "").strip() for value in (subject, predicate, object_value))


def _merge_evidence(existing: tuple[str, ...], incoming: tuple[str, ...]) -> tuple[str, ...]:
    """1.0 ``db.py:3406-3408``：``list(dict.fromkeys(existing + evidence_ids))``——保序去重。"""
    seen: dict[str, None] = {}
    for item in (*existing, *incoming):
        if item and item not in seen:
            seen[item] = None
    return tuple(seen)


def decide_fact_write(
    *,
    existing: Fact | None,
    object_value: str,
    evidence_ids: tuple[str, ...],
    confidence: float,
) -> FactWrite:
    """下一次写入该做什么。``existing`` 是当前 ``active`` 的那条（没有就传 ``None``）。"""
    incoming = tuple(dict.fromkeys(item for item in evidence_ids if item))
    if existing is not None and existing.object == object_value:
        return FactWrite(
            action=FactWriteAction.REINFORCE,
            status=existing.status,
            revision=existing.revision,
            evidence_ids=_merge_evidence(existing.evidence_ids, incoming),
            # 1.0 用 SQL 的 ``MAX(confidence, ?)``：只抬不降。已经确信的事不会因为
            # 又来了一条低置信证据而变得可疑。
            confidence=max(existing.confidence, float(confidence)),
            supersedes_fact_id=existing.supersedes_fact_id,
        )
    return FactWrite(
        action=FactWriteAction.INSERT,
        # 1.0 ``db.py:3410``：``status = "pending" if active else "active"``。
        # 有 active 就说明这是一次**改口**，改口要人确认；没有 active 就是首次写入。
        status=FactStatus.PENDING if existing is not None else FactStatus.ACTIVE,
        revision=(existing.revision + 1) if existing is not None else 1,
        evidence_ids=incoming,
        confidence=float(confidence),
        supersedes_fact_id=existing.id if existing is not None else None,
    )


def confirm(existing_fact: Fact, *, now: datetime) -> tuple[Fact, Fact | None]:
    """确认一条 ``pending`` 事实：它转 ``active``，同 ``(subject, predicate)`` 的原
    ``active`` 转 ``superseded``。

    返回 ``(要激活的那条, 要作废的那条或 None)``。1.0 ``confirm_fact`` 先按
    ``subject + predicate``（**不带 scope**）无条件作废其他 active 行——那是个范围
    过宽的更新，跨相册同名事实会被一起作废。2.0 把作废范围收在本 scope 内，
    见 ``s4-deviations.md`` §2 第 7 项。
    """
    if existing_fact.status not in (FactStatus.PENDING, FactStatus.ACTIVE):
        raise ValueError(f"只有 pending/active 的事实可以确认，当前是 {existing_fact.status}")
    activated = replace(existing_fact, status=FactStatus.ACTIVE)
    return activated, existing_fact


def retract(fact: Fact) -> Fact:
    """拒绝一条事实。1.0 ``reject_fact`` 把任何状态都改成 ``retracted``，不校验来源状态。"""
    return replace(fact, status=FactStatus.RETRACTED)


def superseded_by(fact: Fact) -> Fact:
    return replace(fact, status=FactStatus.SUPERSEDED)
