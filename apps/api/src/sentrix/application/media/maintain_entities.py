"""非人物实体抽取用例 ``MaintainObservationEntities``（S6 · T127）。

对应 1.0 ``db.py:3755-3954`` 的 ``maintain_observation_entities``——**导入流水线**上的一步
（1.0 的调用点是 ``pipeline.py:336`` / ``:471`` / ``:594``）。它把一次观察里的地点、物件、
时间变成实体，并给它们挂上属性与「这条观察提到了它」。

**只做决定的那一半在 domain**（``domain/identity/extraction.py``：词表、分类、
日历标签、要建哪些实体）；这里做剩下三件：建、写属性、挂关联。

**与 1.0 的一处有意收窄：不建关系。** 1.0 那个函数的最后一组循环会给每个实体建两条
关系（``create_relationship(entity, "出现在", place_entity)`` 与
``(entity, "记录于", time_entity)``）。``relationships`` 表与
``RelationshipRepository`` 的归属是 **S7**（设计 §8 的 S7 = family graph + relationships），
所以那两条按 2026-09-30 的裁决留到 S7——**实体本身不依赖关系就有用**
（T128 的路由与 T131 的召回都只要实体）。这条收窄记在 S6 计划 §4 Task 127。

**两处「1.0 也这样」的留白**（不是省略，见 ``domain/identity/extraction.py`` 的 docstring）：
``atmosphere`` 那一类不建（它的两个来源——语义词表与 ``emotions``——2.0 都没有，
而 1.0 在词表不可用时同样产不出名字）；``semantic_primary`` / ``semantic_details``
两个属性不写（1.0 把它们关在 ``has_semantic_place`` / ``if spec:`` 里面）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sentrix.domain.identity.extraction import (
    EVENT_RELATIONS,
    build_entity_specs,
    object_category,
    time_semantics,
)
from sentrix.domain.identity.models import EntityRecord
from sentrix.domain.identity.ports import EntityRepository
from sentrix.domain.media.models import GpsPoint, Observation
from sentrix.domain.media.ports import AssetRepository
from sentrix.domain.memories.ports import EventParticipantRepository

# 1.0 在属性上记的来源标记，逐字。它们不是日志：``maintain_property`` 的「同值抬置信度」
# 与「用户值优先」两条判据都按 ``source`` 分派，所以这几个串是行为的一部分。
SOURCE_OBSERVATION = "observation_extraction"
SOURCE_OBJECT_TAXONOMY = "object_taxonomy_v1"
SOURCE_TIMESTAMP = "timestamp_derivation"
SOURCE_ASSET_GPS = "asset_gps"


@dataclass(frozen=True, slots=True)
class EntityExtraction:
    """一次抽取的结果。调用方要的两件事：建/找到了哪些实体、地点与时间分别是哪一个。"""

    entities: tuple[EntityRecord, ...] = field(default_factory=tuple)
    place_entity_id: str = ""
    time_entity_id: str = ""


class MaintainObservationEntities:
    def __init__(
        self,
        *,
        entities: EntityRepository,
        events: EventParticipantRepository,
        assets: AssetRepository,
    ) -> None:
        self._entities = entities
        self._events = events
        # 地点的 ``geo`` 属性来自 **asset 的 GPS**（1.0 ``db.py:3839-3844`` 的
        # ``gps_place = parse_gps_place(asset.get("captured_location"))``），
        # 而 GPS 在 asset 上不在 observation 上——所以这一件要读 asset。
        self._assets = assets

    def execute(self, observation: Observation, *, event_id: str = "") -> EntityExtraction:
        """把一次观察里的非人物实体落下来。**可重跑**：同一条观察跑第二次不会产生
        重复的实体（``find_or_create_entities`` 按名字查）、也不会产生重复的关联行
        （``link_observation`` 的 ``ON CONFLICT``），只会抬置信度。"""
        scope_id = observation.scope_id
        specs = build_entity_specs(
            place=observation.place,
            objects=tuple(observation.objects),
            captured_at=observation.captured_at,
        )
        found: Mapping[tuple[str, str], EntityRecord] = self._entities.find_or_create_entities(
            specs, scope_id=scope_id, confidence=observation.confidence
        )
        if not found:
            return EntityExtraction()

        asset = self._assets.get(observation.asset_id)
        evidence = [observation.id, event_id] if event_id else [observation.id]
        place_entity = found.get(("place", self._place_name(observation)))
        time_entity = found.get(
            ("time", observation.captured_at.date().isoformat())
            if observation.captured_at is not None
            else ("time", "")
        )

        for entity in found.values():
            self._entities.link_observation(entity.id, observation.id, confidence=observation.confidence)
            self._write_properties(
                entity,
                observation,
                location=asset.captured_location if asset else None,
                evidence=evidence,
            )
            if event_id:
                self._events.upsert_entity(
                    event_id,
                    entity.id,
                    EVENT_RELATIONS.get(entity.entity_type, "关联实体"),
                    evidence,
                    observation.confidence,
                )

        return EntityExtraction(
            entities=tuple(found.values()),
            place_entity_id=place_entity.id if place_entity else "",
            time_entity_id=time_entity.id if time_entity else "",
        )

    @staticmethod
    def _place_name(observation: Observation) -> str:
        """与 ``build_entity_specs`` 同一套兜底——两处必须给同一个名字，
        否则这里会找不到刚建出来的那个地点实体。"""
        from sentrix.domain.identity.extraction import UNKNOWN_PLACE

        return observation.place.strip() or UNKNOWN_PLACE

    def _write_properties(
        self,
        entity: EntityRecord,
        observation: Observation,
        *,
        location: GpsPoint | None,
        evidence: list[str],
    ) -> None:
        """按类型写属性。**每条都逐条指着 1.0 的那一行**（``db.py:3838-3934``）。

        地点这一类在 2.0 的输入下写两条、**不写第三条**：

        - ``geo`` ← asset 的 GPS（1.0 ``:3839-3844``）。**2.0 有**——``captured_location``
          在 asset 上。
        - ``scene_type`` ← ``selected_scene or (visual_place if not has_semantic_place
          else "")``（1.0 ``:3790``）。无语义词表时 ``selected_scene`` 是空串、
          ``has_semantic_place`` 是假，于是它**就等于自由文本地点**。
        - ``visual_place_descriptions`` ← 1.0 ``:3859-3864`` 的条件是
          ``visual_place and (visual_place != scene_type or has_semantic_place)``。
          把上面那两个值代进去：``scene_type == visual_place`` 且
          ``has_semantic_place`` 为假 —— **这个合取在 2.0 的输入下恒为假，所以不写**。
          那是 1.0 在同样输入下的行为，不是省略。

        ``semantic_primary`` / ``semantic_details`` 同理：1.0 把它们关在
        ``if has_semantic_place:`` 里面（``:3849-3856``）。
        """
        confidence = observation.confidence
        if entity.entity_type == "place":
            if location is not None:
                self._entities.maintain_property(
                    entity.id,
                    "geo",
                    {"latitude": location.latitude, "longitude": location.longitude},
                    confidence=confidence,
                    evidence_ids=evidence,
                    source=SOURCE_ASSET_GPS,
                )
            if observation.place:
                self._entities.maintain_property(
                    entity.id,
                    "scene_type",
                    observation.place,
                    confidence=confidence,
                    evidence_ids=evidence,
                    source=SOURCE_OBSERVATION,
                )
            return
        if entity.entity_type == "object":
            # 1.0 ``:3891-3899`` 的前两条：``label`` 与 ``category``。
            # 后两条（``semantic_primary`` / ``semantic_details``）关在 ``if spec:`` 里，
            # 那是语义词表的产物，2.0 没有——见模块 docstring。
            self._entities.maintain_property(
                entity.id,
                "label",
                entity.canonical_name,
                confidence=confidence,
                evidence_ids=evidence,
                source=SOURCE_OBSERVATION,
            )
            self._entities.maintain_property(
                entity.id,
                "category",
                object_category(entity.canonical_name),
                confidence=confidence,
                evidence_ids=evidence,
                source=SOURCE_OBJECT_TAXONOMY,
            )
            return
        if entity.entity_type == "time" and observation.captured_at is not None:
            # 1.0 ``:3925-3934``：四个标量 + 一个多值的 part_of_day。
            semantics = time_semantics(observation.captured_at)
            for key in ("date", "year", "month", "season"):
                self._entities.maintain_property(
                    entity.id,
                    key,
                    semantics[key],
                    confidence=confidence,
                    evidence_ids=evidence,
                    source=SOURCE_TIMESTAMP,
                )
            self._entities.maintain_property_values(
                entity.id,
                "part_of_day",
                [semantics["part_of_day"]],
                confidence=confidence,
                evidence_ids=evidence,
                source=SOURCE_TIMESTAMP,
            )
