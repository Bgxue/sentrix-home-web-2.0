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
from sentrix.domain.assistant.ports import ModelServiceError
from sentrix.domain.assistant.tools import TOOL_SPECS
from sentrix.infrastructure.assistant.chat_client import ChatModelError, OpenAIChatModel
from sentrix.infrastructure.assistant.model_endpoint import ServedModel


class _Handler(BaseHTTPRequestHandler):
    received: list[dict] = []
    response: dict = {}
    status: int = 200
    # 这个假端点**服务哪些模型名**。POST 里带的名字不在其中就回 vLLM 那个 404，
    # 形状照抄实测响应（``{"error":{"message":"The model `X` does not exist.",
    # "type":"NotFoundError","code":404}}``）。llama.cpp 不校验名字，所以它没有这一条
    # ——这里选严的那个，宽的那个（不校验）是它的子集。
    served: list[str] = ["test-model"]

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的接口
        payload = json.dumps(self._models()).encode()
        self._send(payload, 200)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的接口
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append({"path": self.path, "body": body})
        if body.get("model") not in type(self).served:
            message = f"The model `{body.get('model')}` does not exist."
            payload = json.dumps(
                {"error": {"message": message, "type": "NotFoundError", "code": 404}}
            ).encode()
            self._send(payload, 404)
            return
        self._send(json.dumps(type(self).response).encode(), type(self).status)

    @classmethod
    def _models(cls) -> dict:
        return {"object": "list", "data": [{"id": name, "object": "model"} for name in cls.served]}

    def _send(self, payload: bytes, status: int) -> None:
        self.send_response(status)
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
    _Handler.served = ["test-model"]


def chat_model(server: str) -> OpenAIChatModel:
    """被测客户端：名字由 ``ServedModel`` 问端点要，与生产装配完全一样。

    测试里不再传字符串——构造参数已经收成一个 ``ServedModel``，因为「客户端能拿一个
    写死的名字」正是 2026-09-24 那次事故的入口。
    """
    return OpenAIChatModel(server, ServedModel(server))


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

    result = chat_model(server).complete(messages(), TOOL_SPECS)

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

    result = chat_model(server).complete(messages(), TOOL_SPECS)

    assert result.wants_tools is True
    assert result.tool_calls[0].name == "search_memories"
    assert result.tool_calls[0].call_id == "call_abc"
    assert result.tool_calls[0].parse() == {"query": "海边"}


def test_the_tools_field_carries_standard_json_schema(server: str):
    _Handler.response = reply()

    chat_model(server).complete(messages(), TOOL_SPECS)

    sent = _Handler.received[0]["body"]
    assert sent["tool_choice"] == "auto"
    # 顺序与 ``TOOL_SPECS`` 一致（``query_photo_people`` 是 S6 T122 加的第五个）。
    # 与 ``test_assistant_policy.test_the_tool_list_is_exactly_what_ships`` 是两处
    # 故意的重复：那条钉**注册表**，这条钉**真的上了线的那份**（发出去的就是它们）。
    assert [tool["function"]["name"] for tool in sent["tools"]] == [
        "search_memories",
        "query_memory_facts",
        "query_photo_people",
        "inspect_photo",
        "read_photo_text",
        "get_result_page",
        "get_person_profile",
        "final_answer",
    ]
    assert sent["tools"][0]["function"]["parameters"]["type"] == "object"


def test_the_system_message_is_first_and_only_once(server: str):
    """严格 chat template（Qwen 系、llama.cpp 的 jinja）要求 system 只能在开头。

    1.0 因为中途 append 一条 ``role=system`` 而报 ``400 System message must be at the
    beginning``；生产路径跑的是宽松的 Gemma 模板所以看不出来，PhotoBench 上的严格模板
    有 84% 的题在第二步就挂。这里的守卫在客户端里，越界直接抛，不会发出请求。
    """
    _Handler.response = reply()

    chat_model(server).complete(messages(), TOOL_SPECS)

    roles = [message["role"] for message in _Handler.received[0]["body"]["messages"]]
    assert roles == ["system", "user"]


def test_a_system_message_in_the_middle_is_refused_before_the_request(server: str):
    broken = [
        ChatMessage(ChatRole.SYSTEM, "系统提示"),
        ChatMessage(ChatRole.USER, "问题"),
        ChatMessage(ChatRole.SYSTEM, "中途追加的约束"),
    ]

    with pytest.raises(ChatModelError):
        chat_model(server).complete(broken, TOOL_SPECS)

    assert _Handler.received == []


def test_a_conversation_must_start_with_a_system_message(server: str):
    with pytest.raises(ChatModelError):
        chat_model(server).complete([ChatMessage(ChatRole.USER, "没有 system")], TOOL_SPECS)

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

    chat_model(server).complete(conversation, TOOL_SPECS)

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

    chat_model(server).complete(conversation, TOOL_SPECS)

    sent = _Handler.received[0]["body"]["messages"]
    assert sent[2]["content"] is None
    assert sent[2]["tool_calls"][0]["id"] == "c1"
    # 不确定结果的普通 assistant 消息仍然带字符串内容。
    assert sent[0]["content"] == "你是家庭相册助手。"


def test_a_non_200_response_is_a_model_service_error(server: str):
    """非 200 是**服务这一侧**的失败，所以是 ``ModelServiceError``——用户会看到
    「模型服务当前不可用」，而不是「规划不出来」."""
    _Handler.status = 500
    _Handler.response = {"error": "boom"}

    with pytest.raises(ModelServiceError) as error:
        chat_model(server).complete(messages(), TOOL_SPECS)

    assert "500" in str(error.value)


def test_an_unreachable_endpoint_is_a_model_service_error():
    """端点整个连不上时，**探测**就先炸了（``/v1/models`` 打不通）。"""
    model = OpenAIChatModel(
        "http://127.0.0.1:1/v1", ServedModel("http://127.0.0.1:1/v1", timeout_s=1.0), timeout_s=1.0
    )

    with pytest.raises(ModelServiceError) as error:
        model.complete(messages(), TOOL_SPECS)

    assert "不可达" in str(error.value)


def test_an_unrecognisable_body_is_a_model_service_error(server: str):
    _Handler.response = {"nope": True}

    with pytest.raises(ModelServiceError):
        chat_model(server).complete(messages(), TOOL_SPECS)


# ---- 后端被换掉（裁决一的那次事故） -----------------------------------------


def test_the_swapped_backend_is_picked_up_on_the_same_request(server: str):
    """8100 从 llama.cpp 换成 vLLM 的那一刻：下一个请求自己重新问、自己用新名字成功。

    这是缓存策略的另一半——TTL 决定「多久白探一次」，这条决定「换完之后多久恢复」。
    只靠 TTL 的话，换后端之后会有一整个 TTL 窗口的请求全部以 404 收场。
    """
    _Handler.served = ["gemma4-qat-iq3xxs"]
    _Handler.response = reply("这是你要的海边照片。")
    model = chat_model(server)

    assert model.complete(messages(), TOOL_SPECS).content == "这是你要的海边照片。"

    # 运维把 8100 换成了 vLLM，服务的是另一个名字。客户端手里还是旧的那个（缓存没过期）。
    _Handler.served = ["gemma4-12b-it"]

    # 同一个请求里自己恢复：带旧名字撞 404 → 重探 → 用新名字重发 → 成功。
    assert model.complete(messages(), TOOL_SPECS).content == "这是你要的海边照片。"

    assert [item["body"]["model"] for item in _Handler.received] == [
        "gemma4-qat-iq3xxs",  # 换后端之前的那次
        "gemma4-qat-iq3xxs",  # 换后端之后的第一次：手里还是旧名字，撞 404
        "gemma4-12b-it",  # 重探之后重发，用的是端点现在真正服务的名字
    ]


def test_a_404_that_is_not_a_swapped_backend_is_not_retried(server: str):
    """名字没变过就不重发：拿同一个名字再问一次只会拿到同一个错，白花一次调用。

    端点长这样：「``/v1/models`` 说服务 test-model，``/v1/chat/completions`` 却 404」——
    路由没挂对、反向代理配错都会长成这样。它与「后端被换掉」的区别只有一个：重探拿回来
    的名字**没变**。
    """
    _Handler.status = 404
    _Handler.response = {"error": "not found"}

    with pytest.raises(ModelServiceError):
        chat_model(server).complete(messages(), TOOL_SPECS)

    assert len(_Handler.received) == 1, "名字没变就只该打一次"


def test_a_chat_call_on_an_endpoint_with_no_models_fails_loudly(server: str):
    """探测失败在**发请求之前**就收场：不会带着一个猜出来的名字打过去。"""
    _Handler.served = []

    with pytest.raises(ModelServiceError) as error:
        chat_model(server).complete(messages(), TOOL_SPECS)

    assert "没有任何模型" in str(error.value)
    assert _Handler.received == []


def test_max_tokens_follows_the_behaviour_baseline_formula(server: str):
    """``min(1501, 4400 - 提示词 token)``，下限 64——1.0 ``app.py:2680-2692``。"""
    _Handler.response = reply()
    long_question = "问" * 9000

    chat_model(server).complete(
        [ChatMessage(ChatRole.SYSTEM, "系统"), ChatMessage(ChatRole.USER, long_question)],
        TOOL_SPECS,
    )

    assert _Handler.received[0]["body"]["max_tokens"] == 64


def test_a_short_prompt_gets_the_full_answer_budget(server: str):
    _Handler.response = reply()

    chat_model(server).complete(messages(), TOOL_SPECS)

    assert _Handler.received[0]["body"]["max_tokens"] == 1501


# ---- 思考通道不能漏进用户可见的答案 -------------------------------------------
#
# 实测（2026-09-25，8100 的 vLLM + gemma-4-12B-it）：工具往返两轮之后，模型给出的
# 最终 content 是 ``'<|channel>thought\n<channel|>为您找到了去年在海边的照片，请查看。'``
# —— 前半段是 Gemma 4 的思考通道标记，用户不该看到。
#
# **为什么只能在客户端剥、不能加服务端 flag。** 8100 是 1.0 与 2.0 共用的端点，
# ``--reasoning-parser`` 一加，8091 那边 1.0 的答案也跟着变。所以剥的动作落在 2.0
# 自己的客户端里。
#
# **为什么不是 ``chat_template_kwargs={"enable_thinking": False}``。** 实测 vLLM
# 0.26.0 确实支持这个 per-request 字段（``enable_thinking=True`` 会改变输出，说明它
# 被吃进去了），但 gemma-4 模板里 ``enable_thinking | default(false)`` 本来就是 False
# ——传 False 与不传逐字相同，挡不住这段标记。所以只能在响应侧剥。

_THOUGHT = "<|channel>thought\n<channel|>"


def test_a_thought_channel_is_not_shown_in_the_answer(server: str):
    """空思考通道 + 答案：用户只该看到答案那一半。"""
    _Handler.response = reply(f"{_THOUGHT}为您找到了去年在海边的照片，请查看。")

    result = chat_model(server).complete(messages(), TOOL_SPECS)

    assert result.content == "为您找到了去年在海边的照片，请查看。"
    assert "<|channel>" not in result.content
    assert "<channel|>" not in result.content
    # 空的思考通道没有内容可留。
    assert result.thought == ""


def test_a_non_empty_thought_channel_is_kept_but_off_the_answer(server: str):
    """模型真的推理了：推理过程不出现在答案里，也不无声丢掉——它留在 ``ModelReply`` 上。

    丢掉是错的（这是模型的推理，是这一回合唯一能解释「它为什么这么答」的东西），
    塞进答案是错的（用户问的是照片，不是模型的独白）。所以：答案给用户，
    推理挂在回复对象上，同一条 DEBUG 日志留痕。
    """
    _Handler.response = reply(
        "<|channel>thought\n用户问的是海边，我应该先查检索再交付原图。\n<channel|>为您找到了 2 张海边的照片。"
    )

    result = chat_model(server).complete(messages(), TOOL_SPECS)

    assert result.content == "为您找到了 2 张海边的照片。"
    assert result.thought == "用户问的是海边，我应该先查检索再交付原图。"


def test_a_dangling_channel_close_is_dropped(server: str):
    """半截标记也要清掉：``enable_thinking=True`` 下实测到 ``'<channel|>我为您找到了…'``
    ——只有闭合符、没有开始符，同样不能端给用户。"""
    _Handler.response = reply("<channel|>我为您找到了这些照片。")

    result = chat_model(server).complete(messages(), TOOL_SPECS)

    assert result.content == "我为您找到了这些照片。"


def test_an_unterminated_thought_channel_never_becomes_the_answer(server: str):
    """思考通道开了就没闭合（截断）：被它吞掉的正文宁可判成「这轮没有答案」，
    也不能原样端出去。空答案会走 ``parse_failure`` 的重试，带标记的答案是静默的错。"""
    _Handler.response = reply("<|channel>thought\n用户好像是想找海边的照片……")

    result = chat_model(server).complete(messages(), TOOL_SPECS)

    assert result.content == ""
    assert result.thought == "用户好像是想找海边的照片……"


def test_a_thought_channel_in_the_middle_keeps_both_halves(server: str):
    """标记出现在正文中间时，只删通道本身，两边的正文都要留下。"""
    _Handler.response = reply("好的，<|channel>thought\n先查检索。\n<channel|>我这就去找。")

    result = chat_model(server).complete(messages(), TOOL_SPECS)

    assert result.content == "好的，我这就去找。"
    assert result.thought == "先查检索。"


def test_an_answer_without_any_channel_is_untouched(server: str):
    """反向门禁：剥的动作只删通道，别的一个字都不许动。

    这条不是凑数——它把「顺手 strip 一下」「顺手把空行规整一下」这类跟本次修复
    无关的行为变更堵在门外。首尾空白照原样透传（``run_turn`` 自己会 strip，
    那是它的事）。
    """
    _Handler.response = reply("  这是你要的海边照片。  ")

    result = chat_model(server).complete(messages(), TOOL_SPECS)

    assert result.content == "  这是你要的海边照片。  "
    assert result.thought == ""
