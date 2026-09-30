"""知识端点：语义画像 / 声明 / 记忆空间（S6 · T130）。

对应 1.0 ``app.py:1921-1932`` 的 ``GET /api/knowledge``。**只做 HTTP 映射**——
三条取数各自在仓储里，``scope_id`` 对 claims 的过滤在 Python 里做（1.0 也是这样，
而且是在 ``LIMIT 1000`` **之后**做，两步的顺序是行为的一部分）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from sentrix.api.deps import get_container
from sentrix.api.schemas.knowledge import (
    KnowledgeResponse,
    KnowledgeSpacePayload,
    SemanticClaimPayload,
    SemanticProfilePayload,
)
from sentrix.bootstrap.container import Container
from sentrix.domain.identity.models import SemanticClaimRecord, SemanticProfileRecord

router = APIRouter(tags=["knowledge"])

# 1.0 ``app.py:1923`` 的 ``list_semantic_claims(person_id, 1000)``。这个数是**扫描窗口**，
# 不是「某个人最多有几条」——见下面 ``scope_id`` 那一层的顺序。
CLAIMS_SCAN_LIMIT = 1000


def _profile_payload(record: SemanticProfileRecord) -> SemanticProfilePayload:
    return SemanticProfilePayload(
        id=record.id,
        scope_id=record.scope_id,
        person_id=record.person_id,
        summary_zh=record.summary_zh,
        activity_summary_zh=record.activity_summary_zh,
        place_summary_zh=record.place_summary_zh,
        appearance_summary_zh=record.appearance_summary_zh,
        preference_summary_zh=record.preference_summary_zh,
        event_memory=list(record.event_memory),
        patterns=list(record.patterns),
        evidence_ids=list(record.evidence_ids),
        revision=record.revision,
        created_at=record.created_at.isoformat() if record.created_at else None,
        updated_at=record.updated_at.isoformat() if record.updated_at else None,
    )


def _claim_payload(record: SemanticClaimRecord) -> SemanticClaimPayload:
    return SemanticClaimPayload(
        id=record.id,
        scope_id=record.scope_id,
        person_id=record.person_id,
        dimension=record.dimension,
        predicate=record.predicate,
        value_text=record.value_text,
        normalized_value_text=record.normalized_value_text,
        normalization_version=record.normalization_version,
        support_count=record.support_count,
        value_entity_id=record.value_entity_id,
        valid_from=record.valid_from,
        valid_to=record.valid_to,
        supporting_event_ids=list(record.supporting_event_ids),
        evidence_ids=list(record.evidence_ids),
        status=record.status,
        confidence=record.confidence,
        confidence_source=record.confidence_source,
        supersedes_claim_id=record.supersedes_claim_id,
        revision=record.revision,
        created_at=record.created_at.isoformat() if record.created_at else None,
        updated_at=record.updated_at.isoformat() if record.updated_at else None,
    )


@router.get("/knowledge")
def get_knowledge(
    person_id: str | None = None,
    scope_id: str | None = None,
    container: Container = Depends(get_container),
) -> KnowledgeResponse:
    """语义知识的三块。1.0 ``app.py:1921-1932``。

    **``profiles`` 那一支是 N+1**：不给 ``person_id`` 时对 scope 内每个实体轮流查一次
    （1.0 就是一个生成器加 ``if item`` 过滤）。照搬——它的顺序因此等于实体的
    ``updated_at DESC``。
    """
    profiles: list[SemanticProfileRecord] = []
    if person_id:
        found = container.semantic_repository.get_profile(person_id)
        if found is not None:
            profiles.append(found)
    else:
        for entity in container.entity_repository.list_entities(scope_id=scope_id):
            found = container.semantic_repository.get_profile(entity.id)
            if found is not None:
                profiles.append(found)

    # **先按窗口取，再按 scope 过滤。** 1.0 的 ``scope_id`` 那一层是在
    # ``list_semantic_claims(..., 1000)`` **之后**做的 Python 过滤，两步的顺序是行为的一部分
    # （反过来的话，一个 scope 会拿到比 1.0 多的行）。
    claims = container.semantic_repository.list_claims(person_id, limit=CLAIMS_SCAN_LIMIT)
    if scope_id:
        claims = [claim for claim in claims if claim.scope_id == scope_id]

    return KnowledgeResponse(
        profiles=[_profile_payload(item) for item in profiles],
        claims=[_claim_payload(item) for item in claims],
        spaces=[
            KnowledgeSpacePayload(id=space.id, name=space.name, kind=space.kind, status=space.status)
            for space in container.spaces.list_all()
        ],
    )
