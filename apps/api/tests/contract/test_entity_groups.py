"""`GET /entity-groups` 与 `/entity-groups/{id}` 的契约测试（S6 · T129）。

1.0 ``app.py:1871-1882`` 加 ``db.py:4194-4266``。这里钉的是**分组的边界**：谁进组、
谁被跳过、组的 id 怎么拼、以及「单成员组也返回」。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

NOW = "2026-09-23T10:00:00+00:00"

SCOPE = "album1"


def _db(client: TestClient):
    return client.app.state.container.database


def _add_entity(
    client: TestClient,
    entity_id: str,
    *,
    name: str,
    entity_type: str = "place",
    status: str = "confirmed",
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '', 0.0, ?, ?)",
            (entity_id, SCOPE, entity_type, name, status, NOW, NOW),
        )


@pytest.fixture
def group_client(client: TestClient) -> TestClient:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, '主相册', 1, ?, ?)",
            (SCOPE, NOW, NOW),
        )
    return client


def _groups(client: TestClient) -> list[dict]:
    return client.get("/api/v1/entity-groups").json()["groups"]


def test_two_places_of_the_same_concept_land_in_one_group(group_client: TestClient):
    """「西湖」与「南湖」都命中概念表的「湖」→ 同一个「滨水空间」。"""
    _add_entity(group_client, "ent_1", name="西湖")
    _add_entity(group_client, "ent_2", name="南湖")

    groups = _groups(group_client)

    assert len(groups) == 1
    assert groups[0]["canonical_name"] == "滨水空间"
    assert groups[0]["is_semantic_cluster"] is True
    assert groups[0]["member_entity_ids"] == ["ent_1", "ent_2"]


def test_a_single_member_group_is_still_returned(group_client: TestClient):
    """**没有成员数阈值。** 1.0 那一条只有 ``derive_entity_merge_candidates`` 才有
    （``len(members) < 2: continue``），分组列表**不是**那条路由。

    定点变异：加一个「成员数不足就跳过」的判断，这条变红。
    """
    _add_entity(group_client, "ent_1", name="书房")

    groups = _groups(group_client)

    assert len(groups) == 1
    assert groups[0]["canonical_name"] == "书房"
    assert groups[0]["is_semantic_cluster"] is False


def test_people_never_enter_a_group(group_client: TestClient):
    """1.0 ``db.py:4204`` 循环里跳过 person——人物有自己的一页。"""
    _add_entity(group_client, "ent_person", name="妈妈", entity_type="person")
    _add_entity(group_client, "ent_place", name="西湖")

    groups = _groups(group_client)

    assert [group["canonical_name"] for group in groups] == ["滨水空间"]


def test_rejected_entities_are_skipped_twice(group_client: TestClient):
    """SQL 层排过一遍（``list_entities`` 的 status 条件），循环里是**第二道**。
    两条都在，所以这条用例钉的是「结果里没有它」，不是「哪一道起作用」。"""
    _add_entity(group_client, "ent_live", name="西湖")
    _add_entity(group_client, "ent_dead", name="南湖", status="rejected")

    groups = _groups(group_client)

    assert groups[0]["member_entity_ids"] == ["ent_live"]


def test_the_group_id_is_built_from_scope_type_and_label(group_client: TestClient):
    """``semantic_group:{scope}:{type}:{label}``——**拼出来的**，所以同一个概念在同一个
    scope 下永远是同一个 id（没有「创建组」这种动作）。

    定点变异：改分隔符或前缀，这条变红。
    """
    _add_entity(group_client, "ent_1", name="西湖")

    assert _groups(group_client)[0]["id"] == "semantic_group:album1:place:滨水空间"


def test_the_rationale_says_how_the_group_was_formed(group_client: TestClient):
    """``rationale`` 取**第一个**成员的那次判定（1.0 是 ``setdefault``），
    键名是 ``normalized_key`` 而不是判定函数返回的 ``matched_label``。"""
    _add_entity(group_client, "ent_1", name="西湖")

    rationale = _groups(group_client)[0]["rationale"]

    assert rationale == {"strategy": "semantic_concept", "normalized_key": "滨水空间"}


def test_source_labels_follow_the_sorted_members(group_client: TestClient):
    """``source_labels`` 由**排完序的**成员重算（1.0 ``:4234``），不是追加顺序。"""
    _add_entity(group_client, "ent_b", name="西湖")
    _add_entity(group_client, "ent_a", name="南湖")

    group = _groups(group_client)[0]

    assert group["source_labels"] == sorted(group["source_labels"])


def test_a_private_place_shows_its_alias_in_the_members(group_client: TestClient):
    """私密地点在**成员那一层**就被换成别名——1.0 ``db.py:4226`` 追加进 members 的是
    ``public_entity`` 投影过的 dict。而 ``source_labels`` 用的是投影后的名字。
    """
    _add_entity(group_client, "ent_1", name="某某小区")
    with _db(group_client).connect() as connection, connection:
        for key, value in (("private_flag", "true"), ("alias", '"家"')):
            connection.execute(
                "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
                " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
                " VALUES (?, 'ent_1', ?, ?, 'user', 1.0, 'active', '[]', 1, ?, ?)",
                (f"prop_{key}", key, value, NOW, NOW),
            )

    group = _groups(group_client)[0]

    assert group["members"][0]["canonical_name"] == "家"
    assert group["members"][0]["private"] is True
    assert group["source_labels"] == ["家"]


def test_the_group_detail_is_404_for_an_unknown_id(group_client: TestClient):
    response = group_client.get("/api/v1/entity-groups/semantic_group:nope:place:x")

    assert response.status_code == 404
    assert response.json()["message"] == "semantic entity group not found"


def test_the_group_detail_merges_its_members_observations(group_client: TestClient):
    """详情把各成员的观察按 id 去重后扁平合并；``relationships`` **不在**（S7）。

    定点变异：在 ``EntityGroupDetailResponse`` 上加一个 ``relationships``，这条变红。
    """
    _add_entity(group_client, "ent_1", name="西湖")
    _add_entity(group_client, "ent_2", name="南湖")
    with _db(group_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
            " updated_at) VALUES ('asset_1', ?, 'a.jpg', 'image', '/tmp/a.jpg', ?, ?)",
            (SCOPE, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
            " created_at, updated_at) VALUES ('obs_1', ?, 'asset_1', 'vlm', ?, ?, ?)",
            (SCOPE, NOW, NOW, NOW),
        )
        # 两个成员都指向同一条观察——合并后只该出现一次。
        for entity_id in ("ent_1", "ent_2"):
            connection.execute(
                "INSERT INTO entity_observations (entity_id, observation_id, confidence, source,"
                " created_at) VALUES (?, 'obs_1', 0.5, 'observation_extraction', ?)",
                (entity_id, NOW),
            )

    group_id = _groups(group_client)[0]["id"]
    body = group_client.get(f"/api/v1/entity-groups/{group_id}").json()

    assert [item["id"] for item in body["observations"]] == ["obs_1"]
    assert len(body["members"]) == 2
    assert "relationships" not in body
