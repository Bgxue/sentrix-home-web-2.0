"""画像修订仓储。

移植自 1.0 ``backend/db.py:6366-6440``。**最重要的一条是 ``user_locked`` 短路**：
用户锁住的版本不被顶掉、也不产生新版本。它是用户在画像上唯一的否决权，落在**写入口**
而不是界面上的禁用按钮——界面上禁用了，导入后的自动触发照样会写进去。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from sentrix.domain.identity.models import (
    PORTRAIT_STATUS_ACTIVE,
    PORTRAIT_STATUS_SUPERSEDED,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.portraits import SqlitePortraitRepository

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC).isoformat()

TEXT_V1 = "他经常在周末带着家人去附近的公园，喜欢拍孩子跑动的样子。" * 3
TEXT_V2 = "他最近开始在家做饭，照片里出现的厨房场景明显变多了。" * 3


def add_space(db: Database, scope_id: str) -> None:
    """``person_portrait_revisions.scope_id`` 有指向 ``memory_spaces`` 的外键，
    ``person_id`` 有指向 ``entities`` 的——两行都得先存在，否则插画像时 FK 才报错，
    而报错信息只说「外键失败」，不说是哪一个。"""
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES (?, ?, 'household', 'active', ?, ?) ON CONFLICT(id) DO NOTHING",
            (scope_id, scope_id, NOW, NOW),
        )


def add_person(db: Database, entity_id: str, scope_id: str = "album1") -> None:
    """插一行实体——``create`` 要从它读 scope。"""
    add_space(db, scope_id)
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES (?, ?, 'person', '妈妈', 'confirmed', '', 1.0, ?, ?)",
            (entity_id, scope_id, NOW, NOW),
        )


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    """夹具分两个而不是从仓储上摸私有属性——那条路走一次就会有人走第二次。"""
    return migrations_ready_db


@pytest.fixture
def repo(db: Database) -> SqlitePortraitRepository:
    return SqlitePortraitRepository(db)


def test_the_first_revision_is_number_one(db: Database, repo: SqlitePortraitRepository) -> None:
    add_person(db, "person_a")  # noqa: SLF001 - 夹具里没有别的入口
    created = repo.create("person_a", portrait_text=TEXT_V1, prompt_version="v1")

    assert created.revision == 1
    assert created.status == PORTRAIT_STATUS_ACTIVE
    assert created.portrait_text == TEXT_V1


def test_a_second_revision_supersedes_the_first(db: Database, repo: SqlitePortraitRepository) -> None:
    add_person(db, "person_a")
    first = repo.create("person_a", portrait_text=TEXT_V1, prompt_version="v1")
    second = repo.create("person_a", portrait_text=TEXT_V2, prompt_version="v2")

    assert second.revision == 2
    assert repo.get(first.id).status == PORTRAIT_STATUS_SUPERSEDED  # type: ignore[union-attr]
    assert repo.get_active("person_a").id == second.id  # type: ignore[union-attr]


def test_a_locked_revision_is_not_ovverridden(db: Database, repo: SqlitePortraitRepository) -> None:
    """**这条是写入口的否决权。** 锁住之后 ``create`` 原样返回当前版本，不写新行。

    钉住的是「不写新行」而不只是「返回的是旧的」——后者在一个仍然插入新行的实现里
    也成立（插完再把旧的返回）。所以这里连修订总数一起断言。
    """
    add_person(db, "person_a")
    locked = repo.create("person_a", portrait_text=TEXT_V1, prompt_version="v1")
    repo.set_lock(locked.id, True)

    returned = repo.create("person_a", portrait_text=TEXT_V2, prompt_version="v2")

    assert returned.id == locked.id
    assert returned.portrait_text == TEXT_V1
    assert len(repo.list_for_person("person_a")) == 1, "锁住时不许产生新行"


def test_unlocking_lets_writing_resume(db: Database, repo: SqlitePortraitRepository) -> None:
    add_person(db, "person_a")
    first = repo.create("person_a", portrait_text=TEXT_V1, prompt_version="v1")
    repo.set_lock(first.id, True)
    repo.set_lock(first.id, False)

    assert repo.get(first.id).status == PORTRAIT_STATUS_ACTIVE  # type: ignore[union-attr]
    second = repo.create("person_a", portrait_text=TEXT_V2, prompt_version="v2")
    assert second.revision == 2


def test_a_locked_revision_still_counts_as_active(db: Database, repo: SqlitePortraitRepository) -> None:
    """``get_active`` 收 ``active`` 与 ``user_locked`` 两种状态。

    锁住的是「这一版别被顶掉」，不是「这一版作废」——只查 ``active`` 会让一个锁住的
    画像在界面上凭空消失。
    """
    add_person(db, "person_a")
    only = repo.create("person_a", portrait_text=TEXT_V1, prompt_version="v1")
    repo.set_lock(only.id, True)

    assert repo.get_active("person_a").id == only.id  # type: ignore[union-attr]


def test_feedback_is_recorded_without_creating_a_revision(
    db: Database, repo: SqlitePortraitRepository
) -> None:
    add_person(db, "person_a")
    created = repo.create("person_a", portrait_text=TEXT_V1, prompt_version="v1")

    updated = repo.set_feedback(created.id, "厨房那句写得对")

    assert updated.user_feedback == "厨房那句写得对"  # type: ignore[union-attr]
    assert updated.revision == 1  # type: ignore[union-attr]
    assert len(repo.list_for_person("person_a")) == 1


def test_revisions_are_listed_newest_first(db: Database, repo: SqlitePortraitRepository) -> None:
    add_person(db, "person_a")
    for index in range(3):
        repo.create("person_a", portrait_text=TEXT_V1, prompt_version=f"v{index}")

    assert [item.revision for item in repo.list_for_person("person_a")] == [3, 2, 1]


def test_themes_and_evidence_refs_round_trip(db: Database, repo: SqlitePortraitRepository) -> None:
    add_person(db, "person_a")
    themes = [{"title": "周末的公园", "refs": ["a1"]}]
    refs = [{"kind": "event", "id": "event1"}]

    created = repo.create(
        "person_a", portrait_text=TEXT_V1, prompt_version="v1", themes=themes, evidence_refs=refs
    )

    assert created.themes == tuple(themes)
    assert created.evidence_refs == tuple(refs)


def test_scope_comes_from_the_entity(db: Database, repo: SqlitePortraitRepository) -> None:
    add_person(db, "person_a", scope_id="album2")
    assert repo.create("person_a", portrait_text=TEXT_V1, prompt_version="v1").scope_id == "album2"


def test_a_missing_entity_is_rejected_by_the_constraints(
    db: Database, repo: SqlitePortraitRepository
) -> None:
    """实体不存在时**插不进去**，而不是悄悄落到 ``home-default``。

    1.0 在这里有个 ``or "home-default"`` 的兜底（``db.py:6388``），2.0 没搬——
    ``person_portrait_revisions.person_id`` 有指向 ``entities`` 的外键，
    ``entities.scope_id`` 又是 ``NOT NULL DEFAULT 'home-default'``（``0006:25``），
    所以那条兜底覆盖的「实体查不到」在 2.0 走不到：INSERT 自己就会被约束拒绝。
    默认值改由**表定义**承担，位置比仓储更早，也正是它该在的地方。

    **scope 与 person 在同一条 INSERT 里解析**（子查询），所以没有「先查 scope 再插」
    那个窗口——那个窗口里缺失实体报的是查找代码的错，而不是数据库的约束错。
    """
    add_space(db, "home-default")

    with pytest.raises(sqlite3.IntegrityError):
        repo.create("查无此人", portrait_text=TEXT_V1, prompt_version="v1")


def test_reading_an_unknown_revision_is_none_not_an_error(
    db: Database, repo: SqlitePortraitRepository
) -> None:
    assert repo.get("portrait_不存在") is None
    assert repo.get_active("查无此人") is None
    assert repo.list_for_person("查无此人") == []
