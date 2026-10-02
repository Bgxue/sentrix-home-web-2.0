"""家庭图谱端点：关系图读取。

对应 1.0 ``app.py:1648`` 的 ``GET /api/family-graph``。

**读侧直连仓储，不建用例**——与 ``routes/identity.py`` 的实体目录同一体例：三步都是
「按 scope 取，再把记录搬进响应模型」，没有要落地的领域决定。真正带规则的
``family_graph_people``（1.0 那个函数就在 store 里）落在仓储层。

**``run`` 这个键 b1 不返回。** 1.0 的响应里有它（``latest_family_analysis_run``），
但 b1 阶段没有任何东西会创建 run，返回恒 ``null`` 的键读起来像「确实没跑过」。
S7b2 补上它、同时补上会创建 run 的那两条路。

**``relationships`` 跨整张图。** 1.0 是一个对 ``scope_ids`` 的展平推导，所以合并过
scope 之后关系也跟着铺开——而 ``scope_id`` 那个键仍然只是入参那一个。照搬。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from sentrix.api.deps import get_container
from sentrix.api.schemas.family import (
    FamilyGraphResponse,
    FamilyMembershipPayload,
    FamilyPersonPayload,
    FamilyPortraitPayload,
    FamilyRelationshipPayload,
    FamilyRepresentativeMedia,
)
from sentrix.bootstrap.container import Container
from sentrix.domain.identity.family import (
    FamilyMembershipRecord,
    FamilyPersonProjection,
    FamilyPortraitRecord,
    FamilyRelationshipRecord,
)

router = APIRouter(tags=["family"])


def _optional_isoformat(value: datetime | None) -> str | None:
    """与 ``routes/identity.py`` / ``routes/relationships.py`` 的同名私有函数逐字相同。"""

    return value.isoformat() if value is not None else None


def _membership_payload(record: FamilyMembershipRecord) -> FamilyMembershipPayload:
    return FamilyMembershipPayload(
        id=record.id,
        scope_id=record.scope_id,
        entity_id=record.entity_id,
        membership=record.membership,
        source=record.source,
        confidence=record.confidence,
        evidence_refs=list(record.evidence_refs),
        inference_run_id=record.inference_run_id,
        state=record.state,
        locked=record.locked,
        revision=record.revision,
        supersedes_id=record.supersedes_id,
        created_at=_optional_isoformat(record.created_at),
        updated_at=_optional_isoformat(record.updated_at),
    )


def _portrait_payload(record: FamilyPortraitRecord) -> FamilyPortraitPayload:
    return FamilyPortraitPayload(
        id=record.id,
        scope_id=record.scope_id,
        entity_id=record.entity_id,
        portrait_text=record.portrait_text,
        evidence_refs=list(record.evidence_refs),
        source=record.source,
        inference_run_id=record.inference_run_id,
        state=record.state,
        locked=record.locked,
        revision=record.revision,
        created_at=_optional_isoformat(record.created_at),
        updated_at=_optional_isoformat(record.updated_at),
    )


def _relationship_payload(record: FamilyRelationshipRecord) -> FamilyRelationshipPayload:
    return FamilyRelationshipPayload(
        id=record.id,
        scope_id=record.scope_id,
        family_graph_id=record.family_graph_id,
        subject_entity_id=record.subject_entity_id,
        predicate=record.predicate,
        object_entity_id=record.object_entity_id,
        inverse_predicate=record.inverse_predicate,
        source=record.source,
        confidence=record.confidence,
        evidence_refs=list(record.evidence_refs),
        inference_run_id=record.inference_run_id,
        state=record.state,
        locked=record.locked,
        revision=record.revision,
        supersedes_id=record.supersedes_id,
        created_at=_optional_isoformat(record.created_at),
        updated_at=_optional_isoformat(record.updated_at),
    )


def _person_payload(person: FamilyPersonProjection) -> FamilyPersonPayload:
    return FamilyPersonPayload(
        id=person.id,
        scope_id=person.scope_id,
        display_name=person.display_name,
        membership=(_membership_payload(person.membership) if person.membership is not None else None),
        portrait=_portrait_payload(person.portrait) if person.portrait is not None else None,
        avatar_face_instance_id=person.avatar_face_instance_id,
        appearance_count=person.appearance_count,
        representative_media=[
            FamilyRepresentativeMedia(
                id=str(item["id"]),
                media_type=str(item["media_type"]),
                file_name=str(item["file_name"]),
            )
            for item in person.representative_media
        ],
    )


@router.get("/family-graph")
def family_graph(
    scope_id: str,
    container: Container = Depends(get_container),
) -> FamilyGraphResponse:
    """一张家庭图谱。1.0 ``app.py:1648-1656``。

    ``scope_id`` 是**必填**（1.0 的签名里没有默认值），合并过 scope 时它会顺着
    ``family_scope_groups`` 展开成 ``scope_ids``。
    """
    repository = container.family_graph_repository
    scope_ids = repository.graph_scope_ids(scope_id)
    return FamilyGraphResponse(
        scope_id=scope_id,
        people=[_person_payload(item) for item in repository.family_graph_people(scope_ids)],
        scope_ids=list(scope_ids),
        relationships=[
            _relationship_payload(item)
            for item_scope_id in scope_ids
            for item in repository.list_effective_relationships(item_scope_id)
        ],
    )
