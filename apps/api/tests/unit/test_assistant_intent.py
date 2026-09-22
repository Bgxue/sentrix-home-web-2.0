"""规划的单元测试：提示词、解析、归一、带错误回灌的重试。

钉的是 1.0 ``backend/agent_runtime/goal_planner.py`` 的行为：解析器的有限修复边界、
``scope_mismatch`` / ``invalid_planner_action`` 的判据、重试次数与回灌内容、
以及 ``planner_call_error`` 只在模型调用**抛异常**时出现。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from sentrix.application.assistant.goal_planner import GoalPlanner, PlannerResult
from sentrix.domain.assistant.evidence_ledger import EvidenceType
from sentrix.domain.assistant.intent import (
    MIN_REQUIREMENTS,
    build_prompt,
    correction_hint,
    history_prompt_suffix,
    normalize_payload,
    parse_declaration,
    parse_json,
    planner_evidence_types,
    root_brace_balance,
)
from sentrix.domain.assistant.models import ChatMessage, ModelReply

SCOPE = "album_cbfbfce14789"


def declaration(*evidence_types: str, goal: str = "找海边的照片", scope: str | None = None) -> str:
    payload: dict = {
        "action": "declare",
        "declaration": {
            "goal": goal,
            "requirements": [
                {"id": f"req_{index + 1}", "evidence_type": item, "description": f"需要 {item}"}
                for index, item in enumerate(evidence_types)
            ],
        },
    }
    if scope is not None:
        payload["declaration"]["scope_id"] = scope
    return json.dumps(payload, ensure_ascii=False)


# ---- 提示词 -----------------------------------------------------------------


def test_the_prompt_lists_exactly_the_s3_evidence_registry():
    prompt = build_prompt()

    for item in planner_evidence_types():
        assert f"- {item}" in prompt
    assert planner_evidence_types() == ("delivered_photo", "person_profile", "photo_set")


def test_the_prompt_never_shows_a_real_scope_id():
    """1.0 的教训：示例里出现真实 scope_id 会被小模型整段照抄，抄错一位就 scope_mismatch。"""
    assert SCOPE not in build_prompt()
    assert "scope_id" in build_prompt()  # 提示词要明确说「不要输出 scope_id」


def test_history_is_folded_into_the_system_text_not_a_second_system_message():
    assert history_prompt_suffix("") == ""
    assert history_prompt_suffix("user: 你好") == "\n\n历史对话背景：\nuser: 你好"


def test_each_failure_reason_has_its_own_correction_hint():
    assert "scope_id" in correction_hint("scope_mismatch")
    assert "完整闭合" in correction_hint("invalid_planner_action")
    assert "至少要声明一条" in correction_hint("no_requirements")
    # 认不出来的原因也要给一句通用的修正指令，不能返回空串。
    assert correction_hint("something_else").startswith("你上一次的输出无法被解析")


# ---- 解析器的有限修复 -------------------------------------------------------


def test_a_plain_object_parses():
    assert parse_json('{"action":"declare"}') == {"action": "declare"}


def test_a_fenced_object_parses():
    assert parse_json('```json\n{"action":"declare"}\n```') == {"action": "declare"}


def test_a_trailing_fence_without_a_leading_one_parses():
    """1.0 的注释：结尾围栏必须独立剥离，否则补 ``}`` 的修复路径会被跳过，实测 5 题里 4 题因此失败。"""
    assert parse_json('{"action":"declare"}\n```') == {"action": "declare"}


def test_one_missing_root_brace_is_repaired():
    assert parse_json('{"action":"declare","declaration":{"goal":"g"}') == {
        "action": "declare",
        "declaration": {"goal": "g"},
    }


def test_more_than_one_missing_brace_is_not_repaired():
    """只在「根括号正好差一个」时补——乱补会把坏输出修成一个看似合法的错输出。"""
    with pytest.raises(ValueError):
        parse_json('{"action":"declare","declaration":{"goal":"g"')


def test_root_brace_balance_ignores_braces_inside_strings():
    assert root_brace_balance('{"a":"}"}') == 0
    assert root_brace_balance('{"a":"\\""}') == 0
    assert root_brace_balance("{") == 1


# ---- 归一 -------------------------------------------------------------------


def test_scope_and_goal_are_injected_when_the_model_omits_them():
    normalized = normalize_payload(
        {"action": "declare", "requirements": []}, scope_id=SCOPE, default_goal="问题"
    )

    assert normalized["declaration"]["scope_id"] == SCOPE
    assert normalized["declaration"]["goal"] == "问题"


def test_string_requirements_are_expanded_and_deduplicated():
    normalized = normalize_payload(
        {"action": "declare", "requirements": ["photo", "image", "person"]},
        scope_id=SCOPE,
        default_goal="问题",
    )

    assert [item["evidence_type"] for item in normalized["declaration"]["requirements"]] == [
        "photo_set",
        "person_profile",
    ]


@pytest.mark.parametrize(
    ("alias", "expected"),
    (
        ("photo", "photo_set"),
        ("image", "photo_set"),
        ("memory_asset", "photo_set"),
        ("deliver", "delivered_photo"),
    ),
)
def test_known_aliases_map_onto_the_registry(alias: str, expected: str):
    normalized = normalize_payload(
        {"action": "declare", "requirements": [{"evidence_type": alias}]},
        scope_id=SCOPE,
        default_goal="问题",
    )

    assert normalized["declaration"]["requirements"][0]["evidence_type"] == expected


def test_an_unknown_alias_is_left_alone_for_validation_to_reject():
    """认不出来的别名不能静默改写成某个类型——那等于替模型编需求。"""
    normalized = normalize_payload(
        {"action": "declare", "requirements": [{"evidence_type": "location_metadata"}]},
        scope_id=SCOPE,
        default_goal="问题",
    )

    assert normalized["declaration"]["requirements"][0]["evidence_type"] == "location_metadata"
    assert parse_declaration(declaration("location_metadata"), scope_id=SCOPE, question="问题") == (
        None,
        "invalid_planner_action",
    )


# ---- 校验 -------------------------------------------------------------------


def test_a_well_formed_declaration_parses_into_requirements():
    requirements, reason = parse_declaration(
        declaration("photo_set", "delivered_photo"), scope_id=SCOPE, question="问题"
    )

    assert reason == ""
    assert requirements is not None
    assert [item.evidence_type for item in requirements] == [
        EvidenceType.PHOTO_SET,
        EvidenceType.DELIVERED_PHOTO,
    ]


def test_an_invented_scope_is_a_scope_mismatch():
    requirements, reason = parse_declaration(
        declaration("photo_set", scope="album_somewhere_else"), scope_id=SCOPE, question="问题"
    )

    assert (requirements, reason) == (None, "scope_mismatch")


def test_a_missing_scope_is_not_a_mismatch():
    """1.0 的 ``_normalize_payload`` 会把缺失的 scope 补上——模型本来就不该产出它。"""
    requirements, reason = parse_declaration(declaration("photo_set"), scope_id=SCOPE, question="问题")

    assert reason == ""
    assert requirements is not None


def test_garbage_is_an_invalid_planner_action():
    assert parse_declaration("我不知道该怎么规划。", scope_id=SCOPE, question="问题") == (
        None,
        "invalid_planner_action",
    )


def test_zero_requirements_is_rejected():
    """1.0 允许零需求；S3 不允许——零需求会让这一轮一次工具都不调。"""
    assert MIN_REQUIREMENTS == 1
    assert parse_declaration(declaration(), scope_id=SCOPE, question="问题") == (
        None,
        "no_requirements",
    )


def test_a_final_action_is_not_a_declaration():
    assert parse_declaration('{"action":"final","answer":"你好"}', scope_id=SCOPE, question="问题") == (
        None,
        "invalid_planner_action",
    )


# ---- 规划器 -----------------------------------------------------------------


class ScriptedPlannerModel:
    """按顺序回话的模型；``Exception`` 表示这次调用抛异常。"""

    def __init__(self, replies: Sequence[str | Exception]) -> None:
        self._replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[object]) -> ModelReply:
        assert tuple(tools) == (), "规划调用不带工具"
        self.calls.append(list(messages))
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(reply, Exception):
            raise reply
        return ModelReply(content=reply, finish_reason="stop")


def test_a_clean_declaration_costs_exactly_one_call():
    model = ScriptedPlannerModel([declaration("photo_set")])

    result = GoalPlanner(model).declare("给我看看海边的照片", scope_id=SCOPE)

    assert result.ok
    assert len(model.calls) == 1


def test_a_model_error_becomes_planner_call_error():
    """``planner_call_error`` 只在模型调用**抛异常**时出现——1.0 ``goal_planner.py:135``。"""
    model = ScriptedPlannerModel([RuntimeError("connection refused")])

    result = GoalPlanner(model).declare("给我看看海边的照片", scope_id=SCOPE)

    assert result == PlannerResult(fallback_reason="planner_call_error")


def test_a_missing_brace_is_repaired_without_a_second_call():
    model = ScriptedPlannerModel([declaration("photo_set")[:-1]])

    result = GoalPlanner(model).declare("给我看看海边的照片", scope_id=SCOPE)

    assert result.ok
    assert len(model.calls) == 1


def test_a_garbled_output_is_retried_with_the_previous_output_rolled_back():
    """重试不是重发同一句话：上一轮的原文与失败原因都要回灌（1.0 的注释写得很直白）。"""
    model = ScriptedPlannerModel(["我不会规划。", declaration("photo_set")])

    result = GoalPlanner(model).declare("给我看看海边的照片", scope_id=SCOPE)

    assert result.ok
    assert len(model.calls) == 2
    second = [message.content for message in model.calls[1]]
    assert "我不会规划。" in second  # 上一轮原文
    assert "无法被解析" in second[-1]  # 失败原因翻译成的修正指令


def test_it_gives_up_after_the_configured_number_of_attempts():
    model = ScriptedPlannerModel(["不是 JSON"])

    result = GoalPlanner(model, max_attempts=3).declare("给我看看海边的照片", scope_id=SCOPE)

    assert result.fallback_reason == "invalid_planner_action"
    assert len(model.calls) == 3


def test_the_default_attempt_count_is_the_1_0_default(monkeypatch):
    monkeypatch.delenv("SENTRIX_PLANNER_MAX_ATTEMPTS", raising=False)
    model = ScriptedPlannerModel(["不是 JSON"])

    GoalPlanner(model).declare("给我看看海边的照片", scope_id=SCOPE)

    assert len(model.calls) == 3


def test_the_attempt_count_can_be_overridden_by_env(monkeypatch):
    """1.0 读 ``SENTRIX_PLANNER_MAX_ATTEMPTS``；这个旋钮照搬。"""
    monkeypatch.setenv("SENTRIX_PLANNER_MAX_ATTEMPTS", "2")
    model = ScriptedPlannerModel(["不是 JSON"])

    GoalPlanner(model).declare("给我看看海边的照片", scope_id=SCOPE)

    assert len(model.calls) == 2


def test_history_goes_into_the_system_message_not_a_second_one():
    model = ScriptedPlannerModel([declaration("photo_set")])

    GoalPlanner(model).declare("那她呢", scope_id=SCOPE, history="user: 妈妈是谁")

    first, second = model.calls[0]
    assert first.role.value == "system"
    assert "历史对话背景：\nuser: 妈妈是谁" in first.content
    assert second.role.value == "user"
    assert all(message.role.value != "system" for message in model.calls[0][1:])
