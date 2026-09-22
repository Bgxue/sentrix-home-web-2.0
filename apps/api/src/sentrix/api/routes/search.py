"""内容词检索。只做 HTTP 映射，召回与融合在 application 层。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from sentrix.api.deps import get_container
from sentrix.api.routes.media import to_summary
from sentrix.api.schemas.search import SearchHit, SearchResponse
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual, VisualSearchRequest
from sentrix.bootstrap.container import Container

log = logging.getLogger("sentrix.api.search")

router = APIRouter(tags=["search"])


@router.get("/search")
def search_assets(
    q: str = Query(..., min_length=1, max_length=200, description="内容词，例如 桥 / 海边 / 红色"),
    scope_id: str = "home-default",
    limit: int = Query(20, ge=1, le=100),
    container: Container = Depends(get_container),
) -> SearchResponse:
    use_case = RetrieveVisual(
        embedder=container.visual_embedder,
        vectors=container.vector_repository,
        index=container.vector_index,
    )
    result = use_case.execute(VisualSearchRequest(text=q, scope_id=scope_id, limit=limit))

    items: list[SearchHit] = []
    for hit in result.hits:
        asset = container.asset_repository.get(hit.asset_id)
        if asset is None:
            # 向量还在、资产没了（scope 被删等）。产品硬约束第 5 条要求查询返回
            # **可打开的**原图，所以只能丢弃——但丢弃这件事必须留痕。
            log.warning("检索命中了一个已不存在的资产，已丢弃: %s", hit.asset_id)
            continue
        items.append(
            SearchHit(
                asset=to_summary(asset),
                score=hit.score,
                channels=dict(hit.channels),
                raw_scores=dict(hit.raw_scores),
            )
        )

    return SearchResponse(
        query=q,
        backend=result.backend,
        degraded=result.degraded,
        degraded_reason=result.degraded_reason,
        authoritative_vectors=result.authoritative_vectors,
        items=items,
    )
