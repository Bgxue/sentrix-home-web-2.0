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
) -> str:
    """``photo_count`` / ``delivered_count`` 来自结果集这一侧的事实，不是模型的话。"""
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

    parts.append(f"（本轮因「{_REASON_LABEL[reason]}」提前结束。）")
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
    TerminationReason.MODEL_ERROR: "模型不可用",
    TerminationReason.GUARD_BLOCKED: "回答未通过事实校验",
    TerminationReason.CANCELLED: "已取消",
}


def clarify_answer(reason: TerminationReason, details: Sequence[str] = ()) -> str:
    """连账本都还没建起来时的兜底（例如第一次模型调用就失败）。"""
    head = f"这次没能完成检索：{_REASON_LABEL[reason]}。"
    if details:
        return head + " " + " ".join(details)
    return head
