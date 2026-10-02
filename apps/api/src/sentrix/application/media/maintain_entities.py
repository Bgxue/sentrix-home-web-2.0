"""非人物实体抽取用例 ``MaintainObservationEntities``（S6 · T127）。

对应 1.0 ``db.py:3755-3954`` 的 ``maintain_observation_entities``——**导入流水线**上的一步
（1.0 的调用点是 ``pipeline.py:336`` / ``:471`` / ``:594``）。它把一次观察里的地点、物件、
时间变成实体，并给它们挂上属性与「这条观察提到了它」。

**只做决定的那一半在 domain**（``domain/identity/extraction.py``：词表、分类、
日历标签、要建哪些实体）；这里做剩下四件：建、写属性、挂关联、建关系。

**S6 时这里有一处有意收窄，S7a · T204 已补回。** 1.0 那个函数的最后一组循环会给每个
实体建两条关系（``create_relationship(entity, "出现在", place_entity)`` 与
``(entity, "记录于", time_entity)``，``db.py:3943-3952``）。S6 按 2026-09-30 的裁决
把它们留到 S7，理由是「实体本身不依赖关系就有用」；T204 补上——现在 ``relationships``
有读者了（T201 的实体详情、T202 的实体目录计数、T203 的关系图）。

**三条照搬：**

- **自环挡在调用点**（``entity.id != place_entity.id`` / ``!= time_entity.id``）——
  ``create_relationship`` 自己**不做**自环校验，这两行判断是唯一的防线；
- ``evidence`` 复用上面那个变量（``[observation_id, event_id] if event_id else
  [observation_id]``），两处是同一个表达式；
- ``status`` **不传**，走仓储默认的 ``pending``——这两条边是「机器看出来的」，
  等用户确认（1.0 的两处调用同样没传）。

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
from sentrix.domain.identity.ports import EntityRepository, RelationshipRepository
from sentrix.domain.media.models import GpsPoint, Observation
from sentrix.domain.media.ports import AssetRepository
from sentrix.domain.memories.ports import EventParticipantRepository

# 1.0 在属性上记的来源标记，逐字。它们不是日志：``maintain_property`` 的「同值抬置信度」
# 与「用户值优先」两条判据都按 ``source`` 分派，所以这几个串是行为的一部分。
SOURCE_OBSERVATION = "observation_extraction"
SOURCE_OBJECT_TAXONOMY = "object_taxonomy_v1"
SOURCE_TIMESTAMP = "timestamp_derivation"
SOURCE_ASSET_GPS = "asset_gps"

# 1.0 ``db.py:3944`` / ``:3949`` 的两个谓词字面量。它们是**交付数据**不是日志：
# 实体详情（T201）、关系图（T203）与以后的助手都按这两个串读。
APPEARS_AT = "出现在"
RECORDED_AT = "记录于"


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
        relationships: RelationshipRepository,
    ) -> None:
        self._entities = entities
        self._events = events
        self._relationships = relationships
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
            # 1.0 ``db.py:3943-3952`` 的那两条。**自环只在这里挡**——
            # ``create_relationship`` 没有自环校验（1.0 也没有）。
            if place_entity is not None and entity.id != place_entity.id:
                self._relationships.create(
                    subject_entity_id=entity.id,
                    predicate=APPEARS_AT,
                    object_entity_id=place_entity.id,
                    evidence_ids=evidence,
                    confidence=observation.confidence,
                )
            if time_entity is not None and entity.id != time_entity.id:
                self._relationships.create(
                    subject_entity_id=entity.id,
                    predicate=RECORDED_AT,
                    object_entity_id=time_entity.id,
                    evidence_ids=evidence,
                    confidence=observation.confidence,
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
