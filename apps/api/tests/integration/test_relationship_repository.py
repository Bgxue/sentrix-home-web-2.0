"""关系仓储（家庭图谱的边）。

移植自 1.0 ``backend/db.py:5590`` / ``:5601`` / ``:5970`` / ``:6014`` / ``:5609``。

**这一层只写关系行**（用户 2026-09-28 裁决 A）：1.0 的 ``create_relationship`` 还会在
``status == 'active'`` 时调 ``maintain_relationship_claim`` 往事实层写，2.0 把那一半
留给 application——``infrastructure-purity`` 契约不允许基础设施层直接调用例。
**且不回滚**：claim 维护失败时这一行留着。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.relationships import (
    SqliteRelationshipRepository,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC).isoformat()


def add_space(db: Database, scope_id: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES (?, ?, 'household', 'active', ?, ?) ON CONFLICT(id) DO NOTHING",
            (scope_id, scope_id, NOW, NOW),
        )


def add_entity(
    db: Database, entity_id: str, scope_id: str, *, kind: str = "person", name: str = "妈妈"
) -> None:
    add_space(db, scope_id)
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
            " summary, confidence, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'confirmed', '', 1.0, ?, ?)",
            (entity_id, scope_id, kind, name, NOW, NOW),
        )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    return migrations_ready_db


@pytest.fixture
def repo(db: Database) -> SqliteRelationshipRepository:
    return SqliteRelationshipRepository(db)


@pytest.fixture
def family(db: Database) -> dict[str, str]:
    add_entity(db, "mom", "album1", name="妈妈")
    add_entity(db, "dad", "album1", name="爸爸")
    add_entity(db, "dog", "album1", kind="object", name="狗")
    return {"mom": "mom", "dad": "dad", "dog": "dog"}


def test_a_new_relationship_starts_pending(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    record = repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad")

    assert record.status == "pending"
    assert record.scope_id == "album1"
    assert record.revision == 1


def test_creating_with_active_status_is_active(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    assert (
        repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="active").status
        == "active"
    )


def test_cross_space_entities_are_rejected(
    repo: SqliteRelationshipRepository, db: Database, family: dict[str, str]
) -> None:
    """**两端必须同属一个记忆空间。** 跨空间的关系会让家庭图谱把两个相册的人接起来，
    而那种错在图上看起来只是「多了一条边」。"""
    add_entity(db, "other", "album2", name="另一个妈妈")

    with pytest.raises(ValueError, match="same memory space"):
        repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="other")


def test_an_unknown_entity_is_rejected_too(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    """1.0 把「查不到」与「不同空间」并成同一个 ``ValueError``，照搬：
    两端都有外键，查不到就是调用方拿错了 id，与跨空间同属编程错误。"""
    with pytest.raises(ValueError, match="same memory space"):
        repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="查无此人")


def test_repeating_the_same_claim_merges_instead_of_duplicating(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    """同一 ``(scope, subject, predicate, object)`` 不新建：**证据并集、置信度取 MAX、
    修订 +1**。这三件事分开看都像实现细节，合起来才是「同一件事被说第二遍时怎么记」。"""
    first = repo.create(
        subject_entity_id="mom",
        predicate="配偶",
        object_entity_id="dad",
        evidence_ids=["obs_1"],
        confidence=0.4,
    )
    second = repo.create(
        subject_entity_id="mom",
        predicate="配偶",
        object_entity_id="dad",
        evidence_ids=["obs_2"],
        confidence=0.9,
    )

    assert second.id == first.id
    assert second.evidence_ids == ("obs_1", "obs_2"), "证据要并集，不能只剩新的"
    assert second.confidence == 0.9, "MAX 而不是覆盖"
    assert second.revision == 2


def test_evidence_order_is_preserved(repo: SqliteRelationshipRepository, family: dict[str, str]) -> None:
    """1.0 用 ``dict.fromkeys`` 而不是 ``set``：去重且**保序**，因为证据顺序会在
    证据包里显示出来。"""
    record = repo.create(
        subject_entity_id="mom",
        predicate="配偶",
        object_entity_id="dad",
        evidence_ids=["obs_b", "obs_a", "obs_b"],
    )
    assert record.evidence_ids == ("obs_b", "obs_a")


def test_the_same_triple_never_produces_two_rows(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    """同一三元组经 ``create`` 只会有一行——**这条是 ``confirm`` 里那段 supersede
    够不够得着的前提**，所以单独钉住。

    实测（2026-09-28）：先 active 再 pending，两次拿到的是**同一个 id**，表里一行。
    于是 ``confirm`` 里「把其它 active 的顶掉」那条 UPDATE 在本仓储的 API 下匹配不到行。

    **但它不删**，与 portraits 里去掉的那条兜底不同：那条是被**表约束**（外键 +
    ``NOT NULL``）挡死的，是 schema 保证；这条只被 ``create`` 的**去重策略**挡住，
    而 schema 明确支持同一三元组多行（``supersedes_relationship_id`` 就是那条链）。
    策略会变、schema 不会——所以留着它，但**不假装它被测到了**。
    """
    first = repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="active")
    second = repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="pending")

    assert second.id == first.id
    assert [item.id for item in repo.list_for_entity("mom")] == [first.id]


def test_confirm_makes_a_pending_edge_active(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    """``confirm`` 至少要做到这一件：把 pending 转 active，并把状态写回去。"""
    record = repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad")
    assert record.status == "pending"

    confirmed = repo.confirm(record.id)

    assert confirmed.status == "active"  # type: ignore[union-attr]
    assert repo.get(record.id).status == "active"  # type: ignore[union-attr]


def test_retract_keeps_the_row(repo: SqliteRelationshipRepository, family: dict[str, str]) -> None:
    """撤回是**转状态 + 修订 +1**，不是删行——撤回也要留痕。"""
    record = repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="active")

    retracted = repo.retract(record.id)

    assert retracted.status == "retracted"  # type: ignore[union-attr]
    assert retracted.revision == record.revision + 1  # type: ignore[union-attr]
    assert repo.get(record.id) is not None, "行还在"
    assert record.id not in {item.id for item in repo.list_person_relationships()}


def test_person_edges_exclude_non_person_entities(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    """**两端都是 person 这条过滤在 SQL 里**（1.0 ``db.py:5603``）：实体表里还有物品、
    氛围等类型，只按 id 取会把「妈妈 — 养 — 狗」也当成家庭图谱的边。"""
    repo.create(subject_entity_id="mom", predicate="养", object_entity_id="dog", status="active")
    repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="active")

    edges = repo.list_person_relationships()

    assert [item.predicate for item in edges] == ["配偶"]


def test_edges_carry_both_names(repo: SqliteRelationshipRepository, family: dict[str, str]) -> None:
    """证据包要的是**人话的关系**（「妈妈 — 配偶 — 爸爸」）；只给 id 的包对模型没有
    信息量。两个名字都来自 ``JOIN entities``。"""
    repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="active")

    edge = repo.list_person_relationships()[0]

    assert (edge.subject_name, edge.object_name) == ("妈妈", "爸爸")


def test_listing_for_an_entity_covers_both_directions(
    repo: SqliteRelationshipRepository, family: dict[str, str]
) -> None:
    """它可能是 subject 也可能是 object——只查一个方向会让「谁跟这个人有关系」
    在他是宾语时漏掉一半。"""
    repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="active")
    repo.create(subject_entity_id="dad", predicate="父亲", object_entity_id="dog", status="active")

    assert len(repo.list_for_entity("dad")) == 2


def test_scope_filter_narrows_the_edges(
    repo: SqliteRelationshipRepository, db: Database, family: dict[str, str]
) -> None:
    add_entity(db, "aunt", "album2", name="姑姑")
    add_entity(db, "uncle", "album2", name="姑父")
    repo.create(subject_entity_id="mom", predicate="配偶", object_entity_id="dad", status="active")
    repo.create(subject_entity_id="aunt", predicate="配偶", object_entity_id="uncle", status="active")

    assert len(repo.list_person_relationships()) == 2
    assert len(repo.list_person_relationships(scope_id="album2")) == 1
