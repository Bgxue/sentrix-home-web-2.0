"""图结构记忆的有界拓展检索。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sentrix.domain.memories.ports import MemoryGraphRepository
from sentrix.domain.retrieval.models import CandidateHit

GRAPH_CHANNEL = "graph"
GRAPH_MAX_HOPS = 4


@dataclass(frozen=True, slots=True)
class GraphSearchResult:
    hits: Sequence[CandidateHit] = ()
    backend: str = "sqlite_graph"
    degraded: bool = False
    reason: str = ""


class RetrieveGraph:
    """从已有 primary 召回结果出发，遍历图并补充未命中的 asset。"""

    def __init__(self, *, graph: MemoryGraphRepository) -> None:
        self._graph = graph

    def execute(
        self,
        *,
        seed_asset_ids: Sequence[str],
        scope_id: str | None,
        limit: int,
    ) -> GraphSearchResult:
        # 跨相册检索没有一个可安全混用的 scope 图。此时保留原有五路结果，
        # 同时把限制写入 trace，避免把「图没有跑」伪装成「图跑了但没召回」。
        if not scope_id:
            return GraphSearchResult(degraded=True, reason="graph_requires_scope")
        status = self._graph.status(scope_id)
        if not status.available:
            return GraphSearchResult(degraded=True, reason=status.reason or "graph_not_built")
        expanded = self._graph.expand_assets(
            seed_asset_ids,
            scope_id=scope_id,
            max_hops=GRAPH_MAX_HOPS,
            limit=max(1, limit),
        )
        return GraphSearchResult(
            hits=tuple(
                CandidateHit(
                    asset_id=item.asset_id,
                    channel=GRAPH_CHANNEL,
                    rank=rank,
                    raw_score=item.score,
                    score_kind="graph_path",
                    higher_is_better=True,
                )
                for rank, item in enumerate(expanded, 1)
            )
        )
