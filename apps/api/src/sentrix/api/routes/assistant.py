"""助手端点。只做 HTTP 映射，回合的一切规则在 domain / application 层。

会话的增删改查，以及「建一个回合并立刻返回 turn_id」——执行放在工作线程里，
回合先落库（``pending``）再由事件流订阅。
"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Response

from sentrix.api.deps import get_container
from sentrix.api.schemas.assistant import (
    AttachmentSummary,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationSummary,
    CreateConversationRequest,
    MessageSummary,
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
from sentrix.domain.assistant.models import Conversation, TurnRecord

log = logging.getLogger("sentrix.api.assistant")

router = APIRouter(tags=["assistant"])


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
