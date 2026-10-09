"""一次问答回合：规划 → 工具循环 → 证据账本 → 答案门禁 → 回答。

这是设计文档里「在线链路」9 步中 S3 覆盖的那一段（问题 → 规划 → 工具循环 → 检索取证 →
证据账本 → 答案门禁 → 答案）。留给后续切片的：双层防伪的语义评审层（S6）、恢复作答的
四级阶梯（S6/S7）、六路取证的补齐（S6）。

1.0 把这段写成一个 1,700 行的 ``AgentRuntime.run()``，里面混着模型调用、工具派发、
SQLite、预算计数与十几条软门禁。2.0 的划分是：

- **规则**（``domain/assistant``）：状态、策略、账本、门禁——无 IO，可单测；
- **端口**（``domain/assistant/ports.py``）：模型、存储、事件流——domain 只声明形状；
- **本模块**：把上面两样接起来，并**唯一一次**决定「这一轮为什么结束」。

三个 1.0 的失败模式在这里被结构性消掉：

1. **``termination_reason`` 落空**：``TurnResult.termination_reason`` 的类型就是枚举，
   收尾只走 ``_finish`` 一个出口，循环外还有一层兜底防止将来有人加了新出口却忘了声明原因。
2. **零工具调用**：规划要求至少一条证据需求（``intent.MIN_REQUIREMENTS``）。1.0 的规划
   允许「零需求」，而 JIT 提示词会因此直接告诉模型「当前没有待确认的证据需求……可直接
   输出 final」（``jit_prompt.py:200``）——1.0 实测 29.3% 的回合一次工具都没调。
3. **``planner_call_error``**：审计过的那 1,974 条全部落在模型端点不可用的黑障窗口里
   （见 ``domain/assistant/intent.py``），所以规划照 1.0 仍然是一次模型调用；端点真挂了
   就以 ``planner_blocked`` 收场，而不是靠规则猜出一组需求继续往下跑。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sentrix.application.assistant.goal_planner import (
    PLANNER_CALL_ERROR,
    GoalPlanner,
    PlannerResult,
)
from sentrix.application.assistant.judge import FaithfulnessJudge
from sentrix.application.assistant.tools import (
    AssistantToolbox,
    DeliveredPhotos,
    ToolOutcome,
    is_empty_result,
)
from sentrix.domain.assistant import emergency
from sentrix.domain.assistant.evidence_ledger import EvidenceLedger, EvidenceType
from sentrix.domain.assistant.final_guard import FinalGuard, GuardFacts, answered_with_gap
from sentrix.domain.assistant.jit import select_tools
from sentrix.domain.assistant.models import (
    Attachment,
    ChatMessage,
    ChatRole,
    ProgressEvent,
    ProgressStage,
    ProgressStatus,
    TerminationReason,
    ToolArgumentError,
    ToolCall,
    TurnRecord,
    TurnStatus,
)
from sentrix.domain.assistant.policy import detect_policy_refusal
from sentrix.domain.assistant.ports import (
    ChatModel,
    ConversationRepository,
    ModelServiceError,
    PromptTooLongError,
    TurnRepository,
)
from sentrix.domain.assistant.profile import DEFAULT_PROFILE, TurnProfile
from sentrix.domain.assistant.task_state import ACTING, FINALIZING, TaskState, ToolCallRecord
from sentrix.domain.assistant.tool_policy import ToolAction, ToolPolicy
from sentrix.domain.assistant.tools import FINAL_ANSWER_TOOL, TOOLS_BY_NAME
from sentrix.domain.errors import DomainError

log = logging.getLogger("sentrix.assistant.run_turn")

HISTORY_LIMIT = 8

SYSTEM_PROMPT = """你是这个家庭相册的助手。你能做的只有两件事：检索照片，和读取已确认人物的档案。

规则：
- 需要照片证据时必须先调用工具，不要凭印象回答。
- 回答必须用 final_answer 收尾，不要直接输出正文结束本轮。
- 只要这一轮取到了照片、而用户问的又是关于这些照片的事（看看、是哪张、有没有…），
  就要在 final_answer 的 selected_image_handles 里填上**确实要展示给用户**的那几张，
  最多 6 张；只取到候选不算交付。搜索返回的全部候选不要整批塞进去。
- 工具没给到的信息就说不知道，不要编造。
- handle（photo_1 这类）只在调用工具时使用，不要写进给用户的回答里。
- 用户用中文提问，就用中文回答。"""

# 模型这一轮没给出可用输出时给它的一句提醒。1.0 对这类输入走的是**解析重试**
# （``runtime.py`` 的 ``parse_retries``：模型输出解析不出 action 就带着原因再问一次，
# 用完重试次数才以 ``parse_failure`` 收尾），而不是另立一个终止原因。这里照 1.0 的口径：
# 提醒一次，再空就按 ``PARSE_FAILURE`` 收尾，用确定性兜底把已有证据说清楚。
PARSE_FAILURE_NUDGE = "你上一条回复是空的。请直接用一两句中文把结论说给用户，不要再调用工具。"

# 连续几轮工具调用一条有用的结果都没拿到时，给模型的一次提醒。
#
# **为什么不能在工具层解决**：被拒的调用不推进任务、不占工具额度，**只烧模型步**
# （``run_turn`` 的循环是「问一次模型 → 跑它要的工具 → 再问一次」，被拒也要走完这一圈）。
# 实测 762 次被拒里有 51% 之后模型继续试同一个工具，426 步是「整步全被拒」——占全部
# 2667 步的 16%。工具层的每一条判据都拦不住这个，因为**每一次被拒在它自己看来都是对的**。
# 这是循环级的活性问题，所以护栏也在循环这一层。
STALLED_NUDGE = (
    "你连续几轮调用的工具都没有返回可用的结果。请立即基于已经取到的内容作答；"
    "确实拿不到的就如实说明拿不到。不要再调用刚刚被拒绝过的工具。"
)

# 连续几轮全白跑之后提醒。取 2：一轮是偶发（某张图刚好读不到字），两轮就是卡住了。
STALLED_ROUNDS_BEFORE_NUDGE = 2

# ---- 收尾前的三条一次性提醒（1.0 ``runtime.py:2540-2660``）----------------------
#
# 三条的**动作完全一样**：把模型刚才那个 final 塞回对话 → 追加一条 user 提醒 → 继续。
# 各自一个闩（``TaskState.block_guard_code``），所以每条只给一次；模型在提醒之后坚持
# 输出 final → **放行，代码不覆盖**（1.0 是 ``decision = partial_answer`` /
# ``accept_with_insufficient_evidence``，2.0 现在就是这个行为）。
#
# **为什么这一层非有不可。** 实测 212 道题声明了 ``delivered_photo``，其中 145 道零交付，
# 而这 145 道里交付工具**一次都没被调用过**（0 次被拒、0 次失败）——不是「想交交不了」，
# 是「根本没走到」。同类：``visible_text`` 声明 213 道、155 道未满足，其中 **147 道从未
# 调用过** ``read_photo_text``。两条都不是检索失败，是**规划与执行脱节**；工具层看不见它，
# 因为**没有任何一次调用是错的**。

# ① 声明了结构化统计需求、却一次都没查过精确统计。
STRUCTURED_FACT_NUDGE = (
    "该问题声明了全量结构化统计需求。若要报告数量、日期或分组，"
    "请调用 query_memory_facts 获取当前相册的精确统计；"
    "如果条件不支持或没有可确认口径，也可以直接输出 final 如实说明，"
    "不要把检索候选数当作统计答案。"
)

# ② 还有**没试过**的必需需求。
UNATTEMPTED_NUDGE_TEMPLATE = (
    "还有声明的证据需求尚未尝试：{pending}。当前可用工具：{available}。"
    "请先用兼容工具获取证据；如果确实没有对应的照片或记录可以确认"
    "（例如问题本身没有可查的答案），请直接输出 final 如实说明，不要编造。"
)

# ③ 零工具调用却给出了具体数字/金额/年份。
FABRICATION_NUDGE = (
    "你的回答给出了具体的数字/金额/年份，但本轮还没有检索相册来核实。"
    "请先调用 search_memories 检索相关照片（必要时再 read_photo_text / inspect_photo "
    "查看照片里的文字或细节）；如果相册里确实没有能确认该数字的依据，"
    "请如实说「现有记录无法确认」，不要编造数字。"
)

#: ③ 的判据。正则逐字取自 1.0 ``runtime.py:2645``。
_QUANTIFIED_CLAIM = re.compile(r"\d[\d,，.万]*\s*(?:元|块钱|万|桌|张|人|个|名|年|月|日|号)")

#: ③ 的例外：纯闲聊 / 让模型写东西 / 问知识，本来就不该去查相册。
#: 1.0 ``runtime.py:382`` 的 ``_CHAT_ONLY_RE``，逐字。
_CHAT_ONLY = re.compile(
    r"你好|谢谢|在吗|再见|哈哈|好的|嗯|哦|你是谁|你叫什么|你会什么|帮我写|"
    r"写一|写个|改一|翻译|解释一下什么是|什么是|怎么用|步骤|教程",
    re.IGNORECASE,
)


def _attempted_evidence_types(state: TaskState) -> set[EvidenceType]:
    """这一轮**被调用过的工具**合起来声明能满足的证据类型。

    一次工具调用不等于证据（``evidence_ledger`` 开头那条纪律），但**「试过」就是试过**
    ——这里问的是「模型有没有去取」，不是「取到了没有」。被拒的调用也算：模型确实发了它。
    """
    attempted: set[EvidenceType] = set()
    for record in state.records:
        spec = TOOLS_BY_NAME.get(record.tool)
        if spec is not None:
            attempted.update(spec.satisfies)
    return attempted


# 规划失败的收尾文案有两条，**按失败原因分岔**（用户 2026-09-25 裁决二）。合成一条是有害的：
# 把「端点挂了」说成「规划不出来」，用户会以为是自己问得不好，于是换个问法接着问；而该做
# 的事是去看那个端点。这正是 2.0 助手在 8100 被换成 vLLM 之后的表现——真因（``404 The model
# `gemma4-qat-iq3xxs` does not exist``）在日志里留全了，用户看到的却是「理解不了」。
#
# 1) 模型答了，但规划结果不可用（``invalid_planner_action`` / ``scope_mismatch`` /
#    ``no_requirements``）。文案是 1.0 生产轨迹里那句话，逐字：``data/sentrix.db`` 里
#    2,126 条 ``planner_blocked`` 的 answer。
PLANNER_BLOCKED_ANSWER = "当前问题的证据需求无法可靠规划，因此暂时无法确认。"

# 2) 模型服务这一侧挂了（``planner_call_error``：端点不可达 / 超时 / 模型名对不上 /
#    端点同时服务了多个模型）。说的是模型服务，不说是规划——并把端点自己的说明接在后面，
#    那句话里有地址，用户与运维据此能直接去查。
MODEL_SERVICE_ANSWER = "模型服务当前不可用，因此这一轮没有开始检索。"


def _drop_oldest_tool_round_trip(messages: Sequence[ChatMessage]) -> list[ChatMessage] | None:
    """丢掉**最老的一整轮工具往返**；没有可丢的返回 ``None``。

    **压缩的单位必须是「一整轮」，不能按消息条数丢。** 严格 chat template 要求每个
    ``tool_call_id`` 都有一条配对的 ``role=tool`` 结果消息，丢一半端点直接 400
    （1.0 ``intent.py`` 的注释记过这条）。所以这里找的是第一条带 ``tool_calls`` 的
    assistant 消息，连同**紧跟其后的全部** tool 结果一起丢。

    丢最老的一轮而不是最新的一轮：最新那轮是模型刚取到的证据，正是它下一步要用的；
    早先那些往返的内容已经体现在模型的推理与后续调用里了。
    """
    kept: list[ChatMessage] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role is ChatRole.ASSISTANT and message.tool_calls:
            index += 1
            while index < len(messages) and messages[index].role is ChatRole.TOOL:
                index += 1
            return kept + list(messages[index:])
        kept.append(message)
        index += 1
    return None


def _planner_failure_answer(planned: PlannerResult) -> str:
    """规划失败时给用户看的那句话。分岔依据是 ``fallback_reason``，不是文案措辞。"""
    if planned.fallback_reason != PLANNER_CALL_ERROR:
        return PLANNER_BLOCKED_ANSWER
    if not planned.detail:
        return MODEL_SERVICE_ANSWER
    return f"{MODEL_SERVICE_ANSWER}（{planned.detail}）"


class TurnNotFoundError(DomainError, LookupError):
    """这条会话 / 这个回合不存在。

    它以前是个**不带码**的 ``LookupError``，由 ``api/routes/assistant.py`` 硬编码成 404
    ——同一条产品行为有两个来源（异常在这里、状态码在路由），改一处就漂，而且只有起了
    HTTP 才验得到。码与状态现在跟着异常走（口径写在 ``domain/errors.py``），路由只放行。
    """

    code = "not_found"
    status_code = 404


class TurnAlreadyClaimedError(RuntimeError):
    """这个回合已经有人在跑了。同一回合只允许一个执行者。"""


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """建回合的输入。**没有 scope**：一个回合属于一个会话，而会话已经拥有 scope。

    让调用方再传一个，等于允许「回合的 scope ≠ 会话的 scope」这种不自洽状态存在。
    它一旦存在，检索就会跑在错误的 scope 上，而且失败是静默的。
    """

    conversation_id: str
    question: str


@dataclass(frozen=True, slots=True)
class TurnResult:
    turn_id: str
    conversation_id: str
    status: TurnStatus
    # 非可选：1.0 的 25,112 条轨迹里 11,304 条读不到这个字段，而没有任何东西会因此报错。
    termination_reason: TerminationReason
    answer: str
    attachments: tuple[Attachment, ...]
    requirements: Sequence[dict[str, Any]]


@dataclass
class _Session:
    """一次回合的可变状态。它把「状态 / 账本 / 交付 / 检索是否为空 / 推送出口」收在一起，
    免得每个方法都要拖五个参数。"""

    state: TaskState
    ledger: EvidenceLedger
    emit: Callable[[ProgressEvent], None] | None = None
    delivered: DeliveredPhotos = field(default_factory=DeliveredPhotos)
    search_returned_nothing: bool = False
    # 1.0 ``runtime.py:2759`` 的 ``l2_faithfulness_checked``：每问只评审一次。
    judged: bool = False
    # 1.0 ``runtime.py:2786-2788`` 的 ``turn.l2_unverified`` / ``l2_unverified_reason``：
    # 这一次 final 曾被 L2 判过不实。**它是「放行时打标」那一半的依据**——见
    # ``_judge_answer`` 里发 ``l2_soft_release`` 的那一处。
    l2_unverified: bool = False
    l2_unverified_reason: str = ""
    # 门禁拦下时写给模型的提醒，由主循环作为一条 user 消息加回去。
    pending_reminder: str = ""
    # 连续几轮「一条有用的结果都没拿到」。见 ``_converse`` 的护栏。
    stalled_rounds: int = 0


class RunTurn:
    def __init__(
        self,
        *,
        model: ChatModel,
        conversations: ConversationRepository,
        turns: TurnRepository,
        toolbox: AssistantToolbox,
        profile: TurnProfile = DEFAULT_PROFILE,
    ) -> None:
        self._model = model
        self._conversations = conversations
        self._turns = turns
        self._toolbox = toolbox
        self._profile = profile
        self._policy = ToolPolicy()
        self._guard = FinalGuard()
        # 规划是一次模型调用，与主循环同一个端点（1.0 也是同一个 ``chat_fn``）。
        self._planner = GoalPlanner(model)
        # L2 语义评审：与主循环同一个端点（1.0 也是同一个 chat_fn）。
        self._judge = FaithfulnessJudge(model)

    # ---- 建回合 -------------------------------------------------------------

    def start(self, request: TurnRequest) -> TurnRecord:
        """落一条用户消息与一个 ``pending`` 回合，立刻返回。

        回合先于执行落库，SSE 才有东西可订阅——1.0 的回合只活在进程内存里，页面刷新之后
        那次回合就再也拿不回来了。

        scope 取自**会话**：回合落库时把它快照进 ``TurnRecord.scope_id``，供检索、消息
        与离线分析使用——但这个值只有一个来源，就是会话。
        """
        conversation = self._conversations.get(request.conversation_id)
        if conversation is None:
            raise TurnNotFoundError("conversation not found")
        now = datetime.now(UTC)
        record = TurnRecord(
            id=f"turn_{now.strftime('%Y%m%d%H%M%S%f')}",
            conversation_id=conversation.id,
            scope_id=conversation.scope_id,
            question=request.question,
            status=TurnStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self._turns.create(record)
        self._conversations.add_message(
            conversation_id=record.conversation_id,
            scope_id=record.scope_id,
            turn_id=record.id,
            role=ChatRole.USER.value,
            content=request.question,
            created_at=now,
        )
        return record

    # ---- 跑回合 -------------------------------------------------------------

    def run(self, turn_id: str, *, emit: Callable[[ProgressEvent], None] | None = None) -> TurnResult:
        """执行回合。``emit`` 在每条进度事件落库之后被调用一次（SSE 靠它推送）。"""
        record = self._turns.get(turn_id)
        if record is None:
            raise TurnNotFoundError("turn not found")
        if not self._turns.claim(turn_id, datetime.now(UTC)):
            raise TurnAlreadyClaimedError(turn_id)

        session = _Session(
            state=TaskState(
                turn_id=record.id,
                conversation_id=record.conversation_id,
                scope_id=record.scope_id,
                question=record.question,
                profile=self._profile,
            ),
            ledger=EvidenceLedger(scope_id=record.scope_id, requirements=[]),
            emit=emit,
        )

        # 1.0 ``runtime.py:1725``：政策拒答在**任何模型调用之前**返回固定的拒绝语——
        # 不规划、不调工具、不进模型。位置也照搬（在规划之前）。
        refusal = detect_policy_refusal(record.question)
        answer = ""
        if refusal is not None:
            session.state.finish(TerminationReason.POLICY_REFUSAL)
            answer = refusal
        else:
            history = "\n".join(
                f"{message.role.value}: {message.content}"
                for message in self._conversations.history_for_prompt(record.conversation_id, HISTORY_LIMIT)
                if message.turn_id != record.id
            )
            planned = self._planner.declare(record.question, scope_id=record.scope_id, history=history)
            if not planned.ok:
                # 1.0 ``runtime.py:1828``：规划不出来就以 planner_blocked 收场，不再往下走。
                # 拿一条编出来的需求去检索，比诚实地说规划失败更糟。
                # 终止原因仍是 ``planner_blocked``（与 1.0 的库可比），但**文案按原因分岔**：
                # 服务侧挂掉时说模型服务，模型自己规划不出时才说规划。
                log.warning(
                    "规划失败: turn=%s reason=%s detail=%s",
                    turn_id,
                    planned.fallback_reason,
                    planned.detail,
                )
                session.state.finish(TerminationReason.PLANNER_BLOCKED)
                answer = _planner_failure_answer(planned)
            else:
                session.ledger.requirements = list(planned.requirements or ())
                self._publish(
                    session,
                    session.state.emit(
                        ProgressStage.PLANNING,
                        ProgressStatus.COMPLETE,
                        "已确认需要哪些证据。",
                        kind="plan",
                        payload={"requirements": list(session.ledger.as_payload())},
                    ),
                )
                try:
                    answer = self._converse(session, record)
                except Exception:
                    # 兜底不是「错误处理」，是「绝不让收尾原因落空」：任何未预料的异常也
                    # 必须以一个有名字的原因结束，而不是留下一个半开的回合。
                    log.exception("回合执行中抛出未预料的异常: %s", turn_id)
                    session.state.finish(TerminationReason.MODEL_ERROR)
                    answer = emergency.clarify_answer(TerminationReason.MODEL_ERROR)

        # 收尾前给还开着的需求补上真实原因：**是试过没拿到，还是根本没试**。
        # 必须在最后一次 ``as_payload()`` 之前——它同时喂给落库与返回值。
        session.ledger.mark_unmet(attempted=_attempted_evidence_types(session.state))

        reason = session.state.termination_reason or session.state.finish(TerminationReason.MODEL_ERROR)
        status = _status_for(reason)
        self._publish(
            session,
            session.state.emit(
                ProgressStage.TERMINAL,
                ProgressStatus.COMPLETE if status is TurnStatus.COMPLETE else ProgressStatus.PARTIAL,
                "已完成本轮回答。" if status is TurnStatus.COMPLETE else "这一轮没能完整完成。",
                kind="terminal",
                payload={"status": status.value, "termination_reason": reason.value},
            ),
        )

        now = datetime.now(UTC)
        # 顺序有意义：**先**把回答写成消息，**再**把回合标成终态。
        # 「终态」是「这一切都已经落库」的承诺——SSE 的 complete 帧就发在这个状态上，
        # 反过来的话，读到 complete 的调用方会看到一个还没有回答的会话。
        self._conversations.add_message(
            conversation_id=record.conversation_id,
            scope_id=record.scope_id,
            turn_id=turn_id,
            role=ChatRole.ASSISTANT.value,
            content=answer,
            created_at=now,
        )
        self._turns.finish(
            turn_id,
            status=status.value,
            termination_reason=reason.value,
            answer=answer,
            attachments=session.delivered.items,
            requirements=list(session.ledger.as_payload()),
            retrieved_candidates=session.state.retrieved_candidates,
            updated_at=now,
        )
        return TurnResult(
            turn_id=turn_id,
            conversation_id=record.conversation_id,
            status=status,
            termination_reason=reason,
            answer=answer,
            attachments=session.delivered.items,
            requirements=[dict(item) for item in session.ledger.as_payload()],
        )

    # ---- 主循环 -------------------------------------------------------------

    def _converse(self, session: _Session, record: TurnRecord) -> str:
        state = session.state
        messages = self._initial_messages(record)

        while True:
            if not state.can_step():
                reason = (
                    TerminationReason.TIMEOUT
                    if state.wall_remaining() <= 0
                    else TerminationReason.STEP_BUDGET_EXHAUSTED
                )
                state.finish(reason)
                return self._stop(session, reason)

            state.model_steps += 1
            state.phase = ACTING
            self._publish(
                session,
                state.emit(ProgressStage.THINKING, ProgressStatus.RUNNING, "正在理解你的问题…"),
            )

            # **JIT 剪枝。** 1.0 每一转只发当前这一步用得上的那几份精简契约
            # （``jit_prompt.py`` 的 ``LITE_TOOL_SCHEMAS``，每工具 <40 token），
            # 2.0 原先每次发全量八份完整 schema——实测 5408 字符 ≈ 2704 token，
            # 而 1.0 的真实 ``prompt_tokens`` 中位数只有 1437。见 ``jit.py``。
            #
            # ``tools_exhausted`` 传的是**两条**预算的合成：工具额度用完（``tool_calls``
            # 到顶）与墙钟只剩写答案的时间（``has_final_reserve`` 为假）。两者都意味着
            # 「这一转不该再调工具了」，而 ``select_tools`` 对它的处置是**一个工具都不发**：
            # 模型于是别无选择，只能说话。实测 ``step_budget_exhausted`` 的 123 道里
            # 102 道判 0，根子就是模型到最后几步仍然只在撞墙、从没被逼着开口。
            specs = select_tools(
                session.ledger,
                has_result_set=state.current_result_set_id is not None,
                tools_exhausted=not state.can_call_tool(),
            )
            try:
                reply = self._model.complete(messages, specs)
            except PromptTooLongError as error:
                # 提示词撑爆了窗口。**先压历史再试一次**——这是它区别于别的模型服务
                # 故障的地方：真因在调用方这一侧，压缩就解决得了。
                #
                # **一直丢到装得下为止**，不是丢一轮就重试。一次工具往返可能只占几百
                # token，而超出的量可能上千——丢一轮不够时原先的实现直接收尾，把一次
                # 「压缩就能救回来」的回合判成了模型故障。每次丢一整轮，所以这个循环
                # 的步数上界就是历史里的往返数。
                while True:
                    compacted = _drop_oldest_tool_round_trip(messages)
                    if compacted is None:
                        log.warning("提示词超预算，而历史里已经没有可压缩的工具往返: %s", error)
                        return self._model_error_stop(session, error)
                    messages = compacted
                    self._publish(
                        session,
                        state.emit(
                            ProgressStage.RECOVERING,
                            ProgressStatus.RUNNING,
                            "上下文太满，正在压缩早先的工具往返…",
                            kind="guard",
                            payload={"code": "prompt_too_long"},
                        ),
                    )
                    try:
                        reply = self._model.complete(messages, specs)
                        break
                    except PromptTooLongError:
                        continue
                    except Exception as retry_error:
                        return self._model_error_stop(session, retry_error)
            except Exception as error:
                return self._model_error_stop(session, error)

            if not reply.wants_tools:
                # 正文收尾仍然有效，只是它交不出图——交付是 ``final_answer`` 上的字段，
                # 正文里根本没有那个字段。**不在这里逼模型改用终止动作**：原生协议的默认
                # 收尾就是一条消息，每轮为它多烧一个模型步，等于把「独立交付动作」那一步
                # 原样搬了个地方，而这一版要修的正是那一步。改用终止动作交给提示词与
                # 工具表去推动；真推不动时，账本会把这一轮如实标成带缺口。
                answer = self._judge_answer(session, messages, reply.content)
                if answer is not None:
                    return answer
                messages.append(ChatMessage(ChatRole.USER, session.pending_reminder))
                continue

            messages.append(ChatMessage(ChatRole.ASSISTANT, reply.content, tool_calls=reply.tool_calls))
            terminal = next((call for call in reply.tool_calls if call.name == FINAL_ANSWER_TOOL), None)
            productive = False
            for call in reply.tool_calls:
                if call.name == FINAL_ANSWER_TOOL:
                    # 终止动作不进 ``_run_tool``：它不检索也不读库，交付与作答在同一步里
                    # 完成。但仍然要回一条 tool 消息——带了 tool_calls 却不给配对结果，
                    # 严格 chat template 的端点会直接 400。
                    messages.append(
                        ChatMessage(
                            ChatRole.TOOL,
                            # 措辞取「收到」而不是「已采纳」：门禁可能随后把它打回，
                            # 那时模型会带着提醒再来一次，说「已采纳」就是在骗它。
                            json.dumps({"received": True}, ensure_ascii=False),
                            tool_call_id=call.call_id,
                        )
                    )
                    continue
                text, attachments, empty, got_something = self._run_tool(session, call)
                productive = productive or got_something
                session.delivered = session.delivered.merge(attachments)
                session.search_returned_nothing = session.search_returned_nothing or empty
                messages.append(ChatMessage(ChatRole.TOOL, text, tool_call_id=call.call_id))

            if terminal is not None:
                answer = self._finish_with_final_answer(session, messages, terminal)
                if answer is not None:
                    return answer
                messages.append(ChatMessage(ChatRole.USER, session.pending_reminder))
                continue

            # 循环级的活性护栏：连续几轮一条有用的结果都没拿到，就该让模型收口。
            # 提醒**只给一次**（``block_guard_code`` 的既有口径），再犯就照走——
            # 不能因为模型不改就一直转，``can_step()`` 是最后一道闸。
            session.stalled_rounds = 0 if productive else session.stalled_rounds + 1
            if session.stalled_rounds >= STALLED_ROUNDS_BEFORE_NUDGE and state.block_guard_code(
                "stalled_on_denials"
            ):
                self._publish(
                    session,
                    state.emit(
                        ProgressStage.RECOVERING,
                        ProgressStatus.RUNNING,
                        "连续几轮工具调用都没有结果，让它基于已有内容作答…",
                        kind="guard",
                        payload={"code": "stalled_on_denials"},
                    ),
                )
                messages.append(ChatMessage(ChatRole.USER, STALLED_NUDGE))
                continue

    def _model_error_stop(self, session: _Session, error: Exception) -> str:
        """模型这一侧的失败收尾。

        ``detail`` 只带**服务侧**给的说明，与规划那条同一条理由（用户 2026-09-25 裁决二）：
        异常文案里往往已经写着端点与服务端给的原因（8100 缺 tool-call flag 时那句 400
        就是），转述比在上层另拼一句更准；别的原因（2.0 自己拼错了请求、提示词太大）
        不往里塞，免得把内部形状说给用户听——与 ``goal_planner`` 同一条边界。
        """
        log.warning("模型调用失败: %s", error)
        session.state.finish(TerminationReason.MODEL_ERROR)
        detail = str(error) if isinstance(error, ModelServiceError) else ""
        return self._stop(session, TerminationReason.MODEL_ERROR, detail=detail)

    def _finish_with_final_answer(
        self, session: _Session, messages: Sequence[ChatMessage], call: ToolCall
    ) -> str | None:
        """终止动作：交付与作答**在同一步里**完成。

        这是 1.0 ``runtime.py:309`` 的 ``{"action":"final","answer":...,"selected_image_handles":[...]}``
        在原生 tool-calling 下的等价物。1.0 的实测是：``get_original_photos`` 这个独立工具
        在生产上被调用 **0 次**，239 道图片题全部走这个字段；把它做成一次单独的工具调用
        只会多花一个模型步，而模型并没有因此更愿意交付。

        **它不走 ``_run_tool``**：那条路是检索与读库，要过 ``ToolPolicy`` 的工具预算；
        收尾不该是一个「预算用完就被拒」的动作——模型步快耗尽时，它更需把话说完、把已经
        取到的图交出去。1.0 也是这个形状：``final`` 是动作，不是工具。

        返回 ``None`` 表示这一轮的答案被拦下（调用方把提醒写进对话后继续循环）。
        """
        state = session.state
        try:
            arguments = call.parse()
        except ToolArgumentError:
            state.record(
                ToolCallRecord(
                    call_id=call.call_id,
                    tool=call.name,
                    signature=call.name,
                    status="error",
                    failure_reason="bad_arguments",
                )
            )
            self._publish(
                session,
                state.emit(
                    ProgressStage.RECOVERING,
                    ProgressStatus.RUNNING,
                    "收尾动作的参数无法解析，再问一次…",
                    kind="guard",
                    payload={"code": "bad_arguments"},
                ),
            )
            session.pending_reminder = "final_answer 的参数不是合法 JSON，请重新调用一次。"
            return None

        answer = str(arguments.get("answer") or "").strip()
        raw_handles = arguments.get("selected_image_handles")
        handles = (
            [item for item in raw_handles if isinstance(item, str)] if isinstance(raw_handles, list) else []
        )

        outcome = self._toolbox.deliver(state.current_result_set_id or "", handles, scope_id=state.scope_id)
        # **交付整体替换，不是累加**（失败时也替换成空）。1.0 是
        # ``turn.selected_image_handles = ...``——最后那个 final 说了算；累加会让一次被
        # 门禁打回的答案留下的图，混进后来那次没选图的交付里。
        session.delivered = DeliveredPhotos(items=outcome.attachments)
        if outcome.attachments:
            session.ledger.satisfy(EvidenceType.DELIVERED_PHOTO, call_id=call.call_id)

        state.record(
            ToolCallRecord(
                call_id=call.call_id,
                tool=call.name,
                signature=state.signature(call.name, arguments),
                status="ok" if outcome.ok else "error",
                failure_reason=outcome.failure_reason,
                observation=outcome.observation,
                model_visible=outcome.model_visible,
            )
        )
        self._tool_event(
            session,
            call,
            ProgressStatus.OK if outcome.ok else ProgressStatus.DENIED,
            _tool_status_text(call.name, outcome),
            "tool_result" if outcome.ok else "tool_error",
        )
        # 交付失败**不打断作答**：图交不出去是这一轮的缺口，不是这一轮不能回答。1.0 也是
        # 这样——``_normalize_selected_image_handles`` 把认不出来的 handle 直接丢掉，
        # 从不因此拒绝那个 final。
        return self._judge_answer(session, messages, answer)

    def _judge_answer(self, session: _Session, messages: Sequence[ChatMessage], answer: str) -> str | None:
        """返回 ``None`` 表示这次不是最终回答（继续循环），返回字符串表示最终答案。

        被门禁拦下时返回 ``None`` 并把提醒写进对话——调用方负责 ``continue``。
        """
        state = session.state
        answer = answer.strip()
        if not answer:
            # 1.0 的 ``parse_retries``：输出不可用就带着原因再问一次，用完次数才收尾。
            if state.block_guard_code("parse_failure"):
                self._publish(
                    session,
                    state.emit(
                        ProgressStage.RECOVERING,
                        ProgressStatus.RUNNING,
                        "模型这次没给出内容，再问一次…",
                        kind="guard",
                        payload={"code": "parse_failure"},
                    ),
                )
                session.pending_reminder = PARSE_FAILURE_NUDGE
                return None
            # 重试过仍然是空的。已经取到的证据不能被丢掉——用确定性的方式把事实说清楚。
            # 终止原因用 1.0 的 ``parse_failure``，不自造一个 1.0 没有的「空答案」。
            state.finish(TerminationReason.PARSE_FAILURE)
            return self._stop(session, TerminationReason.PARSE_FAILURE)

        verdict = self._guard.check(
            answer=answer,
            facts=GuardFacts.from_state(state),
        )
        if verdict.passed:
            if self._review_with_l2(session, messages, answer):
                # 与门禁拦下同一条路：调用方把 pending_reminder 加回对话后继续循环。
                return None
            reminder = self._one_shot_evidence_reminder(session, answer)
            if reminder is not None:
                session.pending_reminder = reminder
                return None
            if session.l2_unverified:
                # 1.0 ``runtime.py:2961-2968``：某次 final 曾被语义评审判不实、经软引导后
                # 放行 → **打标供分析**。1.0 把它记成一条 ``type="judge"`` / ``status=
                # "soft_release"`` / ``possibly_fabricated: True`` 的 step；
                # 2.0 落在进度事件上（那是同类信息的同一出口）。
                #
                # **这一格不是日志，是判据**：没有它，「评审放行的答案」与「评审通过的答案」
                # 在轨迹上完全一样，而评测与排查要区分的正是这两者。
                self._publish(
                    session,
                    state.emit(
                        ProgressStage.FINALIZING,
                        ProgressStatus.OK,
                        "已基于复核后的回答收尾。",
                        kind="judge",
                        payload={
                            "code": "l2_soft_release",
                            "possibly_fabricated": True,
                            "reason": session.l2_unverified_reason,
                        },
                    ),
                )
            state.finish(
                TerminationReason.ANSWERED_WITH_GAP
                if answered_with_gap(session.ledger)
                else TerminationReason.ANSWERED
            )
            return answer

        if verdict.hard:
            # 1.0 ``runtime.py:2784-2791``：硬拦不重试，直接走确定性兜底——把内部标识或
            # 别人的相册交付给用户是安全边界，不给第二次机会。
            #
            # **硬拦也要发一条事件。** 在此之前它一条都不发（truth 级在下面有
            # ``kind="guard"``，硬拦没有），于是被硬拦的回合在轨迹里既看不到触发了哪条
            # 规则、也看不到被打回的那段原文——实测那两道题因此完全不可诊断。
            # 用户看的文案里不该出现规则码，但**轨迹里必须有**。
            self._publish(
                session,
                state.emit(
                    ProgressStage.RECOVERING,
                    ProgressStatus.DENIED,
                    "回答触及安全边界，本轮不再由模型作答。",
                    kind="guard",
                    payload={"code": verdict.code, "hard": True},
                ),
            )
            state.finish(TerminationReason.GUARD_BLOCKED)
            return self._stop(session, TerminationReason.GUARD_BLOCKED)

        if not state.block_guard_code(verdict.code):
            # truth 级只重试一次（1.0 的 ``max_guard_retries = 1``），第二次放行并如实
            # 标注成「有未取到的证据」——再拦下去只会把模型步烧光。
            state.finish(TerminationReason.ANSWERED_WITH_GAP)
            return answer

        state.phase = FINALIZING
        self._publish(
            session,
            state.emit(
                ProgressStage.RECOVERING,
                ProgressStatus.RUNNING,
                verdict.message,
                kind="guard",
                payload={"code": verdict.code},
            ),
        )
        session.pending_reminder = verdict.message
        return None

    def _one_shot_evidence_reminder(self, session: _Session, answer: str) -> str | None:
        """收尾前的三条一次性提醒。返回提醒文案，或 ``None``（无事可提）。

        位置是**L1 与 L2 都通过之后、``state.finish()`` 之前**：走到这里说明这句回答
        形式上没毛病，而这三条问的是另一个问题——**模型是不是压根没去取证**。
        1.0 也把它们排在 L2 之后（``runtime.py:2540`` 起）。

        三条的先后与互斥逐条照 1.0：① 只针对 ``structured_fact``；② 在 ① 已经提醒过时
        把 ``structured_fact`` 排除掉（否则同一件事会被提醒两遍）；③ 只看「零工具调用」
        这一条，与账本无关。
        """
        state = session.state
        if not state.can_step():
            # 没有模型步了，提醒只会换来一次「预算用尽」的收尾。1.0 同样要求
            # ``turn.budget.can_model_step()``。
            return None

        attempted = _attempted_evidence_types(state)
        unattempted = [
            item for item in session.ledger.open_requirements() if item.evidence_type not in attempted
        ]

        # ① 声明了结构化统计需求，却一次都没查过精确统计——模型很可能正打算拿
        # ``search_memories`` 的候选数当答案报出去（1.0 ``runtime.py:116`` 记过这条）。
        structured = [item for item in unattempted if item.evidence_type is EvidenceType.STRUCTURED_FACT]
        if structured and state.block_guard_code("structured_fact_reminder"):
            self._reminder_event(session, "structured_fact_reminder", [item.id for item in structured])
            return STRUCTURED_FACT_NUDGE

        # ② 还有没试过的必需需求。这里**不问「为什么没满足」**，只问「试过没有」——
        # 取过没取到是检索的问题，根本没去取是规划与执行脱节，两者修法完全不同。
        rest = [
            item
            for item in unattempted
            if not (
                item.evidence_type is EvidenceType.STRUCTURED_FACT
                and "structured_fact_reminder" in state.guard_blocked_codes
            )
        ]
        # 1.0 要求 ``available`` 非空：一个工具都给不出去时，提醒「请用工具」是空话。
        available = select_tools(
            session.ledger,
            has_result_set=state.current_result_set_id is not None,
            tools_exhausted=not state.can_call_tool(),
        )
        if rest and available and state.block_guard_code("gate_unattempted"):
            self._reminder_event(session, "gate_unattempted", [item.id for item in rest])
            return UNATTEMPTED_NUDGE_TEMPLATE.format(
                pending="、".join(f"{item.id}:{item.evidence_type.value}" for item in rest),
                available="、".join(spec.name for spec in available),
            )

        # ③ 零工具调用，却报出了具体数字/金额/年份。需求为空（模型认为无需证据）**不**
        # 等于可以凭空编数字——这是一条与账本无关的兜底。
        #
        # 「零工具调用」要**排掉终止动作自己**：走到这里时它刚被记进 ``records``，直接看
        # ``not state.records`` 会恒为假，这条规则就成了死代码。
        called_tools = [record for record in state.records if record.tool != FINAL_ANSWER_TOOL]
        if (
            _QUANTIFIED_CLAIM.search(answer)
            and not called_tools
            and not _CHAT_ONLY.search(state.question)
            and state.block_guard_code("fabrication_check")
        ):
            self._reminder_event(session, "fabrication_check", [])
            return FABRICATION_NUDGE
        return None

    def _reminder_event(self, session: _Session, code: str, requirement_ids: Sequence[str]) -> None:
        self._publish(
            session,
            session.state.emit(
                ProgressStage.RECOVERING,
                ProgressStatus.RUNNING,
                "看起来还有证据没有去取，提醒模型一次…",
                kind="guard",
                payload={"code": code, "requirements": list(requirement_ids)},
            ),
        )

    def _review_with_l2(self, session: _Session, messages: Sequence[ChatMessage], answer: str) -> bool:
        """L2 语义评审。返回 ``True`` 表示「继续循环，重新作答」。

        调用条件与 1.0 ``runtime.py:2753`` 一致：L1 已通过（能走到这里就是）、本轮有工具
        结果、还有模型步、本回合没评审过。评审本身也计一个模型步——它花的是同一份预算。

        只有**事实性**问题（编造/矛盾/漏报/过度声称）才退回重写；``missing_disclosure``
        只是披露建议，不拦事实正确的回答（1.0 ``judge.py:174-176`` 的 severity 分层）。
        """
        state = session.state
        if session.judged or not state.records or not state.can_step():
            return False
        session.judged = True
        state.model_steps += 1
        verdict = self._judge.review(query=state.question, answer=answer, transcript=messages)
        if verdict.faithful or not verdict.truth_problems:
            return False
        # 1.0 ``runtime.py:2786-2788``：判过不实就先把标记落下来。**标记在「判不实」那一刻
        # 落、在「放行」那一刻发**——中间隔着一次软引导，而软引导之后**不再复评**
        # （``judged`` 那道闩）。所以这条等于「这条答案带着一次未复评的不实判决出门了」。
        session.l2_unverified = True
        session.l2_unverified_reason = "; ".join(str(item) for item in verdict.truth_problems)
        state.phase = FINALIZING
        self._publish(
            session,
            state.emit(
                ProgressStage.RECOVERING,
                ProgressStatus.RUNNING,
                verdict.reminder,
                kind="judge",
                payload={"code": "l2_unfaithful"},
            ),
        )
        session.pending_reminder = verdict.reminder
        return True

    def _run_tool(self, session: _Session, call: ToolCall) -> tuple[str, tuple[Attachment, ...], bool, bool]:
        """执行一次工具调用，返回 ``(发回模型的 JSON, 本次交付, 检索是否为空, 有没有拿到东西)``。

        最后那一格是给主循环数「模型是不是卡住了」用的：被拒、执行失败、拿到空结果，
        都不算拿到了东西。**它不参与任何工具自己的判据**，只回答「这一轮白跑了吗」。
        """
        state = session.state
        decision = self._policy.decide(call, state)

        if decision.action is ToolAction.DENY:
            state.record(
                ToolCallRecord(
                    call_id=call.call_id,
                    tool=call.name,
                    signature=decision.signature or call.name,
                    status="denied",
                    failure_reason=decision.code,
                )
            )
            self._tool_event(session, call, ProgressStatus.DENIED, decision.reason, "tool_error")
            return (
                json.dumps({"error": decision.code, "message": decision.reason}, ensure_ascii=False),
                (),
                False,
                False,
            )

        if decision.action is ToolAction.REPLAY:
            state.record(
                ToolCallRecord(
                    call_id=call.call_id,
                    tool=call.name,
                    signature=decision.signature,
                    status="replayed",
                    failure_reason="duplicate_call",
                )
            )
            self._tool_event(session, call, ProgressStatus.OK, decision.reason, "tool_result")
            # 回放**算拿到了东西**：模型手上多了一份（它刚问过的）结果，不算白跑。
            return json.dumps(dict(decision.replay), ensure_ascii=False), (), False, True

        self._tool_event(session, call, ProgressStatus.RUNNING, f"正在执行 {call.name}…", "model_action")
        outcome = self._toolbox.execute(
            call.name,
            decision.arguments,
            scope_id=state.scope_id,
            now=datetime.now(UTC),
            # 1.0 的 ``inspect_photo`` 用它找「问题里提到的那个已确认人物」当裁剪目标
            # （``tools.py:2162-2165``）。1.0 从可变的 ``task_state`` 里取，2.0 的
            # ``TaskState`` 没有那一格——**这不是加状态，是把已经在手上的那个字符串传下去**。
            question=state.question,
        )
        state.record(
            ToolCallRecord(
                call_id=call.call_id,
                tool=call.name,
                signature=decision.signature,
                status="ok" if outcome.ok else "error",
                failure_reason=outcome.failure_reason,
                observation=outcome.observation,
                model_visible=outcome.model_visible,
            )
        )

        if outcome.ok:
            for evidence_type in TOOLS_BY_NAME[call.name].satisfies:
                session.ledger.satisfy(evidence_type, call_id=call.call_id)
            self._tool_event(
                session, call, ProgressStatus.OK, _tool_status_text(call.name, outcome), "tool_result"
            )
        elif outcome.blocks is not None:
            # 工具明确说「这类证据本轮拿不到」——账本封口，门禁不再逼模型重试一个查不到
            # 的东西。这是「无证据时输出 gap 不编造」在工具层的落点。
            session.ledger.block(outcome.blocks, reason=outcome.failure_reason)
            self._tool_event(session, call, ProgressStatus.PARTIAL, outcome.failure_reason, "tool_error")
        else:
            self._tool_event(session, call, ProgressStatus.DENIED, outcome.failure_reason, "tool_error")

        return (
            json.dumps(dict(outcome.model_visible), ensure_ascii=False),
            outcome.attachments,
            is_empty_result(outcome),
            outcome.ok,
        )

    # ---- 装配与辅助 ---------------------------------------------------------

    def _initial_messages(self, record: TurnRecord) -> list[ChatMessage]:
        """system 只在最前面拼一次，历史作为真正的消息。

        1.0 把历史、结果集上下文、当前选中照片、人物画像、硬值约束统统折进
        ``messages[0]``，理由是严格 chat template 要求 system 必须在开头
        （``_merge_system_constraint``）。原生消息角色下这条约束只剩一句「别在中途插
        system」——本模块从头到尾只构造一条 system。
        """
        messages = [ChatMessage(ChatRole.SYSTEM, SYSTEM_PROMPT)]
        for message in self._conversations.history_for_prompt(record.conversation_id, HISTORY_LIMIT):
            if message.turn_id == record.id:
                # 本回合的用户消息刚写进去，下面会单独加，别重复。
                continue
            messages.append(ChatMessage(message.role, message.content))
        messages.append(ChatMessage(ChatRole.USER, record.question))
        return messages

    def _stop(self, session: _Session, reason: TerminationReason, *, detail: str = "") -> str:
        """收尾。``detail`` 只由**模型服务这一侧**的失败传入——见 ``_converse`` 的 except。"""
        photo_count = max(
            (int(record.observation.get("total") or 0) for record in session.state.records),
            default=0,
        )
        return emergency.fallback_answer(
            reason=reason,
            ledger=session.ledger,
            photo_count=photo_count,
            delivered_count=len(session.delivered.items),
            detail=detail,
        )

    def _tool_event(
        self,
        session: _Session,
        call: ToolCall,
        status: ProgressStatus,
        text: str,
        kind: str,
    ) -> None:
        stage = ProgressStage.ACTION if status is ProgressStatus.RUNNING else ProgressStage.TOOL_RESULT
        self._publish(
            session,
            session.state.emit(
                stage,
                status,
                text,
                kind=kind,
                payload={"tool": call.name, "tool_call_id": call.call_id},
            ),
        )

    def _publish(self, session: _Session, event: ProgressEvent) -> None:
        self._turns.append_event(session.state.turn_id, event)
        if session.emit is not None:
            session.emit(event)


def _status_for(reason: TerminationReason) -> TurnStatus:
    # 1.0 的 policy_refusal 出口把 status 置成 ``"complete"``（``runtime.py:1728``）：
    # 拒绝是一次正确的完成，不是失败。照搬。
    if reason in {TerminationReason.ANSWERED, TerminationReason.POLICY_REFUSAL}:
        return TurnStatus.COMPLETE
    if reason is TerminationReason.CANCELLED:
        return TurnStatus.CANCELLED
    if reason is TerminationReason.GUARD_BLOCKED:
        return TurnStatus.BLOCKED
    if reason in {TerminationReason.MODEL_ERROR, TerminationReason.TIMEOUT}:
        return TurnStatus.FAILED
    return TurnStatus.PARTIAL


def _tool_status_text(name: str, outcome: ToolOutcome) -> str:
    if name == "get_result_page":
        # **必须报页号。** 原先它与 ``search_memories`` 共用一句「结果集里共有 N 张候选
        # 照片」——翻到第几页在轨迹里完全看不出来，实测排查时只能数次数、数不出页号。
        # 一条说不出自己做了什么的事件，等于没有诊断力。
        total = int(outcome.observation.get("total") or 0)
        page = int(outcome.model_visible.get("page") or 0)
        return f"已翻到第 {page} 页（这一份共 {total} 张候选）。" if total else "这次检索没有匹配到照片。"
    if name == "search_memories":
        total = int(outcome.observation.get("total") or 0)
        return f"结果集里共有 {total} 张候选照片。" if total else "这次检索没有匹配到照片。"
    if name == FINAL_ANSWER_TOOL:
        delivered = int(outcome.observation.get("delivered") or 0)
        return f"已把 {delivered} 张照片交给用户。" if delivered else "已给出回答。"
    if name == "get_person_profile":
        return "已读取人物档案。" if outcome.observation.get("found") else "没有这位人物的档案。"
    if name == "read_photo_text":
        return str(outcome.model_visible.get("note") or "已读取照片里的文字。")
    if name == "inspect_photo":
        return str(outcome.model_visible.get("observation") or "已复核这张照片。")
    if name == "query_photo_people":
        # 那句话本来就是为模型写的一句人话（1.0 ``tools.py:2602``），
        # 直接拿来当进度条文本——另编一句会让两处对同一件事各说各的。
        return str(outcome.model_visible.get("summary") or "已读取照片里的人物。")
    return "工具执行完成。"
