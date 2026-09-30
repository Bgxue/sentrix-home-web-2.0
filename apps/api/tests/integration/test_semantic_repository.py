"""``semantic_profiles`` / ``semantic_claims`` 只读仓储的真 SQLite 用例（S6 · T130）。

两张表从 T127 建起来之后一直没有读者，所以这里钉的是**第一次读它们时最容易读错的
那几件事**：

- ``list_claims`` **恒带状态白名单**（``active`` / ``pending``）——``superseded`` 的
  那些是改口留下的旧值，混进「当前声明」会让同一个人同时有两个互相矛盾的说法；
- ``person_id`` 列里存的是**实体 id 而不是「人」**——名字骗人，但它是 1.0 的列名；
- 三个 ``*_json`` 列要解成序列，且**解不开时回落空表**（1.0 的 ``_decode`` 就是这样）。
"""

from __future__ import annotations

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.semantic import SqliteSemanticRepository

NOW = "2026-09-23T10:00:00+00:00"
LATER = "2026-09-24T10:00:00+00:00"

SCOPE = "album1"


@pytest.fixture
def semantic(migrations_ready_db: Database) -> dict[str, object]:
    db = migrations_ready_db
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, '主相册', 1, ?, ?)",
            (SCOPE, NOW, NOW),
        )
        for entity_id in ("ent_1", "ent_2"):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, 'person', ?, 'confirmed', '', 0.0, ?, ?)",
                (entity_id, SCOPE, entity_id, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO semantic_profiles (id, scope_id, person_id, summary_zh,"
            " event_memory_json, patterns_json, evidence_ids_json, revision, created_at,"
            " updated_at) VALUES ('profile_1', ?, 'ent_1', '喜欢爬山',"
            " '[{\"event_id\": \"ev_1\"}]', '[]', '[\"obs_1\"]', 1, ?, ?)",
            (SCOPE, NOW, NOW),
        )
        # 三条声明：两条 active/pending、一条 superseded（改口留下的旧值）。
        for claim_id, status, updated in (
            ("claim_active", "active", LATER),
            ("claim_pending", "pending", NOW),
            ("claim_old", "superseded", LATER),
        ):
            connection.execute(
                "INSERT INTO semantic_claims (id, scope_id, person_id, dimension, predicate,"
                " value_text, supporting_event_ids_json, evidence_ids_json, status, confidence,"
                " created_at, updated_at)"
                " VALUES (?, ?, 'ent_1', 'preference', '喜欢', ?, '[\"ev_1\"]', '[\"obs_1\"]',"
                " ?, 0.5, ?, ?)",
                (claim_id, SCOPE, claim_id, status, NOW, updated),
            )
        connection.execute(
            "INSERT INTO semantic_claims (id, scope_id, person_id, dimension, predicate,"
            " value_text, supporting_event_ids_json, evidence_ids_json, status, confidence,"
            " created_at, updated_at)"
            " VALUES ('claim_other_person', ?, 'ent_2', 'preference', '喜欢', '游泳', '[]', '[]',"
            " 'active', 0.5, ?, ?)",
            (SCOPE, NOW, NOW),
        )
    return {"repo": SqliteSemanticRepository(db), "db": db}


def test_a_profile_round_trips_its_json_columns(semantic):
    """``event_memory`` / ``patterns`` / ``evidence_ids`` 解成序列，不是原始串。"""
    profile = semantic["repo"].get_profile("ent_1")

    assert profile is not None
    assert profile.summary_zh == "喜欢爬山"
    assert profile.event_memory == ({"event_id": "ev_1"},)
    assert profile.evidence_ids == ("obs_1",)


def test_an_unknown_person_has_no_profile(semantic):
    assert semantic["repo"].get_profile("ent_nope") is None


def test_a_broken_json_column_falls_back_to_an_empty_list(semantic):
    """1.0 的 ``_decode`` 解不开时回落空表——**不是抛**。库里真有坏行时，
    一个坏列不该让整张表读不出来。

    定点变异：把 ``_json_list`` 的 try/except 去掉，这条变成 ``JSONDecodeError``。
    """
    with semantic["db"].connect() as connection, connection:
        connection.execute("UPDATE semantic_profiles SET patterns_json = '不是 JSON' WHERE id = 'profile_1'")

    profile = semantic["repo"].get_profile("ent_1")

    assert profile is not None
    assert profile.patterns == ()


def test_claims_exclude_superseded_by_default(semantic):
    """**恒带状态白名单。** ``superseded`` 是改口留下的旧值，混进「当前声明」会让同一个
    人同时有两个互相矛盾的说法。

    定点变异：把 ``status IN (...)`` 去掉，这条变红。
    """
    claims = semantic["repo"].list_claims("ent_1")

    assert {item.id for item in claims} == {"claim_active", "claim_pending"}


def test_claims_are_newest_first(semantic):
    """``ORDER BY updated_at DESC``——同一个人两条，先出 ``LATER`` 那条。"""
    claims = semantic["repo"].list_claims("ent_1")

    assert [item.id for item in claims] == ["claim_active", "claim_pending"]


def test_claims_can_be_listed_across_people(semantic):
    """``person_id`` 为空 = 全表（仍受 ``limit`` 约束），T130 的 ``scope_id`` 过滤
    就是在这一层结果上做的。"""
    claims = semantic["repo"].list_claims()

    assert {item.id for item in claims} == {
        "claim_active",
        "claim_pending",
        "claim_other_person",
    }


def test_the_claim_limit_is_applied(semantic):
    """``limit`` 落在 SQL 上，不是取全表再切片。

    定点变异：把 ``LIMIT ?`` 去掉，这条变红（会拿到 3 条）。
    """
    claims = semantic["repo"].list_claims(None, limit=2)

    assert len(claims) == 2


def test_the_person_column_holds_an_entity_id_not_a_person(semantic):
    """``person_id`` 存的是**实体 id**。名字骗人，但换掉它会让新老库的行对不上。

    这条钉的是「别顺手给它加一个 `entity_type == 'person'` 的门」——1.0 的
    ``get_entity_detail`` 就是拿非人物实体的 id 查这张表的。
    """
    profile = semantic["repo"].get_profile("ent_1")

    assert profile is not None
    assert profile.person_id == "ent_1"
