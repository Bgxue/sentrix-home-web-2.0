"""答案门禁与兜底答复的单元测试。

门禁只做确定性判定，**不做语义等价**——「三个人」和「3」是不是同一个数，正则判不对，
只会误伤事实正确的回答（1.0 为此把原来的一批正则规则删掉了，见 ``final_guard.py:1-22``）。
语义评审是 S6 的事。

每条规则都配一个反向用例：构造函数让它通过，证明它不是恒真的。
"""

from __future__ import annotations

from sentrix.domain.assistant.emergency import clarify_answer, fallback_answer
from sentrix.domain.assistant.evidence_ledger import (
    EvidenceLedger,
    EvidenceRequirement,
    EvidenceType,
)
from sentrix.domain.assistant.final_guard import FinalGuard, answered_with_gap
from sentrix.domain.assistant.models import TerminationReason
from sentrix.domain.assistant.task_state import TaskState, ToolCallRecord

DESCRIPTIONS = {
    EvidenceType.PHOTO_SET: "从相册里取到与问题相关的照片。",
    EvidenceType.DELIVERED_PHOTO: "把要展示的照片交付给用户，让它们真的能打开。",
    EvidenceType.PERSON_PROFILE: "读取这位已确认人物的家庭角色。",
}


def ledger(*evidence_types: EvidenceType) -> EvidenceLedger:
    return EvidenceLedger(
        scope_id="album1",
        requirements=[
            EvidenceRequirement(id=f"r{i}", evidence_type=t, description=DESCRIPTIONS[t])
            for i, t in enumerate(evidence_types)
        ],
    )


def state_with_search(failure_reason: str = "") -> TaskState:
    from sentrix.domain.assistant.profile import DEFAULT_PROFILE

    state = TaskState(
        turn_id="turn_1",
        conversation_id="conv_1",
        scope_id="album1",
        question="给我看看在海边的照片",
        profile=DEFAULT_PROFILE,
    )
    state.record(
        ToolCallRecord(
            call_id="call_1",
            tool="search_memories",
            signature="s",
            status="ok" if not failure_reason else "error",
            failure_reason=failure_reason,
        )
    )
    return state


def check(
    answer: str,
    *,
    evidence: tuple[EvidenceType, ...] = (),
    state: TaskState | None = None,
    empty: bool = False,
):
    return FinalGuard().check(
        answer=answer,
        ledger=ledger(*evidence),
        state=state if state is not None else state_with_search(),
        search_returned_nothing=empty,
    )


# ---- 安全 -------------------------------------------------------------------


def test_an_internal_asset_id_in_the_answer_is_blocked():
    verdict = check("这张照片的编号是 asset_9f3a21b7c4，可以打开。")

    assert verdict.passed is False
    assert verdict.code == "internal_reference_leak"


def test_a_photo_handle_in_the_answer_is_blocked():
    verdict = check("我找到的是 photo_3 这张。")

    assert verdict.passed is False
    assert verdict.code == "internal_reference_leak"


def test_a_normal_answer_is_not_blocked_by_the_safety_rule():
    """反向用例：没有内部标识时这条规则必须放行，否则它就是恒真的。"""
    assert check("这是你要的海边照片。").passed is True


# ---- 占位符 -----------------------------------------------------------------


def test_an_unfilled_placeholder_is_blocked():
    verdict = check("照片拍摄于[地点名称]，你可以看看。")

    assert verdict.passed is False
    assert verdict.code == "placeholder_leak"


def test_a_bracketed_but_filled_answer_is_not_blocked():
    assert check("海边的照片一共有两张。").passed is True


# ---- 存在性矛盾 --------------------------------------------------------------


def test_denying_without_ever_searching_is_blocked():
    state = state_with_search()
    state.records.clear()

    verdict = FinalGuard().check(
        answer="我没有找到相关照片。",
        ledger=ledger(EvidenceType.PHOTO_SET),
        state=state,
        search_returned_nothing=False,
    )

    assert verdict.passed is False
    assert verdict.code == "denial_without_search"


def test_claiming_a_hit_when_the_search_was_empty_is_blocked():
    verdict = check("我找到了这些海边的照片。", empty=True)

    assert verdict.passed is False
    assert verdict.code == "fabrication_from_empty"


def test_a_hedged_answer_about_an_empty_search_is_not_blocked():
    """「无法确认」是诚实收尾，不是编造。"""
    assert check("这次没有匹配到海边照片，可能没有拍到过。", empty=True).passed is True


def test_denying_after_a_search_is_not_blocked():
    """反向用例：检索过之后说没找到，是合法结论。"""
    assert check("我没有找到海边照片。").passed is True


# ---- 证据门 -----------------------------------------------------------------


def test_an_open_requirement_blocks_the_first_final_answer():
    verdict = check("我找到了这些海边的照片。", evidence=[EvidenceType.DELIVERED_PHOTO])

    assert verdict.passed is False
    assert verdict.code == "evidence_gate"
    assert "交付" in verdict.message


def test_a_satisfied_ledger_does_not_block():
    answer_ledger = ledger(EvidenceType.PHOTO_SET)
    answer_ledger.satisfy(EvidenceType.PHOTO_SET, call_id="call_1")

    verdict = FinalGuard().check(
        answer="这是你要的海边照片。",
        ledger=answer_ledger,
        state=state_with_search(),
        search_returned_nothing=False,
    )

    assert verdict.passed is True


def test_the_evidence_gate_does_not_fire_when_tools_are_exhausted():
    """预算没了就不再拦——拦了也没有工具可用，只会让回合以空答案收场。"""
    state = state_with_search()
    for index in range(state.profile.max_tool_calls):
        state.record(
            ToolCallRecord(call_id=f"c{index}", tool="get_result_page", signature=f"s{index}", status="ok")
        )

    verdict = FinalGuard().check(
        answer="这是你要的海边照片。",
        ledger=ledger(EvidenceType.DELIVERED_PHOTO),
        state=state,
        search_returned_nothing=False,
    )

    assert verdict.passed is True


def test_the_evidence_gate_does_not_fire_before_any_tool_call():
    """一次工具都没调的回合由 denial_without_search 管，两条规则不要重叠。"""
    state = state_with_search()
    state.records.clear()

    verdict = FinalGuard().check(
        answer="这是你要的海边照片。",
        ledger=ledger(EvidenceType.PHOTO_SET),
        state=state,
        search_returned_nothing=False,
    )

    assert verdict.passed is True


def test_answered_with_gap_tracks_the_ledger():
    complete = ledger(EvidenceType.PHOTO_SET)
    complete.satisfy(EvidenceType.PHOTO_SET, call_id="call_1")

    assert answered_with_gap(complete) is False
    assert answered_with_gap(ledger(EvidenceType.PHOTO_SET)) is True


# ---- 兜底答复 ----------------------------------------------------------------


def test_the_fallback_reports_what_was_actually_retrieved():
    answer = fallback_answer(
        reason=TerminationReason.STEP_BUDGET_EXHAUSTED,
        ledger=ledger(EvidenceType.DELIVERED_PHOTO),
        photo_count=6,
        delivered_count=0,
    )

    assert "6 张" in answer
    assert "交付" in answer
    assert "模型步数用尽" in answer


def test_the_fallback_does_not_pretend_a_photo_exists_when_none_was_found():
    """1.0 的兜底只有一条尾句（``emergency.py:80``），「有检索结果但被丢弃」与「真的
    一无所获」共用同一句话。2.0 按事实分档。"""
    answer = fallback_answer(
        reason=TerminationReason.MODEL_ERROR,
        ledger=ledger(),
        photo_count=0,
        delivered_count=0,
    )

    assert "没有检索到照片" in answer
    assert "模型不可用" in answer


def test_clarify_answer_names_the_reason():
    assert "超时" in clarify_answer(TerminationReason.TIMEOUT)
