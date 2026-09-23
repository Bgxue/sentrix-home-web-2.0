"""记忆域端口：事件仓储与事实仓储。domain 只声明形状，SQL 在 infrastructure。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sentrix.domain.memories.events import EventParticipantRecord, EventRecord
from sentrix.domain.memories.facts import Fact


class EventRepository(Protocol):
    def create(
        self,
        *,
        event_id: str,
        scope_id: str,
        time_start: datetime | None,
        time_end: datetime | None,
        place: str,
        admin_district: str,
        activity: str,
        summary: str,
        confidence: float,
        aggregation_score: float,
        aggregation_breakdown: dict[str, object],
    ) -> EventRecord: ...

    def get(self, event_id: str) -> EventRecord | None: ...

    def list_active(self, scope_id: str, *, limit: int) -> Sequence[EventRecord]: ...

    def list_by_scope(
        self, scope_id: str, *, limit: int, offset: int = 0, place: str = ""
    ) -> Sequence[EventRecord]: ...

    def count_by_scope(self, scope_id: str, *, place: str = "") -> int: ...

    def apply_summary(
        self,
        event_id: str,
        *,
        title: str,
        event_type: str,
        activity: str,
        summary: str,
    ) -> EventRecord: ...

    def observation_ids(self, event_id: str) -> Sequence[str]: ...

    def events_for_observations(self, observation_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """这些观察各自落在哪些事件上，反着查一次。

        1.0 ``db.py:5550-5554`` 的 ``_refresh_event_participants`` 开头那段：确认一个人
        之后手里只有一串 ``observation_id``，而参与者是**事件**级的投影，所以要先按
        事件把它们分好组——一次确认通常只碰一两个事件，逐条观察各开一次事务是纯浪费。

        返回 ``{event_id: (observation_id, ...)}``，观察按 id 升序去重（同一条观察不会
        在同一个事件里出现两次），**事件本身没有固定顺序**——调用方按事件逐个写，
        顺序不影响结果。
        """
        ...

    def attach(self, event_id: str, observation_id: str) -> None: ...

    def fold_observation(
        self,
        event_id: str,
        *,
        time_start: datetime | None,
        time_end: datetime | None,
        place: str,
        admin_district: str,
        activity: str,
        summary: str,
        confidence: float,
        aggregation_score: float,
        aggregation_breakdown: dict[str, object],
    ) -> EventRecord: ...

    def merge(self, source_event_id: str, target_event_id: str) -> None: ...

    def dissolve(self, event_id: str) -> None: ...


class EventParticipantRepository(Protocol):
    """事件参与者与事件-实体两张投影表。

    **它为什么与 ``EventRepository`` 分开。** 这两张表存的不是事件本身，而是「谁出现在
    这个事件里」——写它们的是身份侧的确认动作（一个人被认出来 → 事件里补一条参与者），
    读它们的是摘要与后续的检索投影。合成一个端口会让「谁改了事件」在类型上消失，
    与 ``domain/identity/ports.py`` 把 ``FaceRepository`` / ``EntityRepository`` 拆开
    是同一条理由。

    **``rehome`` 不是可有可无的搬运。** ``event_participants.event_id`` 上有指向
    ``events(id)`` 的外键，而每次连接都 ``PRAGMA foreign_keys = ON``
    （``infrastructure/persistence/database.py``）。1.0 的 ``_merge_events``（``db.py:5530``）
    自己写过一句注释记这件事：「SQLite correctly rejects deleting an event that still has
    audit, entity, participant-memory, or feedback rows pointing at it」。所以重分段合并
    两个事件之前，必须先把源事件的两张投影行改挂到目标事件上，否则 ``merge`` 的
    ``DELETE FROM events`` 会整体失败。
    """

    def upsert_participant(
        self,
        event_id: str,
        person_id: str,
        *,
        role: str,
        evidence_ids: Sequence[str] = (),
        confidence: float = 0.0,
    ) -> EventParticipantRecord | None:
        """写一条参与者：已有同一 ``(event, person, role)`` 就合并证据、只抬置信度、``revision + 1``。

        合并而不是覆盖，是因为同一个人的两条证据（两张不同的照片）都要能被审计到。
        人不存在于 ``entities`` 时返回 ``None`` 且不写任何行——1.0
        ``upsert_event_participant`` 的第一句就是这个判据（``db.py:2487``）。
        """
        ...

    def list_participants(self, event_id: str) -> Sequence[EventParticipantRecord]: ...

    def rehome(self, source_event_id: str, target_event_id: str) -> int:
        """把源事件的两张投影行搬到目标事件上（按 1.0 的合并语义），返回搬动的参与者行数。

        ``event_participants`` 上有 ``UNIQUE(event_id, person_id, role)``：直接 UPDATE 会
        在两边都有同一角色时撞唯一约束，所以按行读出来走 ``upsert_participant`` 的合并
        语义再删源行。``event_entities`` 的主键是 ``(event_id, entity_id, relation)``，
        同样处理。

        **只搬不复制**：搬完之后源行必须消失，否则外键会挡住调用方的
        ``DELETE FROM events``——那正是本方法存在的理由。
        """
        ...


class FactRepository(Protocol):
    def active_for(self, *, scope_id: str, subject: str, predicate: str) -> Fact | None: ...

    def insert(self, fact: Fact) -> Fact: ...

    def reinforce(self, fact_id: str, *, evidence_ids: tuple[str, ...], confidence: float) -> Fact: ...

    def set_status(self, fact_id: str, status: str) -> Fact: ...

    def list_facts(self, scope_id: str, *, limit: int) -> Sequence[Fact]: ...

    def supersede_others(self, *, scope_id: str, subject: str, predicate: str, keep_fact_id: str) -> int: ...
