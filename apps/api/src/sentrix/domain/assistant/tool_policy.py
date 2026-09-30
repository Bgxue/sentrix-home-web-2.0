"""工具策略：一次工具调用要么被执行，要么被拒绝，要么命中缓存。

对应 1.0 ``agent_runtime/tool_policy.py``。1.0 的 ``authorize`` 只做四件事（白名单 /
只读 / readiness / 预算），其余收敛全部散在 runtime 主循环的十几个裸计数器里。2.0 把
它们**全部收进这一个决定**——于是「为什么这次调用没执行」有唯一答案，而不是要去主循环
里逐行找是哪一个 ``continue`` 拦下的。

1.0 实测出的四条收敛规则，逐条保留：

- ``search_memories`` 每回合最多 1 次（``runtime.py:1895``）。1.0 的实现有个边界漏洞：
  拦截条件里带了 ``and turn.budget.can_model_step()``，预算耗尽后拦截失效，**第二次
  search 会被真的执行**。这里把计数与预算分开，不存在这个短路。
- 同工具同失败原因连续 2 次 → 拦下并逼模型换动作（``runtime.py:3013``）。
- 完全相同的调用走缓存回放，不重复执行、不计预算（``runtime.py:3035``）。
- 预算耗尽 → 拒绝（``tool_policy.py:37``）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sentrix.domain.assistant.models import ToolArgumentError, ToolCall
from sentrix.domain.assistant.task_state import TaskState
from sentrix.domain.assistant.tools import TOOLS_BY_NAME, CostClass

# 同工具同失败原因连续几次之后强制换动作。
MAX_CONSECUTIVE_FAILURES = 2


class ToolAction(StrEnum):
    EXECUTE = "execute"
    REPLAY = "replay"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ToolDecision:
    action: ToolAction
    code: str = "ok"
    reason: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    signature: str = ""
    replay: Mapping[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.action is not ToolAction.DENY


class ToolPolicy:
    """无状态：所有计数都在 ``TaskState`` 上。"""

    def decide(self, call: ToolCall, state: TaskState) -> ToolDecision:
        spec = TOOLS_BY_NAME.get(call.name)
        if spec is None:
            return ToolDecision(
                action=ToolAction.DENY,
                code="unknown_tool",
                reason=(f"没有名为 {call.name} 的工具。可用工具：" + "、".join(sorted(TOOLS_BY_NAME))),
            )

        try:
            arguments = call.parse()
        except ToolArgumentError as error:
            return ToolDecision(
                action=ToolAction.DENY, code="bad_arguments", reason=f"参数不是合法 JSON：{error}"
            )

        missing = [name for name in spec.required if not arguments.get(name)]
        if missing:
            return ToolDecision(
                action=ToolAction.DENY,
                code="missing_argument",
                reason=f"{call.name} 缺少必填参数：{'、'.join(missing)}",
            )

        signature = TaskState.signature(call.name, arguments)
        cached = state.cached(signature)
        if cached is not None and state.replayed(signature):
            return ToolDecision(
                action=ToolAction.DENY,
                code="duplicate_call_stopped",
                reason="同一个调用已经回放过一次，结果不会变，请基于它作答。",
            )
        if cached is not None:
            # 1.0 也是回缓存 + 提示直接作答（``runtime.py:3035``）：重复调用不该白烧预算，
            # 但也不能让模型以为工具返回了「新东西」。
            return ToolDecision(
                action=ToolAction.REPLAY,
                code="duplicate_call",
                reason="这个调用刚刚已经执行过，结果与上次相同，请直接作答或换个动作。",
                arguments=arguments,
                signature=signature,
                replay=cached.model_visible,
            )

        if call.name == "search_memories" and state.search_calls >= state.profile.max_search_calls:
            return ToolDecision(
                action=ToolAction.DENY,
                code="search_call_limit",
                reason=(
                    "search_memories 每轮只需调用一次。要看更多候选用 get_result_page 翻页"
                    "（result_set_id 用上一次返回的），不要改词重搜。"
                ),
            )

        if not state.can_call_tool():
            return ToolDecision(
                action=ToolAction.DENY,
                code="tool_budget_exhausted",
                reason="本轮的工具调用预算已经用完，请基于已有结果作答。",
            )

        # 1.0 ``tool_policy.py:37-38`` 的 ``can_tool_call(inspection=...)``。**单独一条、
        # 单独一个码**：它与上面那条是两笔预算（工具有 6 次，精读只有 1 次），
        # 合在一起拒就只能说一句「预算用完了」，而模型分不出是「别再调工具了」
        # 还是「这次可以换张图但不能再精读」。1.0 正是合着写的。
        if spec.cost_class is CostClass.EXPENSIVE and not state.can_inspect():
            return ToolDecision(
                action=ToolAction.DENY,
                code="inspection_budget_exhausted",
                reason=(
                    "本轮的精读预算已经用完（一轮最多精读一张图）。"
                    "请基于已经看过的内容作答，不要反复检查同一张照片。"
                ),
            )

        for record in reversed(state.records):
            if record.tool != call.name:
                continue
            if (
                record.failure_reason
                and state.consecutive_failures(call.name, record.failure_reason) >= MAX_CONSECUTIVE_FAILURES
            ):
                return ToolDecision(
                    action=ToolAction.DENY,
                    code="repeated_failure",
                    reason=(
                        f"{call.name} 已经用同样的方式失败 {MAX_CONSECUTIVE_FAILURES} 次"
                        f"（{record.failure_reason}），换一个动作，不要重试。"
                    ),
                )
            break

        return ToolDecision(
            action=ToolAction.EXECUTE,
            code="ok",
            arguments=arguments,
            signature=signature,
        )
