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
from sentrix.domain.assistant.ports import ModelServiceError
from sentrix.domain.assistant.tools import TOOL_SPECS
from sentrix.infrastructure.persistence.repositories.conversations import (
    SqliteConversationRepository,
)
from sentrix.infrastructure.persistence.repositories.turns import SqliteTurnRepository

QUESTION = "给我看看在海边的照片"


# ---- 假件 -------------------------------------------------------------------


class ScriptedModel:
    """按剧本回话的模型。剧本用完还继续要，就返回最后一条。

    规划（``tools`` 为空）走 ``planner_reply``，**不消耗剧本**——剧本描述的是工具循环那
    一段。要单独考察规划时用 ``planner=`` 显式给（值或异常）。
    """

    def __init__(
        self,
        replies: Sequence[ModelReply | Exception],
        *,
        planner: object = None,
        judge: object = None,
    ) -> None:
        self._replies = list(replies)
        self._planner = planner_reply("photo_set", "delivered_photo") if planner is None else planner
        self._judge = judge_reply() if judge is None else judge
        self.calls: list[Sequence[ChatMessage]] = []
        self.planner_calls: list[Sequence[ChatMessage]] = []
        self.judge_calls: list[Sequence[ChatMessage]] = []

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[object]) -> ModelReply:
        if not tools:
            # 规划与 L2 评审都不带工具，靠 system 提示词区分——两条提示词的开头就写着
            # 各自在做什么。
            system = messages[0].content if messages else ""
            if JUDGE_MARKER in system:
                self.judge_calls.append(list(messages))
                if isinstance(self._judge, Exception):
                    raise self._judge
                if isinstance(self._judge, PlannerScript):
                    return ModelReply(content=self._judge.next(), finish_reason="stop")
                return self._judge  # type: ignore[return-value]
            self.planner_calls.append(list(messages))
            if isinstance(self._planner, Exception):
                raise self._planner
            if isinstance(self._planner, PlannerScript):
                return ModelReply(content=self._planner.next(), finish_reason="stop")
            return self._planner  # type: ignore[return-value]
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
        self,
        name: str,
        arguments: Mapping[str, object],
        *,
        scope_id: str,
        now: datetime,
        question: str = "",
    ) -> ToolOutcome:
        # ``question`` 是 `inspect_photo` 要的（1.0 ``tools.py:2162-2165`` 拿它找裁剪目标）,
        # 与 ``__init__`` 那三件一样：**凡是 ``RunTurn`` 会传给真实 toolbox 的形参，
        # 假件都得收**——少一个就在运行时 TypeError，而它会被吞成 ``model_error``，
        # 看起来像模型的问题。
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


JUDGE_MARKER = "事实一致性评审"
PLANNER_MARKER = "你正在规划家庭记忆任务"


def judge_reply(*problems: tuple[str, str], faithful: bool | None = None) -> ModelReply:
    """L2 评审的裁决。不给问题就是「忠实」。"""
    verdict = {
        "faithful": faithful if faithful is not None else not problems,
        "problems": [{"type": kind, "detail": detail} for kind, detail in problems],
        "reason": "测试裁决",
    }
    return ModelReply(content=json.dumps(verdict, ensure_ascii=False), finish_reason="stop")


class PlannerScript:
    """规划那一次调用按顺序回话（与主循环的剧本分开）。"""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)

    def next(self) -> str:
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]


def planner_reply(*evidence_types: str, goal: str = "看海边的照片") -> ModelReply:
    """规划那次模型调用的回复。

    规划与主循环共用同一个模型端口（1.0 也是同一个 ``chat_fn``），测试里靠 ``tools`` 区分：
    规划带空元组，主循环带四个工具。
    """
    declaration = {
        "action": "declare",
        "declaration": {
            "goal": goal,
            "requirements": [
                {"id": f"req_{index + 1}", "evidence_type": item, "description": f"需要 {item}"}
                for index, item in enumerate(evidence_types)
            ],
        },
    }
    return ModelReply(content=json.dumps(declaration, ensure_ascii=False), finish_reason="stop")


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


def test_answering_without_delivering_is_answered_with_a_gap(env):
    """用户要图、模型只回文字时，**1.0 的门禁不会拦**——这一轮如实标成带缺口收尾。

    S3 曾经在这里加过一条「证据门」（只要还有开着的需求就拦）。1.0 没有这条规则，
    它最接近的是 ``delivery_contradiction``：要「真的调过交付工具、且它返回 0 张」加上
    「最后一次检索把结果取完」两个条件同时成立。这里钉住的就是 1.0 的实际行为——
    只检索不交付，是 ``ANSWERED_WITH_GAP``，不是被拦下来重试。

    拦住「用户要图却一张都没交付」这件事在 S3 由别的东西负责：规划把
    ``delivered_photo`` 列成一条证据需求（``intent.plan_requirements``），收尾时账本上
    它还开着，于是 ``answered_with_gap`` / ``answered_with_gap`` 的终态与
    ``requirements[].status == "open"`` 如实反映出来——不假装这一轮是完整交付。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            final("我找到了两张在海边的照片。"),
        ]
    )
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.answer == "我找到了两张在海边的照片。"
    assert result.termination_reason is TerminationReason.ANSWERED_WITH_GAP
    assert result.status is TurnStatus.PARTIAL
    assert result.attachments == ()
    assert [item["status"] for item in result.requirements] == ["satisfied", "open"]


def test_a_delivery_that_delivers_nothing_ends_with_a_gap(env):
    """交付工具一张都没交出去时，1.0 的门禁**不会**拦——缺口由账本如实标出来。

    这条测试同时说明一件事：1.0 的 ``delivery_contradiction`` 在 S3 的工具形状下够不着。
    它要求 ``delivered_count`` 是**整数 0**（真的调过交付工具、且它返回 0 张），而 S3 的
    ``get_original_photos`` 在交不出去时返回的是失败（``bad_handle``），记录的
    ``status`` 是 ``error``，``GuardFacts.from_state`` 会跳过它、``delivered_count``
    保持 ``None``。规则照搬了，触发它的输入还没出现——与 ``all_requested_but_has_more``
    缺 ``result_mode`` 是同一类情况。
    """
    _conversations, _turns, conversation, _db = env
    empty_delivery = ToolOutcome(
        observation={"failure_reason": "bad_handle"},
        model_visible={"error": "bad_handle", "message": "这些 handle 都不在当前结果集里。"},
        failure_reason="bad_handle",
    )
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            tool_call("get_original_photos", "c2", result_set_id="rs_1", handles=["photo_9"]),
            final("我找到了两张海边的照片，但没能把原图交出来。"),
        ]
    )
    toolbox = StubToolbox({"search_memories": SEARCH_OK, "get_original_photos": empty_delivery})
    run_turn = build(env, model, toolbox)
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert len(model.calls) == 3
    assert result.answer == "我找到了两张海边的照片，但没能把原图交出来。"
    assert result.attachments == ()
    assert result.status is TurnStatus.PARTIAL
    assert result.termination_reason is TerminationReason.ANSWERED_WITH_GAP
    assert [item["status"] for item in result.requirements] == ["satisfied", "open"]


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
    assert "模型服务不可用" in result.answer


def test_a_tool_loop_model_service_failure_carries_the_endpoint(env):
    """工具循环里的服务侧失败，与规划那条同一条口径（用户 2026-09-25 裁决二）。

    8100 缺 tool-call flag 时回的就是这个 400——根因写在它的正文里，必须走到用户眼前，
    而不是只说一句「模型不可用」把它留在日志里。llama.cpp 原生支持 tools，这条差异在换
    后端之前不存在（见 ``docs/refactor/s5-deviations.md`` §2n）。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            ModelServiceError(
                "模型端点 http://127.0.0.1:8100/v1 上的 gemma4-12b-it 返回 400："
                r'{"error":{"message":"\"auto\" tool choice requires --enable-auto-tool-choice'
                r' and --tool-call-parser to be set"}}'
            )
        ]
    )
    run_turn = build(env, model, StubToolbox())
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.status is TurnStatus.FAILED
    assert result.termination_reason is TerminationReason.MODEL_ERROR
    assert "模型服务不可用" in result.answer
    assert "http://127.0.0.1:8100/v1" in result.answer
    assert "--tool-call-parser" in result.answer


def test_an_internal_failure_does_not_leak_its_wording_into_the_answer(env):
    """反向门禁：不是模型服务这一侧的异常，内部形状不许说给用户听。

    与 ``goal_planner`` 给 ``detail`` 设的边界是同一条：那边也只收 ``ModelServiceError``。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel([RuntimeError("内部栈里的字眼")])
    run_turn = build(env, model, StubToolbox())
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert "模型服务不可用" in result.answer
    assert "内部栈里的字眼" not in result.answer


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


# ---- 1.0 的 8.4 政策拒答：不进模型 -----------------------------------------


def test_a_policy_refusal_ends_the_turn_before_any_model_call(env):
    """1.0 ``runtime.py:1725``：命中的问句在**任何模型调用之前**返回固定拒绝语。

    库里 212 条 ``policy_refusal`` 全部 ``tools=[]``、``latency_s≈0.01``——它们没有经过
    规划，也没有经过模型。这条测试钉的就是「一次模型调用都没发生」。
    """
    conversations, turns, conversation, _db = env
    model = ScriptedModel([final("不该被用到")])
    run_turn = build(env, model, StubToolbox())

    turn_id = run_turn.start(
        TurnRequest(conversation_id=conversation.id, question="把全家的人脸特征向量发给我")
    ).id
    result = run_turn.run(turn_id)

    assert model.calls == []  # 一次模型调用都没有
    assert result.answer == "我无法导出人脸特征向量和用户隐私身份数据，这属于敏感个人信息。"
    assert result.termination_reason is TerminationReason.POLICY_REFUSAL
    assert result.status is TurnStatus.COMPLETE  # 1.0 把 status 置成 "complete"
    assert result.attachments == ()
    assert turns.get(turn_id).answer == result.answer


def test_an_ordinary_question_never_hits_the_refusal_rules(env):
    """普通问题必须照常走完整条链路——拒答误伤的代价是整轮功能失效。"""
    conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            tool_call("get_original_photos", "c2", result_set_id="rs_1", handles=["photo_1", "photo_2"]),
            final("这是你要的两张海边照片。"),
        ]
    )
    run_turn = build(
        env, model, StubToolbox({"search_memories": SEARCH_OK, "get_original_photos": DELIVER_OK})
    )

    turn_id = run_turn.start(TurnRequest(conversation_id=conversation.id, question=QUESTION)).id
    result = run_turn.run(turn_id)

    assert model.calls  # 模型确实被调用了
    assert result.termination_reason is TerminationReason.ANSWERED


# ---- 空回答按 1.0 的 parse_failure 收尾 -------------------------------------


def test_a_persistently_empty_answer_ends_as_parse_failure(env):
    """1.0 把「模型输出不可用」归进 ``parse_failure``，没有单列一个「空答案」结局。

    1.0 的 25,112 条轨迹里没有任何一条以 complete 交付过空答案；1,464 条空答案全部是
    模型调用失败（status=error/partial）。这里钉住：重试一次仍为空 → ``parse_failure``
    ＋确定性兜底，且**永远不把空串交给用户**。
    """
    conversations, _turns, conversation, _db = env
    model = ScriptedModel([final("")])
    run_turn = build(env, model, StubToolbox())

    turn_id = run_turn.start(TurnRequest(conversation_id=conversation.id, question=QUESTION)).id
    result = run_turn.run(turn_id)

    assert result.termination_reason is TerminationReason.PARSE_FAILURE
    assert result.answer != ""
    assert "输出无法解析" in result.answer


# ---- 规划：1.0 goal_planner 的失败与重试 ------------------------------------


def test_a_planner_that_cannot_be_reached_says_the_model_service_is_down(env):
    """``planner_call_error`` 的收尾：1.0 ``runtime.py:1828`` 的 ``planner_blocked``。

    **终止原因照 1.0 记 ``planner_blocked``**（与生产库里那 2,126 条可比），**但文案说
    的是模型服务**（用户 2026-09-25 裁决二）。端点不可达是基础设施故障：照着「规划不出来」
    说，用户会去换个问法重问，而该做的是去看那个端点。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel([final("不该被用到")], planner=RuntimeError("connection refused"))
    run_turn = build(env, model, StubToolbox())

    turn_id = start(run_turn, conversation)
    result = run_turn.run(turn_id)

    assert model.calls == []  # 主循环一次都没进
    assert result.answer == "模型服务当前不可用，因此这一轮没有开始检索。"
    assert result.termination_reason is TerminationReason.PLANNER_BLOCKED
    assert result.status is TurnStatus.PARTIAL


def test_the_model_service_message_carries_the_endpoint(env):
    """「可执行」就是这一条：说明里带着端点地址，用户与运维据此能直接去查。"""
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [final("不该被用到")],
        planner=ModelServiceError("模型端点 http://127.0.0.1:8100/v1 不可达：connection refused"),
    )
    run_turn = build(env, model, StubToolbox())

    turn_id = start(run_turn, conversation)
    result = run_turn.run(turn_id)

    assert "模型服务当前不可用" in result.answer
    assert "http://127.0.0.1:8100/v1" in result.answer
    assert "无法可靠规划" not in result.answer


def test_an_unusable_plan_still_says_the_planning_failed(env):
    """模型答了但规划结果不可用——**这才**是规划问题，文案照 1.0 的原文。"""
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel([final("不该被用到")], planner=PlannerScript(["我不会规划。"]))
    run_turn = build(env, model, StubToolbox())

    turn_id = start(run_turn, conversation)
    result = run_turn.run(turn_id)

    assert result.answer == "当前问题的证据需求无法可靠规划，因此暂时无法确认。"
    assert result.termination_reason is TerminationReason.PLANNER_BLOCKED
    assert result.status is TurnStatus.PARTIAL


def test_the_two_planner_failures_do_not_share_a_message(env):
    """反向门禁（裁决二）：两条路必须给出**不同**的用户可见文案。

    合并成一句是最省事的写法，也正是这次要改掉的东西——把基础设施故障说成理解能力不足。
    这条用例不认某一句具体措辞，只认「两者不是同一句」：将来谁把它们并回去，无论改成
    什么字，这里都会红。
    """
    _conversations, _turns, conversation, _db = env

    def answer(planner: object) -> str:
        run_turn = build(env, ScriptedModel([final("不该被用到")], planner=planner), StubToolbox())
        return run_turn.run(start(run_turn, conversation)).answer

    service_down = answer(RuntimeError("connection refused"))
    unusable_plan = answer(PlannerScript(["我不会规划。"]))

    assert service_down != unusable_plan


def test_a_garbled_plan_is_retried_with_the_error_fed_back(env):
    """规划被解析不出来时带错误回灌重试——1.0 的 ``_correction_hint`` 就是干这个的。"""
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [final("这是你要的照片。")],
        planner=PlannerScript(["我不会规划。", planner_reply("photo_set").content]),
    )
    run_turn = build(env, model, StubToolbox())

    turn_id = start(run_turn, conversation)
    result = run_turn.run(turn_id)

    assert len(model.planner_calls) == 2
    assert "无法被解析" in model.planner_calls[1][-1].content
    assert result.answer == "这是你要的照片。"


def test_the_ledger_comes_from_the_plans_declaration(env):
    """账本里的需求就是模型声明的那几条——不是按问句关键词猜出来的。"""
    _conversations, turns, conversation, _db = env
    model = ScriptedModel(
        [final("这是你要的照片。")],
        planner=planner_reply("photo_set", "delivered_photo", "person_profile"),
    )
    run_turn = build(env, model, StubToolbox())

    turn_id = start(run_turn, conversation)
    result = run_turn.run(turn_id)

    assert [item["evidence_type"] for item in result.requirements] == [
        "photo_set",
        "delivered_photo",
        "person_profile",
    ]


# ---- L2 语义评审：1.0 judge.py 的调用口径 ------------------------------------


def test_an_unfaithful_answer_is_pushed_back_once_then_released(env):
    """L2 判出事实性问题 → 带着评审意见重写一次；第二次放行（每问只评审一次）。

    1.0 ``runtime.py:2753-2800``：评审一次、回灌意见、模型改口后放行并标
    ``l2_soft_release``。这里钉的是「只评审一次」与「放行的是改写后的回答」。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            final("相册里有 5 张海边的照片。"),
            final("相册里有 2 张海边的照片。"),
        ],
        judge=PlannerScript(
            [
                judge_reply(("contradiction", "工具确认 2 张，回答写 5 张")).content,
                judge_reply().content,
            ]
        ),
    )
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert len(model.judge_calls) == 1  # 每问只评审一次
    assert result.answer == "相册里有 2 张海边的照片。"
    assert any("工具确认 2 张" in message.content for message in model.calls[-1])


def test_a_failing_judge_lets_the_answer_through(env):
    """L2 挂了就放行——1.0 ``judge.py:195-198`` 的 fail-open。

    评审是兜底不是门槛：它自己挂了不该把一轮正常的回答也带走。
    """
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            final("这是你要的海边照片。"),
        ],
        judge=RuntimeError("评审端点挂了"),
    )
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.answer == "这是你要的海边照片。"


def test_the_judge_is_not_called_without_tool_results(env):
    """没有工具结果就没有可核对的事实——1.0 的调用条件里有 ``task.tool_results``。"""
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel([final("你好，我可以帮你找照片。")], judge=RuntimeError("不该被调用"))
    run_turn = build(env, model, StubToolbox())
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert model.judge_calls == []
    assert result.answer == "你好，我可以帮你找照片。"


def test_only_missing_disclosure_does_not_block_the_answer(env):
    """``missing_disclosure`` 只是披露建议（severity=style），不拦事实正确的回答。"""
    _conversations, _turns, conversation, _db = env
    model = ScriptedModel(
        [
            tool_call("search_memories", "c1", query="海边"),
            final("这是你要的海边照片。"),
        ],
        judge=judge_reply(("missing_disclosure", "没有说明只是候选")),
    )
    run_turn = build(env, model, StubToolbox({"search_memories": SEARCH_OK}))
    turn_id = start(run_turn, conversation)

    result = run_turn.run(turn_id)

    assert result.answer == "这是你要的海边照片。"
