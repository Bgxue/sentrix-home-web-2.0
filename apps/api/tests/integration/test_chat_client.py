"""模型客户端对真 HTTP 的测试。

用一个真的本地 HTTP 服务（stdlib ``http.server`` 起在线程里）而不是 mock transport：
被测的行为里有一半是「请求长什么样、响应怎么解析」，用假 transport 会把请求序列化也一起
替换掉，而那正是 1.0 出问题的地方（``400 System message must be at the beginning``）。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sentrix.domain.assistant.models import ChatMessage, ChatRole, ToolCall
from sentrix.domain.assistant.tools import TOOL_SPECS
from sentrix.infrastructure.assistant.chat_client import ChatModelError, OpenAIChatModel


class _Handler(BaseHTTPRequestHandler):
    received: list[dict] = []
    response: dict = {}
    status: int = 200

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的接口
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append({"path": self.path, "body": body})
        payload = json.dumps(type(self).response).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        pass  # 测试输出里不要 HTTP 访问日志


@pytest.fixture
def server() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/v1"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def _reset_handler() -> None:
    _Handler.received = []
    _Handler.response = {}
    _Handler.status = 200


def reply(content: str = "好的。", tool_calls: list[dict] | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
    }


def messages() -> list[ChatMessage]:
    return [
        ChatMessage(ChatRole.SYSTEM, "你是家庭相册助手。"),
        ChatMessage(ChatRole.USER, "给我看看在海边的照片"),
    ]


def test_a_plain_reply_is_parsed(server: str):
    _Handler.response = reply("这是你要的海边照片。")

    result = OpenAIChatModel(server, "test-model").complete(messages(), TOOL_SPECS)

    assert result.content == "这是你要的海边照片。"
    assert result.tool_calls == ()
    assert result.wants_tools is False
    assert (result.prompt_tokens, result.completion_tokens) == (11, 3)
    assert _Handler.received[0]["path"] == "/v1/chat/completions"


def test_native_tool_calls_are_parsed(server: str):
    """1.0 让模型在正文里写 JSON，为此写了 150 行解析修复器。原生协议下这些都不需要。"""
    _Handler.response = reply(
        content="",
        tool_calls=[
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "search_memories", "arguments": '{"query": "海边"}'},
            }
        ],
    )

    result = OpenAIChatModel(server, "test-model").complete(messages(), TOOL_SPECS)

    assert result.wants_tools is True
    assert result.tool_calls[0].name == "search_memories"
    assert result.tool_calls[0].call_id == "call_abc"
    assert result.tool_calls[0].parse() == {"query": "海边"}


def test_the_tools_field_carries_standard_json_schema(server: str):
    _Handler.response = reply()

    OpenAIChatModel(server, "test-model").complete(messages(), TOOL_SPECS)

    sent = _Handler.received[0]["body"]
    assert sent["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in sent["tools"]] == [
        "search_memories",
        "get_result_page",
        "get_original_photos",
        "get_person_profile",
    ]
    assert sent["tools"][0]["function"]["parameters"]["type"] == "object"


def test_the_system_message_is_first_and_only_once(server: str):
    """严格 chat template（Qwen 系、llama.cpp 的 jinja）要求 system 只能在开头。

    1.0 因为中途 append 一条 ``role=system`` 而报 ``400 System message must be at the
    beginning``；生产路径跑的是宽松的 Gemma 模板所以看不出来，PhotoBench 上的严格模板
    有 84% 的题在第二步就挂。这里的守卫在客户端里，越界直接抛，不会发出请求。
    """
    _Handler.response = reply()

    OpenAIChatModel(server, "test-model").complete(messages(), TOOL_SPECS)

    roles = [message["role"] for message in _Handler.received[0]["body"]["messages"]]
    assert roles == ["system", "user"]


def test_a_system_message_in_the_middle_is_refused_before_the_request(server: str):
    broken = [
        ChatMessage(ChatRole.SYSTEM, "系统提示"),
        ChatMessage(ChatRole.USER, "问题"),
        ChatMessage(ChatRole.SYSTEM, "中途追加的约束"),
    ]

    with pytest.raises(ChatModelError):
        OpenAIChatModel(server, "test-model").complete(broken, TOOL_SPECS)

    assert _Handler.received == []


def test_a_conversation_must_start_with_a_system_message(server: str):
    with pytest.raises(ChatModelError):
        OpenAIChatModel(server, "test-model").complete(
            [ChatMessage(ChatRole.USER, "没有 system")], TOOL_SPECS
        )

    assert _Handler.received == []


def test_tool_results_are_sent_back_with_their_call_id(server: str):
    _Handler.response = reply()
    conversation = [
        ChatMessage(ChatRole.SYSTEM, "你是家庭相册助手。"),
        ChatMessage(ChatRole.USER, "给我看看在海边的照片"),
        ChatMessage(
            ChatRole.ASSISTANT,
            "",
            # 与模型返回的形状一致：arguments 是 JSON 字符串
            tool_calls=(
                ToolCall(call_id="call_abc", name="search_memories", arguments_json='{"query": "海边"}'),
            ),
        ),
        ChatMessage(ChatRole.TOOL, '{"result_set_id": "rs_1"}', tool_call_id="call_abc"),
    ]

    OpenAIChatModel(server, "test-model").complete(conversation, TOOL_SPECS)

    sent = _Handler.received[0]["body"]["messages"]
    assert sent[2]["tool_calls"][0]["id"] == "call_abc"
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == '{"query": "海边"}'
    assert sent[3]["role"] == "tool"
    assert sent[3]["tool_call_id"] == "call_abc"


def test_an_assistant_tool_call_message_sends_null_content(server: str):
    """带 tool_calls 的 assistant 消息，``content`` 必须是 null 而不是空串。

    实测 8100（gemma4 模板）：写成 ``""`` 时工具结果回填后的下一轮，模型直接吐一个 EOS
    （``finish_reason="stop"``、1 个 token、content 为空），回合以「模型不可用」收场。
    这条断言看起来吹毛求疵，但它挡的是一个只有打真模型才会暴露的失败。
    """
    _Handler.response = reply()
    conversation = [
        ChatMessage(ChatRole.SYSTEM, "你是家庭相册助手。"),
        ChatMessage(ChatRole.USER, "给我看看在海边的照片"),
        ChatMessage(
            ChatRole.ASSISTANT,
            "",
            tool_calls=(ToolCall(call_id="c1", name="search_memories", arguments_json='{"query": "海边"}'),),
        ),
        ChatMessage(ChatRole.TOOL, "{}", tool_call_id="c1"),
    ]

    OpenAIChatModel(server, "test-model").complete(conversation, TOOL_SPECS)

    sent = _Handler.received[0]["body"]["messages"]
    assert sent[2]["content"] is None
    assert sent[2]["tool_calls"][0]["id"] == "c1"
    # 不确定结果的普通 assistant 消息仍然带字符串内容。
    assert sent[0]["content"] == "你是家庭相册助手。"


def test_a_non_200_response_becomes_a_chat_model_error(server: str):
    _Handler.status = 500
    _Handler.response = {"error": "boom"}

    with pytest.raises(ChatModelError) as error:
        OpenAIChatModel(server, "test-model").complete(messages(), TOOL_SPECS)

    assert "500" in str(error.value)


def test_an_unreachable_endpoint_becomes_a_chat_model_error():
    model = OpenAIChatModel("http://127.0.0.1:1/v1", "test-model", timeout_s=1.0)

    with pytest.raises(ChatModelError):
        model.complete(messages(), TOOL_SPECS)


def test_an_unrecognisable_body_becomes_a_chat_model_error(server: str):
    _Handler.response = {"nope": True}

    with pytest.raises(ChatModelError):
        OpenAIChatModel(server, "test-model").complete(messages(), TOOL_SPECS)


def test_max_tokens_follows_the_behaviour_baseline_formula(server: str):
    """``min(1501, 4400 - 提示词 token)``，下限 64——1.0 ``app.py:2680-2692``。"""
    _Handler.response = reply()
    long_question = "问" * 9000

    OpenAIChatModel(server, "test-model").complete(
        [ChatMessage(ChatRole.SYSTEM, "系统"), ChatMessage(ChatRole.USER, long_question)],
        TOOL_SPECS,
    )

    assert _Handler.received[0]["body"]["max_tokens"] == 64


def test_a_short_prompt_gets_the_full_answer_budget(server: str):
    _Handler.response = reply()

    OpenAIChatModel(server, "test-model").complete(messages(), TOOL_SPECS)

    assert _Handler.received[0]["body"]["max_tokens"] == 1501
