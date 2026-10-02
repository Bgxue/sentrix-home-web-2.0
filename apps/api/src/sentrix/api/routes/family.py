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
from sentrix.api.errors import InvalidRequestError
from sentrix.api.schemas.family import (
    FamilyGraphResponse,
    FamilyMembershipPayload,
    FamilyMembershipRequest,
    FamilyMembershipResponse,
    FamilyPersonPayload,
    FamilyPortraitPayload,
    FamilyPortraitRequest,
    FamilyPortraitResponse,
    FamilyRelationshipPayload,
    FamilyRelationshipRequest,
    FamilyRelationshipResponse,
    FamilyRepresentativeMedia,
    FamilyRetractRequest,
    FamilyRetractResponse,
    FamilyScopeMergeRequest,
    FamilyScopeMergeResponse,
)
from sentrix.bootstrap.container import Container
from sentrix.domain.identity.family import (
    FamilyMembershipRecord,
    FamilyPersonProjection,
    FamilyPortraitRecord,
    FamilyRelationshipRecord,
)

router = APIRouter(tags=["family"])

# 五个写路由**全部**写死的两个值（1.0 逐字）。
# ``confidence=1.0`` 是「用户说的，没有不确定」；``source`` 决定落库行的 ``locked``。
USER_SOURCE = "user_override"
USER_CONFIDENCE = 1.0


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


# ---- 写侧（S7b1 · T306） ---------------------------------------------------------


def _required(message: str, *values: str) -> None:
    """五个写路由共用的必填校验：**任一项为空就 400，消息是 1.0 的原话**。

    1.0 是 ``if not scope_id or not membership:`` 这种裸判断，消息逐字写死——所以这里
    也逐字带出去，不做润色。
    """
    if not all(values):
        raise InvalidRequestError(message)


@router.patch("/family-graph/people/{person_id}/membership")
def update_family_membership(
    person_id: str,
    payload: FamilyMembershipRequest,
    container: Container = Depends(get_container),
) -> FamilyMembershipResponse:
    """改一个人的家庭归属。1.0 ``app.py:1660-1673``。

    ``source="user_override"`` 与 ``confidence=1.0`` 都是**写死的**：这是用户按下的动作，
    落库行 ``locked = 1``，此后模型的推断不许覆盖它。
    """
    scope_id = (payload.scope_id or "").strip()
    membership = (payload.membership or "").strip()
    _required("scope_id and membership are required", scope_id, membership)
    try:
        written = container.family_graph_repository.set_membership(
            scope_id,
            person_id,
            membership,
            source=USER_SOURCE,
            confidence=USER_CONFIDENCE,
            evidence_refs=payload.evidence_refs or (),
        )
    except ValueError as error:
        # **消息不改写**：1.0 是 ``detail=str(error)``，那几句话（unsupported family
        # membership / requires a person entity / …）是调用方用来区分失败的。
        raise InvalidRequestError(str(error)) from error
    return FamilyMembershipResponse(
        ok=True, membership=_membership_payload(written) if written is not None else None
    )


@router.put("/family-graph/relationships")
def update_family_relationship(
    payload: FamilyRelationshipRequest,
    container: Container = Depends(get_container),
) -> FamilyRelationshipResponse:
    """建一对亲属边。1.0 ``app.py:1676-1693``。

    这里**不判两个实体 id 是否给全**——1.0 只判 ``scope_id``，其余四个原样交给仓储，
    由它抛 ``family relationship requires both predicates`` 之类。少判一步的结果是
    错误消息不同，多判一步会挡掉仓储自己那几句话术。
    """
    scope_id = (payload.scope_id or "").strip()
    _required("scope_id is required", scope_id)
    try:
        written = container.family_graph_repository.set_relationship(
            scope_id,
            str(payload.subject_entity_id or ""),
            str(payload.predicate or ""),
            str(payload.object_entity_id or ""),
            str(payload.inverse_predicate or ""),
            source=USER_SOURCE,
            confidence=USER_CONFIDENCE,
            evidence_refs=payload.evidence_refs or (),
        )
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error
    return FamilyRelationshipResponse(
        ok=True, relationships=[_relationship_payload(item) for item in written]
    )


@router.delete("/family-graph/relationships")
def retract_family_relationship(
    payload: FamilyRetractRequest,
    container: Container = Depends(get_container),
) -> FamilyRetractResponse:
    """撤回一对亲属边，**同时落一行 block 挡住模型恢复**。1.0 ``app.py:1696-1708``。"""
    scope_id = (payload.scope_id or "").strip()
    subject_entity_id = (payload.subject_entity_id or "").strip()
    object_entity_id = (payload.object_entity_id or "").strip()
    _required(
        "scope_id, subject_entity_id and object_entity_id are required",
        scope_id,
        subject_entity_id,
        object_entity_id,
    )
    try:
        written = container.family_graph_repository.retract_relationship(
            scope_id, subject_entity_id, object_entity_id
        )
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error
    return FamilyRetractResponse(ok=True, retracted=[_relationship_payload(item) for item in written])


@router.patch("/family-graph/people/{person_id}/portrait")
def update_family_portrait(
    person_id: str,
    payload: FamilyPortraitRequest,
    container: Container = Depends(get_container),
) -> FamilyPortraitResponse:
    """手写一段家庭画像。1.0 ``app.py:1733-1740``。

    **1.0 这个端点没有 ``try/except``**（另外几个都有）——因为 ``write_family_portrait``
    不抛 ``ValueError``（它连 ``source`` 都不校验）。照搬：这里也不接。
    """
    scope_id = (payload.scope_id or "").strip()
    text = (payload.portrait_text or "").strip()
    _required("scope_id and portrait_text are required", scope_id, text)
    written = container.family_graph_repository.write_portrait(
        scope_id,
        person_id,
        text,
        source=USER_SOURCE,
        evidence_refs=payload.evidence_refs or (),
    )
    return FamilyPortraitResponse(
        ok=True, portrait=_portrait_payload(written) if written is not None else None
    )


@router.post("/family-graph/scopes/merge")
def merge_family_graph_scopes(
    payload: FamilyScopeMergeRequest,
    container: Container = Depends(get_container),
) -> FamilyScopeMergeResponse:
    """把若干 scope 并成一张图。1.0 ``app.py:1725-1730``。

    **响应没有 ``ok`` 键**：1.0 直接 ``return store.merge_family_scopes(...)``，
    而那个函数返回 ``{"graph_id", "scope_ids"}``。与另外四个写路由不一致，照搬。
    """
    try:
        graph_id, scope_ids = container.family_graph_repository.merge_scopes(payload.scope_ids or [])
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error
    return FamilyScopeMergeResponse(graph_id=graph_id, scope_ids=list(scope_ids))
