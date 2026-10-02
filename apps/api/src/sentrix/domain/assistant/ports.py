"""助手域端口：模型端点、会话与回合存储、结果集存储、人物目录、进度事件流。

五个端口对应五个「2.0 要自己实现、但 domain 不能知道怎么实现」的东西。装配一律在
``bootstrap/container.py``，字段按这里的协议声明。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from sentrix.domain.assistant.models import (
    Attachment,
    ChatMessage,
    Conversation,
    Message,
    ModelReply,
    PersonProfile,
    ProgressEvent,
    ToolCall,
    TurnRecord,
)
from sentrix.domain.assistant.result_set import ResultSet
from sentrix.domain.assistant.tools import ToolSpec


class ModelServiceError(RuntimeError):
    """模型服务**这一侧**出的问题：端点不可达 / 超时 / 模型名对不上 / 响应不可识别。

    **它为什么必须是一个 domain 里的类型，而不是让调用方去认异常字符串。**
    规划失败底下有两条完全不同的路（用户 2026-09-25 裁决二）：

    - 模型服务挂了——基础设施故障，用户该被告知去看那个端点；
    - 模型答了，但答的东西规划不出需求——模型能力问题。

    两者的用户可见文案必须不同，而 ``application`` 层按分层契约**不许** import
    ``sentrix.infrastructure``（``pyproject.toml`` 的 ``application-purity``），
    于是它认不到 ``infrastructure`` 里那个异常类。把「服务这一侧」的形状声明在端口
    旁边：实现端口的适配器抛它，用端口的用例 catch 它，两边都只依赖 domain。
    """


class ChatModel(Protocol):
    """OpenAI 兼容的对话模型端点。2.0 只连它，不管理它的进程。

    实现这个端口时，**服务这一侧**的失败抛 ``ModelServiceError``——调用方靠它把
    「基础设施故障」与「模型答得不好」分开收尾。
    """

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ModelReply: ...


class ConversationRepository(Protocol):
    def create(self, *, scope_id: str, title: str, created_at: datetime) -> Conversation: ...

    def get(self, conversation_id: str) -> Conversation | None: ...

    def list_for_scope(self, scope_id: str, limit: int) -> Sequence[Conversation]: ...

    def rename(self, conversation_id: str, title: str, updated_at: datetime) -> bool: ...

    def delete(self, conversation_id: str, updated_at: datetime) -> bool: ...

    def add_message(
        self,
        *,
        conversation_id: str,
        scope_id: str,
        turn_id: str,
        role: str,
        content: str,
        created_at: datetime,
    ) -> Message: ...

    def list_messages(self, conversation_id: str, limit: int) -> Sequence[Message]: ...

    def history_for_prompt(self, conversation_id: str, limit: int) -> Sequence[Message]: ...


class TurnRepository(Protocol):
    """回合的持久化。SSE 的断线续传完全依赖它——1.0 的进度只存在进程内存的一个 dict
    里，页面一刷新就永远拿不到那次回合了，这正是前端不得不轮询的原因之一。"""

    def create(self, record: TurnRecord) -> None: ...

    def get(self, turn_id: str) -> TurnRecord | None: ...

    def claim(self, turn_id: str, updated_at: datetime) -> bool:
        """把 ``pending`` 置为 ``running``。返回是否抢到——并发只允许一个执行者。"""
        ...

    def append_event(self, turn_id: str, event: ProgressEvent) -> None: ...

    def events_after(self, turn_id: str, step_index: int) -> Sequence[ProgressEvent]: ...

    def finish(
        self,
        turn_id: str,
        *,
        status: str,
        termination_reason: str,
        answer: str,
        attachments: Sequence[Attachment],
        requirements: Sequence[Mapping[str, Any]],
        updated_at: datetime,
    ) -> None: ...

    def list_for_conversation(self, conversation_id: str, limit: int) -> Sequence[TurnRecord]: ...


class ResultSetRepository(Protocol):
    def add(self, result_set: ResultSet) -> None: ...

    def get(self, result_set_id: str) -> ResultSet | None: ...


class PersonDirectory(Protocol):
    """已确认人物的目录：有没有这个人、他在家庭图谱里的归属与关系。

    ``membership`` 缺省是 ``"unknown"``（1.0 ``db.py:2875`` 的 ``or "unknown"``）——
    **没有归属行与「归属不明」是同一件事**，不区分。``relationships`` 为空数组同理：
    查到了人但没有关系数据，不是「查不到这个人」。
    """

    def lookup(self, name: str, scope_id: str) -> PersonProfile | None: ...


class TurnEventBroker(Protocol):
    """把正在跑的回合的进度事件实时推给订阅者。

    这是「前端不轮询」的落点：SSE 连接订阅这个流，事件一到就写出去，而不是每 300ms
    去查一次状态。已结束回合的补偿走 ``TurnRepository.events_after``——两条路合起来
    覆盖「实时」与「迟到加入」，1.0 缺的正是后者。
    """

    def open(self, turn_id: str) -> None:
        """回合开始时登记，保证连接比第一条事件更早到达时也订阅得上。"""
        ...

    def publish(self, turn_id: str, event: ProgressEvent) -> None: ...

    def finish(self, turn_id: str) -> None: ...

    def subscribe(self, turn_id: str) -> AsyncIterator[ProgressEvent | None]: ...


class ToolExecutor(Protocol):
    """把一次工具调用变成一份观察。实现放在 application 层。"""

    def execute(self, call: ToolCall, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...
