"""事实写入决定与状态迁移。对应 1.0 ``backend/db.py:3397-3460``。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.memories.facts import (
    Fact,
    FactStatus,
    FactWriteAction,
    confirm,
    decide_fact_write,
    is_writable_fact,
    retract,
    superseded_by,
)

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)


def fact(**overrides) -> Fact:
    fields: dict[str, object] = {
        "id": "fact_1",
        "scope_id": "album1",
        "subject": "妈妈",
        "predicate": "去过",
        "object": "赵县",
        "status": FactStatus.ACTIVE,
        "confidence": 0.65,
        "evidence_ids": ("obs_1",),
        "revision": 1,
    }
    fields.update(overrides)
    return Fact(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("subject", "predicate", "object_value", "expected"),
    [
        ("妈妈", "去过", "赵县", True),
        ("", "去过", "赵县", False),
        ("妈妈", "  ", "赵县", False),
        ("妈妈", "去过", None, False),
        (None, None, None, False),
    ],
)
def test_is_writable_fact_requires_all_three(subject, predicate, object_value, expected):
    assert is_writable_fact(subject, predicate, object_value) is expected


def test_same_object_reinforces_instead_of_inserting():
    """值没变 → 合并证据、抬 confidence，不新建行。"""
    decision = decide_fact_write(
        existing=fact(), object_value="赵县", evidence_ids=("obs_2",), confidence=0.9
    )
    assert decision.action is FactWriteAction.REINFORCE
    assert decision.evidence_ids == ("obs_1", "obs_2")
    assert decision.revision == 1
    assert decision.supersedes_fact_id is None


def test_reinforce_keeps_the_higher_confidence():
    """1.0 用 SQL 的 ``MAX(confidence, ?)``——只抬不降。低置信证据不能让已确信的事变可疑。"""
    decision = decide_fact_write(
        existing=fact(confidence=0.9), object_value="赵县", evidence_ids=("obs_2",), confidence=0.2
    )
    assert decision.confidence == pytest.approx(0.9)


def test_reinforce_dedupes_evidence_preserving_order():
    decision = decide_fact_write(
        existing=fact(evidence_ids=("obs_1", "obs_2")),
        object_value="赵县",
        evidence_ids=("obs_2", "obs_3"),
        confidence=0.5,
    )
    assert decision.evidence_ids == ("obs_1", "obs_2", "obs_3")


def test_changed_object_creates_a_pending_row_that_supersedes():
    """有 active 时新值进 ``pending``——改口要人确认，这与 1.0 一致。"""
    decision = decide_fact_write(
        existing=fact(), object_value="保定", evidence_ids=("obs_5",), confidence=0.7
    )
    assert decision.action is FactWriteAction.INSERT
    assert decision.status is FactStatus.PENDING
    assert decision.revision == 2
    assert decision.supersedes_fact_id == "fact_1"


def test_first_write_is_active_with_revision_one():
    decision = decide_fact_write(existing=None, object_value="赵县", evidence_ids=("obs_1",), confidence=0.65)
    assert decision.status is FactStatus.ACTIVE
    assert decision.revision == 1
    assert decision.supersedes_fact_id is None


def test_blank_evidence_is_dropped():
    decision = decide_fact_write(
        existing=None, object_value="赵县", evidence_ids=("obs_1", "", "obs_1"), confidence=0.5
    )
    assert decision.evidence_ids == ("obs_1",)


def test_confirm_promotes_pending_to_active():
    activated, _ = confirm(fact(status=FactStatus.PENDING), now=NOW)
    assert activated.status is FactStatus.ACTIVE
    assert activated.id == "fact_1"


def test_confirm_rejects_already_retracted():
    with pytest.raises(ValueError, match="只有 pending/active"):
        confirm(fact(status=FactStatus.RETRACTED), now=NOW)


def test_retract_and_supersede_are_distinct_terminal_states():
    """``retracted`` 是人拒绝的，``superseded`` 是被新值顶掉的——两者含义不同。"""
    assert retract(fact()).status is FactStatus.RETRACTED
    assert superseded_by(fact()).status is FactStatus.SUPERSEDED
