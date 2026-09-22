"""一次回合的完整行为：真 SQLite、脚本化的模型、打桩的工具。

模型与工具的形状是真的（原生 tool_calls、两份投影的 ToolOutcome），只有「模型说什么」
是脚本化的——那正是被测对象之外的东西。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from sentrix.application.assistant.manage_conversations import ManageConversations
from sentrix.application.assistant.run_turn import (
    RunTurn,
    TurnAlreadyClaimedError,
    TurnNotFoundError,
    TurnRequest,
)
from sentrix.application.assistant.tools import ToolOutcome
from sentrix.domain.assistant.models import (
    Attachment,
    ChatMessage,
    ChatRole,
    ModelReply,
    ProgressEvent,
    TerminationReason,
    ToolCall,
    TurnStatus,
)
from sentrix.domain.assistant.tools import TOOL_SPECS
from sentrix.infrastructure.persistence.repositories.conversations import (
    SqliteConversationRepository,
)
from sentrix.infrastructure.persistence.repositories.turns import SqliteTurnRepository

QUESTION = "给我看看在海边的照片"


# ---- 假件 -------------------------------------------------------------------


class ScriptedModel:
    """按剧本回话的模型。剧本用完还继续要，就返回最后一条。"""

    def __init__(self, replies: Sequence[ModelReply | Exception]) -> None:
        self._replies = list(replies)
        self.calls: list[Sequence[ChatMessage]] = []

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[object]) -> ModelReply:
        self.calls.append(list(messages))
        assert [spec.name for spec in tools] == [spec.name for spec in TOOL_SPECS]
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


class StubToolbox:
    def __init__(self, outcomes: Mapping[str, ToolOutcome] | None = None) -> None:
        self._outcomes = dict(outcomes or {})
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def execute(
        self, name: str, arguments: Mapping[str, object], *, scope_id: str, now: datetime
    ) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))
        outcome = self._outcomes.get(name)
        if outcome is None:
            return ToolOutcome(
                observation={"failure_reason": "not_stubbed"},
                model_visible={"error": "not_stubbed"},
                failure_reason="not_stubbed",
            )
        return outcome


def final(content: str) -> ModelReply:
    return ModelReply(content=content, finish_reason="stop")


def tool_call(name: str, call_id: str, **arguments: object) -> ModelReply:
    return ModelReply(
        content="",
        finish_reason="tool_calls",
        tool_calls=(ToolCall(call_id=call_id, name=name, arguments_json=json.dumps(arguments)),),
    )


SEARCH_OK = ToolOutcome(
    observation={"result_set_id": "rs_1", "query": "海边", "total": 2, "has_more": False},
    model_visible={
        "result_set_id": "rs_1",
        "query": "海边",
        "page": 1,
        "page_size": 6,
        "has_more": False,
        "preview": [
            {"handle": "photo_1", "file_name": "a.jpg", "captured_at": "2017-11-05T16:44:57"},
            {"handle": "photo_2", "file_name": "b.jpg", "captured_at": "2018-08-12T09:10:00"},
        ],
    },
)

DELIVER_OK = ToolOutcome(
    observation={"result_set_id": "rs_1", "delivered": 2, "rejected": []},
    model_visible={"result_set_id": "rs_1", "delivered": 2, "rejected": []},
    attachments=(
        Attachment(handle="photo_1", asset_id="asset_1", file_name="a.jpg"),
        Attachment(handle="photo_2", asset_id="asset_2", file_name="b.jpg"),
    ),
)

SEARCH_EMPTY = ToolOutcome(
    observation={"result_set_id": "rs_2", "query": "海边", "total": 0, "has_more": False},
    model_visible={"result_set_id": "rs_2", "query": "海边", "page": 1, "preview": [], "has_more": False},
)


# ---- 夹具 -------------------------------------------------------------------


@pytest.fixture
def env(migrations_ready_db):
    conversations = SqliteConversationRepository(migrations_ready_db)
    turns = SqliteTurnRepository(migrations_ready_db)
    conversation = conversations.create(
        scope_id="album1", title="新对话", created_at=datetime(2026, 9, 22, tzinfo=UTC)
    )
    return conversations, turns, conversation, migrations_ready_db


def manage_of(env) -> ManageConversations:
    conversations, turns, _conversation, _db = env
    return ManageConversations(conversations=conversations, turns=turns)


def build(env, model, toolbox) -> RunTurn:
    conversations, turns, _conversation, _db = env
    return RunTurn(model=model, conversations=conversations, turns=turns, toolbox=toolbox)


def start(run_turn, conversation) -> str:
    return run_turn.start(TurnRequest(conversation_id=conversation.id, question=QUESTION)).id


# ---- 正常链路 ---------------------------------------------------------------


def test_the_full_loop_retrieves_delivers_and_answers(env):
    conversations, turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            tool_call("get_original_photos", "c2", result_set_id="rs_1", handles=["photo_1", "photo_2"]),
            final("这是你要的两张海边照片。"),
        ]
    )
    toolbox = StubToolbox({"search_memories": SEARCH_OK, "get_original_photos": DELIVER_OK})
    run_turn = build(env, model, toolbox)
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.status is TurnStatus.COMPLETE
    assert result.termination_reason is TerminationReason.ANSWERED
    assert result.answer == "这是你要的两张海边照片。"
    assert [item.asset_id for item in result.attachments] == ["asset_1", "asset_2"]
    assert [item["status"] for item in result.requirements] == ["satisfied", "satisfied"]
    assert turns.get(turn_id).status is TurnStatus.COMPLETE
    assert turns.get(turn_id).attachments[0].handle == "photo_1"


def test_the_answer_is_persisted_as_an_assistant_message(env):
    conversations, _turns, conversation, _db = env
    model = ScriptedModel([tool_call("search_memories", "c1", query="海边"), final("给你。")])
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    run_turn.run(turn_id)

    messages = conversations.list_messages(conversation.id, 10)
    assert [(message.role, message.content) for message in messages] == [
        (ChatRole.USER, QUESTION),
        (ChatRole.ASSISTANT, "给你。"),
    ]


def test_progress_events_are_persisted_in_order(env):
    _conversations, turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            tool_call("get_original_photos", "c2", result_set_id="rs_1", handles=["photo_1"]),
            final("给你。"),
        ]
    )
    toolbox = StubToolbox(
        {
            "search_memories": SEARCH_OK,
            "get_original_photos": ToolOutcome(
                observation={"result_set_id": "rs_1", "delivered": 1, "rejected": []},
                model_visible={"result_set_id": "rs_1", "delivered": 1, "rejected": []},
                attachments=(Attachment(handle="photo_1", asset_id="asset_1", file_name="a.jpg"),),
            ),
        }
    )
    run_turn = build(env, model, toolbox)
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    events = turns.events_after(turn_id, 0)
    assert [event.step_index for event in events] == list(range(1, len(events) + 1))
    assert events[0].kind == "plan"
    assert [event.payload["requirements"][0]["status"] for event in events[:1]] == ["open"]
    assert events[-1].kind == "terminal"
    # 事件里记的原因与返回值必须是同一个——1.0 的收尾分类器会覆盖前面设好的原因，
    # 于是轨迹里记的和代码里写的经常不是一回事。
    assert events[-1].payload["termination_reason"] == result.termination_reason.value == "answered"


def test_the_emitted_events_match_what_was_persisted(env):
    """``emit`` 的回调拿到的就是落库的那一份——SSE 与历史回放不该有两个真相。"""
    _conversations, turns, conversation, _db = env
    model = ScriptedModel([tool_call("search_memories", "c1", query="海边"), final("给你。")])
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)
    emitted: list[ProgressEvent] = []

    run_turn.run(turn_id, emit=emitted.append)

    persisted = turns.events_after(turn_id, 0)
    assert [(event.step_index, event.text) for event in emitted] == [
        (event.step_index, event.text) for event in persisted
    ]


def test_history_is_replayed_to_the_model_without_duplicating_the_question(env):
    _conversations, turns, conversation, _db = env
    model = ScriptedModel([tool_call("search_memories", "c1", query="海边"), final("第一轮。")])
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    run_turn.run(start(run_turn, conversation))

    second = ScriptedModel([final("第二轮。")])
    run_turn_2 = build(env, second, StubToolbox())
    run_turn_2.run(start(run_turn_2, conversation))

    sent = second.calls[0]
    assert sent[0].role is ChatRole.SYSTEM
    assert [message.role for message in sent] == [
        ChatRole.SYSTEM,
        ChatRole.USER,
        ChatRole.ASSISTANT,
        ChatRole.USER,
    ]
    assert sent[1].content == QUESTION
    assert sent[2].content == "第一轮。"
    assert sent[3].content == QUESTION


# ---- 门禁 -------------------------------------------------------------------


def test_the_delivery_gate_pushes_the_model_to_hand_over_the_photos(env):
    """实测 8100：问「给我看看在海边的照片」，模型检索一次之后直接给文字，一张图都没有。

    门禁拦一次、提醒它调 get_original_photos，然后才放行。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            final("我找到了两张在海边的照片。"),
            tool_call("get_original_photos", "c2", result_set_id="rs_1", handles=["photo_1"]),
            final("这是你要的照片。"),
        ]
    )
    toolbox = StubToolbox(
        {
            "search_memories": SEARCH_OK,
            "get_original_photos": ToolOutcome(
                observation={"result_set_id": "rs_1", "delivered": 1, "rejected": []},
                model_visible={"result_set_id": "rs_1", "delivered": 1, "rejected": []},
                attachments=(Attachment(handle="photo_1", asset_id="asset_1", file_name="a.jpg"),),
            ),
        }
    )
    run_turn = build(env, model, toolbox)
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.answer == "这是你要的照片。"
    assert [item.asset_id for item in result.attachments] == ["asset_1"]
    assert ("get_original_photos", {"result_set_id": "rs_1", "handles": ["photo_1"]}) in toolbox.calls


def test_a_model_that_keeps_refusing_to_deliver_is_let_through_with_a_gap(env):
    """门禁只提醒一次：提醒过就放行，并如实标注这一轮带着缺口收尾。

    1.0 的零检索拦截没有标志位，模型坚持不调工具就会一直被拦到预算耗尽。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            final("我找到了两张在海边的照片。"),
            final("就是这两张，您可以点开看。"),
        ]
    )
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.answer == "就是这两张，您可以点开看。"
    assert result.termination_reason is TerminationReason.ANSWERED_WITH_GAP
    assert result.status is TurnStatus.PARTIAL
    assert result.attachments == ()
    assert len(model.calls) == 3


def test_an_empty_search_cannot_be_reported_as_a_hit(env):
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            final("我找到了这些海边的照片。"),
            final("这次没有匹配到海边的照片，可能没有拍到过。"),
        ]
    )
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_EMPTY}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.answer == "这次没有匹配到海边的照片，可能没有拍到过。"


def test_a_leaked_internal_identifier_is_never_delivered(env):
    """安全边界不做第二次让步：两次都泄漏就给确定性兜底，不把内部标识交给用户。"""
    _conversations, _turns, conversation, _db = env
    leak = "这张是 photo_1，asset_9f3a21b7c4。"
    model = ScriptedModel([tool_call("search_memories", "c1", query="海边"), final(leak), final(leak)])
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.status is TurnStatus.BLOCKED
    assert result.termination_reason is TerminationReason.GUARD_BLOCKED
    assert "photo_1" not in result.answer


# ---- 收敛与预算 --------------------------------------------------------------


def test_a_second_search_is_refused_and_the_model_is_told_to_page(env):
    _conversations, turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            tool_call("search_memories", "c2", query="海边2"),
            final("这是你要的照片。"),
        ]
    )
    toolbox = StubToolbox({"search_memories": SEARCH_OK})
    run_turn = build(env, model, toolbox)
    turn_id = start(run_turn, conversation)

    run_turn.run(turn_id)

    assert [name for name, _ in toolbox.calls] == ["search_memories"]
    refusals = [event for event in turns.events_after(turn_id, 0) if event.status.value == "denied"]
    assert refusals and "get_result_page" in refusals[0].text


def test_a_repeated_identical_call_is_replayed_not_re_executed(env):
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("get_result_page", "c1", result_set_id="rs_1", page=1),
            tool_call("get_result_page", "c2", result_set_id="rs_1", page=1),
            final("这是你要的照片。"),
        ]
    )
    toolbox = StubToolbox({"get_result_page": SEARCH_OK})
    run_turn = build(env, model, toolbox)
    turn_id = start(run_turn, conversation)

    run_turn.run(turn_id)

    assert len(toolbox.calls) == 1


def test_the_model_step_budget_ends_the_turn_with_a_reason(env):
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel([tool_call("get_result_page", "c1", result_set_id="rs_1", page=1)])
    run_turn = build(env, model, StubToolbox({"get_result_page": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.termination_reason is TerminationReason.STEP_BUDGET_EXHAUSTED
    assert result.status is TurnStatus.PARTIAL
    assert "结束" in result.answer


def test_a_model_failure_ends_the_turn_as_failed_not_complete(env):
    """1.0 把模型失败压成 ``status="complete"``，前端拿到的是一个「成功」的帧。"""
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel([RuntimeError("端点挂了")])
    run_turn = build(env, model, StubToolbox())
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.status is TurnStatus.FAILED
    assert result.termination_reason is TerminationReason.MODEL_ERROR
    assert "模型不可用" in result.answer


def test_an_unexpected_exception_still_produces_a_named_reason(env):
    """兜底那层 ``finally`` 的用处：将来的新出口忘了声明原因时，也不会留下半开的回合。

    反向验证：这里让工具箱抛出一个不在预期内的异常，断言收尾原因仍然是枚举里的一个值。
    """

    class Exploding(StubToolbox):
        def execute(self, name, arguments, *, scope_id, now):  # type: ignore[override]
            raise ValueError("工具内部炸了")

    _conversations, turns, conversation, _db = env
    model = ScriptedModel([tool_call("search_memories", "c1", query="海边"), final("好的。")])
    run_turn = build(env, model, Exploding())
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.termination_reason is TerminationReason.MODEL_ERROR
    assert turns.get(turn_id).status is TurnStatus.FAILED


# ---- 回合的边界 --------------------------------------------------------------


def test_a_turn_cannot_be_started_twice(env):
    _conversations, _turns, conversation, _db = env
    run_turn = build(env, ScriptedModel([final("好。")]), StubToolbox())
    turn_id = start(run_turn, conversation)
    run_turn.run(turn_id)

    with pytest.raises(TurnAlreadyClaimedError):
        run_turn.run(turn_id)


def test_starting_a_turn_on_a_missing_conversation_fails(env):
    run_turn = build(env, ScriptedModel([final("好。")]), StubToolbox())

    with pytest.raises(TurnNotFoundError):
        run_turn.start(TurnRequest(conversation_id="conv_nope", question=QUESTION))


def test_start_persists_a_pending_turn_before_anything_runs(env):
    """回合先落库，SSE 才有东西可订阅——1.0 的回合只活在进程内存里。"""
    _conversations, turns, conversation, _db = env
    run_turn = build(env, ScriptedModel([final("好。")]), StubToolbox())

    turn_id = start(run_turn, conversation)

    record = turns.get(turn_id)
    assert record.status is TurnStatus.PENDING
    assert record.question == QUESTION


def test_the_conversation_title_follows_the_first_question(env):
    conversations, _turns, conversation, _db = env
    manage = manage_of(env)

    manage.ensure_title(conversation.id, QUESTION)

    assert conversations.get(conversation.id).title == QUESTION


def test_an_existing_title_is_not_overwritten(env):
    conversations, _turns, _conversation, _db = env
    renamed = conversations.create(
        scope_id="album1", title="我自己起的", created_at=datetime(2026, 9, 22, tzinfo=UTC)
    )
    manage = manage_of(env)

    manage.ensure_title(renamed.id, QUESTION)

    assert conversations.get(renamed.id).title == "我自己起的"


def test_a_long_question_is_truncated_into_a_title(env):
    conversations, _turns, _conversation, _db = env
    manage = manage_of(env)

    created = manage.create(scope_id="album1")
    manage.ensure_title(created.id, "问" * 80)

    title = conversations.get(created.id).title
    assert len(title) == 25  # 24 个字 + 省略号
    assert title.endswith("…")


def test_an_empty_question_keeps_the_default_title(env):
    conversations, _turns, _conversation, _db = env
    manage = manage_of(env)

    created = manage.create(scope_id="album1")
    manage.ensure_title(created.id, "   ")

    assert conversations.get(created.id).title == "新对话"
