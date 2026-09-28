"""人物瞬间仓储。

移植自 1.0 ``backend/db.py:6079-6109`` / ``:6158`` / ``:6218``。**核心是跨空间守卫**：
一条瞬间引用的六个对象必须同属一个记忆空间。这种错不会自己暴露——它只会让画像把两个
空间的人混成一个，而那时候已经看不出是哪一步引入的。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.moments import SqliteMomentRepository

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC).isoformat()


def seed(db: Database, scope_id: str, suffix: str, *, person: bool = True) -> dict[str, str]:
    """在一个空间里造齐六个引用对象，返回它们的 id。"""
    ids = {
        "person": f"person_{suffix}",
        "cluster": f"cluster_{suffix}",
        "event": f"event_{suffix}",
        "observation": f"obs_{suffix}",
        "asset": f"asset_{suffix}",
        "face": f"face_{suffix}",
    }
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES (?, ?, 'household', 'active', ?, ?) ON CONFLICT(id) DO NOTHING",
            (scope_id, scope_id, NOW, NOW),
        )
        if person:
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, 'person', '妈妈', 'confirmed', '', 1.0, ?, ?)",
                (ids["person"], scope_id, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, member_count, created_at, updated_at)"
            " VALUES (?, ?, 'confirmed', 1, ?, ?)",
            (ids["cluster"], scope_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO events (id, scope_id, title, created_at, updated_at) VALUES (?, ?, '事件', ?, ?)",
            (ids["event"], scope_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES (?, ?, 'a.jpg', 'image', '/tmp/a.jpg', ?, ?)",
            (ids["asset"], scope_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
            " VALUES (?, ?, ?, 'vlm', ?, ?)",
            (ids["observation"], scope_id, ids["asset"], NOW, NOW),
        )
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
            " embedding_model, embedding_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 0.9, 0.8, ?, 'front', 'buffalo_l', 'w600k_r50', ?)",
            (
                ids["face"],
                ids["asset"],
                ids["observation"],
                ids["cluster"],
                json.dumps([0, 0, 10, 10]),
                json.dumps([1.0, 0.0]),
                json.dumps({}),
                NOW,
            ),
        )
    return ids


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    return migrations_ready_db


@pytest.fixture
def repo(db: Database) -> SqliteMomentRepository:
    return SqliteMomentRepository(db)


def draft(ids: dict[str, str], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "person_id": ids["person"],
        "observation_id": ids["observation"],
        "prompt_version": "moments-v1",
        "cluster_id": ids["cluster"],
        "event_id": ids["event"],
        "asset_id": ids["asset"],
        "face_instance_id": ids["face"],
        "action_text": "在厨房做饭",
    }
    payload.update(overrides)
    return payload


def test_a_moment_takes_the_scope_of_the_person(repo: SqliteMomentRepository, db: Database) -> None:
    ids = seed(db, "album1", "a")
    record = repo.upsert(**draft(ids))  # type: ignore[arg-type]

    assert record.scope_id == "album1"
    assert record.status == "active"
    assert record.action_text == "在厨房做饭"


def test_the_face_instance_scope_comes_through_its_asset(repo: SqliteMomentRepository, db: Database) -> None:
    """``face_instances`` 没有 ``scope_id`` 列，要经它的 ``asset_id`` 找
    （1.0 ``_face_instance_scope``，``db.py:6097``）。其它五个引用都能直接给出空间，
    只有这一个绕了一层——绕错会让守卫拿不到它的空间，于是跨空间的瞬间不被拦下。
    """
    ids = seed(db, "album1", "a")
    assert repo.upsert(**draft(ids)).scope_id == "album1"  # type: ignore[arg-type]


def test_writing_the_same_triple_again_updates_instead_of_duplicating(
    repo: SqliteMomentRepository, db: Database
) -> None:
    ids = seed(db, "album1", "a")
    first = repo.upsert(**draft(ids))  # type: ignore[arg-type]
    second = repo.upsert(**draft(ids, action_text="在阳台浇花"))  # type: ignore[arg-type]

    assert second.id == first.id, "upsert 键是 (person, observation, prompt_version)"
    assert second.action_text == "在阳台浇花"
    assert len(repo.list_for(person_id=ids["person"])) == 1


def test_a_different_prompt_version_is_a_different_moment(repo: SqliteMomentRepository, db: Database) -> None:
    """换提示词版本是一条**新的**瞬间，不是覆盖旧的——两版要能同时存在，
    否则改提示词就等于把旧版的结果抹掉，没有对照。"""
    ids = seed(db, "album1", "a")
    repo.upsert(**draft(ids))  # type: ignore[arg-type]
    repo.upsert(**draft(ids, prompt_version="moments-v2"))  # type: ignore[arg-type]

    assert len(repo.list_for(person_id=ids["person"])) == 2


def test_crossing_memory_spaces_is_rejected(repo: SqliteMomentRepository, db: Database) -> None:
    """**这是这个仓储最重要的一条。** 事件在另一个空间时必须抛，而不是「取第一个」。

    取第一个会把上游的归属错误吞掉，而它之后只会以「画像把两个空间的人混成一个」
    的形式浮出来——那时已经看不出是哪一步引入的。
    """
    home = seed(db, "album1", "a")
    other = seed(db, "album2", "b", person=False)

    with pytest.raises(ValueError, match="same memory space"):
        repo.upsert(**draft(home, event_id=other["event"]))  # type: ignore[arg-type]


def test_the_update_does_not_move_the_upsert_key_columns(repo: SqliteMomentRepository, db: Database) -> None:
    """冲突时只更新内容列，不动 ``scope_id`` / ``person_id`` / ``observation_id`` /
    ``prompt_version``——那四个是键，改了就不是同一条了。"""
    ids = seed(db, "album1", "a")
    repo.upsert(**draft(ids))  # type: ignore[arg-type]
    updated = repo.upsert(**draft(ids, action_text="改了"))  # type: ignore[arg-type]

    assert (updated.person_id, updated.observation_id, updated.prompt_version) == (
        ids["person"],
        ids["observation"],
        "moments-v1",
    )
    assert updated.scope_id == "album1"


def test_lists_are_filtered_by_person_and_scope(repo: SqliteMomentRepository, db: Database) -> None:
    first = seed(db, "album1", "a")
    second = seed(db, "album2", "b")
    repo.upsert(**draft(first))  # type: ignore[arg-type]
    repo.upsert(**draft(second))  # type: ignore[arg-type]

    assert len(repo.list_for()) == 2
    assert len(repo.list_for(scope_id="album1")) == 1
    assert len(repo.list_for(person_id=first["person"])) == 1


def test_the_default_filter_is_active_only(repo: SqliteMomentRepository, db: Database) -> None:
    """默认按 ``active`` 筛，``status=None`` 才是不筛。**这两个必须分得开**：
    把「不筛」写成默认值，已作废的瞬间会重新进入画像的证据包。"""
    ids = seed(db, "album1", "a")
    repo.upsert(**draft(ids))  # type: ignore[arg-type]
    with db.connect() as connection, connection:
        connection.execute("UPDATE person_moments SET status = 'superseded'")

    assert repo.list_for() == []
    assert repo.list_for(status=None) != []


def test_json_columns_round_trip(repo: SqliteMomentRepository, db: Database) -> None:
    ids = seed(db, "album1", "a")
    record = repo.upsert(  # type: ignore[arg-type]
        **draft(ids, interaction_target_ids=["person_x"], social_role_cues=["长辈"])
    )

    assert record.interaction_target_ids == ("person_x",)
    assert record.social_role_cues == ("长辈",)
