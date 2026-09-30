"""``GET /knowledge`` 的契约测试（S6 · T130）。

1.0 ``app.py:1921-1932``。这里钉三件事：三块都在、``scope_id`` 的过滤**发生在取数之后**、
以及 ``profiles`` 那一支的两条路径（给 ``person_id`` / 不给）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

NOW = "2026-09-23T10:00:00+00:00"
LATER = "2026-09-24T10:00:00+00:00"

SCOPE = "album1"
OTHER = "album2"


def _db(client: TestClient):
    return client.app.state.container.database


@pytest.fixture
def knowledge_client(client: TestClient) -> TestClient:
    """两个空间、两个人物实体、一条画像、三条声明（两条在 SCOPE、一条在 OTHER）。"""
    database = _db(client)
    with database.connect() as connection, connection:
        for scope_id in (SCOPE, OTHER):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                (scope_id, scope_id, NOW, NOW),
            )
        for entity_id, scope_id in (("ent_1", SCOPE), ("ent_2", OTHER)):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, 'person', ?, 'confirmed', '', 0.0, ?, ?)",
                (entity_id, scope_id, entity_id, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO semantic_profiles (id, scope_id, person_id, summary_zh, revision,"
            " created_at, updated_at) VALUES ('profile_1', ?, 'ent_1', '喜欢爬山', 1, ?, ?)",
            (SCOPE, NOW, NOW),
        )
        # 两条在 SCOPE、一条在 OTHER；``claim_old`` 是改口留下的旧值。
        for claim_id, scope_id, status, updated in (
            ("claim_a", SCOPE, "active", LATER),
            ("claim_old", SCOPE, "superseded", LATER),
            ("claim_b", OTHER, "active", NOW),
        ):
            connection.execute(
                "INSERT INTO semantic_claims (id, scope_id, person_id, dimension, predicate,"
                " value_text, status, confidence, created_at, updated_at)"
                " VALUES (?, ?, 'ent_1', 'preference', '喜欢', ?, ?, 0.5, ?, ?)",
                (claim_id, scope_id, claim_id, status, NOW, updated),
            )
    return client


def test_knowledge_returns_the_three_blocks(knowledge_client: TestClient):
    body = knowledge_client.get("/api/v1/knowledge").json()

    assert set(body) == {"profiles", "claims", "spaces"}


def test_claims_are_scoped_in_python_after_the_scan(knowledge_client: TestClient):
    """``scope_id`` 的过滤**发生在取数之后**——1.0 是先 ``LIMIT 1000`` 再 Python 过滤。

    反过来的话（先在 SQL 里按 scope 过滤再取 1000）会拿到比 1.0 多的行。这条用例用
    「另一个空间的那条不出现」把行为钉住；两步顺序本身由仓储层的 limit 用例钉。

    ``claim_old`` 是 superseded，连它也不该出现。
    """
    body = knowledge_client.get("/api/v1/knowledge", params={"scope_id": SCOPE}).json()

    assert [item["id"] for item in body["claims"]] == ["claim_a"]


def test_claims_carry_their_scope(knowledge_client: TestClient):
    """不给 scope 时两个空间的都回来——过滤是**参数**驱动的，不是恒有的。"""
    body = knowledge_client.get("/api/v1/knowledge").json()

    assert {item["id"] for item in body["claims"]} == {"claim_a", "claim_b"}


def test_profiles_come_from_every_entity_in_scope(knowledge_client: TestClient):
    """不给 ``person_id`` 时对 scope 内每个实体轮流查一次（1.0 的 N+1）。"""
    body = knowledge_client.get("/api/v1/knowledge", params={"scope_id": SCOPE}).json()

    assert [item["id"] for item in body["profiles"]] == ["profile_1"]


def test_a_person_id_narrows_profiles_to_that_one(knowledge_client: TestClient):
    body = knowledge_client.get("/api/v1/knowledge", params={"person_id": "ent_1"}).json()

    assert [item["id"] for item in body["profiles"]] == ["profile_1"]


def test_a_person_id_without_a_profile_returns_an_empty_list(knowledge_client: TestClient):
    """1.0 是 ``if profile:`` 才 append——查不到就没有，不是给一个空壳。"""
    body = knowledge_client.get("/api/v1/knowledge", params={"person_id": "ent_2"}).json()

    assert body["profiles"] == []


def test_the_profile_json_columns_are_sequences(knowledge_client: TestClient):
    """三个 ``*_json`` 列在响应里是数组，键名**去掉了 ``_json`` 后缀**。

    这是一处**偏离**：1.0 的 ``_decode`` 是就地换值、保留键名的。2.0 的模型层把
    「解过了」表达在类型与名字上——留着后缀会让人以为拿到的是串。
    """
    profile = knowledge_client.get("/api/v1/knowledge").json()["profiles"][0]

    assert profile["event_memory"] == []
    assert profile["patterns"] == []
    assert profile["evidence_ids"] == []
    assert "event_memory_json" not in profile


def test_spaces_come_from_the_space_repository(knowledge_client: TestClient):
    body = knowledge_client.get("/api/v1/knowledge").json()

    assert {item["id"] for item in body["spaces"]} == {SCOPE, OTHER}
    assert set(body["spaces"][0]) == {"id", "name", "kind", "status"}
