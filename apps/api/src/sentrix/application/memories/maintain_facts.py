"""事实写入用例：把一条观察产出的候选事实按 1.0 的规则落进 ``facts`` 表。

对应 1.0 ``pipeline.py:346-350``——``process()`` 里那段逐条 ``maintain_fact``。
**调用时机是观察级的**：一条观察写完之后立刻处理它自己那组候选事实，1.0 也是这个
位置（``add_observation`` → ``merge_observation_into_event`` → ``maintain_fact``）。

门禁是 1.0 原文的两段合取（``pipeline.py:348``）::

    all(fact.get(k) for k in ("subject","predicate","object")) and
    self.store.is_confirmed_person_name(fact.get("subject"), scope_id)

第一段是纯规则 ``is_writable_fact``（``domain/memories/facts.py``）；第二段是
``confirmed_people``，与 ``AssignEvents`` 的同名参数同构——**S5 之前恒为空集**。
1.0 的 ``is_confirmed_person_name`` 查的是 ``entities`` 表里
``entity_type='person' AND status='confirmed'`` 的行（``db.py:1494-1503``），
而 2.0 的身份层（人脸聚类 → 簇确认 → 命名 → entities）是 S5，S4 没有这张表。

所以这条路径在当前装配下**一行都写不出来**。这是门禁的真实后果，不是接线没接上：
「事实表按设计为空」与「事实表因为没人调而恒空」是两件事，这条用例把前者变成可断言的。
要改门禁（放宽到非人物主语也写，或换个确认判据）是**用户裁决点**，不在这里自己定。

候选事实的来源是同一处的第二个缺口：1.0 的候选来自模型自报的 ``facts``
（``model_clients.py:1130-1198`` 的 ``analyze_image`` 让它回 ``facts`` 数组），
2.0 的观察器**明确不搬**（``s4-deviations.md`` 第 30 条：事实由 ``maintain_fact``
规则决定，不由模型自报，属于 S5）。所以 ``candidates`` 这个协作者在 S4 的生产装配下
返回空元组，真来源随 S5 一起接。即使把门禁放开，**只放门禁也仍然出不了东西**——
这一点写在接线处的注释里，免得下一个人以为「放宽门禁就够了」。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sentrix.domain.media.models import Observation
from sentrix.domain.memories.facts import Fact, FactWriteAction, decide_fact_write, is_writable_fact
from sentrix.domain.memories.ports import FactRepository

# 1.0 ``pipeline.py:350``：``normalize_confidence(fact.get("confidence"), result.get("confidence", 0.5))``
# 的兜底值。模型没给 confidence 时用它。
DEFAULT_FACT_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class FactCandidate:
    """一条候选事实。1.0 里它是个 dict（``{"subject","predicate","object","confidence"}``）。"""

    subject: str
    predicate: str
    object: str
    confidence: float = DEFAULT_FACT_CONFIDENCE


def _new_fact_id() -> str:
    return f"fact_{uuid.uuid4().hex[:12]}"


class MaintainFacts:
    def __init__(
        self,
        *,
        facts: FactRepository,
        candidates: Callable[[Observation], Sequence[FactCandidate]],
        confirmed_people: Callable[[str], frozenset[str]] = lambda _scope_id: frozenset(),
    ) -> None:
        self._facts = facts
        self._candidates = candidates
        # S5 之前恒为空集：没有 entities 就没有「已确认人物」。不在这里编一个假来源。
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

    def _write(self, observation: Observation, candidate: FactCandidate) -> str:
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
