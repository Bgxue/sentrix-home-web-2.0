"""结构化记忆图的构建、持久化和有限拓展。"""

from __future__ import annotations

from datetime import UTC, datetime

from sentrix.application.memories.build_graph import BuildMemoryGraph
from sentrix.application.retrieval.retrieve_graph import RetrieveGraph
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.memories.events import EventRecord
from sentrix.domain.memories.graph import GraphEdge, GraphExpansionHit, GraphNode, GraphStatus
from sentrix.domain.memories.ports import MemoryGraphRepository
from sentrix.infrastructure.persistence.repositories.memory_graph import (
    SqliteMemoryGraphRepository,
)

SCOPE = "graph-scope"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def test_sqlite_graph_replaces_scope_and_expands_assets(migrations_ready_db):
    repository = SqliteMemoryGraphRepository(migrations_ready_db)
    stats = repository.replace_scope(
        SCOPE,
        nodes=(
            GraphNode("asset:a", SCOPE, "asset", "a"),
            GraphNode("event:e", SCOPE, "event", "e"),
            GraphNode("asset:b", SCOPE, "asset", "b"),
        ),
        edges=(
            GraphEdge("edge:a", SCOPE, "asset:a", "event:e", "event_contains_asset"),
            GraphEdge("edge:b", SCOPE, "event:e", "asset:b", "event_contains_asset"),
        ),
        build_revision="revision-1",
    )

    assert stats.node_count == 3
    assert stats.edge_count == 2
    assert repository.status(SCOPE) == GraphStatus(
        scope_id=SCOPE,
        available=True,
        node_count=3,
        edge_count=2,
        build_revision="revision-1",
    )
    hits = repository.expand_assets(("a",), scope_id=SCOPE, max_hops=3, limit=10)
    assert [(hit.asset_id, hit.hops) for hit in hits] == [("b", 2)]

    repository.replace_scope(SCOPE, nodes=(), edges=(), build_revision="revision-2")
    assert repository.status(SCOPE).node_count == 0
    assert repository.expand_assets(("a",), scope_id=SCOPE, max_hops=3, limit=10) == ()


class _ListSource:
    def __init__(self, values):
        self.values = tuple(values)

    def list_by_scope(self, scope_id, *, limit):
        return self.values

    def list_scope(self, scope_id, *, limit):
        return self.values

    def list_entities(self, *, scope_id, status=None):
        return self.values

    def list_facts(self, scope_id, *, limit):
        return self.values

    def list_relationships(self, scope_id):
        return self.values


class _EventSource(_ListSource):
    def __init__(self, values, observations):
        super().__init__(values)
        self.observations = observations

    def observation_ids(self, event_id):
        return self.observations


class _ParticipantSource:
    def list_participants(self, event_id):
        return ()


def _asset(asset_id: str, path: str) -> Asset:
    return Asset(
        id=asset_id,
        scope_id=SCOPE,
        file_name=f"{asset_id}.jpg",
        media_type=MediaType.IMAGE,
        path=path,
        mime_type="image/jpeg",
        size_bytes=1,
        status=AssetStatus.PROCESSED,
        content_sha256=f"sha-{asset_id}",
        captured_at=NOW,
        captured_location=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _observation(observation_id: str, asset_id: str) -> Observation:
    return Observation(
        id=observation_id,
        scope_id=SCOPE,
        asset_id=asset_id,
        source_type="import",
        captured_at=NOW,
        confidence=0.8,
        created_at=NOW,
        updated_at=NOW,
        caption="家庭活动",
        activity="聚会",
        objects=("蛋糕",),
    )


def test_build_graph_consumes_standard_memory_records_without_keyframes(migrations_ready_db):
    assets = (_asset("a", "/tmp/a.jpg"), _asset("b", "/tmp/b.jpg"))
    observations = (_observation("obs-a", "a"), _observation("obs-b", "b"))
    event = EventRecord(
        id="event-1",
        scope_id=SCOPE,
        title="聚会",
        event_type="家庭活动",
        time_start=NOW,
        time_end=NOW,
        place="家",
        admin_district="",
        activity="聚会",
        summary="家庭聚会",
        confidence=0.9,
        aggregation_score=0.9,
        aggregation_breakdown={},
        created_at=NOW,
    )
    graph = SqliteMemoryGraphRepository(migrations_ready_db)
    use_case = BuildMemoryGraph(
        assets=_ListSource(assets),
        observations=_ListSource(observations),
        events=_EventSource((event,), ("obs-a", "obs-b")),
        participants=_ParticipantSource(),
        entities=_ListSource(()),
        facts=_ListSource(()),
        relationships=_ListSource(()),
        graph=graph,
    )

    stats = use_case.execute(SCOPE)

    assert stats.node_count >= 6
    assert stats.edge_count >= 5
    assert graph.status(SCOPE).available
    assert [hit.asset_id for hit in graph.expand_assets(("a",), scope_id=SCOPE, max_hops=4, limit=10)] == [
        "b"
    ]


class _FakeGraph(MemoryGraphRepository):
    def replace_scope(self, scope_id, *, nodes, edges, build_revision):
        raise NotImplementedError

    def status(self, scope_id):
        return GraphStatus(scope_id=scope_id, available=True)

    def expand_assets(self, seed_asset_ids, *, scope_id, max_hops, limit):
        return (GraphExpansionHit(asset_id="asset-b", score=0.8, hops=2),)


def test_retrieve_graph_is_explicitly_degraded_without_scope():
    use_case = RetrieveGraph(graph=_FakeGraph())

    result = use_case.execute(seed_asset_ids=("asset-a",), scope_id=None, limit=10)

    assert result.degraded is True
    assert result.reason == "graph_requires_scope"


def test_retrieve_graph_maps_expansion_to_graph_candidates():
    use_case = RetrieveGraph(graph=_FakeGraph())

    result = use_case.execute(seed_asset_ids=("asset-a",), scope_id=SCOPE, limit=10)

    assert result.degraded is False
    assert [(hit.asset_id, hit.channel, hit.rank) for hit in result.hits] == [("asset-b", "graph", 1)]
