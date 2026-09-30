"""知识端点的传输模型。对应 1.0 ``app.py:1921-1932`` 的三块。

**``claims`` 是本端点唯一被 1.0 前端读的块**（``src/app.js:872`` 的
``semanticKnowledgeView``）；``profiles`` 与 ``spaces`` 在前端**零读取**——侧栏的空间
列表走的是另一条路由 ``/api/memory-spaces``（``src/app.js:1161``）。三块都照搬，因为
它们是 1.0 的 API 契约，而且 ``profiles`` 正是 ``GET /entities/{id}`` 里留给 T130 的
那一块。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SemanticProfilePayload(BaseModel):
    """``semantic_profiles`` 一行。三个 JSON 列的键名**去掉了 ``_json`` 后缀**——
    仓储已经把值解成列表，还留着后缀会让人以为拿到的是串。1.0 的 ``_decode`` 是就地
    换值、保留键名的（``event_memory_json`` 仍是这个名字），**这是一处偏离**：
    2.0 的模型层把「解过了」这件事表达在类型与名字上。"""

    id: str
    scope_id: str
    person_id: str
    summary_zh: str
    activity_summary_zh: str
    place_summary_zh: str
    appearance_summary_zh: str
    preference_summary_zh: str
    event_memory: list[Any]
    patterns: list[Any]
    evidence_ids: list[str]
    revision: int
    created_at: str | None
    updated_at: str | None


class SemanticClaimPayload(BaseModel):
    """``semantic_claims`` 一行。``person_id`` **指实体 id 而不是「人」**——1.0 的
    列名如此，且它拿非人物实体的 id 查过这张表（``db.py:4622``）。"""

    id: str
    scope_id: str
    person_id: str
    dimension: str
    predicate: str
    value_text: str
    normalized_value_text: str | None
    normalization_version: str
    support_count: int
    value_entity_id: str | None
    valid_from: str | None
    valid_to: str | None
    supporting_event_ids: list[str]
    evidence_ids: list[str]
    status: str
    confidence: float
    confidence_source: str
    supersedes_claim_id: str | None
    revision: int
    created_at: str | None
    updated_at: str | None


class KnowledgeSpacePayload(BaseModel):
    """记忆空间。**只有四个字段**，而 1.0 返回的是 ``memory_spaces`` 整行（八个）。

    少掉的四个里 ``include_in_people`` 是有语义的，但 2.0 的 ``MemorySpace``
    模型就没带它——那是 S1 定下的形状，不在本次范围内。
    """

    id: str
    name: str
    kind: str
    status: str


class KnowledgeResponse(BaseModel):
    profiles: list[SemanticProfilePayload]
    claims: list[SemanticClaimPayload]
    spaces: list[KnowledgeSpacePayload]
