"""内容词检索。只做 HTTP 映射，召回与融合在 application 层。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from sentrix.api.deps import get_container
from sentrix.api.routes.media import to_summary
from sentrix.api.schemas.search import SearchHit, SearchResponse
from sentrix.application.retrieval.retrieve_entity import RetrieveEntity
from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, RetrieveEvidence
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.bootstrap.container import Container

log = logging.getLogger("sentrix.api.search")

router = APIRouter(tags=["search"])


def build_retrieve_evidence(container: Container) -> RetrieveEvidence:
    """把四条召回通道与编排接起来。**只在这里接一次**。

    1.0 的对应物是 ``build_default_retrievers`` 加 ``EvidenceRetrievalKernel`` 的构造
    （``backend/retrieval/__init__.py:65-85``）：只有**被启用**的通道才进 retriever 列表。
    1.0 的 text_ann 在 ``defaults.json`` 里是 ``"text": true``（启用的），
    而 2.0 到 S6 T115 才把它补上——那之前这里确实不构造它，因为「缺通道」在 1.0 里
    就是「不在列表里」，不是「构造一个返回空的通道」。现在五条都在了。

    ``recognize_place`` 的判据是「这张图本来就该有地点」= asset 上有 GPS，而
    ``asset_places`` 里没有行（``index_place.py`` 的第三个状态）。
    """
    return RetrieveEvidence(
        visual=RetrieveVisual(
            embedder=container.visual_embedder,
            vectors=container.vector_repository,
            index=container.vector_index,
        ),
        text=RetrieveTextAnn(embedder=container.text_embedder, vectors=container.vector_repository),
        entity=RetrieveEntity(index=container.lexical_search, observations=container.observation_repository),
        lexical=RetrieveLexical(index=container.lexical_search),
        metadata=RetrieveMetadata(
            assets=container.asset_repository,
            places=container.asset_place_repository,
            aliases=container.place_aliases,
            recognize_place=lambda asset_id: _has_gps(container, asset_id),
        ),
    )


def _has_gps(container: Container, asset_id: str) -> bool:
    asset = container.asset_repository.get(asset_id)
    return asset is not None and asset.captured_location is not None


@router.get("/search")
def search_assets(
    q: str = Query(..., min_length=1, max_length=200, description="内容词，例如 桥 / 海边 / 红色"),
    # 空串与不传都表示**全部相册**（1.0 的 `if scope_id:`）。默认值从 "home-default"
    # 改成 "" 是行为修正：原来不传会去查一个叫 home-default 的空间，而它不存在。
    scope_id: str = "",
    limit: int = Query(20, ge=1, le=100),
    container: Container = Depends(get_container),
) -> SearchResponse:
    use_case = build_retrieve_evidence(container)
    result = use_case.execute(EvidenceRequest(text=q, scope_id=scope_id or None, limit=limit))

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
