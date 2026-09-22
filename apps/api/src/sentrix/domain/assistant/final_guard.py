"""答案门禁：模型给出最终回答之前，用确定性规则过一道。

对应 1.0 ``agent_runtime/final_guard.py`` 的 L1，并保留它最要紧的一条纪律（写在
``final_guard.py:1-22`` 与 ``judge.py:6-13`` 的文档里）：**代码永远不做语义等价判定**。
1.0 为此把原来的一批正则规则删掉了——「三个人」和「3」是不是同一个数，正则判不对，
只会误伤事实正确的回答。语义评审是 L2（判分模型）的事，属于 S6；S3 只做 L1。

L1 只保留三类：

- **安全**：内部标识泄漏。模型把 ``asset_xxx`` / ``photo_1`` / 表名写进回答，用户看到的是
  系统内部结构。1.0 ``final_guard.py:155-179`` 的硬拦规则。
- **占位符**：模板没填就交付。``final_guard.py:97-98``。
- **存在性矛盾**：检索结果为空却断言「找到了」，或一次检索都没做就断言「没找到」。
  ``final_guard.py:99-102`` 与 ``_check_existence``（``:115-153``）。

第四条是 2.0 补的：**证据门**。1.0 的零检索拦截（``runtime.py:2568``）只拦「没检索却
断言没有」，对「用户要图、模型却只回一段文字」完全没有保护——实测 8100 上问「给我看看
在海边的照片」，模型调用了一次 ``search_memories`` 之后直接给文字，一张图都没交付。
2.0 把「要交付」变成一条证据需求，没满足就提醒一次。

**所有门禁只拦一次**（``TaskState.block_guard_code``）：提醒过就放行并如实标注。1.0 的
教训是反过来的——那道零检索拦截没有标志位，模型只要坚持不调工具就会被拦到预算耗尽，
白烧 8 个模型步（实测 29% 的回合零工具调用）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sentrix.domain.assistant.evidence_ledger import EvidenceLedger
from sentrix.domain.assistant.task_state import TaskState
from sentrix.domain.assistant.tools import RETRIEVAL_TOOLS

# 内部标识：1.0 ``final_guard.py:164-172`` 的两条正则。
_INTERNAL_ID = re.compile(r"\b(?:asset|obs|entity|mention|claim|turn|conversation|result_set)_[a-z0-9]{6,}\b")
_PHOTO_HANDLE = re.compile(r"(?<![A-Za-z0-9_])photo_\d+(?![A-Za-z0-9_])")
_INTERNAL_TABLE = re.compile(r"\b(?:assets|observations|memory_vectors|agent_turns|agent_result_sets)\b")

# 模板占位符：``[地点名称]`` / ``{{query}}`` / ``TODO``。
_PLACEHOLDER = re.compile(
    r"\[[^\[\]]{0,48}(?:填入|此处|占位|待填|名称|名字|姓名|数量|时间|地点|内容|数字|照片|记录)[^\[\]]{0,36}\]"
    r"|\{\{[^}]{1,48}\}\}"
    r"|\bTODO\b"
)

# 断言「找到了」。与 1.0 的 ``_FOUND_DELIVERY`` 同构，但**去掉了 ``_ANY_DENIAL`` 的整句
# 否定门槛**：那套「句中出现任何否定词就豁免」的规则把「没找到 A，但找到了 B」也一起
# 豁免了。这里只看断言本身。
_FOUND_CLAIM = re.compile(
    r"(?:已|为)?(?:为您|为你)?找到|已找到|找到了|这是(?:您|你)要找的|这就是(?:您|你)?要的"
    r"|有(?:一张|两张|几张|这些)?(?:照片|记录)"
)

# 断言「没找到」。
_DENY_EXISTS = re.compile(
    r"没(?:有)?找到|未找到|找不到|查无"
    r"|没有(?:任何|符合|相关|一张|一个|一条|拍到|拍过|去过)?(?:照片|记录|回忆|记忆|相关)"
)
# 模糊表述豁免：「无法确认」「可能没有」不是断言。
_HEDGE = re.compile(r"无法确认|不能确认|不确定|还不能|暂时|无法判断|记不清|可能没有|未必|不能确定")


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    passed: bool
    code: str = ""
    message: str = ""

    @staticmethod
    def ok() -> GuardVerdict:
        return GuardVerdict(passed=True)


class FinalGuard:
    def check(
        self,
        *,
        answer: str,
        ledger: EvidenceLedger,
        state: TaskState,
        search_returned_nothing: bool,
    ) -> GuardVerdict:
        """``search_returned_nothing`` 由调用方从工具观察里算出——门禁只看「这次检索
        空不空」这一个事实，不解析工具结果的结构。"""
        searched = any(record.tool in RETRIEVAL_TOOLS for record in state.records)
        for verdict in (
            self._safety(answer),
            self._placeholder(answer),
            self._existence(answer, searched=searched, search_returned_nothing=search_returned_nothing),
            self._evidence_gate(ledger=ledger, state=state),
        ):
            if verdict is not None:
                return verdict
        return GuardVerdict.ok()

    # ---- 规则 ---------------------------------------------------------------

    @staticmethod
    def _safety(answer: str) -> GuardVerdict | None:
        if _INTERNAL_ID.search(answer) or _PHOTO_HANDLE.search(answer) or _INTERNAL_TABLE.search(answer):
            return GuardVerdict(
                passed=False,
                code="internal_reference_leak",
                message=(
                    "回答里出现了系统内部标识（照片编号或记录 id）。"
                    "请用「这张照片」这类说法重写，不要写 photo_1 或 asset_ 开头的串。"
                ),
            )
        return None

    @staticmethod
    def _placeholder(answer: str) -> GuardVerdict | None:
        if _PLACEHOLDER.search(answer):
            return GuardVerdict(
                passed=False,
                code="placeholder_leak",
                message="回答里有没填上的模板占位符，请把方括号/花括号里的占位替换成真实内容，或删掉那句话。",
            )
        return None

    @staticmethod
    def _existence(answer: str, *, searched: bool, search_returned_nothing: bool) -> GuardVerdict | None:
        hedged = bool(_HEDGE.search(answer))
        if not searched and _DENY_EXISTS.search(answer) and not hedged:
            return GuardVerdict(
                passed=False,
                code="denial_without_search",
                message="你还没有检索过相册，不能说「没有找到」。请先调用 search_memories。",
            )
        if search_returned_nothing and _FOUND_CLAIM.search(answer) and not hedged:
            return GuardVerdict(
                passed=False,
                code="fabrication_from_empty",
                message="这次检索没有返回任何照片，不能声称找到了。请如实说明没有匹配，或换个说法检索。",
            )
        return None

    @staticmethod
    def _evidence_gate(*, ledger: EvidenceLedger, state: TaskState) -> GuardVerdict | None:
        """还有开着的证据需求、预算也还有，就先别急着收尾。

        只对**尝试过工具**的回合生效。一次工具都没调的回合由上面那条
        ``denial_without_search`` 管，两条不要重复拦同一件事。
        """
        open_items = ledger.open_requirements()
        if not open_items:
            return None
        if not state.records:
            return None
        if not state.can_call_tool() or not state.can_step():
            return None
        unmet = "、".join(item.description.rstrip("。；") for item in open_items)
        return GuardVerdict(
            passed=False,
            code="evidence_gate",
            message=(
                f"还有没拿到的证据：{unmet}。"
                "请先调用相应的工具把它们取到；确实取不到时，在回答里如实说明缺什么。"
            ),
        )


def answered_with_gap(ledger: EvidenceLedger) -> bool:
    """收尾时是否还有没满足的需求——用来区分「答完了」与「如实说了答不出来」。"""
    return not ledger.is_complete
