"""助手端点。只做 HTTP 映射，回合的一切规则在 domain / application 层。

SSE 的形状与设计 §4.3 一致，沿用 1.0 已验证的两类事件：``progress`` 与 ``complete``。
与 1.0 的三处刻意差异：

1. **事件带 ``id:``，且断线可从 ``Last-Event-ID`` 续。** 1.0 的 ``sent`` 是闭包局部变量，
   每次重连都归零并把全部历史重发一遍（``app.py:2982``）。2.0 的 ``step_index`` 在回合内
   单调递增，正好当事件 id 用。
2. **``complete`` 帧不再重发整条进度数组。** 1.0 把它又塞了一遍（``app.py:2989``）。
3. **回合终态是真实的。** 1.0 把模型失败、超时、guard 拦截全部压成 ``status="complete"``
   （``app.py:3433``），只有未捕获的 Python 异常才是 ``error``，前端拿到的每一个失败都是
   一个「成功」的帧。2.0 的 ``status`` 直接来自 ``TurnStatus``。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from sentrix.api.deps import get_container
from sentrix.api.schemas.assistant import (
    AttachmentSummary,
    CompleteEventPayload,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationSummary,
    CreateConversationRequest,
    MessageSummary,
    ProgressEventPayload,
    RenameConversationRequest,
    StartTurnRequest,
    StartTurnResponse,
    TurnSummary,
)
from sentrix.application.assistant.manage_conversations import ManageConversations
from sentrix.application.assistant.run_turn import (
    RunTurn,
    TurnNotFoundError,
    TurnRequest,
)
from sentrix.application.assistant.tools import AssistantToolbox
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.bootstrap.container import Container
from sentrix.domain.assistant.models import Conversation, ProgressEvent, TurnRecord

log = logging.getLogger("sentrix.api.assistant")

router = APIRouter(tags=["assistant"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _conversation_summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        scope_id=conversation.scope_id,
        title=conversation.title,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        last_message_at=conversation.last_message_at.isoformat(),
    )


def _attachments(record: TurnRecord) -> list[AttachmentSummary]:
    return [
        AttachmentSummary(handle=item.handle, asset_id=item.asset_id, file_name=item.file_name)
        for item in record.attachments
    ]


def _turn_summary(record: TurnRecord) -> TurnSummary:
    return TurnSummary(
        id=record.id,
        question=record.question,
        status=record.status.value,
        termination_reason=record.termination_reason,
        answer=record.answer,
        attachments=_attachments(record),
        created_at=record.created_at.isoformat(),
    )


def _run_turn(container: Container) -> RunTurn:
    return RunTurn(
        model=container.chat_model,
        conversations=container.conversation_repository,
        turns=container.turn_repository,
        toolbox=AssistantToolbox(
            retrieval=RetrieveVisual(
                embedder=container.visual_embedder,
                vectors=container.vector_repository,
                index=container.vector_index,
            ),
            assets=container.asset_repository,
            result_sets=container.result_set_repository,
            people=container.person_directory,
        ),
    )


@router.post("/assistant/conversations", status_code=201)
def create_conversation(
    payload: CreateConversationRequest, container: Container = Depends(get_container)
) -> ConversationSummary:
    use_case = ManageConversations(
        conversations=container.conversation_repository, turns=container.turn_repository
    )
    return _conversation_summary(use_case.create(scope_id=payload.scope_id, title=payload.title))


@router.get("/assistant/conversations")
def list_conversations(
    scope_id: str = "home-default", container: Container = Depends(get_container)
) -> ConversationListResponse:
    use_case = ManageConversations(
        conversations=container.conversation_repository, turns=container.turn_repository
    )
    return ConversationListResponse(
        items=[_conversation_summary(item) for item in use_case.list_for_scope(scope_id)]
    )


@router.get("/assistant/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str, container: Container = Depends(get_container)
) -> ConversationDetailResponse:
    use_case = ManageConversations(
        conversations=container.conversation_repository, turns=container.turn_repository
    )
    detail = use_case.detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationDetailResponse(
        conversation=_conversation_summary(detail.conversation),
        messages=[
            MessageSummary(
                id=message.id,
                role=message.role.value,
                content=message.content,
                turn_id=message.turn_id,
                created_at=message.created_at.isoformat(),
            )
            for message in detail.messages
        ],
        turns=[_turn_summary(turn) for turn in detail.turns],
    )


@router.patch("/assistant/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    container: Container = Depends(get_container),
) -> ConversationSummary:
    use_case = ManageConversations(
        conversations=container.conversation_repository, turns=container.turn_repository
    )
    if not use_case.rename(conversation_id, payload.title):
        raise HTTPException(status_code=404, detail="conversation not found")
    detail = use_case.detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return _conversation_summary(detail.conversation)


@router.delete("/assistant/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, container: Container = Depends(get_container)) -> Response:
    use_case = ManageConversations(
        conversations=container.conversation_repository, turns=container.turn_repository
    )
    if not use_case.delete(conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return Response(status_code=204)


@router.post("/assistant/conversations/{conversation_id}/turns", status_code=202)
def start_turn(
    conversation_id: str,
    payload: StartTurnRequest,
    container: Container = Depends(get_container),
) -> StartTurnResponse:
    """建一个回合并立刻返回 ``turn_id``，执行放在工作线程里。

    回合先落库（``pending``）、再由 SSE 订阅，是「前端不轮询」能成立的前提：SSE 端点
    永远有东西可订阅，哪怕连接晚到几百毫秒。
    """
    manage = ManageConversations(
        conversations=container.conversation_repository, turns=container.turn_repository
    )
    run_turn = _run_turn(container)
    try:
        record = run_turn.start(
            TurnRequest(conversation_id=conversation_id, scope_id=payload.scope_id, question=payload.message)
        )
    except TurnNotFoundError as error:
        # application 抛领域错误，映射成什么状态码是这里的事。
        raise HTTPException(status_code=404, detail="conversation not found") from error
    manage.ensure_title(conversation_id, payload.message)

    broker = container.turn_event_broker
    # 先登记再起飞：SSE 可能比工作线程的第一条事件更早连上来。
    broker.open(record.id)

    def worker() -> None:
        try:
            run_turn.run(record.id, emit=lambda event: broker.publish(record.id, event))
        except Exception:
            log.exception("回合执行失败: %s", record.id)
        finally:
            broker.finish(record.id)

    threading.Thread(target=worker, name=f"turn-{record.id}", daemon=True).start()
    return StartTurnResponse(turn_id=record.id, conversation_id=conversation_id, status=record.status.value)


def _format_progress(event: ProgressEvent) -> str:
    data = ProgressEventPayload(
        text=event.text,
        status=event.status.value,
        stage=event.stage.value,
        step_index=event.step_index,
        timestamp=event.timestamp,
        kind=event.kind,
        payload=dict(event.payload),
    ).model_dump()
    return f"id: {event.step_index}\nevent: progress\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _format_complete(record: TurnRecord) -> str:
    data = CompleteEventPayload(
        turn_id=record.id,
        status=record.status.value,
        termination_reason=record.termination_reason,
        answer=record.answer,
        attachments=[
            AttachmentSummary(handle=item.handle, asset_id=item.asset_id, file_name=item.file_name)
            for item in record.attachments
        ],
        requirements=[dict(item) for item in record.requirements],
    ).model_dump()
    return f"event: complete\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resume_index(last_event_id: str) -> int:
    try:
        return max(0, int(last_event_id))
    except (TypeError, ValueError):
        return 0


@router.get("/assistant/turns/{turn_id}/events")
async def turn_events(
    turn_id: str,
    request: Request,
    last_event_id: str = Header(default="", alias="Last-Event-ID"),
    container: Container = Depends(get_container),
) -> StreamingResponse:
    """回合事件的 SSE 流。跑完即关，不需要前端再问一次状态。

    订阅顺序是**先读库、再订阅内存流**：库里是所有已经落盘的事件（断线重连与迟到加入
    都能补），内存流是此刻还没落盘的增量。两边按 ``step_index`` 去重。
    """
    if container.turn_repository.get(turn_id) is None:
        raise HTTPException(status_code=404, detail="turn not found")

    turns = container.turn_repository
    broker = container.turn_event_broker
    cursor = _resume_index(last_event_id)

    async def body() -> AsyncIterator[str]:
        nonlocal cursor
        for stored in turns.events_after(turn_id, cursor):
            cursor = stored.step_index
            yield _format_progress(stored)

        record = turns.get(turn_id)
        if record is not None and record.status.is_terminal:
            yield _format_complete(record)
            return

        async for event in broker.subscribe(turn_id):
            if await request.is_disconnected():
                return
            if event is None:
                break
            if event.step_index <= cursor:
                # 数据库那一轮已经发过了。broker 的 backlog 从回合一开始就记，
                # 重叠是正常的，靠 step_index 去重。
                continue
            cursor = event.step_index
            yield _format_progress(event)

        record = turns.get(turn_id)
        if record is not None:
            yield _format_complete(record)

    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)
