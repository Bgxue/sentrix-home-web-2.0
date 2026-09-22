"""助手端点的请求/响应模型。字段与设计 §4.3 的 SSE 契约对齐。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    id: str
    scope_id: str
    title: str
    created_at: str
    updated_at: str
    last_message_at: str


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]


class CreateConversationRequest(BaseModel):
    scope_id: str = "home-default"
    title: str = ""


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class MessageSummary(BaseModel):
    id: str
    role: str
    content: str
    turn_id: str
    created_at: str


class AttachmentSummary(BaseModel):
    handle: str
    asset_id: str
    file_name: str


class TurnSummary(BaseModel):
    id: str
    question: str
    status: str
    termination_reason: str
    answer: str
    attachments: list[AttachmentSummary]
    created_at: str


class ConversationDetailResponse(BaseModel):
    conversation: ConversationSummary
    messages: list[MessageSummary]
    turns: list[TurnSummary]


class StartTurnRequest(BaseModel):
    # 一条空消息不是「一轮空对话」，是一次调用方错误，所以这里直接挡在 422。
    message: str = Field(min_length=1, max_length=2000)
    scope_id: str = "home-default"
    conversation_id: str = ""


class StartTurnResponse(BaseModel):
    turn_id: str
    conversation_id: str
    status: str


class ProgressEventPayload(BaseModel):
    """``progress`` 事件的 ``data``。

    字段集沿用 1.0（``runtime.py:1664-1687``），并补上 ``kind`` 与 ``payload``。
    ``step_index`` 同时是 SSE 的 ``id:`` 值，于是 ``Last-Event-ID`` 断线续传天然可用。
    """

    text: str
    status: str
    stage: str
    step_index: int
    timestamp: str
    kind: str = "status"
    payload: dict[str, Any] = Field(default_factory=dict)


class CompleteEventPayload(BaseModel):
    """``complete`` 事件的 ``data``。

    与 1.0 的一处刻意差异：**不再把整条 ``public_progress`` 又塞一遍**。1.0 的 ``complete``
    帧把已经逐条发过的进度事件完整重发（``app.py:2989``），是纯粹的带宽浪费。
    """

    type: str = "complete"
    turn_id: str
    status: str
    termination_reason: str
    answer: str
    attachments: list[AttachmentSummary]
    requirements: list[dict[str, Any]]
    error: str = ""
