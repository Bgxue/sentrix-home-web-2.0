"""维护端点的契约：路径、方法、请求默认值、响应形状，以及**它不在导入链上**。

只跑 HTTP。这个端点不做任何模型调用（它读的是库里已有的向量），所以契约测试里不需要
假件——除了那条「导入不触发它」的对照，导入会走人脸检测，那一步换成不给脸的假件。

**「不自动跑」在这里是可断言的**：同一份数据，走导入路径之后那些该被重聚类撤掉的行
仍然在；走这个端点之后才消失。只断言「端点存在」的话，把它挂到导入链上一样绿。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from sentrix.domain.identity.faces import DEFAULT_MATCH_THRESHOLD, DEFAULT_MINIMUM_QUALITY
from sentrix.domain.identity.models import FaceDetection

NOW = "2026-09-23T10:00:00+00:00"
HOME = "album1"


def _db(client: TestClient):
    return client.app.state.container.database


class NoFacesDetector:
    """一张脸都不给。真件第一次 ``detect`` 会加载 RetinaFace 权重并跑 GPU 推理。"""

    def detect(self, path: str) -> Sequence[FaceDetection]:
        return ()

    def detect_identity_photo(self, path: str) -> Sequence[FaceDetection]:
        return ()


def _seed(client: TestClient) -> None:
    """两个簇（三张一模一样、已验证的脸）+ 一个没有簇的候选人物。

    三张脸的向量相同，所以重聚类会把 ``clu_2`` 并进 ``clu_1`` —— ``clu_2`` 因此成为
    「该被撤掉的那个簇」，而 ``ent_orphan`` 是「该被撤掉的候选人物」。这两行就是那条
    导入 / 维护对照的观测面。
    """
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (HOME, HOME, NOW, NOW),
        )
        for index in (1, 2, 3):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (f"asset_{index}", HOME, f"asset_{index}.jpg", f"/tmp/a{index}.jpg", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at,"
                " updated_at) VALUES (?, ?, ?, 'import', ?, ?)",
                (f"obs_{index}", HOME, f"asset_{index}", NOW, NOW),
            )
        for cluster_id, member_count in (("clu_1", 2), ("clu_2", 1)):
            connection.execute(
                "INSERT INTO face_clusters (id, scope_id, status, member_count, created_at,"
                " updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                (cluster_id, HOME, member_count, NOW, NOW),
            )
        for index in (1, 2, 3):
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id,"
                " embedding_json, detection_confidence, quality, pose_bucket, embedding_model,"
                " embedding_version, validity, created_at)"
                " VALUES (?, ?, ?, ?, '[1.0,0.0]', 0.9, 0.9, 'frontal', 'buffalo_l',"
                " 'w600k_r50', 'verified', ?)",
                (f"ins_{index}", f"asset_{index}", f"obs_{index}", "clu_1" if index < 3 else "clu_2", NOW),
            )
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('ent_orphan', ?, 'person', '待确认人物簇 · 已撤销', 'pending', '', 0.5, ?, ?)",
            (HOME, NOW, NOW),
        )


def _changed_state(client: TestClient) -> tuple[object, object]:
    """两处「只该被维护端点改掉」的痕迹：落选簇与没有簇的候选人物。"""
    with _db(client).connect() as connection:
        cluster = connection.execute("SELECT status FROM face_clusters WHERE id = 'clu_2'").fetchone()[
            "status"
        ]
        entity = connection.execute("SELECT status FROM entities WHERE id = 'ent_orphan'").fetchone()[
            "status"
        ]
    return cluster, entity


def test_the_defaults_are_the_clusterer_class_defaults(client: TestClient):
    """不传参数时按 ``FaceClusterer`` 的类默认跑——0.30 / 0.30，**不是** 1.0 两个
    benchmark 调用方的 0.30 / 0.55。那对实参属于被判死的调用方（``s5-deviations`` 证据 39）。
    """
    body = client.post("/api/v1/maintenance/recluster-faces", json={}).json()

    assert body["threshold"] == pytest.approx(DEFAULT_MATCH_THRESHOLD)
    assert body["minimum_quality"] == pytest.approx(DEFAULT_MINIMUM_QUALITY)
    assert DEFAULT_MATCH_THRESHOLD == 0.30
    assert DEFAULT_MINIMUM_QUALITY == 0.30


def test_the_response_carries_the_counts_of_what_changed(client: TestClient):
    """维护入口跑完必须有**可核的产出**，只有一句「完成」不是。

    字段集合整体断言：少一个计数、多一个没人用的字段都要红。
    """
    response = client.post("/api/v1/maintenance/recluster-faces", json={"scope_id": HOME})

    assert response.status_code == 200
    assert set(response.json()) == {
        "instances",
        "clusters",
        "noise",
        "attached_uncertain",
        "retired_clusters",
        "retired_candidates",
        "threshold",
        "minimum_quality",
    }


def test_the_request_can_override_both_thresholds(client: TestClient):
    """能覆盖才是「维护入口」：只能按一个写死的口径跑的话，它只是一个按钮。"""
    body = client.post(
        "/api/v1/maintenance/recluster-faces",
        json={"scope_id": HOME, "threshold": 0.45, "minimum_quality": 0.55},
    ).json()

    assert body["threshold"] == pytest.approx(0.45)
    assert body["minimum_quality"] == pytest.approx(0.55)


def test_an_empty_library_is_not_an_error(client: TestClient):
    """库里一张脸都没有时返回全零，不是 500——维护入口在空库上跑一次是正常操作。"""
    body = client.post("/api/v1/maintenance/recluster-faces", json={}).json()

    assert body["instances"] == 0
    assert body["clusters"] == 0


def test_importing_a_photo_does_not_trigger_the_global_pass(client: TestClient):
    """**对照**：同一份数据，导入路径不碰那些该被撤掉的行，维护端点才碰。

    这是「不自动跑」（用户裁决③）在行为上的凭证。断言的是**内容**：空壳簇的状态与
    那个没有簇的候选人物。只断言「导入返回 201」的话，把它挂到导入链上照样绿。
    """
    client.app.state.container = dataclasses.replace(
        client.app.state.container, face_detector=NoFacesDetector()
    )
    _seed(client)

    imported = client.post(
        "/api/v1/media/import",
        files={"file": ("one.jpg", b"\xff\xd8\xff\xe0not-a-real-jpeg", "image/jpeg")},
        data={"scope_id": HOME},
    )

    assert imported.status_code == 201
    assert _changed_state(client) == ("pending", "pending")

    reclustered = client.post("/api/v1/maintenance/recluster-faces", json={"scope_id": HOME})

    assert reclustered.status_code == 200
    assert reclustered.json()["retired_clusters"] == 1
    assert reclustered.json()["retired_candidates"] == 1
    assert _changed_state(client) == ("rejected", "rejected")
