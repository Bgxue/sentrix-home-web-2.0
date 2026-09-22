"""回合状态、预算与终止原因的单元测试。

这些断言钉的是 ``docs/refactor/behaviour-baseline.md`` 里冻结的助手预算（8 步 / 6 调用 /
180s / ``max_search_calls=1``），以及 1.0 三个失败模式在 2.0 里的结构性修法。
"""

from __future__ import annotations

import pytest

from sentrix.domain.assistant.models import (
    ProgressStage,
    ProgressStatus,
    TerminationReason,
    ToolArgumentError,
    ToolCall,
)
from sentrix.domain.assistant.profile import DEFAULT_PROFILE, TurnProfile
from sentrix.domain.assistant.task_state import TERMINAL, TaskState, ToolCallRecord


def make_state(profile: TurnProfile = DEFAULT_PROFILE) -> TaskState:
    """本文件自用；跨文件的版本在 ``conftest.py`` 的 ``make_state`` fixture 里。"""
    return TaskState(
        turn_id="turn_1",
        conversation_id="conv_1",
        scope_id="album1",
        question="给我看看在海边的照片",
        profile=profile,
    )


def test_profile_matches_behaviour_baseline():
    assert DEFAULT_PROFILE.name == "goal_driven_candidate"
    assert DEFAULT_PROFILE.max_steps == 8
    assert DEFAULT_PROFILE.max_tool_calls == 6
    assert DEFAULT_PROFILE.wall_time_s == 180.0
    assert DEFAULT_PROFILE.max_search_calls == 1


def test_termination_reason_has_no_empty_member():
    """1.0 的 25,112 条轨迹里有 11,304 条读不到这个字段。

    根因有两条：2026-09-10 之前的轨迹根本没写这个键，以及政策拒答那条提前 return 绕过了
    收尾分类器（72 条空串）。2.0 的防线是类型——``TurnResult.termination_reason`` 的类型
    就是这个枚举，不是 ``str``、更不是 ``str | None``。这里钉住「每个成员都有非空的
    字符串值」，任何新加的成员都不会退化成空串。
    """
    values = [reason.value for reason in TerminationReason]
    assert all(values)
    assert len(set(values)) == len(values)


def test_emit_allocates_step_index_itself():
    """``step_index`` 由 emit 分配，调用方无从指定——1.0 靠每个发出点自己数。"""
    state = make_state()

    first = state.emit(ProgressStage.PLANNING, ProgressStatus.COMPLETE, "一")
    second = state.emit(ProgressStage.THINKING, ProgressStatus.RUNNING, "二")

    assert (first.step_index, second.step_index) == (1, 2)
    assert [event.step_index for event in state.progress] == [1, 2]


def test_emit_records_a_timestamp_and_payload():
    state = make_state()

    event = state.emit(ProgressStage.PLANNING, ProgressStatus.COMPLETE, "已确认需要哪些证据。", kind="plan")

    # 不断言具体时间（它是机器时钟的函数），只钉住格式与 kind 的传递。
    assert event.timestamp[:2] == "20"
    assert "T" in event.timestamp
    assert event.kind == "plan"


def test_model_step_budget_ignores_the_final_reserve():
    """``can_step`` 只看墙钟是否走完；``can_call_tool`` 才检查 final_reserve。

    1.0 的这个非对称是刻意的（``budget_manager.py:38-51``）：最后 10 秒要留给写答案，
    不能拿去调工具。反过来把 final_reserve 也套到模型步上，就会退化成「预算耗尽后空答案」。
    """
    state = make_state()
    state.started_at -= DEFAULT_PROFILE.wall_time_s - DEFAULT_PROFILE.final_reserve_s / 2

    assert state.wall_remaining() < DEFAULT_PROFILE.final_reserve_s
    assert state.can_step() is True
    assert state.can_call_tool() is False


def test_step_budget_stops_at_the_profile_limit():
    state = make_state()
    state.model_steps = DEFAULT_PROFILE.max_steps

    assert state.can_step() is False


def test_tool_budget_counts_only_successful_calls():
    """被拒绝的调用与缓存回放都不计预算——1.0 只在执行成功后才 ``record_tool_call``。"""
    state = make_state()
    for status in ("ok", "ok", "denied", "error", "replayed"):
        state.record(ToolCallRecord(call_id="c", tool="search_memories", signature=status, status=status))

    assert state.tool_calls == 2


def test_search_calls_counts_every_issued_search():
    """``search_calls`` 数的是「模型发出了几次 search_memories」，被拒的那次也要算。

    1.0 的计数器在**每次** search action 上自增（含被拒的那次，``runtime.py:2992``）。
    不算的话，模型可以靠「先被拒一次、再重发」绕过 max_search_calls。
    """
    state = make_state()
    state.record(ToolCallRecord(call_id="c1", tool="search_memories", signature="a", status="ok"))
    state.record(ToolCallRecord(call_id="c2", tool="search_memories", signature="b", status="denied"))

    assert state.search_calls == 2


def test_signature_ignores_argument_order():
    left = TaskState.signature("get_result_page", {"page": 2, "result_set_id": "rs_1"})
    right = TaskState.signature("get_result_page", {"result_set_id": "rs_1", "page": 2})

    assert left == right


def test_cached_returns_only_successful_records():
    state = make_state()
    state.record(ToolCallRecord(call_id="c1", tool="search_memories", signature="s", status="error"))

    assert state.cached("s") is None

    state.record(
        ToolCallRecord(
            call_id="c2",
            tool="search_memories",
            signature="s",
            status="ok",
            model_visible={"result_set_id": "rs_1"},
        )
    )

    assert state.cached("s") is not None


def test_replayed_is_false_until_a_replay_is_recorded():
    state = make_state()

    assert state.replayed("s") is False
    state.record(ToolCallRecord(call_id="c1", tool="search_memories", signature="s", status="replayed"))
    assert state.replayed("s") is True


def test_consecutive_failures_stops_at_a_different_tool():
    state = make_state()
    state.record(
        ToolCallRecord(
            call_id="c1", tool="search_memories", signature="a", status="error", failure_reason="empty_query"
        )
    )
    state.record(
        ToolCallRecord(
            call_id="c2", tool="search_memories", signature="b", status="error", failure_reason="empty_query"
        )
    )
    state.record(
        ToolCallRecord(
            call_id="c3", tool="get_result_page", signature="c", status="error", failure_reason="empty_query"
        )
    )

    assert state.consecutive_failures("search_memories", "empty_query") == 0
    assert state.consecutive_failures("get_result_page", "empty_query") == 1


def test_finish_records_the_reason_and_closes_the_phase():
    state = make_state()

    assert state.termination_reason is None
    assert state.finish(TerminationReason.ANSWERED) is TerminationReason.ANSWERED
    assert state.termination_reason is TerminationReason.ANSWERED
    assert state.phase == TERMINAL


def test_block_guard_code_fires_once_per_code():
    """门禁只提醒一次。1.0 的零检索拦截没有标志位，模型坚持不调工具就能烧光 8 个模型步。"""
    state = make_state()

    assert state.block_guard_code("evidence_gate") is True
    assert state.block_guard_code("evidence_gate") is False
    assert state.block_guard_code("placeholder_leak") is True
    # 拦下不等于改变阶段——阶段由主循环切，标志位只管「这条规则提过醒了没有」。
    assert state.guard_blocked_codes == {"evidence_gate", "placeholder_leak"}


def test_tool_call_parses_a_json_object():
    call = ToolCall(call_id="c1", name="search_memories", arguments_json='{"query": "海边"}')

    assert call.parse() == {"query": "海边"}


def test_tool_call_rejects_broken_json():
    call = ToolCall(call_id="c1", name="search_memories", arguments_json="{oops")

    with pytest.raises(ToolArgumentError):
        call.parse()


def test_tool_call_rejects_a_non_object_payload():
    call = ToolCall(call_id="c1", name="search_memories", arguments_json="[1, 2]")

    with pytest.raises(ToolArgumentError):
        call.parse()


def test_empty_arguments_parse_to_an_empty_mapping():
    assert ToolCall(call_id="c1", name="search_memories").parse() == {}
