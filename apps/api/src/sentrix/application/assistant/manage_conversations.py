"""会话的增删改查。

对应 1.0 ``backend/agent_conversation.py`` 的 ``create_conversation`` / ``list_conversations``
/ ``auto_title`` / ``ensure_title`` / ``delete_conversation``。

标题是**确定性截断**，不调模型——1.0 也是这个做法（``agent_conversation.py:107``：
``text[:24] + "…"``）。为了给会话起个名字再打一次模型，是拿一次可能失败的调用换一行
本来就够用的文本。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.assistant.models import Conversation, Message, TurnRecord
from sentrix.domain.assistant.ports import ConversationRepository, TurnRepository

TITLE_MAX_CHARS = 24
DEFAULT_TITLE = "新对话"


@dataclass(frozen=True, slots=True)
class ConversationDetail:
    conversation: Conversation
    messages: Sequence[Message]
    turns: Sequence[TurnRecord]


def title_from(question: str) -> str:
    text = (question or "").strip().replace("\n", " ")
    if not text:
        return DEFAULT_TITLE
    return text[:TITLE_MAX_CHARS] + ("…" if len(text) > TITLE_MAX_CHARS else "")


class ManageConversations:
    def __init__(self, *, conversations: ConversationRepository, turns: TurnRepository) -> None:
        self._conversations = conversations
        self._turns = turns

    def create(self, *, scope_id: str, title: str = "") -> Conversation:
        return self._conversations.create(
            scope_id=scope_id, title=title or DEFAULT_TITLE, created_at=datetime.now(UTC)
        )

    def list_for_scope(self, scope_id: str, limit: int = 50) -> Sequence[Conversation]:
        return self._conversations.list_for_scope(scope_id, limit)

    def detail(self, conversation_id: str, limit: int = 200) -> ConversationDetail | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            return None
        return ConversationDetail(
            conversation=conversation,
            messages=self._conversations.list_messages(conversation_id, limit),
            turns=self._turns.list_for_conversation(conversation_id, limit),
        )

    def rename(self, conversation_id: str, title: str) -> bool:
        return self._conversations.rename(conversation_id, title, datetime.now(UTC))

    def delete(self, conversation_id: str) -> bool:
        return self._conversations.delete(conversation_id, datetime.now(UTC))

    def ensure_title(self, conversation_id: str, question: str) -> None:
        """首轮提问之后把「新对话」换成问题摘要。已经是别的内容就不动。"""
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.title not in {DEFAULT_TITLE, ""}:
            return
        self._conversations.rename(conversation_id, title_from(question), datetime.now(UTC))
