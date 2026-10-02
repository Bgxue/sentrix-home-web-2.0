"""家庭图谱端点的传输模型（S7b1 · T305）。

字段与 1.0 ``app.py:1648-1656`` 那个响应逐一对齐：``membership`` / ``portrait`` /
``relationships`` 三处 1.0 直接返回**整行**（不像别处会裁），这里也照给全列。

**``run`` 这个键 b1 不返回**（S7b2 补）。1.0 的响应里有它，但 b1 阶段**没有任何东西
会创建 run**——自动触发归 b2、手动路由也归 b2。按「不留恒空的占位键」，返回一个恒
``null`` 的 ``run`` 读起来像「确实没跑过」，而真相是「b1 还没有会跑的路径」。
"""

from __future__ import annotations

from pydantic import BaseModel


class FamilyMembershipPayload(BaseModel):
    """``family_person_memberships`` 一行。"""

    id: str
    scope_id: str
    entity_id: str
    membership: str
    source: str
    confidence: float
    evidence_refs: list[str]
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    supersedes_id: str | None
    created_at: str | None
    updated_at: str | None


class FamilyPortraitPayload(BaseModel):
    """``family_person_portraits`` 一行。**与人物画像那套（``PersonPortrait*``）不是一张表。**"""

    id: str
    scope_id: str
    entity_id: str
    portrait_text: str
    evidence_refs: list[str]
    source: str
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    created_at: str | None
    updated_at: str | None


class FamilyRelationshipPayload(BaseModel):
    """``family_relationships`` 一行。库里一条边是**两行**，所以这里也是两条。"""

    id: str
    scope_id: str
    family_graph_id: str | None
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    inverse_predicate: str
    source: str
    confidence: float
    evidence_refs: list[str]
    inference_run_id: str | None
    state: str
    locked: bool
    revision: int
    supersedes_id: str | None
    created_at: str | None
    updated_at: str | None


class FamilyRepresentativeMedia(BaseModel):
    """``representative_media`` 的一项。**三个键**，1.0 ``db.py:5662-5669`` 逐字。

    这是「**这个人的脸出现在哪些照片**」，不是「这个实体最新的观察」——两条查询的
    连接路径都不同（``face_instances⋈face_clusters⋈assets`` 对
    ``entity_observations⋈observations⋈assets``）。
    """

    id: str
    media_type: str
    file_name: str


class FamilyPersonPayload(BaseModel):
    """``people[]`` 的一项。``display_name`` 的占位是 **「待命名人物」**（1.0 这个端点
    里的原词，与 ``app.py`` 那处的「待命名成员」不是一个串）。"""

    id: str
    scope_id: str
    display_name: str
    membership: FamilyMembershipPayload | None
    portrait: FamilyPortraitPayload | None
    avatar_face_instance_id: str | None
    appearance_count: int
    representative_media: list[FamilyRepresentativeMedia]


class FamilyGraphResponse(BaseModel):
    """``GET /family-graph`` 的响应。

    **``relationships`` 是跨整张图的**：1.0 ``app.py:1655`` 是一个对 ``scope_ids``
    的展平推导——``people`` 走合并后的全部 scope，关系也一样。这条容易看漏，
    因为 ``scope_id`` 那个键仍然只是入参那一个。
    """

    scope_id: str
    people: list[FamilyPersonPayload]
    scope_ids: list[str]
    relationships: list[FamilyRelationshipPayload]
