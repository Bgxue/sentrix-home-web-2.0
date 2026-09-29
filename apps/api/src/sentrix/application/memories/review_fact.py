"""事实的确认与驳回：把 ``pending`` 升成 ``active``（并作废同类的旧值），或置成 ``retracted``。

逐行移植 1.0 ``backend/db.py:3424`` 的 ``confirm_fact`` 与 ``:3449`` 的 ``reject_fact``。

**这个用例补的是一条断掉的链路。** 写入侧是完整的：``MaintainFacts`` 在模型改口时会
插一条 ``pending``（``domain/memories/facts.py`` 的 ``decide_fact_write``），
``supersedes_fact_id`` 也指回了旧值。但**没有任何东西能把这条 pending 升上去**——
``FactRepository.set_status`` / ``supersede_others`` / ``list_facts`` 三个方法写在仓储里、
全仓零调用者。于是模型改口产生的新值永远停在 ``pending``：既不生效、也删不掉、
也没有界面看得见它。2026-09-29 的方法级可达性门禁第一次跑就把这三个报了出来。

**两件事必须在同一个用例里。** 确认不只是「把这条改成 active」——1.0 的
``confirm_fact`` 先作废同类旧值再升级新值（``db.py:3428-3429`` 两条 UPDATE）。
只做后者的话，同一个 ``(subject, predicate)`` 会同时有**两条 active**，
而「同一 subject+predicate 只有一条 active」是写入规则
（``facts.py`` 模块 docstring 第 20 行）明确保证的不变量。破坏它不会报错，
只会让下一次 ``active_for`` 取回哪一条变成看运气。
"""

from __future__ import annotations

from sentrix.domain.errors import DomainError
from sentrix.domain.memories.facts import Fact, FactStatus
from sentrix.domain.memories.ports import FactRepository


class FactNotFoundError(DomainError, LookupError):
    """1.0 的 ``confirm_fact`` 抛 ``KeyError``、``reject_fact`` 的前置检查抛
    ``HTTPException(404)``——同一个条件在 1.0 里有两套错误类型。2.0 统一成一条带码的
    领域错误，由 ``domain_error_handler`` 映射成 404。"""

    code = "not_found"
    status_code = 404


class ReviewFact:
    def __init__(self, *, facts: FactRepository) -> None:
        self._facts = facts

    def confirm(self, fact_id: str) -> Fact:
        """把这条事实升为 ``active``，并把同 scope 同 ``(subject, predicate)`` 的
        其他 ``active`` 行置为 ``superseded``。

        **1.0 的作废语句不带 scope**（``db.py:3428``），跨相册的同名事实会被一起作废；
        2.0 收在本 scope 内。这条偏离记在 ``s4-deviations.md`` §2 第 7 项，
        由 ``SqliteFactRepository.supersede_others`` 的 docstring 再次点名。
        """
        fact = self._require(fact_id)
        self._facts.supersede_others(
            scope_id=fact.scope_id,
            subject=fact.subject,
            predicate=fact.predicate,
            keep_fact_id=fact.id,
        )
        return self._facts.set_status(fact.id, FactStatus.ACTIVE.value)

    def reject(self, fact_id: str) -> Fact:
        """把这条事实置为 ``retracted``。

        **驳回不作废同类旧值。** 1.0 的 ``reject_fact`` 只有一条 UPDATE；
        驳回一条 pending 之后，原来那条 active 仍然生效——这正是要的效果：
        「新值不对」与「这件事没有旧值」是两回事。
        """
        fact = self._require(fact_id)
        return self._facts.set_status(fact.id, FactStatus.RETRACTED.value)

    def _require(self, fact_id: str) -> Fact:
        fact = self._facts.get(fact_id)
        if fact is None:
            raise FactNotFoundError(f"fact {fact_id} not found")
        return fact
