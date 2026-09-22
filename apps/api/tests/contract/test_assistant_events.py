"""``GET /api/v1/assistant/turns/{id}/events`` 的 SSE 契约。

事件形状沿用 1.0 已验证的两类（``app.py:2986`` / ``:2997``）：``progress`` 与 ``complete``。
本文件钉住的是 2.0 相对 1.0 的四处修正：

1. 前端不需要轮询就能拿到全过程（1.0 的 ``GET /api/assistant/turn/{id}`` 被调 668,490 次）；
2. 事件带 ``id:``，断线可以从 ``Last-Event-ID`` 续（1.0 每次重连都从头重发）；
3. ``complete`` 帧不再把整条进度数组重复一遍（1.0 ``app.py:2989``）；
4. ``status`` 是真实的终态（1.0 把模型失败、超时、guard 拦截全部压成 ``complete``）。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from sentrix.domain.assistant.models import ChatMessage, ModelReply, ToolCall
from sentrix.main import create_app


class ScriptedChatModel:
    def __init__(self, replies: Sequence[ModelReply]) -> None:
        self._replies = list(replies)
        self.calls = 0

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[object]) -> ModelReply:
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


class FailingModel:
    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[object]) -> ModelReply:
        raise RuntimeError("端点挂了")


def build_client(settings, model) -> TestClient:
    app = create_app(settings)
    app.state.container = dataclasses.replace(app.state.container, chat_model=model)
    return TestClient(app)


def read_events(client: TestClient, turn_id: str, **headers: str) -> list[dict]:
    """把 SSE 流读完，返回 ``[{event, data}]``。"""
    lines = []
    with client.stream(
        "GET", f"/api/v1/assistant/turns/{turn_id}/events", headers=headers or None
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in response.iter_lines() if line]

    events: list[dict] = []
    current: dict = {}
    for line in lines:
        if line.startswith("id: "):
            current["id"] = int(line[4:])
        elif line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
            events.append(current)
            current = {}
    return events


def run_turn(client: TestClient, message: str = "给我看看在海边的照片") -> tuple[str, str]:
    conversation = client.post("/api/v1/assistant/conversations", json={"scope_id": "album1"}).json()
    turn = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/turns",
        json={"message": message, "scope_id": "album1"},
    ).json()
    return conversation["id"], turn["turn_id"]


@pytest.fixture
def client(settings) -> TestClient:
    return build_client(settings, ScriptedChatModel([final("这是一句回答。")]))


def test_the_stream_ends_by_itself_after_complete(client: TestClient):
    """一次请求拿完全过程——这就是「前端不轮询」的全部含义。

    反向断言：1.0 那个被调了 66 万次的轮询端点 ``GET /api/assistant/turn/{id}``
    在 2.0 里**不存在**。留着它，前端迟早会回去轮询。
    """
    _conversation_id, turn_id = run_turn(client)

    events = read_events(client, turn_id)

    assert events[-1]["event"] == "complete"
    assert client.get(f"/api/v1/assistant/turns/{turn_id}").status_code == 404


def test_progress_events_carry_the_designed_field_set(client: TestClient):
    _conversation_id, turn_id = run_turn(client)

    progress = [item for item in read_events(client, turn_id) if item["event"] == "progress"]

    assert progress, "至少要有一条进度事件"
    for item in progress:
        data = item["data"]
        assert set(data) == {
            "text",
            "status",
            "stage",
            "step_index",
            "timestamp",
            "kind",
            "payload",
        }
        assert data["stage"] in {
            "thinking",
            "planning",
            "inspecting",
            "action",
            "tool_result",
            "recovering",
            "finalizing",
            "terminal",
        }
        assert data["status"] in {"running", "ok", "denied", "partial", "complete", "cancelled"}


def test_the_first_progress_event_is_the_plan(client: TestClient):
    """规划是 S3 的第一步，也是「为什么这一轮要取证」的书面记录。"""
    _conversation_id, turn_id = run_turn(client)

    first = read_events(client, turn_id)[0]

    assert first["data"]["kind"] == "plan"
    assert first["data"]["stage"] == "planning"
    assert [item["evidence_type"] for item in first["data"]["payload"]["requirements"]] == [
        "photo_set",
        "delivered_photo",
    ]


def test_progress_ids_are_monotonic_and_usable_for_resume(client: TestClient):
    _conversation_id, turn_id = run_turn(client)

    progress = [item for item in read_events(client, turn_id) if item["event"] == "progress"]

    ids = [item["id"] for item in progress]
    assert ids == sorted(ids)
    assert ids == list(range(1, len(ids) + 1))


def test_the_terminal_event_is_always_last_before_complete(client: TestClient):
    _conversation_id, turn_id = run_turn(client)

    events = read_events(client, turn_id)

    assert events[-2]["data"]["kind"] == "terminal"
    assert events[-2]["data"]["stage"] == "terminal"
    assert events[-1]["event"] == "complete"


def test_the_complete_frame_does_not_repeat_the_progress_array(client: TestClient):
    """1.0 的 ``complete`` 帧把已经逐条发过的 public_progress 又完整塞了一遍。"""
    _conversation_id, turn_id = run_turn(client)

    complete = read_events(client, turn_id)[-1]["data"]

    assert complete["type"] == "complete"
    assert set(complete) == {
        "type",
        "turn_id",
        "status",
        "termination_reason",
        "answer",
        "attachments",
        "requirements",
        "error",
    }
    assert complete["turn_id"] == turn_id
    assert complete["answer"] == "这是一句回答。"
    assert complete["termination_reason"] == "answered_with_gap"
    # 用户要图、模型只回了一段文字——终态必须如实反映这件事，不能报 complete。
    assert complete["status"] == "partial"
    assert [item["status"] for item in complete["requirements"]] == ["open", "open"]


def test_a_model_failure_is_reported_as_failed_not_complete(settings):
    """1.0 把模型失败压成 ``status="complete"``，只有未捕获的 Python 异常才是 error。"""
    client = build_client(settings, FailingModel())
    _conversation_id, turn_id = run_turn(client)

    complete = read_events(client, turn_id)[-1]["data"]

    assert complete["status"] == "failed"
    assert complete["termination_reason"] == "model_error"
    assert "模型不可用" in complete["answer"]


def test_a_late_subscriber_still_gets_the_whole_turn(client: TestClient):
    """回合跑完之后才连上来，照样拿得到全过程。

    1.0 的进度只存在进程内存里，页面一刷新就再也拿不回来——那正是轮询成为主路径的
    原因之一。2.0 的事件先落库，历史与实时各走一条路。
    """
    _conversation_id, turn_id = run_turn(client)
    read_events(client, turn_id)  # 第一次连接把回合跑完

    events = read_events(client, turn_id)

    assert [item["event"] for item in events][-1] == "complete"
    assert events[-1]["data"]["answer"] == "这是一句回答。"
    assert len([item for item in events if item["event"] == "progress"]) == len(
        [item for item in read_events(client, turn_id) if item["event"] == "progress"]
    )


def test_last_event_id_resumes_without_replaying(client: TestClient):
    """断线重连从 ``Last-Event-ID`` 之后继续，不重发已经收到的事件。"""
    _conversation_id, turn_id = run_turn(client)
    first = read_events(client, turn_id)
    resume_from = [item["id"] for item in first if item["event"] == "progress"][1]

    resumed = read_events(client, turn_id, **{"Last-Event-ID": str(resume_from)})

    ids = [item["id"] for item in resumed if item["event"] == "progress"]
    assert ids and min(ids) > resume_from
    assert resumed[-1]["event"] == "complete"


def test_a_garbage_last_event_id_falls_back_to_a_full_replay(client: TestClient):
    _conversation_id, turn_id = run_turn(client)

    events = read_events(client, turn_id, **{"Last-Event-ID": "not-a-number"})

    assert [item["id"] for item in events if item["event"] == "progress"][0] == 1


def test_two_turns_in_one_conversation_each_get_their_own_stream(settings):
    client = build_client(
        settings,
        ScriptedChatModel([tool_call("get_result_page", "c1", result_set_id="rs_1", page=1)]),
    )
    conversation_id, first_turn = run_turn(client)

    second_turn = client.post(
        f"/api/v1/assistant/conversations/{conversation_id}/turns",
        json={"message": "再看看别的", "scope_id": "album1"},
    ).json()["turn_id"]

    assert first_turn != second_turn
    first = read_events(client, first_turn)
    second = read_events(client, second_turn)
    assert first[-1]["data"]["turn_id"] == first_turn
    assert second[-1]["data"]["turn_id"] == second_turn
    # 每个回合的 step_index 各自从 1 开始——前端拿它当 key 时必须限定回合作用域。
    assert [item["id"] for item in first if item["event"] == "progress"][0] == 1
    assert [item["id"] for item in second if item["event"] == "progress"][0] == 1


def test_a_tool_call_turn_emits_action_and_result_events(settings):
    client = build_client(
        settings,
        ScriptedChatModel(
            [
                tool_call("search_memories", "c1", query="海边"),
                final("这是你要的照片。"),
            ]
        ),
    )
    _conversation_id, turn_id = run_turn(client)

    progress = [item["data"] for item in read_events(client, turn_id) if item["event"] == "progress"]

    kinds = [item["kind"] for item in progress]
    assert "model_action" in kinds
    assert "tool_result" in kinds
    action = next(item for item in progress if item["kind"] == "model_action")
    assert action["payload"]["tool"] == "search_memories"
    assert action["payload"]["tool_call_id"] == "c1"


def test_a_finished_turn_is_visible_in_the_conversation_detail(client: TestClient):
    """回合收尾之后，会话里该有的东西都在。

    这条用例**必须**放在 SSE 这一侧：它靠「把事件流读完」来等待回合结束，而这正是
    「一次请求拿完全过程」的用法本身。放在 Task 42 的会话契约里会变成依赖一个还不存在
    的端点——逐任务验证时它就是这么暴露出来的。
    """
    conversation_id, turn_id = run_turn(client)
    # 读完 SSE 流就意味着回合已经收尾。
    read_events(client, turn_id)

    detail = client.get(f"/api/v1/assistant/conversations/{conversation_id}").json()

    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "这是一句回答。"
    assert detail["turns"][0]["status"] == "partial"
    assert detail["turns"][0]["termination_reason"] == "answered_with_gap"
