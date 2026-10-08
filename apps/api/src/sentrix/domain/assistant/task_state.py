"""回合状态：计数器、进度流与终止原因。

1.0 把这些散在 ``AgentRuntime.run()`` 的局部变量里——``search_call_count``、
``inspected_handles``、``parse_retries``、``guard_retries``、``recent_tool_failures``
等十三个裸 int/bool（``runtime.py:1885-1936``），每个都靠相邻代码维持不变量。2.0 收进
一个对象：规则写在方法里，越界由方法拒绝，而不是靠「记得在那行加个判断」。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sentrix.domain.assistant.models import (
    ProgressEvent,
    ProgressStage,
    ProgressStatus,
    TerminationReason,
)
from sentrix.domain.assistant.profile import TurnProfile
from sentrix.domain.assistant.tools import (
    FINAL_ANSWER_TOOL,
    RETRIEVAL_TOOLS,
    TOOLS_BY_NAME,
    CostClass,
)

# 阶段用模块级字符串常量而不是枚举：它只影响日志可读性，不参与任何判定。
PLANNING = "planning"
ACTING = "acting"
FINALIZING = "finalizing"
TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """一次工具调用的结果。``signature`` 是「工具名 + 规范化参数」的指纹，用来识别
    重复调用；``model_visible`` 是真正发回模型的那份投影。"""

    call_id: str
    tool: str
    signature: str
    status: str
    failure_reason: str = ""
    observation: Mapping[str, Any] = field(default_factory=dict)
    model_visible: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class TaskState:
    turn_id: str
    conversation_id: str
    scope_id: str
    question: str
    profile: TurnProfile
    started_at: float = field(default_factory=time.monotonic)
    phase: str = PLANNING
    model_steps: int = 0
    records: list[ToolCallRecord] = field(default_factory=list)
    progress: list[ProgressEvent] = field(default_factory=list)
    guard_blocked_codes: set[str] = field(default_factory=set)
    termination_reason: TerminationReason | None = None

    # ---- 进度 ---------------------------------------------------------------

    def emit(
        self,
        stage: ProgressStage,
        status: ProgressStatus,
        text: str,
        *,
        kind: str = "status",
        payload: Mapping[str, Any] | None = None,
    ) -> ProgressEvent:
        """追加一条进度事件并返回它。

        ``step_index`` 由这里统一分配（``len(progress) + 1``），调用方无从跳过或重复——
        1.0 靠每个发出点自己数，是「跨回合不唯一」之外还多一层「同一回合内可能撞号」。
        """
        event = ProgressEvent(
            step_index=len(self.progress) + 1,
            stage=stage,
            status=status,
            text=text,
            kind=kind,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            payload=payload or {},
        )
        self.progress.append(event)
        return event

    # ---- 预算 ---------------------------------------------------------------

    @property
    def tool_calls(self) -> int:
        """计入预算的工具调用次数。

        被拒绝的调用与缓存回放都不计——1.0 也是这个口径（``tool_policy.py:61-62`` 只在
        执行成功后才 ``record_tool_call``）：拒绝不是执行，不该再罚一次预算。

        **终止动作（``final_answer``）也不计。** 它不是取证工具，是作答那一步；算进来的
        话，「回答被门禁打回一次」会顺带吃掉一次工具配额，而 6 次这个数是照着 1.0 的
        生产 profile 定的。
        """
        return sum(1 for record in self.records if record.status == "ok" and record.tool != FINAL_ANSWER_TOOL)

    @property
    def search_calls(self) -> int:
        return sum(1 for record in self.records if record.tool == "search_memories")

    @property
    def current_result_set_id(self) -> str | None:
        """最近一次成功检索的结果集——交付时按它解 handle。

        1.0 把这一格存在可变的 ``task.current_result_set`` 上（``runtime.py:1428``）。
        2.0 不新增状态：检索记录的观察里本来就带着 ``result_set_id``，倒着找第一条
        成功的就是它。多存一份迟早会与记录对不上。
        """
        for record in reversed(self.records):
            if record.status == "ok" and record.tool in RETRIEVAL_TOOLS:
                value = record.observation.get("result_set_id")
                return str(value) if value else None
        return None

    @property
    def inspection_calls(self) -> int:
        """一轮里已经**成功执行**过几次精读（``cost_class == EXPENSIVE``）。

        与 ``search_calls`` 不同，这里**按契约查而不是按工具名硬编码**：昂贵与否是
        ``ToolSpec`` 上的一个声明（1.0 ``cost_class``），第二个昂贵工具出现时
        不该再来这个文件加一行。``TOOLS_BY_NAME`` 用 ``.get`` 兜底——未知工具名在
        ``ToolPolicy`` 那一步就被拒了，根本落不进 ``records``，但这条属性不该因此抛。
        """
        return sum(
            1
            for record in self.records
            if record.status == "ok"
            and (spec := TOOLS_BY_NAME.get(record.tool)) is not None
            and spec.cost_class is CostClass.EXPENSIVE
        )

    def can_inspect(self) -> bool:
        """还能不能再精读一次。1.0 ``can_tool_call(inspection=True)`` 的那一半。

        工具预算那半由 ``can_call_tool`` 管，这里只管精读那半——两件事分开，
        拒绝时说得出是哪一条满了（1.0 把它们合在一次检查里，只会回一句
        ``budget exhausted``）。
        """
        return self.inspection_calls < self.profile.max_inspections

    def can_step(self) -> bool:
        """还能不能再问一次模型。

        与 1.0 一致：模型步只看墙钟有没有走完，**不检查 final_reserve**
        （``budget_manager.py:38-44``）。把最后 10 秒留给工具调用而不是写答案，正是
        1.0 实测出的「预算耗尽后空答案」的成因。
        """
        return self.model_steps < self.profile.max_steps and self.wall_remaining() > 0

    def can_call_tool(self) -> bool:
        return self.tool_calls < self.profile.max_tool_calls and self.has_final_reserve()

    def wall_remaining(self) -> float:
        return self.profile.wall_time_s - (time.monotonic() - self.started_at)

    def has_final_reserve(self) -> bool:
        return self.wall_remaining() > self.profile.final_reserve_s

    # ---- 工具调用记录 -------------------------------------------------------

    def record(self, record: ToolCallRecord) -> None:
        self.records.append(record)

    @staticmethod
    def signature(tool: str, arguments: Mapping[str, Any]) -> str:
        """重复调用的判据。键排序后再序列化，参数顺序不同的同一次调用算同一签名。"""
        parts = "&".join(f"{key}={arguments[key]!r}" for key in sorted(arguments))
        return f"{tool}({parts})"

    def cached(self, signature: str) -> ToolCallRecord | None:
        for record in reversed(self.records):
            if record.signature == signature and record.status == "ok":
                return record
        return None

    def replayed(self, signature: str) -> bool:
        """这个签名是否已经回放过一次。

        回放只能给一次：模型拿着同一份结果反复要，等于把模型步烧在一个不会变的答案上。
        第二次同样的调用直接拒，逼它作答或换动作。
        """
        return any(record.signature == signature and record.status == "replayed" for record in self.records)

    def trailing_failures(self, tool: str) -> int:
        """该工具**最近连续几次没有成功**——失败与被拒都算。

        1.0 数的是「同一工具、同一失败原因」（``runtime.py:3013-3027``）：那条判据回答的
        是「同一种错别再犯第二次」。这里问的是另一个问题——**这个工具是不是已经卡住了**。
        预算类拒绝每次的原因都完全合法（``inspection_budget_exhausted`` 出现多少次都是
        对的），可模型连试六次就是卡住，而它每试一次烧一个模型步。

        只看**队尾的连续段**：中间夹了别的工具就不算连续。
        """
        count = 0
        for record in reversed(self.records):
            if record.tool != tool or record.status == "ok":
                break
            count += 1
        return count

    # ---- 收尾 ---------------------------------------------------------------

    def finish(self, reason: TerminationReason) -> TerminationReason:
        """声明回合为何结束。整个回合里只该被调用一次。"""
        self.termination_reason = reason
        self.phase = TERMINAL
        return reason

    def block_guard_code(self, code: str) -> bool:
        """标记某个门禁规则已经拦过一次，返回「这次是新拦的」。

        1.0 的零检索硬拦没有这个标志位（``runtime.py:2568``），于是模型只要一直不调
        工具，这段就会一直 ``continue``，把 8 个模型步全部烧光——29% 的零工具调用里
        有相当一部分是这么来的。门禁只提醒一次，提醒过就放行。
        """
        if code in self.guard_blocked_codes:
            return False
        self.guard_blocked_codes.add(code)
        return True
