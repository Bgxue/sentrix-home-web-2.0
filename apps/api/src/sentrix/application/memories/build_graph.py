"""从 2.0 标准记忆端口构建可重建的结构化记忆图。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime

from sentrix.domain.identity.ports import EntityRepository, RelationshipRepository
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import EventRecord
from sentrix.domain.memories.graph import GraphBuildStats, GraphEdge, GraphNode
from sentrix.domain.memories.ports import (
    EventParticipantRepository,
    EventRepository,
    FactRepository,
    MemoryGraphRepository,
)

log = logging.getLogger("sentrix.memories.build_graph")

GRAPH_READ_LIMIT = 100_000
TEMPORAL_EDGE_MAX_SECONDS = 6 * 60 * 60


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _normalise(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _digest(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in (prefix, *parts))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _node_id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


class BuildMemoryGraph:
    """把已有记忆投影成图。

    这里的输入只有 2.0 的 domain 端口：资产、观察、事件、参与者、实体、事实和关系。
    因此没有关键帧类型判断、Qdrant payload 读取或旧版内存库依赖；上游模块只要把这些
    标准记录写入，就可以直接触发本用例。
    """

    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        events: EventRepository,
        participants: EventParticipantRepository,
        entities: EntityRepository,
        facts: FactRepository,
        relationships: RelationshipRepository,
        graph: MemoryGraphRepository,
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._events = events
        self._participants = participants
        self._entities = entities
        self._facts = facts
        self._relationships = relationships
        self._graph = graph

    def execute(self, scope_id: str) -> GraphBuildStats:
        scope = str(scope_id or "").strip()
        if not scope:
            raise ValueError("构建记忆图需要明确的 scope_id")

        assets = tuple(self._assets.list_by_scope(scope, limit=GRAPH_READ_LIMIT))
        observations = tuple(self._observations.list_scope(scope, limit=GRAPH_READ_LIMIT))
        events = tuple(self._events.list_by_scope(scope, limit=GRAPH_READ_LIMIT))
        entities = tuple(self._entities.list_entities(scope_id=scope))
        facts = tuple(self._facts.list_facts(scope, limit=GRAPH_READ_LIMIT))
        relationships = tuple(self._relationships.list_relationships(scope))

        nodes: dict[str, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        observation_by_id = {item.id: item for item in observations}
        asset_by_id = {item.id: item for item in assets}
        entity_by_id = {item.id: item for item in entities}
        entity_by_name = {
            _normalise(item.canonical_name): item for item in entities if _normalise(item.canonical_name)
        }

        def add_node(node: GraphNode) -> None:
            nodes.setdefault(node.id, node)

        def add_edge(
            source_id: str,
            target_id: str,
            relation: str,
            *,
            weight: float = 1.0,
            attributes: Mapping[str, object] | None = None,
        ) -> None:
            if source_id == target_id:
                return
            edge_id = _node_id(
                "edge",
                _digest(scope, source_id, target_id, relation),
            )
            edges.setdefault(
                edge_id,
                GraphEdge(
                    id=edge_id,
                    scope_id=scope,
                    source_id=source_id,
                    target_id=target_id,
                    relation=relation,
                    weight=min(1.0, max(0.0, float(weight))),
                    attributes=dict(attributes or {}),
                ),
            )

        for asset in assets:
            add_node(
                GraphNode(
                    id=_node_id("asset", asset.id),
                    scope_id=scope,
                    kind="asset",
                    label=asset.file_name,
                    attributes={
                        "asset_id": asset.id,
                        "media_type": asset.media_type.value,
                        "captured_at": _iso(asset.captured_at),
                        "derived_kind": asset.derived_kind or "",
                        "parent_asset_id": asset.parent_asset_id or "",
                    },
                )
            )

        for entity in entities:
            add_node(
                GraphNode(
                    id=_node_id("entity", entity.id),
                    scope_id=scope,
                    kind="entity",
                    label=entity.canonical_name,
                    attributes={
                        "entity_id": entity.id,
                        "entity_type": entity.entity_type,
                        "status": entity.status,
                        "family_role": entity.family_role or "",
                    },
                )
            )

        for event in events:
            event_node = _node_id("event", event.id)
            add_node(
                GraphNode(
                    id=event_node,
                    scope_id=scope,
                    kind="event",
                    label=event.title or event.summary,
                    attributes={
                        "event_id": event.id,
                        "time_start": _iso(event.time_start),
                        "time_end": _iso(event.time_end),
                        "place": event.place,
                        "activity": event.activity,
                        "event_type": event.event_type,
                        "summary": event.summary,
                    },
                )
            )
            for observation_id in self._events.observation_ids(event.id):
                observation = observation_by_id.get(observation_id)
                if observation is None:
                    continue
                observation_node = _node_id("observation", observation.id)
                add_edge(event_node, observation_node, "event_contains_observation")
                if observation.asset_id in asset_by_id:
                    add_edge(event_node, _node_id("asset", observation.asset_id), "event_contains_asset")
            for participant in self._participants.list_participants(event.id):
                if participant.person_id in entity_by_id:
                    add_edge(
                        event_node,
                        _node_id("entity", participant.person_id),
                        "event_has_entity",
                        weight=participant.confidence or 1.0,
                        attributes={"role": participant.role},
                    )

        for observation in observations:
            observation_node = _node_id("observation", observation.id)
            add_node(
                GraphNode(
                    id=observation_node,
                    scope_id=scope,
                    kind="observation",
                    label=observation.caption or observation.activity or observation.place,
                    attributes={
                        "observation_id": observation.id,
                        "asset_id": observation.asset_id,
                        "captured_at": _iso(observation.captured_at),
                        "caption": observation.caption,
                        "activity": observation.activity,
                        "place": observation.place,
                        "event_type": observation.event_type,
                    },
                )
            )
            asset_node = _node_id("asset", observation.asset_id)
            if observation.asset_id in asset_by_id:
                add_edge(observation_node, asset_node, "observation_of_asset")

            concept_values: list[tuple[str, str]] = []
            concept_values.extend(("object", value) for value in observation.objects)
            concept_values.extend(("clothing", value) for value in observation.clothing)
            concept_values.extend(("person", value) for value in observation.people)
            concept_values.extend(
                (kind, value)
                for kind, value in (
                    ("place", observation.place),
                    ("activity", observation.activity),
                    ("event_type", observation.event_type),
                )
                if value
            )
            for concept_type, raw_value in concept_values:
                value = _normalise(raw_value)
                if not value:
                    continue
                mentioned_entity = entity_by_name.get(value) if concept_type == "person" else None
                if mentioned_entity is not None:
                    add_edge(
                        observation_node,
                        _node_id("entity", mentioned_entity.id),
                        "observation_mentions_entity",
                    )
                    continue
                concept_id = _node_id("concept", _digest(scope, concept_type, value))
                add_node(
                    GraphNode(
                        id=concept_id,
                        scope_id=scope,
                        kind="concept",
                        label=str(raw_value).strip(),
                        attributes={"concept_type": concept_type, "normalised": value},
                    )
                )
                add_edge(observation_node, concept_id, "observation_mentions_concept")

            for index, observation_fact in enumerate(observation.facts):
                fact_id = _node_id(
                    "observation_fact",
                    _digest(
                        observation.id,
                        index,
                        observation_fact.subject,
                        observation_fact.predicate,
                        observation_fact.object,
                    ),
                )
                add_node(
                    GraphNode(
                        id=fact_id,
                        scope_id=scope,
                        kind="fact",
                        label=(
                            f"{observation_fact.subject} {observation_fact.predicate} "
                            f"{observation_fact.object}"
                        ).strip(),
                        attributes={
                            "subject": observation_fact.subject,
                            "predicate": observation_fact.predicate,
                            "object": observation_fact.object,
                            "confidence": observation_fact.confidence,
                        },
                    )
                )
                add_edge(fact_id, observation_node, "fact_evidence")

        for persisted_fact in facts:
            fact_node = _node_id("fact", persisted_fact.id)
            add_node(
                GraphNode(
                    id=fact_node,
                    scope_id=scope,
                    kind="fact",
                    label=(
                        f"{persisted_fact.subject} {persisted_fact.predicate} {persisted_fact.object}"
                    ).strip(),
                    attributes={
                        "fact_id": persisted_fact.id,
                        "subject": persisted_fact.subject,
                        "predicate": persisted_fact.predicate,
                        "object": persisted_fact.object,
                        "status": str(persisted_fact.status),
                        "confidence": persisted_fact.confidence,
                    },
                )
            )
            subject = entity_by_name.get(_normalise(persisted_fact.subject))
            if subject is not None:
                add_edge(
                    fact_node,
                    _node_id("entity", subject.id),
                    "fact_about_entity",
                    weight=persisted_fact.confidence,
                )
            for evidence_id in persisted_fact.evidence_ids:
                if evidence_id in observation_by_id:
                    add_edge(fact_node, _node_id("observation", evidence_id), "fact_evidence")

        for relationship in relationships:
            if relationship.status in {"retracted", "superseded"}:
                continue
            if (
                relationship.subject_entity_id not in entity_by_id
                or relationship.object_entity_id not in entity_by_id
            ):
                continue
            add_edge(
                _node_id("entity", relationship.subject_entity_id),
                _node_id("entity", relationship.object_entity_id),
                f"entity_relationship:{relationship.predicate}",
                weight=relationship.confidence or 1.0,
                attributes={"relationship_id": relationship.id, "status": relationship.status},
            )

        self._add_temporal_edges(events, scope, add_edge)

        revision_parts: list[str] = [scope]
        revision_parts.extend(f"asset:{item.id}:{_iso(item.updated_at)}" for item in assets)
        revision_parts.extend(
            f"observation:{item.id}:{item.revision}:{item.updated_at.isoformat()}" for item in observations
        )
        revision_parts.extend(f"event:{item.id}:{item.revision}:{_iso(item.created_at)}" for item in events)
        revision_parts.extend(f"entity:{item.id}:{_iso(item.updated_at)}" for item in entities)
        revision_parts.extend(f"fact:{item.id}:{item.revision}:{item.status}" for item in facts)
        revision_parts.extend(
            f"relationship:{item.id}:{item.revision}:{_iso(item.updated_at)}" for item in relationships
        )
        build_revision = hashlib.sha256("\n".join(sorted(revision_parts)).encode("utf-8")).hexdigest()
        result = self._graph.replace_scope(
            scope,
            nodes=tuple(nodes.values()),
            edges=tuple(edges.values()),
            build_revision=build_revision,
        )
        log.info(
            "记忆图已重建 scope=%s nodes=%d edges=%d revision=%s",
            scope,
            result.node_count,
            result.edge_count,
            build_revision[:12],
        )
        return result

    @staticmethod
    def _add_temporal_edges(
        events: Iterable[EventRecord],
        scope_id: str,
        add_edge: Callable[..., None],
    ) -> None:
        ordered = sorted(
            (event for event in events if event.time_start is not None),
            key=lambda event: (event.time_start, event.id),
        )
        for left, right in zip(ordered, ordered[1:], strict=False):
            assert left.time_start is not None and right.time_start is not None
            gap = (right.time_start - left.time_start).total_seconds()
            if 0 <= gap <= TEMPORAL_EDGE_MAX_SECONDS:
                add_edge(
                    _node_id("event", left.id),
                    _node_id("event", right.id),
                    "event_temporal_next",
                    weight=max(0.1, 1.0 - gap / TEMPORAL_EDGE_MAX_SECONDS),
                    attributes={"gap_seconds": gap, "scope_id": scope_id},
                )
