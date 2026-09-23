"""事实写入用例：把一条观察产出的候选事实按 1.0 的规则落进 ``facts`` 表。

对应 1.0 ``pipeline.py:346-350``——``process()`` 里那段逐条 ``maintain_fact``。
**调用时机是观察级的**：一条观察写完之后立刻处理它自己那组候选事实，1.0 也是这个
位置（``add_observation`` → ``merge_observation_into_event`` → ``maintain_fact``）。

门禁是 1.0 原文的两段合取（``pipeline.py:348``）::

    all(fact.get(k) for k in ("subject","predicate","object")) and
    self.store.is_confirmed_person_name(fact.get("subject"), scope_id)

第一段是纯规则 ``is_writable_fact``（``domain/memories/facts.py``）；第二段由
``confirmed_people`` 提供——**入参是 ``scope_id``，回的是名字集合**（不是 id），
判据是 1.0 ``db.py:1494`` 的 ``is_confirmed_person_name``：只认
``entity_type='person' AND status='confirmed'`` 且 ``canonical_name`` **精确相等**，
**不查别名**。

**两半在 S5b 同时接上**（用户裁决④），此前它们各自都能让这张表恒空：

1. **候选来源**：1.0 的候选来自模型自报的 ``facts``（``model_clients.py:1130-1198``
   的 ``analyze_image``），2.0 的 S4 明确不搬它，于是 ``candidates`` 在生产装配下
   恒为空元组。现在 ``Observation.facts`` 就是那条来源。
2. **门禁**：S4 之前没有身份层，``confirmed_people`` 恒为空集。

S4 的用例里有一条「门禁关着时写不出东西」的断言，它**必须继续红在它该红的地方**：
门禁是「subject 必须是已确认人物的本名」，不是「有 subject 就行」。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence

from sentrix.domain.media.models import Observation, ObservationFact
from sentrix.domain.memories.facts import Fact, FactWriteAction, decide_fact_write, is_writable_fact
from sentrix.domain.memories.ports import FactRepository


def _new_fact_id() -> str:
    """1.0 ``make_id("fact")``。与 ``application/identity/review_cluster.py`` 同一个格式
    ——两条写事实的路径必须给出同一种 id，否则库里会有两种长相的事实。"""
    return f"fact_{uuid.uuid4().hex[:12]}"


class MaintainFacts:
    def __init__(
        self,
        *,
        facts: FactRepository,
        candidates: Callable[[Observation], Sequence[ObservationFact]],
        confirmed_people: Callable[[str], frozenset[str]],
    ) -> None:
        self._facts = facts
        self._candidates = candidates
        # **没有默认值。** S4 给这一个留的是「恒回空集」的默认值，代价与 ``AssignEvents``
        # 的两个来源完全一样：门禁关着与忘了接线在类型上长得一模一样，而「事实恒空」
        # 不报错、只是永远查不到。Task 80 已经在那两处删掉默认值，这里跟着删。
        self._confirmed_people = confirmed_people

    def maintain(self, observation: Observation) -> tuple[str, ...]:
        """写这条观察能写的事实，返回涉及的事实 id。

        返回值保持 1.0 ``process()`` 里 ``fact_ids`` 的语义：同值合并时给的是**被合并
        的那条**的 id，不是一条新行（1.0 ``db.py:3406-3408`` 的 REINFORCE 分支）。
        """
        confirmed = self._confirmed_people(observation.scope_id)
        written: list[str] = []
        for candidate in self._candidates(observation):
            if not is_writable_fact(candidate.subject, candidate.predicate, candidate.object):
                continue
            # 1.0 的门只校验 subject（``is_confirmed_person_name(fact.get("subject"), ...)``）。
            if candidate.subject not in confirmed:
                continue
            written.append(self._write(observation, candidate))
        return tuple(written)

    def purge_unanchored(self, scope_id: str) -> int:
        """删掉本 scope 里 subject 不再是「已确认人物本名」的事实，返回删了几条。

        1.0 ``db.py:3433-3447``。它**不是**维护脚本里的顺手动作：门禁只保证**写入那一刻**
        subject 是已确认人物，人一旦改名或候选被拒，那条事实就失去了锚点，而它仍然会
        被检索命中——「妈妈去过赵县」在人已经不是「妈妈」之后还在应答里出现。

        **S5 没有任何地方调它**（1.0 的唯一调用点是
        ``scripts/maintenance/rebuild_memory.py``，2.0 不复刻脚本）。这不是漏接，是这条
        能力要等一个维护入口才有地方挂；如实登记在 ``s5-deviations.md``。
        """
        return self._facts.purge_unanchored(scope_id)

    def _write(self, observation: Observation, candidate: ObservationFact) -> str:
        existing = self._facts.active_for(
            scope_id=observation.scope_id,
            subject=candidate.subject,
            predicate=candidate.predicate,
        )
        write = decide_fact_write(
            existing=existing,
            object_value=candidate.object,
            evidence_ids=(observation.id,),
            confidence=candidate.confidence,
        )
        if write.action is FactWriteAction.REINFORCE:
            assert existing is not None  # decide_fact_write 只在 existing 非空时给 REINFORCE
            reinforced: Fact = self._facts.reinforce(
                existing.id, evidence_ids=write.evidence_ids, confidence=write.confidence
            )
            return reinforced.id
        stored = self._facts.insert(
            Fact(
                id=_new_fact_id(),
                scope_id=observation.scope_id,
                subject=candidate.subject,
                predicate=candidate.predicate,
                object=candidate.object,
                status=write.status,
                confidence=write.confidence,
                evidence_ids=write.evidence_ids,
                revision=write.revision,
                supersedes_fact_id=write.supersedes_fact_id,
            )
        )
        return stored.id
