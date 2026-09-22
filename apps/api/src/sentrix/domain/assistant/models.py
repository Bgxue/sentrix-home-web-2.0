"""助手域的通用模型：消息、工具调用、模型回复、进度事件、会话与回合记录。

纯数据，零 IO。这里刻意不认识 HTTP、也不认识 SQLite——模型端点由 infrastructure
适配，domain 只声明它需要什么形状的回复。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TurnStatus(StrEnum):
    """回合的状态。

    1.0 只有 ``complete`` / ``cancelled`` / ``error`` 三种对外状态，而 ``error`` 只在
    Python 未捕获异常时才出现——模型失败、超时、guard 拦截全部被 ``app.py:3433`` 压成
    ``complete``。前端拿到的每一个失败都是一个「成功」的帧。这里把业务终态显式列出来。
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self not in {TurnStatus.PENDING, TurnStatus.RUNNING}


class TerminationReason(StrEnum):
    """回合为什么结束。**这个枚举里没有 None**。

    1.0 的 25,112 条轨迹里有 11,304 条读不到 ``termination_reason``。根因不是某一处
    忘了赋值，而是两条独立的路：2026-09-10 之前的 ``save_trajectory`` 根本不带这个键
    （11,304 条的 result_json 只有 answer/intent/telemetry 三个键），以及政策拒答那条
    提前 ``return`` 绕过了 ``_classify_termination``（72 条空串）。更糟的是
    ``_classify_termination`` 在收尾处**无条件覆盖**，13 个精心设置的 reason 里 8 个
    是死代码，而没有任何东西会因此报错。

    2.0 的做法：``TurnResult.termination_reason`` 的类型就是这个枚举（不是 ``str``、
    更不是 ``str | None``），漏赋值是类型错误而不是运行时的沉默；并且**没有任何一处
    覆盖它**——每个出口自己声明为什么结束。
    """

    ANSWERED = "answered"
    ANSWERED_WITH_GAP = "answered_with_gap"
    EMPTY_ANSWER = "empty_answer"
    NO_TOOL_CALL_STOPPED = "no_tool_call_stopped"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
    STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
    TIMEOUT = "timeout"
    MODEL_ERROR = "model_error"
    GUARD_BLOCKED = "guard_blocked"
    CANCELLED = "cancelled"


class ProgressStage(StrEnum):
    """进度事件的阶段。取值集与设计 §4.3 一致，沿用 1.0 已验证的那一套。"""

    THINKING = "thinking"
    PLANNING = "planning"
    INSPECTING = "inspecting"
    ACTION = "action"
    TOOL_RESULT = "tool_result"
    RECOVERING = "recovering"
    FINALIZING = "finalizing"
    TERMINAL = "terminal"


class ProgressStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    DENIED = "denied"
    PARTIAL = "partial"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class ToolArgumentError(ValueError):
    """模型给的参数不是合法的 JSON 对象。"""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求的一次工具调用。

    ``arguments_json`` 保持模型给的原始字符串而不是就地解析：解析失败本身是有信息量的
    事件（1.0 的 ``parse_failure`` 就出自这里），调用方需要知道「模型写了什么」而不是
    只看到一句「参数非法」。
    """

    call_id: str
    name: str
    arguments_json: str = ""

    def parse(self) -> Mapping[str, Any]:
        raw = self.arguments_json.strip() or "{}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ToolArgumentError(str(error)) from error
        if not isinstance(parsed, dict):
            raise ToolArgumentError(f"参数必须是 JSON 对象，收到 {type(parsed).__name__}")
        return parsed


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条发往模型的消息。

    ``tool_calls`` 与 ``tool_call_id`` 是原生 OpenAI 工具协议的两个方向：assistant
    消息带 ``tool_calls``，随后那条 ``role=tool`` 的消息用 ``tool_call_id`` 指回它。
    1.0 把动作序列化成普通文本、把工具结果塞进 user 消息（``runtime.py:2108`` 的注释
    解释了原因），代价是一整套 JSON 修复器与「输出无法解析」的收尾。
    """

    role: ChatRole
    content: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """一条面向用户的进度事件。

    字段集沿用 1.0（``text`` / ``status`` / ``stage`` / ``step_index`` / ``timestamp``）
    并补上 ``kind`` 与 ``payload``。``step_index`` 在**回合内**从 1 单调递增，跨回合不唯一
    ——1.0 的前端拿它当 key 时必须先限定回合作用域，2.0 把它同时用作 SSE 的 ``id:``
    行，于是 ``Last-Event-ID`` 断线续传天然可用。
    """

    step_index: int
    stage: ProgressStage
    status: ProgressStatus
    text: str
    kind: str = "status"
    timestamp: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Attachment:
    """交付给用户的一张可打开的照片。"""

    handle: str
    asset_id: str
    file_name: str


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    scope_id: str
    title: str
    state: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    conversation_id: str
    scope_id: str
    turn_id: str
    role: ChatRole
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """一次回合的持久化投影。``termination_reason`` 为空串表示「还没结束」。"""

    id: str
    conversation_id: str
    scope_id: str
    question: str
    status: TurnStatus
    created_at: datetime
    updated_at: datetime
    answer: str = ""
    termination_reason: str = ""
    attachments: tuple[Attachment, ...] = ()
    requirements: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class PersonProfile:
    """一位已确认人物的档案。S3 只有家庭角色这一格，其余在 S5 落地。

    1.0 的 ``get_person_profile`` 返回 membership / relationships / patterns /
    recent_events / claims / profile_text 七块（``tools.py:2665-2678``），全部来自
    entities / person_patterns / person_event_memory / semantic_claims 四张表——那些表
    在 2.0 属于 S5。**工具的描述因此必须同步收窄**：1.0 的描述承诺了「常去地点/常做
    活动/常同行、近期事件与语义声明」，照抄到 S3 就是让模型去问一堆拿不到的数据。
    """

    name: str
    family_role: str
    confirmed: bool
