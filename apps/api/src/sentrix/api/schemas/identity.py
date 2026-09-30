"""身份端点的传输模型。字段与用例的结论、与仓储的记录一一对应。

**``crop_url`` 是服务端拼好的整串。** 1.0 由前端拼 ``/api/face-instances/{id}/crop``
（``App.vue`` 里那几处模板字符串），于是路径一改就要同时改两处；2.0 把它当作**交付内容**
的一部分给出去，前端只负责取。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FaceCropRef(BaseModel):
    """簇里的一张样本脸。``crop_url`` 直接可打开，前端不再拼路径。"""

    face_instance_id: str
    asset_id: str
    observation_id: str
    file_name: str
    media_type: str
    bbox: list[float]
    detection_confidence: float
    quality: float
    crop_url: str


class FaceClusterSummary(BaseModel):
    """一簇脸。``canonical_name`` / ``family_role`` 为空是**未命名**，不是缺字段。"""

    id: str
    scope_id: str
    status: str
    entity_id: str | None
    canonical_name: str | None
    family_role: str | None
    entity_status: str | None
    member_count: int
    confidence: float
    revision: int
    created_at: str | None
    updated_at: str | None
    # 1.0 ``list_face_clusters`` 现算的两个派生布尔（``db.py:5146``）：能不能审、是不是
    # 只有一张脸的孤证。前端据此决定按钮的可用状态，规则留在服务端。
    reviewable: bool
    single_sample: bool
    samples: list[FaceCropRef]


class FaceClusterListResponse(BaseModel):
    clusters: list[FaceClusterSummary]


class ConfirmClusterRequest(BaseModel):
    # 刻意**不加** ``min_length``：空名字是**用例的判据**（400），不是请求格式错（422）。
    # 1.0 的路由先 strip 再判空，把它写成 pydantic 约束会让状态码从 400 变成 422——
    # 同一个产品行为换了一个码，调用方按码分支的地方就落空了。
    name: str = ""
    family_role: str | None = None


class PersonPayload(BaseModel):
    """一个人物实体。``confirmed`` 与 ``status`` 并存是 1.0 的契约（``app.py:1518``）。"""

    id: str
    scope_id: str
    canonical_name: str
    status: str
    family_role: str | None
    summary: str
    confidence: float
    confirmed: bool
    aliases: list[str]


class ConfirmClusterResponse(BaseModel):
    """``merged_into`` 非空表示这次确认并进了另一个已存在的人（同名合并）。"""

    entity: PersonPayload
    merged_into: str | None


class RejectClusterResponse(BaseModel):
    """``rejected_entity_id`` 为空表示这一簇本来就没有候选实体，调用没有改动任何行。"""

    cluster_id: str
    rejected_entity_id: str | None


class MergeClustersRequest(BaseModel):
    # 空 id 判在路由上（400），不是 pydantic 的 422：1.0 判的就是 400，而空 id 走到
    # 仓储会变成一条「这个簇不存在」的 404——把调用方错误报成资源缺失。
    target_cluster_id: str = ""
    source_cluster_id: str = ""


class SplitClusterRequest(BaseModel):
    face_instance_id: str = ""


class PersonListResponse(BaseModel):
    people: list[PersonPayload]


class PersonDetailResponse(BaseModel):
    entity: PersonPayload
    avatar_face_instance_id: str | None
    clusters: list[FaceClusterSummary]


class RenamePersonRequest(BaseModel):
    name: str = ""


class RenamePersonResponse(BaseModel):
    entity: PersonPayload
    aliases: list[str]


class BatchConfirmItem(BaseModel):
    # 同样不加约束：缺 person_id / name 的条目要**逐件失败**（1.0 的 ``failed`` 数组），
    # 而不是让整批请求 422——那会把其余选中的条目一起丢掉。
    person_id: str = ""
    name: str = ""
    family_role: str | None = None


class BatchConfirmRequest(BaseModel):
    items: list[BatchConfirmItem] = Field(..., min_length=1)


class BatchConfirmOutcome(BaseModel):
    person_id: str
    entity_id: str
    name: str
    family_role: str | None
    # 这一次确认是不是走了同名合并。1.0 回的是 ``native.get("merged_into")`` 的真假。
    merged: bool


class BatchConfirmFailure(BaseModel):
    person_id: str
    name: str
    error: str


class BatchConfirmResponse(BaseModel):
    """批量确认**不做全或无**：一件失败不影响其余，逐件回执。

    1.0 就是这个形状（``app.py:2226`` 的 ``confirmed`` / ``failed`` / ``count``），
    理由是批量确认的输入来自用户逐个点选，一件失败就把整批回滚会丢掉其余选择。
    """

    confirmed: list[BatchConfirmOutcome]
    failed: list[BatchConfirmFailure]
    count: int


class SeedIdentityResponse(BaseModel):
    """预置身份的结论。``photos_without_faces`` 单列，是为了让「静默跳过」可见。"""

    entity_id: str
    cluster_id: str
    name: str
    family_role: str | None
    aliases: list[str]
    face_count: int
    photos_without_faces: int


class EntitySummaryPayload(BaseModel):
    """实体目录里的一条。

    1.0 ``/api/entities`` 返回的是**扁平的**实体记录：``entities`` 表的十三列 +
    ``_decorate_entities`` 补的派生字段 + ``public_entity`` 补的 ``private``。
    这里保持扁平，不套一层 ``entity`` 子对象——套着的是 ``GET /entities/{id}``
    （1.0 ``get_entity_detail`` 返回 ``{"entity": {...}, ...}``），两者形状本来就不同。

    两个**只在特定类型上才有**的键，2.0 用 ``None`` 表达「不适用」而不是把键省掉：
    ``single_sample`` 只有人物有（1.0 非人物那支不写它），``private`` 只有地点有
    （1.0 ``public_entity`` 对非地点原样返回）。1.0 是**键缺席**、2.0 是 ``null``——
    这是一处偏离，理由是固定响应模型下「缺席」只能靠 ``exclude_none`` 或 ``null``，
    而全仓没有 ``exclude_none`` 的先例（``PersonDetailResponse.avatar_face_instance_id``
    同样用 ``None``）。消费方按真值判断时两者等价。

    **没有 ``relationship_count``。** 1.0 有，但它来自 ``relationships`` 表，读路径是 S7。
    按「不留恒空的占位键」，这个键不出现在响应里——恒 0 会让「S7 还没做」读起来像
    「这个实体确实没有关系」。
    """

    id: str
    scope_id: str
    entity_type: str
    canonical_name: str
    status: str
    family_role: str | None
    summary: str
    confidence: float
    identity_state: str
    role_state: str
    name_state: str
    created_at: str | None
    updated_at: str | None
    cluster_count: int
    photo_count: int
    mention_count: int
    evidence_count: int
    reviewable: bool
    single_sample: bool | None
    avatar_face_instance_id: str | None
    preview_asset_id: str | None
    preview_file_name: str | None
    preview_media_type: str | None
    private: bool | None


class EntityListResponse(BaseModel):
    """``GET /entities`` 的响应。1.0 的外层键就是 ``entities``（``app.py:1868``）。"""

    entities: list[EntitySummaryPayload]


class EntityPropertyRequest(BaseModel):
    """``PUT /entities/{id}/properties/{key}`` 的请求体。

    **两个字段都可省，都不是笔误。** 1.0 的路由是裸 ``dict`` 收的（``app.py:1960``），
    取值用 ``payload.get("value")`` 与 ``payload.get("evidence_ids") or []``：
    ``{}`` 会**写入成功**（值落成 ``null``），``"evidence_ids": null`` 等同于没给。
    把它们写成必填字段，这两种请求就变成 422——同一个产品行为换了一个码。
    与 ``ConfirmClusterRequest`` / ``BatchConfirmItem`` 是同一条理由。
    """

    value: Any = None
    evidence_ids: list[str] | None = None


class EntityPropertyPayload(BaseModel):
    """``entity_properties`` 一行。

    这 12 个键就是 1.0 ``db.py:4652-4666`` 的 ``_property_row``：它把 ``value_json`` /
    ``evidence_ids_json`` 解出来换成 ``value`` / ``evidence_ids``，其余列原样。
    响应体**没有外层包裹**（1.0 ``app.py:1962`` 直接返回那个 dict），所以这里也不包。
    """

    id: str
    entity_id: str
    property_key: str
    value: Any
    # ``source`` 是行为的一部分，不是日志：``maintain_property`` 的「同值抬置信度」与
    # 「用户值优先」两条判据都按它分派。用户写下的那一条恒为 ``user``。
    source: str
    confidence: float
    status: str
    evidence_ids: list[str]
    supersedes_property_id: str | None
    revision: int
    created_at: str | None
    updated_at: str | None
