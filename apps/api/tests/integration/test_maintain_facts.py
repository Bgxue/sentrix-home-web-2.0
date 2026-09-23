"""事实写入接线的验收：真 SQLite + 真迁移，验的是「谁在什么时候决定写什么」。

``domain/memories/facts.py`` 的三条纯规则与 ``repositories/facts.py`` 在 S4 建好之后
**没有任何生产调用方**——1.0 的 ``_commit_semantic_image``（``pipeline.py:570-635``）
整段没有一处 ``maintain_fact``，只有 ``process()``（``pipeline.py:346-350``）会调。
用户裁决第 3 条要修的就是这件事，而修的形式是「有调用方」而不是「有测试」。

所以这个文件分三段：

1. **规则侧**：门禁、合并、改口三条在真库上是什么样；
2. **门禁侧**：``confirmed_person_names`` 取的是真 ``entities`` 表——本名精确匹配、
   不看别名、不认未确认的人；
3. **接线侧**：``ImportAssets`` 在导入一条观察之后**真的会调**事实写入，且排在事件
   归属之后（1.0 ``merge_observation_into_event`` → ``maintain_fact`` 的顺序）。

**S4 的两处「按设计为空」在 S5b 同时消掉**（用户裁决④）：

- 候选来源 = 模型自报的 ``facts``，落库在 ``observations.facts_json``，从
  ``Observation.facts`` 读出来；
- 门禁 = ``entities`` 里 ``status='confirmed'`` 的人物本名。

**但门禁仍然是一条真门禁**：下面「subject 不是已确认人物时写不出来」那条断言必须保持
**红在它该红的地方**——谁把门禁放宽成「有 subject 就行」，它就红。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentrix.api.routes.media import build_import_assets
from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.application.media.observe_asset import ObserveAsset
from sentrix.application.memories.maintain_facts import MaintainFacts
from sentrix.domain.identity.models import FaceDetection
from sentrix.domain.media.models import (
    AssetStatus,
    GpsPoint,
    MediaType,
    Observation,
    ObservationFact,
)
from sentrix.domain.media.ports import ObservationSemantics
from sentrix.domain.memories.facts import FactStatus
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
GPS = GpsPoint(30.1839, 120.2642)


@pytest.fixture
def facts(migrations_ready_db: Database) -> SqliteFactRepository:
    return SqliteFactRepository(migrations_ready_db)


def observation(
    *,
    observation_id: str = "obs_1",
    scope_id: str = "album1",
    facts: tuple[ObservationFact, ...] = (),
) -> Observation:
    return Observation(
        id=observation_id,
        scope_id=scope_id,
        asset_id="asset_1",
        source_type="import",
        captured_at=NOW,
        confidence=0.0,
        created_at=NOW,
        updated_at=NOW,
        facts=facts,
    )


def maintainer(
    facts: SqliteFactRepository,
    *,
    candidates: tuple[ObservationFact, ...],
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
        facts, candidates=(ObservationFact(subject="妈妈", predicate="去过", object="赵县"),)
    ).maintain(observation())

    assert written == ()
    assert facts.list_facts("album1", limit=10) == []


def test_a_confirmed_subject_is_written_with_the_observation_as_evidence(facts: SqliteFactRepository):
    """门禁一开（S5 的来源），同一条候选就落库——证明接线本身是活的。"""
    written = maintainer(
        facts,
        candidates=(ObservationFact(subject="妈妈", predicate="去过", object="赵县", confidence=0.7),),
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
        candidates=(ObservationFact(subject="妈妈", predicate="去过", object=""),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation())

    assert written == ()


def test_the_same_value_merges_evidence_instead_of_opening_a_second_row(facts: SqliteFactRepository):
    """同值合并证据 + ``MAX(confidence)``（1.0 ``db.py:3406-3408``）。"""
    use_case = maintainer(
        facts,
        candidates=(ObservationFact(subject="妈妈", predicate="去过", object="赵县", confidence=0.6),),
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
        candidates=(ObservationFact(subject="妈妈", predicate="去过", object="赵县"),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation())
    changed = maintainer(
        facts,
        candidates=(ObservationFact(subject="妈妈", predicate="去过", object="保定"),),
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
            (ObservationFact(subject="妈妈", predicate="去过", object="赵县"),),
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


# ---- 门禁侧：取值来自真 entities 表 ------------------------------------------------


def add_person(db: Database, *, name: str, status: str, scope_id: str = "album1") -> str:
    repository = SqliteEntityRepository(db)
    entity = repository.create_person(name, status=status, scope_id=scope_id)
    return entity.id


def test_the_gate_reads_confirmed_names_from_the_real_entity_table(migrations_ready_db: Database):
    """1.0 ``is_confirmed_person_name``（``db.py:1494``）的集合形态：三条判据都要在。"""
    add_person(migrations_ready_db, name="妈妈", status="confirmed")
    add_person(migrations_ready_db, name="待确认人物簇 · cluster-1", status="pending")

    names = SqliteEntityRepository(migrations_ready_db).confirmed_person_names("album1")

    assert names == frozenset({"妈妈"})


def test_an_alias_is_not_a_confirmed_name(migrations_ready_db: Database):
    """**对照臂**：别名不算。家里人互相叫小名时，按别名放行会让事实落到错的人头上。"""
    entity_id = add_person(migrations_ready_db, name="妈妈", status="confirmed")
    repository = SqliteEntityRepository(migrations_ready_db)
    repository.set_person_aliases(entity_id, ("老妈",))

    names = repository.confirmed_person_names("album1")

    assert names == frozenset({"妈妈"})
    assert "老妈" not in names


def test_the_confirmed_names_are_scoped(migrations_ready_db: Database):
    """另一个 scope 的同名人不能让本 scope 的事实过门。"""
    add_person(migrations_ready_db, name="妈妈", status="confirmed", scope_id="album2")

    assert SqliteEntityRepository(migrations_ready_db).confirmed_person_names("album1") == frozenset()


# ---- 端到端：观察带 facts → 门禁开着 → facts 表里出现该人物的行 -----------------------

FACT_OBSERVATION = ObservationSemantics(
    caption="在赵县吃梨",
    place="果园",
    activity="采摘",
    event_type="出行",
    objects=("梨",),
    facts=(
        ObservationFact(subject="妈妈", predicate="去过", object="赵县", confidence=0.9),
        ObservationFact(subject="邻居", predicate="去过", object="赵县", confidence=0.9),
    ),
    confidence=0.65,
)


class FakeVlmObserver:
    """确定性的观察器：真件要打 8100，而这条用例验的是 facts 从观察走到事实表。"""

    model_id = "fake-vlm"

    def __init__(self, semantics: ObservationSemantics) -> None:
        self._semantics = semantics

    def observe(self, path: Path) -> ObservationSemantics:
        return self._semantics


def build_importer(container_db: Database, tmp_path: Path, semantics: ObservationSemantics) -> ImportAssets:
    """和 ``api/routes/media.py`` 的装配同构：观察器 + 真事实写入（含真门禁）。"""
    maintain = MaintainFacts(
        facts=SqliteFactRepository(container_db),
        # 生产装配里这一句就是 ``Observation.facts``（观察器写进 facts_json，读出来就在
        # 观察上）；这里显式写成字段，是为了让「候选来源」这条路在用例里也走一遍。
        candidates=lambda observation: observation.facts,
        confirmed_people=SqliteEntityRepository(container_db).confirmed_person_names,
    )
    return ImportAssets(
        assets=SqliteAssetRepository(container_db),
        observations=SqliteObservationRepository(container_db),
        storage=FakeStorage(tmp_path),
        metadata=FakeMetadata(),
        observer=ObserveAsset(
            observer=FakeVlmObserver(semantics),
            observations=SqliteObservationRepository(container_db),
        ),
        fact_maintainer=maintain.maintain,
    )


def test_an_observed_fact_for_a_confirmed_person_reaches_the_facts_table(
    tmp_path: Path, migrations_ready_db: Database, facts: SqliteFactRepository
):
    """**这一条就是裁决④的验收**：观察带 facts、门禁开着，事实表里就出现那个人的行。

    断言的是内容（subject / predicate / object / 证据 / 状态），不是行数：只数行数的话，
    把「邻居」那条也写进去一样是绿的——而门禁的全部意义就是「邻居没被确认，不写」。
    """
    add_person(migrations_ready_db, name="妈妈", status="confirmed")

    result = build_importer(migrations_ready_db, tmp_path, FACT_OBSERVATION).execute(
        ImportRequest(scope_id="album1", file_name="one.jpg", content_type="image/jpeg", content=b"abc")
    )

    assert result.observation_id is not None
    assert result.fact_ids != ()
    stored = facts.get(result.fact_ids[0])
    assert stored is not None
    assert (stored.subject, stored.predicate, stored.object) == ("妈妈", "去过", "赵县")
    assert stored.status is FactStatus.ACTIVE
    assert stored.evidence_ids == (result.observation_id,)
    assert stored.confidence == pytest.approx(0.9)
    assert [item.object for item in facts.list_facts("album1", limit=10)] == ["赵县"]
    assert len(result.fact_ids) == 1


def test_the_same_observation_without_a_confirmed_person_writes_nothing(
    tmp_path: Path, migrations_ready_db: Database, facts: SqliteFactRepository
):
    """**对照臂**：同一份观察、只把那个人改成未确认，事实表就一行都没有。

    两条用例共用 ``FACT_OBSERVATION``，差别只在 ``entities`` 那一行——这样「写出了东西」
    就不可能是「观察里本来就有」造成的。
    """
    add_person(migrations_ready_db, name="妈妈", status="pending")

    result = build_importer(migrations_ready_db, tmp_path, FACT_OBSERVATION).execute(
        ImportRequest(scope_id="album1", file_name="one.jpg", content_type="image/jpeg", content=b"abc")
    )

    assert result.fact_ids == ()
    assert facts.list_facts("album1", limit=10) == []


def test_the_facts_survive_a_round_trip_through_the_observation_row(
    tmp_path: Path, migrations_ready_db: Database
):
    """``facts`` 落在 ``observations.facts_json`` 上，不是只在内存里活一次。

    读回来比对的是**三个字段与置信度**：只断言 ``facts != ()`` 的话，把 predicate 与
    object 写成空串也是绿的。
    """
    add_person(migrations_ready_db, name="妈妈", status="confirmed")
    importer = build_importer(migrations_ready_db, tmp_path, FACT_OBSERVATION)

    result = importer.execute(
        ImportRequest(scope_id="album1", file_name="one.jpg", content_type="image/jpeg", content=b"abc")
    )

    assert result.observation_id is not None
    stored = SqliteObservationRepository(migrations_ready_db).get(result.observation_id)
    assert stored is not None
    assert [(item.subject, item.predicate, item.object) for item in stored.facts] == [
        ("妈妈", "去过", "赵县"),
        ("邻居", "去过", "赵县"),
    ]


def test_purge_unanchored_drops_the_facts_of_a_person_who_is_no_longer_confirmed(
    migrations_ready_db: Database, facts: SqliteFactRepository
):
    """门禁只保证**写入那一刻** subject 是已确认人物；人一旦不再是，那条事实就该走。

    1.0 ``db.py:3433-3447``。**这条能力在 S5 没有生产调用者**（1.0 的唯一调用点是
    ``scripts/maintenance/rebuild_memory.py``）——断言它的行为，同时如实登记它还没接上。
    """
    add_person(migrations_ready_db, name="妈妈", status="confirmed")
    maintainer(
        facts,
        candidates=(ObservationFact(subject="妈妈", predicate="去过", object="赵县"),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation())
    assert len(facts.list_facts("album1", limit=10)) == 1

    with migrations_ready_db.connect() as connection, connection:
        connection.execute("UPDATE entities SET status = 'rejected' WHERE canonical_name = '妈妈'")

    assert (
        MaintainFacts(
            facts=facts,
            candidates=lambda _observation: (),
            confirmed_people=SqliteEntityRepository(migrations_ready_db).confirmed_person_names,
        ).purge_unanchored("album1")
        == 1
    )
    assert facts.list_facts("album1", limit=10) == []


class NoFacesDetector:
    """一张脸都不给的人脸检测器。真件第一次 ``detect`` 会加载 RetinaFace 权重并跑
    GPU 推理，而这一条验的是事实那一路，不是人脸那一路。"""

    def detect(self, path: str) -> Sequence[FaceDetection]:
        return ()

    def detect_identity_photo(self, path: str) -> Sequence[FaceDetection]:
        return ()


def test_the_production_wiring_writes_the_observed_fact(client: TestClient):
    """**这一条走生产装配**：``api/routes/media.py`` 的 ``build_import_assets``。

    上面那条 ``build_importer`` 是**同构**装配：它证明得了链路，证明不了「生产那一处
    也是这么接的」。这不是假设——本用例的第一版就是拿 ``build_importer`` 写的，把生产
    里那句 ``candidates=lambda observation: observation.facts`` 换成 S4 的恒空元组，
    它照样是绿的：**用例验的是一个自己拼出来的装配**。
    """
    container = dataclasses.replace(
        client.app.state.container,
        vlm_observer=FakeVlmObserver(FACT_OBSERVATION),
        face_detector=NoFacesDetector(),
    )
    container.entity_repository.create_person("妈妈", status="confirmed", scope_id="album1")

    result = build_import_assets(container, observe=True).execute(
        ImportRequest(scope_id="album1", file_name="one.jpg", content_type="image/jpeg", content=b"abc")
    )

    assert result.fact_ids != ()
    stored = container.fact_repository.get(result.fact_ids[0])
    assert stored is not None
    assert (stored.subject, stored.predicate, stored.object) == ("妈妈", "去过", "赵县")
    assert stored.evidence_ids == (result.observation_id,)
    assert stored.confidence == pytest.approx(0.9)


def test_purge_unanchored_keeps_the_facts_of_a_still_confirmed_person(
    migrations_ready_db: Database, facts: SqliteFactRepository
):
    """**对照臂**：人还在，事实就得留下——判据写宽一点（比如按前缀匹配）这里会红。"""
    add_person(migrations_ready_db, name="妈妈", status="confirmed")
    maintainer(
        facts,
        candidates=(ObservationFact(subject="妈妈", predicate="去过", object="赵县"),),
        confirmed=frozenset({"妈妈"}),
    ).maintain(observation())

    assert (
        MaintainFacts(
            facts=facts,
            candidates=lambda _observation: (),
            confirmed_people=SqliteEntityRepository(migrations_ready_db).confirmed_person_names,
        ).purge_unanchored("album1")
        == 0
    )
    assert len(facts.list_facts("album1", limit=10)) == 1
