"""四个工具的契约与收敛策略的单元测试。

收敛规则全部来自 1.0 实测出的失败：小模型反复改 query 重搜直到工具预算耗尽、同一张图
死磕到底、同样的错误重试到没预算。1.0 把这些散在主循环的十几个裸计数器里，2.0 收进
一个决定。
"""

from __future__ import annotations

from sentrix.domain.assistant.evidence_ledger import EvidenceType
from sentrix.domain.assistant.models import ToolCall
from sentrix.domain.assistant.task_state import ToolCallRecord
from sentrix.domain.assistant.tool_policy import ToolAction, ToolPolicy
from sentrix.domain.assistant.tools import TOOL_SPECS, TOOLS_BY_NAME, openai_tools


def call(name: str, **arguments: object) -> ToolCall:
    import json

    return ToolCall(call_id=f"call_{name}", name=name, arguments_json=json.dumps(arguments))


def test_the_tool_list_is_exactly_what_ships():
    """S3 四个 + S6 的 ``query_memory_facts``。

    **这是一个逐字断言的清单，不是 ``>= 4``。** 工具多一个少一个都会改变模型看到的
    契约，而「多了一个没人注意的工具」正是那种不会报错、只会让模型偶尔调错东西的改动。
    清单变了就必须在这里显式改一次。
    """
    assert [spec.name for spec in TOOL_SPECS] == [
        "search_memories",
        "query_memory_facts",
        "get_result_page",
        "get_original_photos",
        "get_person_profile",
    ]


def test_every_tool_declares_the_evidence_it_satisfies():
    """1.0 的 ``produces_evidence`` 只是一句声明，没有任何地方校验工具真的产出了它。

    2.0 用它驱动账本——「工具调用不等于证据」这条纪律（``requirement_completion.py:63``）
    在 2.0 的落点就是这张显式的绑定表。
    """
    assert TOOLS_BY_NAME["search_memories"].satisfies == (EvidenceType.PHOTO_SET,)
    assert TOOLS_BY_NAME["get_original_photos"].satisfies == (EvidenceType.DELIVERED_PHOTO,)
    assert TOOLS_BY_NAME["get_person_profile"].satisfies == (EvidenceType.PERSON_PROFILE,)


def test_parameters_are_real_json_schema_not_example_values():
    """1.0 的 ``input_schema`` 是示例 dict（``{"query": "", "mode": "best|all|..."}``），
    被 json.dumps 之后拼进提示词——模型看到的是不像 schema 的一段字。这里钉住标准形态。"""
    for spec in TOOL_SPECS:
        assert spec.parameters["type"] == "object"
        assert spec.parameters["additionalProperties"] is False
        assert set(spec.parameters["required"]) == set(spec.required)
        for name in spec.required:
            assert name in spec.parameters["properties"]


def test_openai_tools_wraps_each_spec_as_a_function():
    tools = openai_tools()

    assert len(tools) == len(TOOL_SPECS)
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "search_memories"
    assert tools[0]["function"]["parameters"] == dict(TOOLS_BY_NAME["search_memories"].parameters)


def test_a_valid_call_is_executed_with_parsed_arguments(make_state):
    decision = ToolPolicy().decide(call("search_memories", query="海边"), make_state())

    assert decision.action is ToolAction.EXECUTE
    assert decision.arguments == {"query": "海边"}
    assert decision.signature == "search_memories(query='海边')"


def test_an_unknown_tool_is_denied_with_the_available_names(make_state):
    decision = ToolPolicy().decide(call("delete_everything"), make_state())

    assert decision.action is ToolAction.DENY
    assert decision.code == "unknown_tool"
    assert "search_memories" in decision.reason


def test_broken_json_is_denied_not_raised(make_state):
    state = make_state()
    broken = ToolCall(call_id="c1", name="search_memories", arguments_json="{oops")

    decision = ToolPolicy().decide(broken, state)

    assert decision.action is ToolAction.DENY
    assert decision.code == "bad_arguments"


def test_a_missing_required_argument_is_denied(make_state):
    decision = ToolPolicy().decide(call("get_result_page", page=1), make_state())

    assert decision.action is ToolAction.DENY
    assert decision.code == "missing_argument"
    assert "result_set_id" in decision.reason


def test_search_memories_cannot_be_called_twice(make_state):
    state = make_state()
    policy = ToolPolicy()
    first = call("search_memories", query="海边")
    assert policy.decide(first, state).action is ToolAction.EXECUTE
    state.record(ToolCallRecord(call_id="c1", tool="search_memories", signature="x", status="ok"))

    second = policy.decide(call("search_memories", query="海边2"), state)

    assert second.action is ToolAction.DENY
    assert second.code == "search_call_limit"
    assert "get_result_page" in second.reason


def test_search_limit_does_not_lapse_when_the_model_budget_is_gone(make_state):
    """1.0 的拦截条件里带着 ``and turn.budget.can_model_step()``（``runtime.py:2993``），
    模型步耗尽之后这个 ``and`` 短路，**第二次 search 会被真的执行**。2.0 的计数与预算
    分开，不存在这个短路。

    反向验证：把模型步耗光、工具预算也耗光，拦截仍然成立。
    """
    state = make_state()
    state.model_steps = state.profile.max_steps
    state.record(ToolCallRecord(call_id="c1", tool="search_memories", signature="x", status="ok"))

    decision = ToolPolicy().decide(call("search_memories", query="海边2"), state)

    assert decision.action is ToolAction.DENY
    assert decision.code == "search_call_limit"


def test_a_duplicate_call_replays_the_cached_observation(make_state):
    state = make_state()
    policy = ToolPolicy()
    state.record(
        ToolCallRecord(
            call_id="c1",
            tool="search_memories",
            signature="search_memories(query='海边')",
            status="ok",
            model_visible={"result_set_id": "rs_1"},
        )
    )

    decision = policy.decide(call("search_memories", query="海边"), state)

    assert decision.action is ToolAction.REPLAY
    assert decision.replay == {"result_set_id": "rs_1"}


def test_a_second_duplicate_is_denied(make_state):
    """回放只能给一次：模型拿着同一份结果反复要，等于把模型步烧在一个不会变的答案上。"""
    state = make_state()
    policy = ToolPolicy()
    signature = "search_memories(query='海边')"
    state.record(
        ToolCallRecord(
            call_id="c1",
            tool="search_memories",
            signature=signature,
            status="ok",
            model_visible={"result_set_id": "rs_1"},
        )
    )
    state.record(ToolCallRecord(call_id="c2", tool="search_memories", signature=signature, status="replayed"))

    decision = policy.decide(call("search_memories", query="海边"), state)

    assert decision.action is ToolAction.DENY
    assert decision.code == "duplicate_call_stopped"


def test_tool_budget_exhaustion_denies(make_state):
    state = make_state()
    for index in range(state.profile.max_tool_calls):
        state.record(
            ToolCallRecord(call_id=f"c{index}", tool="get_result_page", signature=f"s{index}", status="ok")
        )

    decision = ToolPolicy().decide(call("get_result_page", result_set_id="rs_1", page=2), state)

    assert decision.action is ToolAction.DENY
    assert decision.code == "tool_budget_exhausted"


def test_the_same_failure_twice_in_a_row_forces_a_change(make_state):
    state = make_state()
    for index in range(2):
        state.record(
            ToolCallRecord(
                call_id=f"c{index}",
                tool="get_result_page",
                signature=f"s{index}",
                status="error",
                failure_reason="unknown_result_set",
            )
        )

    decision = ToolPolicy().decide(call("get_result_page", result_set_id="rs_1", page=2), state)

    assert decision.action is ToolAction.DENY
    assert decision.code == "repeated_failure"


def test_a_single_failure_does_not_block_the_retry(make_state):
    """反向验证：只失败一次时不该拦——否则模型没有第二次机会修正参数。"""
    state = make_state()
    state.record(
        ToolCallRecord(
            call_id="c1",
            tool="get_result_page",
            signature="s",
            status="error",
            failure_reason="unknown_result_set",
        )
    )

    decision = ToolPolicy().decide(call("get_result_page", result_set_id="rs_1", page=2), state)

    assert decision.action is ToolAction.EXECUTE


def test_denied_calls_do_not_consume_the_tool_budget(make_state):
    state = make_state()
    policy = ToolPolicy()
    for index in range(5):
        policy.decide(call(f"nope_{index}"), state)
        state.record(ToolCallRecord(call_id=f"c{index}", tool="nope", signature=f"s{index}", status="denied"))

    assert state.tool_calls == 0
    assert policy.decide(call("search_memories", query="海边"), state).action is ToolAction.EXECUTE
