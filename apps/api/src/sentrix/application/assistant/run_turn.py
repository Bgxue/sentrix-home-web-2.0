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
from sentrix.domain.assistant.evidence_ledger import EvidenceLedger
from sentrix.domain.assistant.final_guard import FinalGuard, GuardFacts, answered_with_gap
from sentrix.domain.assistant.models import (
    Attachment,
    ChatMessage,
    ChatRole,
    ModelReply,
    ProgressEvent,
    ProgressStage,
    ProgressStatus,
    TerminationReason,
    ToolCall,
    TurnRecord,
    TurnStatus,
)
from sentrix.domain.assistant.policy import detect_policy_refusal
from sentrix.domain.assistant.ports import (
    ChatModel,
    ConversationRepository,
    ModelServiceError,
    TurnRepository,
)
from sentrix.domain.assistant.profile import DEFAULT_PROFILE, TurnProfile
from sentrix.domain.assistant.task_state import ACTING, FINALIZING, TaskState, ToolCallRecord
from sentrix.domain.assistant.tool_policy import ToolAction, ToolPolicy
from sentrix.domain.assistant.tools import TOOL_SPECS, TOOLS_BY_NAME
from sentrix.domain.errors import DomainError

log = logging.getLogger("sentrix.assistant.run_turn")

HISTORY_LIMIT = 8

SYSTEM_PROMPT = """你是这个家庭相册的助手。你能做的只有两件事：检索照片，和读取已确认人物的档案。

规则：
- 需要照片证据时必须先调用工具，不要凭印象回答。
- 用户说「给我看看 / 我想看」时，必须调用 get_original_photos 把照片交出去，只回一段文字不算交付。
- 工具没给到的信息就说不知道，不要编造。
- handle（photo_1 这类）只在调用工具时使用，不要写进给用户的回答里。
- 用户用中文提问，就用中文回答。"""

# 模型这一轮没给出可用输出时给它的一句提醒。1.0 对这类输入走的是**解析重试**
# （``runtime.py`` 的 ``parse_retries``：模型输出解析不出 action 就带着原因再问一次，
# 用完重试次数才以 ``parse_failure`` 收尾），而不是另立一个终止原因。这里照 1.0 的口径：
# 提醒一次，再空就按 ``PARSE_FAILURE`` 收尾，用确定性兜底把已有证据说清楚。
PARSE_FAILURE_NUDGE = "你上一条回复是空的。请直接用一两句中文把结论说给用户，不要再调用工具。"

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
    # 门禁拦下时写给模型的提醒，由主循环作为一条 user 消息加回去。
    pending_reminder: str = ""


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

            try:
                reply = self._model.complete(messages, TOOL_SPECS)
            except Exception as error:
                log.warning("模型调用失败: %s", error)
                state.finish(TerminationReason.MODEL_ERROR)
                # 工具循环里的服务侧失败带说明走，与规划那条同一条理由（用户 2026-09-25
                # 裁决二）：异常文案里已经写着端点与服务端给的原因（8100 缺 tool-call flag
                # 时那句 400 就是），转述比在上层另拼一句更准；别的原因（2.0 自己拼错了
                # 请求）不往里塞，免得把内部形状说给用户听——与 ``goal_planner`` 同一条边界。
                detail = str(error) if isinstance(error, ModelServiceError) else ""
                return self._stop(session, TerminationReason.MODEL_ERROR, detail=detail)

            if not reply.wants_tools:
                answer = self._judge_answer(session, messages, reply)
                if answer is not None:
                    return answer
                messages.append(ChatMessage(ChatRole.USER, session.pending_reminder))
                continue

            messages.append(ChatMessage(ChatRole.ASSISTANT, reply.content, tool_calls=reply.tool_calls))
            for call in reply.tool_calls:
                text, attachments, empty = self._run_tool(session, call)
                session.delivered = session.delivered.merge(attachments)
                session.search_returned_nothing = session.search_returned_nothing or empty
                messages.append(ChatMessage(ChatRole.TOOL, text, tool_call_id=call.call_id))

    def _judge_answer(
        self, session: _Session, messages: Sequence[ChatMessage], reply: ModelReply
    ) -> str | None:
        """返回 ``None`` 表示这次不是最终回答（继续循环），返回字符串表示最终答案。

        被门禁拦下时返回 ``None`` 并把提醒写进对话——调用方负责 ``continue``。
        """
        state = session.state
        answer = reply.content.strip()
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
            state.finish(
                TerminationReason.ANSWERED_WITH_GAP
                if answered_with_gap(session.ledger)
                else TerminationReason.ANSWERED
            )
            return answer

        if verdict.hard:
            # 1.0 ``runtime.py:2784-2791``：硬拦不重试，直接走确定性兜底——把内部标识或
            # 别人的相册交付给用户是安全边界，不给第二次机会。
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

    def _run_tool(self, session: _Session, call: ToolCall) -> tuple[str, tuple[Attachment, ...], bool]:
        """执行一次工具调用，返回 ``(发回模型的 JSON, 本次交付, 检索是否为空)``。"""
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
            return json.dumps(dict(decision.replay), ensure_ascii=False), (), False

        self._tool_event(session, call, ProgressStatus.RUNNING, f"正在执行 {call.name}…", "model_action")
        outcome = self._toolbox.execute(
            call.name, decision.arguments, scope_id=state.scope_id, now=datetime.now(UTC)
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
    if name in {"search_memories", "get_result_page"}:
        total = int(outcome.observation.get("total") or 0)
        return f"结果集里共有 {total} 张候选照片。" if total else "这次检索没有匹配到照片。"
    if name == "get_original_photos":
        return f"已把 {outcome.observation.get('delivered', 0)} 张照片交给用户。"
    if name == "get_person_profile":
        return "已读取人物档案。" if outcome.observation.get("found") else "没有这位人物的档案。"
    return "工具执行完成。"
