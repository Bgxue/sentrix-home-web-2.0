"""记忆域端口：事件仓储与事实仓储。domain 只声明形状，SQL 在 infrastructure。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sentrix.domain.memories.events import EventRecord
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

    def list_by_scope(self, scope_id: str, *, limit: int, offset: int = 0) -> Sequence[EventRecord]: ...

    def observation_ids(self, event_id: str) -> Sequence[str]: ...

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


class FactRepository(Protocol):
    def active_for(self, *, scope_id: str, subject: str, predicate: str) -> Fact | None: ...

    def insert(self, fact: Fact) -> Fact: ...

    def reinforce(self, fact_id: str, *, evidence_ids: tuple[str, ...], confidence: float) -> Fact: ...

    def set_status(self, fact_id: str, status: str) -> Fact: ...

    def list_facts(self, scope_id: str, *, limit: int) -> Sequence[Fact]: ...

    def supersede_others(self, *, scope_id: str, subject: str, predicate: str, keep_fact_id: str) -> int: ...
