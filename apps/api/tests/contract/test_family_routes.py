"""家庭图谱端点的契约测试（S7b1 · T305）。

对应 1.0 ``app.py:1648-1656`` 的 ``GET /api/family-graph``。

**这里钉的核心是 ``family_graph_people`` 的三个派生字段与 ``EntityRepository.decorate_entities``
不一样**。它们长得像（都是「头像 / 照片数 / 代表照」），但三处的连接路径或排序都不同，
「看起来能复用」正是最危险的地方：

- ``representative_media`` 走**人脸**（``face_instances⋈face_clusters⋈assets``），
  ``preview_*`` 走**观察**（``entity_observations⋈observations⋈assets``）；
- ``appearance_count`` 数**人脸实例行数**，``photo_count`` 数 ``COUNT(DISTINCT a.id)``；
- 头像的排序键多一位 ``fi.quality DESC``。

所以每条都造一个「两种口径给出不同答案」的夹具。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

NOW = "2026-10-02T10:00:00+00:00"

HOME = "album1"
OTHER = "album2"


def _db(client: TestClient):
    return client.app.state.container.database


@pytest.fixture
def family_client(client: TestClient) -> TestClient:
    """两个空间 · 两个人物（一个 pending）· 一个地点 · 两个资产 · 两条观察。

    外键是开的，父数据按顺序落。
    """
    with _db(client).connect() as connection, connection:
        for scope_id in (HOME, OTHER):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                (scope_id, scope_id, NOW, NOW),
            )
        for asset_id, scope_id in (("asset_1", HOME), ("asset_2", HOME), ("asset_3", OTHER)):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, scope_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
        for observation_id, scope_id, asset_id in (
            ("obs_1", HOME, "asset_1"),
            ("obs_2", HOME, "asset_2"),
            ("obs_3", OTHER, "asset_3"),
        ):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " created_at, updated_at) VALUES (?, ?, ?, 'vlm', ?, ?, ?)",
                (observation_id, scope_id, asset_id, NOW, NOW, NOW),
            )
        for entity_id, scope_id, kind, name, status in (
            ("mom", HOME, "person", "妈妈", "confirmed"),
            ("kid", HOME, "person", "待确认人物簇 · cluster_1", "pending"),
            ("west", HOME, "place", "西湖", "confirmed"),
            ("neighbour", OTHER, "person", "邻居", "confirmed"),
        ):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, '', 0.0, ?, ?)",
                (entity_id, scope_id, kind, name, status, NOW, NOW),
            )
    return client


def _add_cluster(client: TestClient, cluster_id: str, entity_id: str, *, member_count: int = 1) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id,"
            " representative_embedding_json, member_count, revision, created_at, updated_at)"
            " VALUES (?, ?, 'pending', ?, '[]', ?, 1, ?, ?)",
            (cluster_id, HOME, entity_id, member_count, NOW, NOW),
        )


def _add_face(
    client: TestClient,
    face_id: str,
    cluster_id: str,
    *,
    asset_id: str,
    observation_id: str,
    quality: float,
    detection_confidence: float,
    created_at: str = NOW,
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
            " embedding_model, embedding_version, embedding_quality_signal, validity,"
            " area_ratio, sharpness, created_at)"
            " VALUES (?, ?, ?, ?, '[0,0,1,1]', '[]', ?, ?, '[]', 'frontal', 'buffalo_l',"
            " 'w600k_r50', 0.5, 'verified', 0.1, 0.0, ?)",
            (face_id, asset_id, observation_id, cluster_id, detection_confidence, quality, created_at),
        )


def _people(client: TestClient, scope_id: str = HOME) -> list[dict]:
    return client.get("/api/v1/family-graph", params={"scope_id": scope_id}).json()["people"]


def test_an_unknown_scope_is_its_own_graph_with_no_people(family_client: TestClient) -> None:
    """没并过图的 scope 返回**它自己一个**，而且此时图上什么都没有。"""
    body = family_client.get("/api/v1/family-graph", params={"scope_id": "album_nope"}).json()

    assert body["scope_id"] == "album_nope"
    assert body["scope_ids"] == ["album_nope"]
    assert body["people"] == []
    assert body["relationships"] == []


def test_only_people_appear_and_the_pending_one_gets_the_family_graph_placeholder(
    family_client: TestClient,
) -> None:
    """只出 person；pending 的用 **「待命名人物」**。

    定点变异：把占位换成 ``PENDING_CLUSTER_NAME``（「待命名成员」），这条变红——
    **1.0 两个端点用两个词**，家庭图谱这个用的是「人物」。
    """
    people = _people(family_client)

    assert {item["id"] for item in people} == {"mom", "kid"}
    by_id = {item["id"]: item for item in people}
    assert by_id["mom"]["display_name"] == "妈妈"
    assert by_id["kid"]["display_name"] == "待命名人物"
    assert by_id["mom"]["membership"] is None
    assert by_id["mom"]["portrait"] is None


def test_representative_media_comes_from_faces_not_from_observations(family_client: TestClient) -> None:
    """**这张表走人脸，不走观察。**

    夹具故意让两条口径给出**不同**的答案：``mom`` 有两条观察（``obs_1``/``obs_2``），
    但只在 ``asset_1`` 上有一张脸。走观察的那条会给出 ``preview_*`` 的资产；
    走人脸的这条只给 ``asset_1``。

    定点变异：把 ``_MEDIA_SQL`` 换成 ``entity_observations`` 那条，这条变红。
    """
    _add_cluster(family_client, "cluster_mom", "mom")
    _add_face(
        family_client,
        "face_1",
        "cluster_mom",
        asset_id="asset_1",
        observation_id="obs_1",
        quality=0.5,
        detection_confidence=0.5,
    )
    with _db(family_client).connect() as connection, connection:
        for observation_id in ("obs_1", "obs_2"):
            connection.execute(
                "INSERT INTO entity_observations (entity_id, observation_id, confidence,"
                " source, created_at) VALUES ('mom', ?, 0.5, 'observation_extraction', ?)",
                (observation_id, NOW),
            )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert [item["id"] for item in person["representative_media"]] == ["asset_1"]
    assert person["representative_media"][0]["file_name"] == "asset_1.jpg"


def test_representative_media_is_capped_at_three_and_ordered_by_asset_id_desc(
    family_client: TestClient,
) -> None:
    """``ORDER BY a.id DESC LIMIT 3``——**倒序**且**最多三张**。

    定点变异：把 ``a.id DESC`` 写成 ``ASC``，这条变红（顺序反了）；去掉 LIMIT，
    条数变四。两者都不会报错，只会让「代表照」换一批。
    """
    with _db(family_client).connect() as connection, connection:
        for index in range(4):
            asset_id = f"asset_x{index}"
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, HOME, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " created_at, updated_at) VALUES (?, ?, ?, 'vlm', ?, ?, ?)",
                (f"obs_x{index}", HOME, asset_id, NOW, NOW, NOW),
            )
    _add_cluster(family_client, "cluster_mom", "mom")
    for index in range(4):
        _add_face(
            family_client,
            f"face_x{index}",
            "cluster_mom",
            asset_id=f"asset_x{index}",
            observation_id=f"obs_x{index}",
            quality=0.5,
            detection_confidence=0.5,
        )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert [item["id"] for item in person["representative_media"]] == [
        "asset_x3",
        "asset_x2",
        "asset_x1",
    ]


def test_appearance_count_counts_faces_not_distinct_assets(family_client: TestClient) -> None:
    """``COUNT(*)`` over 人脸实例——**同一个人的三张脸在同一张照片上也数三**。

    与 ``photo_count``（``COUNT(DISTINCT a.id)``）正好相反。定点变异：把
    ``_APPEARANCE_SQL`` 换成带 DISTINCT 的那条，这条变红（3 变 1）。
    """
    _add_cluster(family_client, "cluster_mom", "mom", member_count=3)
    for index in range(3):
        _add_face(
            family_client,
            f"face_{index}",
            "cluster_mom",
            asset_id="asset_1",
            observation_id="obs_1",
            quality=0.5,
            detection_confidence=0.5,
        )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert person["appearance_count"] == 3


def test_the_avatar_prefers_quality_over_detection_confidence(family_client: TestClient) -> None:
    """头像排序是 ``fi.quality DESC`` **优先**，然后是 ``detection_confidence``。

    夹具让两个键给出相反的答案：``face_low_quality`` 的检测置信度更高、
    ``face_high_quality`` 的质量更高。

    定点变异：去掉 ``fi.quality DESC`` 那一位（只剩 detection_confidence），
    这条变红——而头像换一张是**看不出对错**的。
    """
    _add_cluster(family_client, "cluster_mom", "mom")
    _add_face(
        family_client,
        "face_low_quality",
        "cluster_mom",
        asset_id="asset_1",
        observation_id="obs_1",
        quality=0.2,
        detection_confidence=0.99,
    )
    _add_face(
        family_client,
        "face_high_quality",
        "cluster_mom",
        asset_id="asset_2",
        observation_id="obs_2",
        quality=0.9,
        detection_confidence=0.30,
    )

    person = {item["id"]: item for item in _people(family_client)}["mom"]

    assert person["avatar_face_instance_id"] == "face_high_quality"


def test_a_merged_graph_spans_people_and_relationships_across_scopes(family_client: TestClient) -> None:
    """合并过 scope 之后，``people`` 与 ``relationships`` 都跨整张图。

    1.0 ``app.py:1655`` 的 ``relationships`` 是一个对 ``scope_ids`` 的**展平推导**，
    所以它跟着 ``people`` 一起铺开——而 ``scope_id`` 那个键仍然只是入参那一个。
    这条容易看漏。
    """
    with _db(family_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_scope_groups (scope_id, graph_id, created_at, updated_at)"
            " VALUES (?, 'graph_1', ?, ?)",
            (HOME, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO family_scope_groups (scope_id, graph_id, created_at, updated_at)"
            " VALUES (?, 'graph_1', ?, ?)",
            (OTHER, NOW, NOW),
        )
        for entity_id, subject, predicate, obj, inverse in (
            ("rel_home", "mom", "母亲", "kid", "儿子"),
            ("rel_other", "neighbour", "朋友", "neighbour", "朋友"),
        ):
            for row_id, left, relation, right, back in (
                (f"{entity_id}_a", subject, predicate, obj, inverse),
                (f"{entity_id}_b", obj, inverse, subject, predicate),
            ):
                connection.execute(
                    "INSERT INTO family_relationships (id, scope_id, subject_entity_id,"
                    " predicate, object_entity_id, inverse_predicate, source, state, locked,"
                    " revision, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'user_override', 'effective', 1, 1, ?, ?)",
                    (
                        row_id,
                        HOME if entity_id == "rel_home" else OTHER,
                        left,
                        relation,
                        right,
                        back,
                        NOW,
                        NOW,
                    ),
                )

    body = family_client.get("/api/v1/family-graph", params={"scope_id": HOME}).json()

    assert body["scope_id"] == HOME
    assert body["scope_ids"] == [HOME, OTHER]
    assert {item["id"] for item in body["people"]} == {"mom", "kid", "neighbour"}
    # 关系也跨图——两条边各落两行，一共四行。
    assert len(body["relationships"]) == 4
