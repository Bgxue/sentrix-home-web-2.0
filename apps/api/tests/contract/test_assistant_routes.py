"""``/api/v1/assistant/*`` 的契约测试。

模型换成脚本化的假件——契约测试要验的是 HTTP 映射与会话生命周期，不是模型。真模型
的验收在 tests/e2e（Task 45）。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from sentrix.domain.assistant.models import ChatMessage, ModelReply, ToolCall
from sentrix.domain.assistant.tools import TOOL_SPECS
from sentrix.main import create_app


class ScriptedChatModel:
    def __init__(self, replies: Sequence[ModelReply]) -> None:
        self._replies = list(replies)
        self.calls = 0

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[object]) -> ModelReply:
        assert [spec.name for spec in tools] == [spec.name for spec in TOOL_SPECS]
        self.calls += 1
        return self._replies[min(self.calls - 1, len(self._replies) - 1)]


def final(content: str) -> ModelReply:
    return ModelReply(content=content, finish_reason="stop")


def tool_call(name: str, call_id: str, **arguments: object) -> ModelReply:
    return ModelReply(
        content="",
        finish_reason="tool_calls",
        tool_calls=(ToolCall(call_id=call_id, name=name, arguments_json=json.dumps(arguments)),),
    )


@pytest.fixture
def client(settings) -> TestClient:
    """把模型换成脚本化的假件，其余装配（真 SQLite、真迁移）保持原样。

    默认剧本是「直接给一个不带交付的答案」——大多数契约用例只关心会话与回合的形状。
    """
    app = create_app(settings)
    app.state.container = dataclasses.replace(
        app.state.container, chat_model=ScriptedChatModel([final("这是一句回答。")])
    )
    return TestClient(app)


def make_conversation(client: TestClient, scope_id: str = "album1") -> dict:
    response = client.post("/api/v1/assistant/conversations", json={"scope_id": scope_id})
    assert response.status_code == 201, response.text
    return response.json()


def start_turn(client: TestClient, conversation_id: str, message: str = "给我看看在海边的照片") -> dict:
    # 不传 scope：回合的 scope 由它所属的会话决定。
    response = client.post(
        f"/api/v1/assistant/conversations/{conversation_id}/turns",
        json={"message": message},
    )
    assert response.status_code == 202, response.text
    return response.json()


# ---- 会话 -------------------------------------------------------------------


def test_creating_a_conversation_returns_its_shape(client: TestClient):
    body = make_conversation(client)

    assert body["scope_id"] == "album1"
    assert body["title"] == "新对话"
    assert body["id"].startswith("conv_")
    assert body["created_at"] == body["updated_at"]


def test_conversations_are_listed_per_scope(client: TestClient):
    created = make_conversation(client)
    make_conversation(client, scope_id="album2")

    listed = client.get("/api/v1/assistant/conversations", params={"scope_id": "album1"}).json()

    assert [item["id"] for item in listed["items"]] == [created["id"]]


def test_a_conversation_can_be_renamed(client: TestClient):
    created = make_conversation(client)

    response = client.patch(f"/api/v1/assistant/conversations/{created['id']}", json={"title": "海边那次"})

    assert response.status_code == 200
    assert response.json()["title"] == "海边那次"


def test_renaming_a_missing_conversation_is_404(client: TestClient):
    assert client.patch("/api/v1/assistant/conversations/conv_nope", json={"title": "x"}).status_code == 404


def test_a_blank_title_is_rejected(client: TestClient):
    created = make_conversation(client)

    response = client.patch(f"/api/v1/assistant/conversations/{created['id']}", json={"title": ""})

    assert response.status_code == 422


def test_a_conversation_can_be_deleted(client: TestClient):
    created = make_conversation(client)

    assert client.delete(f"/api/v1/assistant/conversations/{created['id']}").status_code == 204

    assert client.get(f"/api/v1/assistant/conversations/{created['id']}").status_code == 404
    assert client.delete(f"/api/v1/assistant/conversations/{created['id']}").status_code == 404


def test_the_detail_view_carries_messages_and_turns(client: TestClient):
    created = make_conversation(client)
    turn = start_turn(client, created["id"])

    body = client.get(f"/api/v1/assistant/conversations/{created['id']}").json()

    assert body["conversation"]["id"] == created["id"]
    assert [message["role"] for message in body["messages"]] == ["user"]
    assert [item["id"] for item in body["turns"]] == [turn["turn_id"]]


# ---- 建回合 -----------------------------------------------------------------


def test_starting_a_turn_returns_a_pending_turn_id(client: TestClient):
    created = make_conversation(client)

    body = start_turn(client, created["id"])

    assert body["turn_id"].startswith("turn_")
    assert body["conversation_id"] == created["id"]
    # 回合先落库再执行：SSE 永远有东西可订阅。
    assert body["status"] in {"pending", "running"}


def test_a_turn_inherits_the_scope_of_its_conversation(client: TestClient):
    """回合的 scope 必须来自会话，不能来自请求。

    实测过的坏法：用 ``{"scope_id": "album1"}`` 建会话，再用**不带 scope** 的请求
    建回合，回合会落到 ``StartTurnRequest.scope_id`` 的默认值 ``home-default`` 上。
    检索于是在一个空 scope 里跑，助手如实回答「没有找到」——失败是静默的，表现为
    「搜索质量差」而不是「配置错了」。这条用例钉的就是这条不变量。
    """
    conversation = make_conversation(client, scope_id="album1")

    turn = start_turn(client, conversation["id"])

    recorded = client.app.state.container.turn_repository.get(turn["turn_id"])
    assert recorded is not None
    assert recorded.scope_id == "album1"


def test_a_scope_sent_by_the_caller_cannot_override_the_conversation(client: TestClient):
    """请求里残留的 ``scope_id`` 造不出「回合的 scope ≠ 会话的 scope」。

    缓存里的旧前端仍会带上这个字段。它现在只是一个被忽略的多余字段，而不是一次把
    回合改道到别的 scope 的静默重定向。
    """
    conversation = make_conversation(client, scope_id="album1")

    response = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/turns",
        json={"message": "在吗", "scope_id": "album2"},
    )

    assert response.status_code == 202
    recorded = client.app.state.container.turn_repository.get(response.json()["turn_id"])
    assert recorded is not None
    assert recorded.scope_id == "album1"


def test_a_blank_message_is_rejected(client: TestClient):
    created = make_conversation(client)

    response = client.post(f"/api/v1/assistant/conversations/{created['id']}/turns", json={"message": ""})

    assert response.status_code == 422


def test_starting_a_turn_on_a_missing_conversation_is_404(client: TestClient):
    response = client.post("/api/v1/assistant/conversations/conv_nope/turns", json={"message": "在吗"})

    assert response.status_code == 404


def test_the_conversation_title_follows_the_first_question(client: TestClient):
    created = make_conversation(client)

    start_turn(client, created["id"], message="给我看看在海边的照片")

    detail = client.get(f"/api/v1/assistant/conversations/{created['id']}").json()
    assert detail["conversation"]["title"] == "给我看看在海边的照片"


def test_a_turn_can_be_started_without_creating_a_conversation_first(client: TestClient):
    """不带 ``conversation_id`` 时由调用方先建会话——本切片不做隐式建会话。

    反向验证：这里显式断言「没建会话 = 404」，免得将来有人加一条静默 fallback
    把一次调用方错误变成一次看起来正常的对话。
    """
    response = client.post(
        "/api/v1/assistant/conversations/conv_nope/turns",
        json={"message": "在吗"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "conversation not found"


def test_an_unknown_turn_has_no_event_stream(client: TestClient):
    assert client.get("/api/v1/assistant/turns/turn_nope/events").status_code == 404
