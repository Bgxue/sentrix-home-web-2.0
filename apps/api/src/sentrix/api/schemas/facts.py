"""事实端点的响应模型。

字段与 ``domain.memories.facts.Fact`` 一一对应，**不做裁剪**：1.0 的 ``/api/facts``
原样返回 ``store.list_facts()`` 的行，前端据 ``status`` 决定要不要提示「待确认」。
少带一个 ``status``，那条提示就永远不会出现，而接口照样 200。
"""

from __future__ import annotations

from pydantic import BaseModel


class FactPayload(BaseModel):
    id: str
    scope_id: str
    subject: str
    predicate: str
    object: str
    status: str
    confidence: float
    evidence_ids: list[str]
    revision: int
    supersedes_fact_id: str | None = None


class FactListResponse(BaseModel):
    # 键名 ``facts`` 照 1.0（``app.py:2542`` 的 ``{"facts": [...]}``）——换名字会让
    # 任何按 1.0 契约写的调用方读到空数组，而空数组看起来就是「没有事实」。
    facts: list[FactPayload]
