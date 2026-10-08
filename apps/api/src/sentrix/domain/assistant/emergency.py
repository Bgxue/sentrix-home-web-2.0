"""诚实兜底：预算耗尽 / 模型失败 / 门禁拦死时的确定性收尾。

对应 1.0 ``agent_runtime/emergency.py``。它的文档写得很准确：只基于**已有的**工具观察
产出，不生成新事实、不冒充完整回答。2.0 保留这个定位，并补上 1.0 缺的那一档。

1.0 的兜底只有一条尾句——「这次处理没有完成，没有产生可确认的结果。」
（``emergency.py:80-84``），于是「有检索结果但被丢弃」和「真的一无所获」共用同一句话。
2.0 按账本状态分档：拿到过照片就说拿到过几张（用结果集里的**真实条数**，不是模型
转述的数字），什么都没拿到就说没拿到。
"""

from __future__ import annotations

from collections.abc import Sequence

from sentrix.domain.assistant.evidence_ledger import EvidenceLedger, RequirementStatus
from sentrix.domain.assistant.models import TerminationReason


def _missing_clause(ledger: EvidenceLedger) -> str:
    missing = [
        item.description.rstrip("。；")
        for item in ledger.requirements
        if item.status is not RequirementStatus.SATISFIED
    ]
    if not missing:
        return ""
    return "还差这些没能确认：" + "；".join(missing) + "。"


def fallback_answer(
    *,
    reason: TerminationReason,
    ledger: EvidenceLedger,
    photo_count: int,
    delivered_count: int,
    detail: str = "",
) -> str:
    """``photo_count`` / ``delivered_count`` 来自结果集这一侧的事实，不是模型的话。

    ``detail`` 是**服务这一侧**自己的说明（``ModelServiceError`` 的文案里已经带好端点与
    状态码），只有那一类失败才给：与 ``run_turn`` 里规划那条同一条理由（用户 2026-09-25
    裁决二）——「模型服务不可用」单独一句不可执行，用户与运维照着它不知道该去查哪儿，
    而服务端那句 400 里写着根因。
    """
    parts: list[str] = []
    if photo_count > 0:
        parts.append(f"这一轮检索到 {photo_count} 张候选照片。")
    else:
        parts.append("这一轮没有检索到照片。")
    if delivered_count > 0:
        parts.append(f"其中 {delivered_count} 张已经放在回答下面，可以直接打开。")

    missing = _missing_clause(ledger)
    if missing:
        parts.append(missing)

    tail = f"本轮因「{_REASON_LABEL[reason]}」提前结束"
    if detail:
        tail = f"{tail}：{detail}"
    parts.append(f"（{tail}。）")
    return "".join(parts)


_REASON_LABEL: dict[TerminationReason, str] = {
    TerminationReason.ANSWERED: "已完成",
    TerminationReason.ANSWERED_WITH_GAP: "有未取到的证据",
    # 1.0 的 emergency 尾句用词就是「输出无法解析」（``emergency.py`` 的 ``_REASON_TAIL``）。
    TerminationReason.PARSE_FAILURE: "输出无法解析",
    TerminationReason.POLICY_REFUSAL: "政策拒答",
    TerminationReason.PLANNER_BLOCKED: "无法可靠规划证据需求",
    TerminationReason.NO_TOOL_CALL_STOPPED: "未取证就作答",
    TerminationReason.TOOL_BUDGET_EXHAUSTED: "工具调用预算用尽",
    TerminationReason.STEP_BUDGET_EXHAUSTED: "模型步数用尽",
    TerminationReason.TIMEOUT: "超时",
    # 说的是**模型服务**，不是「模型」。这条文案底下是 ``ModelServiceError``（端点不可达 /
    # 超时 / 模型名对不上 / 服务端 400），用户据此该去看那个端点——「模型不可用」听着像
    # 模型能力不行，与规划那条要分开的那个理由是同一条（用户 2026-09-25 裁决二）。
    TerminationReason.MODEL_ERROR: "模型服务不可用",
    # **不是「事实校验」。** ``GUARD_BLOCKED`` 只在 ``verdict.hard`` 时产生，而硬拦集合
    # （``final_guard.HARD_GUARD_CODES``）是 ``internal_id_leak`` / ``table_name_leak`` /
    # ``viewer_escape`` / ``write_not_allowed``——**四条全是安全与边界**，与"事实对不对"
    # 无关。原来是「回答未通过事实校验」，用户看到的是对失败原因的一句误导。
    TerminationReason.GUARD_BLOCKED: "回答触及了安全边界",
    TerminationReason.CANCELLED: "已取消",
}


def clarify_answer(reason: TerminationReason, details: Sequence[str] = ()) -> str:
    """连账本都还没建起来时的兜底（例如第一次模型调用就失败）。"""
    head = f"这次没能完成检索：{_REASON_LABEL[reason]}。"
    if details:
        return head + " " + " ".join(details)
    return head
