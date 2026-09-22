"""事件归属用例：把一条新观察折进最合适的事件，或为它开一个新事件。

对应 1.0 ``backend/db.py:2287-2330`` 的 ``merge_observation_into_event`` +
``:1962-1990`` 的 ``_event_candidates``。流程一模一样：列本 scope 的活动事件 → 6 小时
硬窗过滤 → 八分量打分 → 取最高分 → ``>= 0.50`` 折进去，否则新建。

**归并顺序是硬约束：视觉向量必须先写。** ``score_event_candidate`` 的 ``visual_boost``
（权重 0.05）与 ``split_guard`` 的判定都来自 ``_event_visual_similarity``，而它读的是
``memory_vectors`` 里 ``space='visual' & source_type='asset'`` 的向量。1.0 在
``pipeline.process()`` 里显式把 ``upsert_vector`` 排在 ``merge_observation_into_event``
**之前**（``pipeline.py:315-325``），并且写了注释「Event selection needs the image
vector before an event exists」。把顺序倒过来不会报错——``_event_visual_similarity``
会返回 ``(0.0, False)``，于是 ``visual_boost`` 恒为 0、``split_guard`` 因为
``not visual_available`` 更容易命中——事件照分、照建，只是分错了。
见 ``s4-deviations.md`` §3 与 Task 56。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.media.models import Asset, Observation
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import (
    CANDIDATE_WINDOW_HOURS,
    EventAnchor,
    EventAnchorSet,
    EventCandidateScore,
    EventRecord,
    ObservationAnchor,
    VisualSignal,
    normalise_tokens,
    rank_candidates,
    score_event_candidate,
    within_candidate_window,
)
from sentrix.domain.memories.ports import EventRepository
from sentrix.domain.places.models import Place
from sentrix.domain.places.ports import AssetPlaceRepository
from sentrix.domain.retrieval.ports import VectorRepository

# 1.0 ``db.py:2221`` 的 ``list_events(1000, scope_id)``。活动事件超过这个数时
# 打分只在前 1000 个里做——1.0 就是这么写死的，2.0 不变，但把它写成常量而不是
# 函数里的字面量，免得被当成随手可调的数。
ACTIVE_EVENT_SCAN_LIMIT = 1000


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    event: EventRecord
    score: EventCandidateScore | None
    created: bool
    candidate_count: int


class EventVisualSimilarity:
    """候选观察与某事件之间最强的资产级视觉相似度。

    对应 1.0 ``db.py:2082-2110`` 的 ``_event_visual_similarity``：

    1. 先取候选观察的 asset 向量（按 ``updated_at DESC`` 取最新一条）；
    2. 再取事件里所有观察的 asset 向量，**要求 ``model_name`` 相同**且维度一致；
    3. 取余弦最大。

    第 2 条的模型名限定是 1.0 就有的（换过嵌入模型之后，旧向量与新向量算余弦是
    纯粹的噪声），2.0 保留。
    """

    def __init__(
        self,
        *,
        vectors: VectorRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
    ) -> None:
        self._vectors = vectors
        self._observations = observations
        self._assets = assets

    def for_observation(self, observation: Observation, event_observation_ids: Sequence[str]) -> VisualSignal:
        from sentrix.domain.retrieval.similarity import cosine_similarity

        incoming = self._vectors.get(space="visual", source_type="asset", source_id=observation.asset_id)
        if incoming is None or not incoming.vector:
            return VisualSignal()
        best: float | None = None
        for observation_id in event_observation_ids:
            other = self._observations.get(observation_id)
            if other is None:
                continue
            vector = self._vectors.get(
                space="visual",
                source_type="asset",
                source_id=other.asset_id,
                model_name=incoming.model_name,
            )
            if vector is None or len(vector.vector) != len(incoming.vector):
                continue
            score = cosine_similarity(incoming.vector, vector.vector)
            best = score if best is None else max(best, score)
        if best is None:
            return VisualSignal()
        return VisualSignal(similarity=best, available=True)


class AssignEvents:
    def __init__(
        self,
        *,
        events: EventRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        places: AssetPlaceRepository,
        visual_similarity: EventVisualSimilarity,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        confirmed_people: Callable[[str], frozenset[str]] = lambda _observation_id: frozenset(),
    ) -> None:
        self._events = events
        self._observations = observations
        self._assets = assets
        self._places = places
        self._visual = visual_similarity
        self._clock = clock
        # S5 之前恒为空集：没有 entity_mentions 就没有「已确认人物」。
        # person_score（权重 0.20）因此在 S5 落地前恒为 0，这一点写在计划里，
        # 不在这里编一个假来源。见 s4-deviations.md §4。
        self._confirmed_people = confirmed_people

    def execute(self, observation: Observation) -> AssignmentResult:
        asset = self._assets.get(observation.asset_id)
        if asset is None:
            raise KeyError(observation.asset_id)
        scope_id = asset.scope_id
        anchor = self._anchor(observation, asset, self._places.get(asset.id))

        scored: list[tuple[EventAnchorSet, EventCandidateScore]] = []
        for event in self._events.list_active(scope_id, limit=ACTIVE_EVENT_SCAN_LIMIT):
            if not within_candidate_window(anchor.captured_at, event.time_start):
                continue
            observation_ids = self._events.observation_ids(event.id)
            anchors = self._event_anchors(observation_ids)
            object_sets = tuple(
                normalise_tokens(item.objects)
                for item in (self._observations.get(observation_id) for observation_id in observation_ids)
                if item is not None and item.objects
            )
            anchor_set = EventAnchorSet(
                event_id=event.id,
                time_start=event.time_start,
                activity=event.activity,
                anchors=anchors,
                object_sets=object_sets,
                people=frozenset(),
            )
            visual = self._visual.for_observation(observation, observation_ids)
            scored.append((anchor_set, score_event_candidate(anchor=anchor, event=anchor_set, visual=visual)))

        ranked = rank_candidates(scored)
        best = ranked[0] if ranked and ranked[0][1].is_candidate() else None
        candidates = [item for item in ranked if item[1].is_candidate()]

        if best is None:
            event = self._create(observation, asset, anchor, [item for item in ranked])
            return AssignmentResult(event=event, score=None, created=True, candidate_count=0)

        winner, score = best
        event = self._fold(observation, winner, score, asset, anchor)
        return AssignmentResult(event=event, score=score, created=False, candidate_count=len(candidates))

    # ---- 内部 ----------------------------------------------------------------

    def _anchor(self, observation: Observation, asset: Asset, place: Place | None) -> ObservationAnchor:
        return ObservationAnchor(
            captured_at=asset.captured_at or observation.captured_at,
            location=asset.captured_location.as_text() if asset.captured_location else "",
            gps=asset.captured_location,
            visual_place=observation.place.strip().lower(),
            visual_event_type=observation.event_type.strip().lower(),
            activity=observation.activity,
            objects=normalise_tokens(observation.objects),
            people=self._confirmed_people(observation.id),
        )

    def _event_anchors(self, observation_ids: Sequence[str]) -> tuple[EventAnchor, ...]:
        anchors: list[EventAnchor] = []
        for observation_id in observation_ids:
            member = self._observations.get(observation_id)
            if member is None:
                continue
            asset = self._assets.get(member.asset_id)
            if asset is None:
                continue
            anchors.append(
                EventAnchor(
                    captured_at=asset.captured_at or member.captured_at,
                    location=asset.captured_location.as_text() if asset.captured_location else "",
                    gps=asset.captured_location,
                    visual_place=member.place.strip().lower(),
                    visual_event_type=member.event_type.strip().lower(),
                )
            )
        return tuple(anchors)

    def _create(
        self,
        observation: Observation,
        asset: Asset,
        anchor: ObservationAnchor,
        diagnostics: Sequence[tuple[EventAnchorSet, EventCandidateScore]],
    ) -> EventRecord:
        place = self._places.get(asset.id)
        captured = anchor.captured_at
        event = self._events.create(
            event_id=_new_event_id(),
            scope_id=asset.scope_id,
            time_start=captured,
            time_end=captured,
            place=place.display() if place else "",
            admin_district=place.admin.district if place else "",
            activity=observation.activity,
            summary=observation.caption or observation.activity,
            confidence=observation.confidence,
            aggregation_score=0.0,
            aggregation_breakdown={
                "selected": False,
                # 1.0 ``db.py:2322``：候选多于一个就记 ambiguity，供人工复核。
                "ambiguity": len(diagnostics) > 1,
                "split_guard": next(
                    (score.split_guard for _, score in diagnostics if score.split_guard), None
                ),
                "candidates": [score.breakdown() for _, score in diagnostics[:5]],
            },
        )
        self._events.attach(event.id, observation.id)
        return event

    def _fold(
        self,
        observation: Observation,
        winner: EventAnchorSet,
        score: EventCandidateScore,
        asset: Asset,
        anchor: ObservationAnchor,
    ) -> EventRecord:
        place = self._places.get(asset.id)
        self._events.attach(winner.event_id, observation.id)
        return self._events.fold_observation(
            winner.event_id,
            time_start=anchor.captured_at,
            time_end=anchor.captured_at,
            place=place.display() if place else "",
            admin_district=place.admin.district if place else "",
            activity=observation.activity,
            summary=observation.caption or observation.activity,
            confidence=observation.confidence,
            aggregation_score=score.total,
            aggregation_breakdown=score.breakdown(),
        )


__all__ = [
    "ACTIVE_EVENT_SCAN_LIMIT",
    "CANDIDATE_WINDOW_HOURS",
    "AssignmentResult",
    "AssignEvents",
    "EventVisualSimilarity",
]
