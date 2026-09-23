"""事实写入接线的验收：真 SQLite + 真迁移，验的是「谁在什么时候决定写什么」。

``domain/memories/facts.py`` 的三条纯规则与 ``repositories/facts.py`` 在 S4 建好之后
**没有任何生产调用方**——1.0 的 ``_commit_semantic_image``（``pipeline.py:570-635``）
整段没有一处 ``maintain_fact``，只有 ``process()``（``pipeline.py:346-350``）会调。
用户裁决第 3 条要修的就是这件事，而修的形式是「有调用方」而不是「有测试」。

所以这个文件分两段：

1. **规则侧**：门禁、合并、改口三条在真库上是什么样；
2. **接线侧**：``ImportAssets`` 在导入一条观察之后**真的会调**事实写入，且排在事件
   归属之后（1.0 ``merge_observation_into_event`` → ``maintain_fact`` 的顺序）。

**S4 的生产装配下事实表按设计是空的**，两个原因彼此独立、都不在接线里：
候选事实没有来源（观察器不搬模型自报的 ``facts``，``s4-deviations.md`` 第 30 条），
且门禁依赖 S5 的身份层（``is_confirmed_person_name`` 查 ``entities``）。
这两条都是用户裁决点，不在这里自己放宽——所以下面「门禁关着」那条断言必须保持
**红在它该红的地方**：谁放宽了门禁，它就红。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.application.memories.maintain_facts import FactCandidate, MaintainFacts
from sentrix.domain.media.models import AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.memories.facts import FactStatus
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
GPS = GpsPoint(30.1839, 120.2642)


@pytest.fixture
def facts(migrations_ready_db: Database) -> SqliteFactRepository:
    return SqliteFactRepository(migrations_ready_db)


def observation(*, observation_id: str = "obs_1", scope_id: str = "album1") -> Observation:
    return Observation(
        id=observation_id,
        scope_id=scope_id,
        asset_id="asset_1",
        source_type="import",
        captured_at=NOW,
        confidence=0.0,
        created_at=NOW,
        updated_at=NOW,
    )


def maintainer(
    facts: SqliteFactRepository,
    *,
    candidates: tuple[FactCandidate, ...],
    confirmed: frozenset[str] = frozenset(),
) -> MaintainFacts:
    return MaintainFacts(
        facts=facts,
        candidates=lambda _observation: candidates,
        confirmed_people=lambda _scope_id: confirmed,
    )


# ---- 规则侧 ----------------------------------------------------------------------


def test_without_a_confirmed_person_the_gate_stays_shut(facts: SqliteFactRepository):
    """S4 的装配就是这一条：没有身份层，subject 不可能是「已确认人物」。

    1.0 的门是 ``store.is_confirmed_person_name(fact["subject"], scope_id)``
    （``pipeline.py:348``），查的是 ``entities`` 里 ``status='confirmed'`` 的行。
    S5 之前那个集合恒空，于是**接进去也出不了东西**——这条断言把「空」的原因钉在
    门禁上，而不是钉在「没人调」上。
    """
    written = maintainer(
        facts, candidates=(FactCandidate(subject="妈妈", predicate="去过", object="赵县"),)
    ).maintain(observation())

    assert written == ()
    assert facts.list_facts("album1", limit=10) == []


def test_a_confirmed_subject_is_written_with_the_observation_as_evidence(facts: SqliteFactRepository):
    """门禁一开（S5 的来源），同一条候选就落库——证明接线本身是活的。"""
    written = maintainer(
        facts,
        candidates=(FactCandidate(subject="妈妈", predicate="去过", object="赵县", confidence=0.7),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation())

    assert len(written) == 1
    stored = facts.get(written[0])
    assert stored is not None
    assert stored.subject == "妈妈"
    assert stored.object == "赵县"
    assert stored.status is FactStatus.ACTIVE
    assert stored.evidence_ids == ("obs_1",)
    assert stored.confidence == pytest.approx(0.7)


def test_a_candidate_missing_a_field_is_skipped(facts: SqliteFactRepository):
    """1.0 ``all(fact.get(k) for k in ("subject","predicate","object"))`` 那一段。"""
    written = maintainer(
        facts,
        candidates=(FactCandidate(subject="妈妈", predicate="去过", object=""),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation())

    assert written == ()


def test_the_same_value_merges_evidence_instead_of_opening_a_second_row(facts: SqliteFactRepository):
    """同值合并证据 + ``MAX(confidence)``（1.0 ``db.py:3406-3408``）。"""
    use_case = maintainer(
        facts,
        candidates=(FactCandidate(subject="妈妈", predicate="去过", object="赵县", confidence=0.6),),
        confirmed=frozenset({"妈妈"}),
    )
    first = use_case.maintain(observation(observation_id="obs_1"))
    second = use_case.maintain(observation(observation_id="obs_2"))

    assert first == second
    stored = facts.get(first[0])
    assert stored is not None
    assert stored.evidence_ids == ("obs_1", "obs_2")
    assert stored.revision == 1
    assert len(facts.list_facts("album1", limit=10)) == 1


def test_a_changed_value_becomes_pending_and_supersedes_the_active_row(facts: SqliteFactRepository):
    """改口进 ``pending`` 并记 ``supersedes_fact_id``（1.0 ``db.py:3410``）。"""
    first = maintainer(
        facts,
        candidates=(FactCandidate(subject="妈妈", predicate="去过", object="赵县"),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation())
    changed = maintainer(
        facts,
        candidates=(FactCandidate(subject="妈妈", predicate="去过", object="保定"),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation(observation_id="obs_2"))

    assert changed != first
    replacement = facts.get(changed[0])
    assert replacement is not None
    assert replacement.status is FactStatus.PENDING
    assert replacement.supersedes_fact_id == first[0]
    assert facts.active_for(scope_id="album1", subject="妈妈", predicate="去过") is not None


# ---- 接线侧 ----------------------------------------------------------------------


class FakeStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def store(self, asset_id: str, file_name: str, content: bytes) -> Path:
        path = self._root / f"{asset_id}-{file_name}"
        path.write_bytes(content)
        return path


class FakeMetadata:
    def read(self, path: Path):  # noqa: ANN201 - 与 MetadataReader 的元组返回一致
        return (NOW, GPS, None)


class Recorder:
    def __init__(self) -> None:
        self.order: list[str] = []


def test_the_import_path_calls_fact_maintenance_after_event_assignment(
    tmp_path: Path, migrations_ready_db: Database, facts: SqliteFactRepository
):
    """接线点：1.0 的顺序是 ``merge_observation_into_event`` → ``maintain_fact``。

    事实是**观察级**的：一条观察处理一组候选，不等批末（批末那一步是事件总结）。
    这条用例在 ``ImportAssets`` 撤掉 ``_maintain_facts`` 调用时必然变红。
    """
    recorder = Recorder()
    maintain = MaintainFacts(
        facts=facts,
        # 门禁在这里是开的：这条用例验的是「调用发生」，不是 S4 门禁的取值。
        candidates=lambda observation: (
            recorder.order.append(f"facts:{observation.id}"),
            (FactCandidate(subject="妈妈", predicate="去过", object="赵县"),),
        )[1],
        confirmed_people=lambda _scope_id: frozenset({"妈妈"}),
    )
    importer = ImportAssets(
        assets=SqliteAssetRepository(migrations_ready_db),
        observations=SqliteObservationRepository(migrations_ready_db),
        storage=FakeStorage(tmp_path),
        metadata=FakeMetadata(),
        event_assigner=lambda observation: recorder.order.append("events") or "evt_1",
        fact_maintainer=maintain.maintain,
    )

    result = importer.execute(
        ImportRequest(scope_id="album1", file_name="one.jpg", content_type="image/jpeg", content=b"abc")
    )

    assert recorder.order == ["events", f"facts:{result.observation_id}"]
    assert len(result.fact_ids) == 1
    assert facts.get(result.fact_ids[0]) is not None


def test_without_a_fact_maintainer_the_import_is_still_an_import(
    tmp_path: Path, migrations_ready_db: Database
):
    """协作者可选：不传事实写入就不写，``fact_ids`` 是空元组而不是 None。"""
    importer = ImportAssets(
        assets=SqliteAssetRepository(migrations_ready_db),
        observations=SqliteObservationRepository(migrations_ready_db),
        storage=FakeStorage(tmp_path),
        metadata=FakeMetadata(),
    )

    result = importer.execute(
        ImportRequest(scope_id="album1", file_name="one.jpg", content_type="image/jpeg", content=b"abc")
    )

    assert result.fact_ids == ()
    assert result.status is AssetStatus.PROCESSED
    assert result.media_type is MediaType.IMAGE
