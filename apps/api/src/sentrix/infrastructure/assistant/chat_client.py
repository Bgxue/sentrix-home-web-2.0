"""8100 上的 llama.cpp 对话端点（OpenAI 兼容 ``/v1``）。

对应 1.0 ``backend/model_clients.py`` 的 OpenAI 分支。三处与 1.0 的差异，每一处都指向
一个实测出的失败：

1. **原生 ``tools`` / ``tool_calls``，不再让模型在正文里写 JSON。**
   1.0 要模型输出 ``{"action": "tool_call", ...}`` 这样的正文 JSON，为此写了一套
   150 行的解析修复器（``runtime.py:1586-1661``：剥围栏、补花括号、补引号、删尾随逗号），
   失败三次就整轮以「输出无法解析」收场。实测 8100 的 llama.cpp 原生支持 tools
   （``finish_reason="tool_calls"``），这一整套解析器连同它的失败模式一起消失。
2. **``messages[0]`` 一定是 system，且只有一条。** 1.0 的 ``400 System message must be
   at the beginning`` 就是这么来的——中途 append 一条 ``role=system``，宽松的 Gemma
   模板容忍，严格的 Qwen 模板直接 400（PhotoBench 上 84% 的题在第二步 preflight 挂掉）。
   1.0 的修法是 ``_merge_system_constraint`` 把新约束折进 messages[0]；2.0 更简单：
   系统提示词只在开头拼一次，之后不再往里加东西。
3. **``max_tokens`` 按剩余上下文算。** 1.0 是 ``min(1501, 4400 - prompt_tokens)``，下限
   64（``app.py:2680-2692``）。8100 的 ``--ctx-size`` 是 8192，沿用同一个公式。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from sentrix.domain.assistant.models import ChatMessage, ChatRole, ModelReply, ToolCall
from sentrix.domain.assistant.tools import ToolSpec, openai_tools

# 1.0 app.py:2680-2692 的三个人工常数：给回答留的 token 预算上限、给提示词留的总量、
# 以及无论上下文多满都必须留下的最小生成量。
MAX_ANSWER_TOKENS = 1501
PROMPT_BUDGET_TOKENS = 4400
MIN_ANSWER_TOKENS = 64


class ChatModelError(RuntimeError):
    """模型端点不可用或返回了无法理解的东西。调用方据此收尾，不要吞掉。"""


def _dump_message(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.role is ChatRole.ASSISTANT and message.tool_calls:
        # 带 tool_calls 的 assistant 消息，``content`` 必须是 **null** 而不是空串。
        # 实测 8100（gemma4 模板）：写成 ``""`` 时工具结果回填后的下一轮，模型直接吐
        # 一个 EOS（``finish_reason="stop"``、1 个 completion token、content 为空），
        # 整个回合因此以「模型不可用」收场——而模型其实一点问题都没有。
        # 这也是 OpenAI 的约定：有 tool_calls 时 content 可为 null。
        payload["content"] = message.content or None
        payload["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments_json},
            }
            for call in message.tool_calls
        ]
    if message.role is ChatRole.TOOL:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _read_tool_calls(raw: object) -> tuple[ToolCall, ...]:
    if not isinstance(raw, list):
        return ()
    calls: list[ToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        function = item.get("function")
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments")
        calls.append(
            ToolCall(
                call_id=str(item.get("id") or f"call_{index}"),
                name=name,
                arguments_json=arguments if isinstance(arguments, str) else json.dumps(arguments),
            )
        )
    return tuple(calls)


class OpenAIChatModel:
    """同步 HTTP 客户端。2.0 不管理 8100 的进程，只连它。"""

    def __init__(self, base_url: str, model: str, *, timeout_s: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ModelReply:
        if not messages or messages[0].role is not ChatRole.SYSTEM:
            raise ChatModelError("对话必须以一条 system 消息开头（严格 chat template 的硬要求）")
        if any(message.role is ChatRole.SYSTEM for message in messages[1:]):
            raise ChatModelError("system 消息只能出现在开头；后续约束请折进第一条")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_dump_message(message) for message in messages],
            "temperature": 0.0,
            "stream": False,
            "max_tokens": self._max_tokens(messages),
        }
        if tools:
            payload["tools"] = list(openai_tools(tools))
            payload["tool_choice"] = "auto"

        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.post(f"{self._base_url}/chat/completions", json=payload)
        except httpx.HTTPError as error:
            raise ChatModelError(f"模型端点不可达：{error}") from error

        if response.status_code != 200:
            raise ChatModelError(f"模型返回 {response.status_code}：{response.text[:200]}")

        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError) as error:
            raise ChatModelError(f"模型响应结构不可识别：{response.text[:200]}") from error

        usage = body.get("usage") or {}
        return ModelReply(
            content=str(message.get("content") or ""),
            tool_calls=_read_tool_calls(message.get("tool_calls")),
            finish_reason=str(choice.get("finish_reason") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    @staticmethod
    def _max_tokens(messages: Sequence[ChatMessage]) -> int:
        """按 1.0 的公式估：``min(1501, 4400 - 提示词 token)``，下限 64。

        这里用字符数除以 2 粗估提示词 token——中文在 gemma 的词表里大致一个字一到两个
        token。估得准不准只影响生成长度上限，不影响正确性；真要精确就得再打一次
        tokenize 端点，那是 S6 的事。
        """
        prompt_tokens = sum(len(message.content) for message in messages) // 2
        return max(MIN_ANSWER_TOKENS, min(MAX_ANSWER_TOKENS, PROMPT_BUDGET_TOKENS - prompt_tokens))
