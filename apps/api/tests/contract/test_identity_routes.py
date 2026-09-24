"""身份端点的契约测试：状态码、响应形状、以及**响应里那几个字面量的内容**。

只跑 HTTP，不碰模型：容器里的检测器与嵌入器是 ``conftest`` 的确定性假件，簇与实例由
用例自己插进真 SQLite。这里验的是传输层——四审阅端点的 400 / 404 / 409、裁剪端点的
``thumbnail`` 尺寸与缓存头、以及 ``pending`` 实体在**接口上**叫「待命名成员」。

**裁决③之后的形状**：领域错误一律走 ``{"code", "message", "detail"}``（``api/errors.py``
的唯一处理器），不再是 ``HTTPException`` 的 ``{"detail"}``。所以断言里同时钉 **码**与
**状态**——只钉状态的话，「400 是从哪来的」这件事仍然没被验到。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentrix.application.identity.person_directory import EntityPersonDirectory
from sentrix.domain.identity.models import PENDING_CLUSTER_NAME

NOW = "2026-09-23T10:00:00+00:00"

HOME = "album1"
HIDDEN = "album-eval"
FRESH = "album-fresh"


def _db(client: TestClient):
    return client.app.state.container.database


def _seed(client: TestClient, *, hidden_scope: bool = False) -> None:
    """一份最小的身份数据集：两个空间、两个资产、三条观察。

    ``HIDDEN`` 空间 ``include_in_people = 0``——它钉住的是「评测空间聚出来的人脸不进
    人物视图」这条空间规则在两个列表端点上都成立。
    """
    with _db(client).connect() as connection, connection:
        for scope_id, include in ((HOME, 1), (HIDDEN, 0)):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (scope_id, scope_id, include, NOW, NOW),
            )
        for asset_id, scope_id in (("asset_1", HOME), ("asset_hidden", HIDDEN)):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, scope_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
        for index in (1, 2):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, ?, ?, 'vlm', ?, ?)",
                (f"obs_{index}", HOME, "asset_1", NOW, NOW),
            )


def add_entity(
    client: TestClient,
    entity_id: str,
    *,
    name: str,
    status: str = "pending",
    scope_id: str = HOME,
    family_role: str | None = None,
    summary: str = "",
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, family_role, summary,"
            "  confidence, created_at, updated_at)"
            " VALUES (?, ?, 'person', ?, ?, ?, ?, 0.0, ?, ?)",
            (entity_id, scope_id, name, status, family_role, summary, NOW, NOW),
        )


def add_cluster(
    client: TestClient,
    cluster_id: str,
    *,
    scope_id: str = HOME,
    status: str = "pending",
    entity_id: str | None = None,
    member_count: int = 0,
    revision: int = 1,
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, representative_embedding_json, member_count,"
            "  revision, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?)",
            (cluster_id, scope_id, status, entity_id, member_count, revision, NOW, NOW),
        )


def add_instance(
    client: TestClient,
    instance_id: str,
    *,
    cluster_id: str | None,
    asset_id: str = "asset_1",
    observation_id: str = "obs_1",
    bbox: tuple[float, ...] = (0.0, 0.0, 10.0, 10.0),
    detection_confidence: float = 0.9,
    quality: float = 0.8,
) -> None:
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, bbox_json, embedding_json,"
            "  detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            "  embedding_version, embedding_quality_signal, validity, area_ratio, sharpness,"
            "  created_at)"
            " VALUES (?, ?, ?, ?, ?, '[]', ?, ?, '[]', 'frontal', 'buffalo_l', 'w600k_r50',"
            "  0.5, 'verified', 0.1, 0.0, ?)",
            (
                instance_id,
                asset_id,
                observation_id,
                cluster_id,
                json.dumps(list(bbox)),
                detection_confidence,
                quality,
                NOW,
            ),
        )


@pytest.fixture
def identity_client(client: TestClient) -> TestClient:
    _seed(client)
    return client


def _error(response) -> tuple[str, str]:
    body = response.json()
    return body["code"], body["message"]


# ---- 簇列表 ----------------------------------------------------------------------


def test_an_empty_library_lists_no_clusters(client: TestClient):
    body = client.get("/api/v1/face-clusters").json()

    assert body == {"clusters": []}


def test_a_pending_cluster_is_shown_as_an_unnamed_member(client: TestClient):
    """未命名簇的名字是**接口上的常量**，不是落库的那个「待确认人物簇 · cluster-x」。"""
    _seed(client)
    add_entity(client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(client, "cluster_1", entity_id="ent_1", member_count=1)
    add_instance(client, "face_1", cluster_id="cluster_1")

    cluster = client.get("/api/v1/face-clusters").json()["clusters"][0]

    assert cluster["canonical_name"] == PENDING_CLUSTER_NAME == "待命名成员"
    assert cluster["family_role"] is None
    assert cluster["entity_status"] == "pending"
    assert cluster["reviewable"] is True
    assert cluster["single_sample"] is True
    # 样本脸的整串地址由服务端给全，前端不拼路径。
    assert cluster["samples"][0]["crop_url"] == "/api/v1/face-instances/face_1/crop"
    assert cluster["samples"][0]["bbox"] == [0.0, 0.0, 10.0, 10.0]


def test_a_rejected_cluster_is_not_listed_unless_asked_for(client: TestClient):
    _seed(client)
    add_cluster(client, "cluster_rejected", status="rejected", member_count=0)

    assert client.get("/api/v1/face-clusters").json()["clusters"] == []
    rejected = client.get("/api/v1/face-clusters", params={"status": "rejected"}).json()
    assert [item["id"] for item in rejected["clusters"]] == ["cluster_rejected"]


def test_clusters_from_a_hidden_space_never_enter_the_people_view(client: TestClient):
    """``include_in_people = 0`` 的空间里的簇一律不出现——这是端口的契约，不是调用方的过滤。"""
    _seed(client)
    add_cluster(client, "cluster_hidden", scope_id=HIDDEN, member_count=3)

    assert client.get("/api/v1/face-clusters").json()["clusters"] == []


def test_scope_filter_narrows_the_list(client: TestClient):
    _seed(client)
    add_cluster(client, "cluster_1", member_count=1)
    add_cluster(client, "cluster_2", scope_id="album2", member_count=1)
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album2', 'album2', 1, ?, ?)",
            (NOW, NOW),
        )

    body = client.get("/api/v1/face-clusters", params={"scope_id": "album2"}).json()

    assert [item["id"] for item in body["clusters"]] == ["cluster_2"]


def test_single_sample_is_false_once_the_cluster_has_two_faces(client: TestClient):
    _seed(client)
    add_entity(client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(client, "cluster_1", entity_id="ent_1", member_count=2)
    add_instance(client, "face_1", cluster_id="cluster_1")
    add_instance(client, "face_2", cluster_id="cluster_1")

    cluster = client.get("/api/v1/face-clusters").json()["clusters"][0]

    assert cluster["single_sample"] is False
    assert len(cluster["samples"]) == 2


# ---- 确认 ------------------------------------------------------------------------


def test_confirm_names_the_person_and_flips_the_cluster(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=1)
    add_instance(identity_client, "face_1", cluster_id="cluster_1")

    response = identity_client.post(
        "/api/v1/face-clusters/cluster_1/confirm", json={"name": "妈妈", "family_role": "母亲"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entity"]["canonical_name"] == "妈妈"
    assert body["entity"]["confirmed"] is True
    assert body["entity"]["family_role"] == "母亲"
    assert body["merged_into"] is None
    cluster = identity_client.get("/api/v1/face-clusters").json()["clusters"][0]
    assert cluster["status"] == "confirmed"
    assert cluster["revision"] == 2


def test_a_blank_name_is_400_not_422(identity_client: TestClient):
    """空名字是**用例的判据**（400），不是请求格式错（422）。

    1.0 的路由先 strip 再判空并抛 400；把它写成 pydantic 的 ``min_length`` 会让状态码
    悄悄从 400 变成 422——同一个产品行为换了一个码，调用方的分支就落空了。
    """
    add_cluster(identity_client, "cluster_1", member_count=1)

    response = identity_client.post("/api/v1/face-clusters/cluster_1/confirm", json={"name": "   "})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "entity name is required")


def test_confirming_an_unknown_cluster_is_404(identity_client: TestClient):
    response = identity_client.post("/api/v1/face-clusters/cluster_nope/confirm", json={"name": "妈妈"})

    assert response.status_code == 404
    assert _error(response)[0] == "not_found"


def test_confirming_with_an_existing_name_merges_into_that_person(identity_client: TestClient):
    add_entity(identity_client, "ent_known", name="妈妈", status="confirmed", family_role="母亲")
    add_cluster(identity_client, "cluster_known", status="confirmed", entity_id="ent_known", member_count=1)
    add_instance(identity_client, "face_known", cluster_id="cluster_known")
    add_entity(identity_client, "ent_new", name="待确认人物簇 · cluster_new")
    add_cluster(identity_client, "cluster_new", entity_id="ent_new", member_count=1)
    add_instance(identity_client, "face_new", cluster_id="cluster_new")

    response = identity_client.post("/api/v1/face-clusters/cluster_new/confirm", json={"name": "妈妈"})

    assert response.status_code == 200
    assert response.json()["merged_into"] == "ent_known"
    assert response.json()["entity"]["id"] == "ent_known"


# ---- 拒绝 / 合并 / 拆分 -----------------------------------------------------------


def test_rejecting_a_confirmed_person_is_409(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")
    add_cluster(identity_client, "cluster_1", status="confirmed", entity_id="ent_1", member_count=1)

    response = identity_client.post("/api/v1/face-clusters/cluster_1/reject")

    assert response.status_code == 409
    assert _error(response) == ("conflict", "confirmed person clusters cannot be rejected")


def test_rejecting_a_pending_cluster_reports_the_deleted_candidate(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=1)

    response = identity_client.post("/api/v1/face-clusters/cluster_1/reject")

    assert response.status_code == 200
    assert response.json() == {"cluster_id": "cluster_1", "rejected_entity_id": "ent_1"}


def test_merge_without_both_ids_is_400(identity_client: TestClient):
    response = identity_client.post(
        "/api/v1/face-clusters/merge", json={"target_cluster_id": "cluster_1", "source_cluster_id": ""}
    )

    assert response.status_code == 400
    assert _error(response)[0] == "invalid_request"


def test_merging_two_confirmed_people_is_409(identity_client: TestClient):
    add_entity(identity_client, "ent_a", name="妈妈", status="confirmed")
    add_entity(identity_client, "ent_b", name="爸爸", status="confirmed")
    add_cluster(identity_client, "cluster_a", status="confirmed", entity_id="ent_a", member_count=1)
    add_cluster(identity_client, "cluster_b", status="confirmed", entity_id="ent_b", member_count=1)

    response = identity_client.post(
        "/api/v1/face-clusters/merge",
        json={"target_cluster_id": "cluster_a", "source_cluster_id": "cluster_b"},
    )

    assert response.status_code == 409
    assert _error(response)[0] == "conflict"


def test_merge_moves_the_faces_and_retires_the_source(identity_client: TestClient):
    add_entity(identity_client, "ent_a", name="待确认人物簇 · cluster_a")
    add_entity(identity_client, "ent_b", name="待确认人物簇 · cluster_b")
    add_cluster(identity_client, "cluster_a", entity_id="ent_a", member_count=1)
    add_cluster(identity_client, "cluster_b", entity_id="ent_b", member_count=1)
    add_instance(identity_client, "face_a", cluster_id="cluster_a")
    add_instance(identity_client, "face_b", cluster_id="cluster_b")

    response = identity_client.post(
        "/api/v1/face-clusters/merge",
        json={"target_cluster_id": "cluster_a", "source_cluster_id": "cluster_b"},
    )

    assert response.status_code == 200
    assert response.json()["member_count"] == 2
    with _db(identity_client).connect() as connection:
        row = connection.execute(
            "SELECT status, member_count FROM face_clusters WHERE id = 'cluster_b'"
        ).fetchone()
    assert (row["status"], row["member_count"]) == ("rejected", 0)


def test_splitting_a_foreign_face_is_404(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=1)
    add_cluster(identity_client, "cluster_2", member_count=1)
    add_instance(identity_client, "face_2", cluster_id="cluster_2")

    response = identity_client.post(
        "/api/v1/face-clusters/cluster_1/split", json={"face_instance_id": "face_2"}
    )

    assert response.status_code == 404
    assert _error(response) == ("not_found", "face instance not found in cluster")


def test_split_returns_the_new_cluster_with_its_own_pending_person(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_1", entity_id="ent_1", member_count=2)
    add_instance(identity_client, "face_1", cluster_id="cluster_1")
    add_instance(identity_client, "face_2", cluster_id="cluster_1")

    response = identity_client.post(
        "/api/v1/face-clusters/cluster_1/split", json={"face_instance_id": "face_2"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] != "cluster_1"
    assert body["member_count"] == 1
    assert body["canonical_name"] == PENDING_CLUSTER_NAME
    assert body["samples"][0]["face_instance_id"] == "face_2"


def test_split_without_a_face_id_is_400(identity_client: TestClient):
    response = identity_client.post("/api/v1/face-clusters/cluster_1/split", json={"face_instance_id": ""})

    assert response.status_code == 400
    assert _error(response)[0] == "invalid_request"


# ---- 人物 ------------------------------------------------------------------------


def test_people_list_shows_the_internal_name_of_a_pending_candidate(identity_client: TestClient):
    add_entity(identity_client, "ent_pending", name="待确认人物簇 · cluster_1", summary="由 buffalo_l 生成")
    add_entity(identity_client, "ent_confirmed", name="妈妈", status="confirmed", family_role="母亲")

    people = identity_client.get("/api/v1/people").json()["people"]

    by_id = {item["id"]: item for item in people}
    assert by_id["ent_pending"]["canonical_name"] == PENDING_CLUSTER_NAME
    assert by_id["ent_pending"]["confirmed"] is False
    assert by_id["ent_confirmed"]["canonical_name"] == "妈妈"
    assert by_id["ent_confirmed"]["family_role"] == "母亲"
    assert by_id["ent_confirmed"]["confirmed"] is True


def test_people_list_hides_a_hidden_space(identity_client: TestClient):
    add_entity(identity_client, "ent_hidden", name="评测人物", scope_id=HIDDEN)

    assert identity_client.get("/api/v1/people").json()["people"] == []


def test_person_detail_carries_aliases_avatar_and_clusters(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed", family_role="母亲")
    add_cluster(identity_client, "cluster_1", status="confirmed", entity_id="ent_1", member_count=1)
    add_instance(identity_client, "face_1", cluster_id="cluster_1", detection_confidence=0.7)
    add_instance(identity_client, "face_2", cluster_id="cluster_1", detection_confidence=0.95)
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
            " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
            " VALUES ('prop_1', 'ent_1', 'aliases', '[\"老妈\"]', 'user', 1.0, 'active', '[]', 1, ?, ?)",
            (NOW, NOW),
        )

    body = identity_client.get("/api/v1/people/ent_1").json()

    assert body["entity"]["aliases"] == ["老妈"]
    assert body["avatar_face_instance_id"] == "face_2"
    assert [item["id"] for item in body["clusters"]] == ["cluster_1"]


def test_person_detail_of_an_unknown_id_is_404(identity_client: TestClient):
    response = identity_client.get("/api/v1/people/ent_nope")

    assert response.status_code == 404
    assert _error(response) == ("not_found", "person not found")


def test_rename_folds_the_old_name_into_aliases(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")

    response = identity_client.patch("/api/v1/people/ent_1/name", json={"name": "母亲"})

    assert response.status_code == 200
    assert response.json()["entity"]["canonical_name"] == "母亲"
    assert response.json()["aliases"] == ["妈妈"]


def test_rename_with_a_blank_name_is_400(identity_client: TestClient):
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed")

    response = identity_client.patch("/api/v1/people/ent_1/name", json={"name": "  "})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "person name is required")


def test_renaming_a_pending_candidate_keeps_it_unnamed_on_the_wire(identity_client: TestClient):
    """改名不等于确认这个人（1.0 ``db.py:4459`` 只动 ``name_state``）。

    所以 ``pending`` 的实体改完名字，接口上仍然显示「待命名成员」——界面据此知道这个人
    还没被认过。这条钉住的是「改名」与「确认」是两件事。
    """
    add_entity(identity_client, "ent_1", name="待确认人物簇 · cluster_1")

    response = identity_client.patch("/api/v1/people/ent_1/name", json={"name": "妈妈"})

    assert response.status_code == 200
    assert response.json()["entity"]["canonical_name"] == PENDING_CLUSTER_NAME
    assert response.json()["entity"]["confirmed"] is False
    with _db(identity_client).connect() as connection:
        row = connection.execute(
            "SELECT canonical_name, name_state, status FROM entities WHERE id = 'ent_1'"
        ).fetchone()
    assert (row["canonical_name"], row["name_state"], row["status"]) == ("妈妈", "confirmed", "pending")


def test_rename_of_an_unknown_person_is_404(identity_client: TestClient):
    response = identity_client.patch("/api/v1/people/ent_nope/name", json={"name": "妈妈"})

    assert response.status_code == 404
    assert _error(response)[0] == "not_found"


def test_batch_confirm_reports_each_item_and_keeps_going(identity_client: TestClient):
    """一件失败不影响其余：批量确认的输入来自用户逐个点选，整批回滚会丢掉其余选择。"""
    add_entity(identity_client, "ent_ok", name="待确认人物簇 · cluster_1")
    add_cluster(identity_client, "cluster_ok", entity_id="ent_ok", member_count=1)

    response = identity_client.post(
        "/api/v1/people/batch-confirm",
        json={
            "items": [
                {"person_id": "ent_ok", "name": "妈妈", "family_role": "母亲"},
                {"person_id": "ent_missing", "name": "爸爸"},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["confirmed"] == [
        {
            "person_id": "ent_ok",
            "entity_id": "ent_ok",
            "name": "妈妈",
            "family_role": "母亲",
            "merged": False,
        }
    ]
    assert body["failed"] == [{"person_id": "ent_missing", "name": "爸爸", "error": "person not found"}]


def test_batch_confirm_with_no_items_is_422(identity_client: TestClient):
    """空 items 是**请求格式**错（pydantic 的 min_length），不是领域判据。"""
    response = identity_client.post("/api/v1/people/batch-confirm", json={"items": []})

    assert response.status_code == 422


# ---- 人物目录（把容器里那个字段当作被测对象） --------------------------------------


def test_the_container_directory_finds_a_confirmed_person(identity_client: TestClient):
    """S3 的 ``NoIdentityDirectory`` 常驻 ``None``；S5 换上真实实现后这里必须查得到。"""
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed", family_role="母亲")
    add_entity(identity_client, "ent_pending", name="待确认人物簇 · cluster_1")

    directory = identity_client.app.state.container.person_directory

    profile = directory.lookup("妈妈", HOME)
    assert profile is not None
    assert (profile.name, profile.family_role, profile.confirmed) == ("妈妈", "母亲", True)
    assert directory.lookup("待确认人物簇 · cluster_1", HOME) is None
    assert directory.lookup("", HOME) is None


def test_the_directory_answers_by_alias(identity_client: TestClient):
    """按别名问到时，答案里的名字是**本名**——否则模型会把一个人当成两个人。"""
    add_entity(identity_client, "ent_1", name="妈妈", status="confirmed", family_role="母亲")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
            " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
            " VALUES ('prop_1', 'ent_1', 'aliases', '[\"老妈\"]', 'user', 1.0, 'active', '[]', 1, ?, ?)",
            (NOW, NOW),
        )

    profile = identity_client.app.state.container.person_directory.lookup("老妈", HOME)

    assert profile is not None
    assert profile.name == "妈妈"


def test_the_directory_is_the_entity_backed_implementation(client: TestClient):
    """装配点换过人了：S3 那个永远返回 ``None`` 的适配器已经不在了。"""
    assert isinstance(client.app.state.container.person_directory, EntityPersonDirectory)


# ---- 人脸裁剪 --------------------------------------------------------------------


def _write_jpeg(path: Path, size: tuple[int, int] = (640, 480)) -> None:
    from PIL import Image

    Image.new("RGB", size, (200, 120, 60)).save(path, format="JPEG")


def test_crop_returns_a_jpeg_with_private_cache_headers(identity_client: TestClient, tmp_path: Path):
    _write_jpeg(tmp_path / "asset_1.jpg")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET path = ? WHERE id = 'asset_1'", (str(tmp_path / "asset_1.jpg"),)
        )
    add_instance(identity_client, "face_1", cluster_id=None, bbox=(10.0, 20.0, 210.0, 220.0))

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"
    from PIL import Image

    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (200, 200)


def test_crop_of_an_unknown_instance_is_404(identity_client: TestClient):
    response = identity_client.get("/api/v1/face-instances/face_nope/crop")

    assert response.status_code == 404
    assert _error(response) == ("not_found", "face instance not found")


def test_crop_of_a_missing_file_is_422(identity_client: TestClient):
    """资产行在、磁盘上的文件不在：请求语法没问题，内容处理不了。"""
    add_instance(identity_client, "face_1", cluster_id=None)

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 422
    assert _error(response)[0] == "unprocessable"


def test_crop_of_a_degenerate_box_is_422(identity_client: TestClient, tmp_path: Path):
    _write_jpeg(tmp_path / "asset_1.jpg")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET path = ? WHERE id = 'asset_1'", (str(tmp_path / "asset_1.jpg"),)
        )
    # 夹到图内之后是空框：右 0 <= 左 0。
    add_instance(identity_client, "face_1", cluster_id=None, bbox=(0.0, 0.0, -50.0, -50.0))

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 422
    assert _error(response)[0] == "unprocessable"


def test_crop_with_a_short_bbox_is_422(identity_client: TestClient, tmp_path: Path):
    _write_jpeg(tmp_path / "asset_1.jpg")
    with _db(identity_client).connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET path = ? WHERE id = 'asset_1'", (str(tmp_path / "asset_1.jpg"),)
        )
    add_instance(identity_client, "face_1", cluster_id=None, bbox=(1.0, 2.0))

    response = identity_client.get("/api/v1/face-instances/face_1/crop")

    assert response.status_code == 422
    assert _error(response) == ("unprocessable", "face crop is unavailable: invalid face bounding box")


# ---- 身份预置：按人脸实例 ----------------------------------------------------------
#
# 裁决二新增的入口。契约层的两件事：**不碰检测器**（所以这里能跑，而按照片那条路要
# insightface 权重）与**不存在的实例是 404 而不是 422**。


class ExplodingDetector:
    """真被调上就说明实例那条路又去「挑照片里最好的脸」了。"""

    def detect(self, path: str):
        raise AssertionError(f"按实例预置不该调用检测器: {path}")

    def detect_identity_photo(self, path: str):
        raise AssertionError(f"按实例预置不该调用检测器: {path}")


def add_embedded_instance(
    client: TestClient, instance_id: str, *, embedding: list[float], scope_id: str = HOME
) -> None:
    """一条带**真向量**的实例——``add_instance`` 落的是空向量，那是「没有向量」的意思。"""
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, bbox_json, embedding_json,"
            "  detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            "  embedding_version, embedding_quality_signal, validity, area_ratio, sharpness,"
            "  created_at)"
            " VALUES (?, 'asset_1', 'obs_1', NULL, '[10.0, 10.0, 110.0, 110.0]', ?,"
            "  0.95, 0.88, '[]', 'frontal', 'buffalo_l', 'buffalo_l', 0.4, 'verified',"
            "  0.1, 0.3, ?)",
            (instance_id, json.dumps(embedding), NOW),
        )


def test_seeding_by_a_stored_face_instance_never_calls_the_detector(identity_client: TestClient):
    """实例入口把库里那一行**原样**当作身份样本。

    把检测器换成「一调就炸」的替身，这条仍然是 201——这正是它与照片那条路的区别，
    也是它能在契约测试里跑起来的原因（那条路要 insightface 权重）。
    """
    import dataclasses

    add_embedded_instance(identity_client, "fi_mom", embedding=[1.0, 0.0, 0.0, 0.0])
    identity_client.app.state.container = dataclasses.replace(
        identity_client.app.state.container, face_detector=ExplodingDetector()
    )

    response = identity_client.post(
        "/api/v1/people/seed",
        data={"name": "妈妈", "family_role": "母亲", "scope_id": HOME, "face_instance_ids": "fi_mom"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["name"] == "妈妈"
    assert response.json()["face_count"] == 1
    assert response.json()["photos_without_faces"] == 0
    cluster = identity_client.get("/api/v1/face-clusters").json()["clusters"][0]
    assert cluster["status"] == "confirmed"
    assert cluster["member_count"] == 1
    assert cluster["canonical_name"] == "妈妈"


def test_an_unknown_face_instance_is_a_404_not_a_422(identity_client: TestClient):
    """指向一个不存在的对象（404）与「图里没有人脸」（422）是两件事。

    合并成一个码，调用方就分不清该换 id 还是该换图。
    """
    response = identity_client.post(
        "/api/v1/people/seed", data={"name": "妈妈", "face_instance_ids": "fi_ghost"}
    )

    assert response.status_code == 404, response.text
    assert _error(response) == ("not_found", "face instance not found: fi_ghost")


def test_seeding_with_neither_files_nor_instances_is_a_400(identity_client: TestClient):
    """1.0 的 ``files`` 是必填，所以这条在 1.0 不存在；两条入口之后它是新的边界。"""
    response = identity_client.post("/api/v1/people/seed", data={"name": "妈妈"})

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "files or face_instance_ids is required")


def test_an_empty_name_is_still_rejected_before_either_entrance(identity_client: TestClient):
    """1.0 ``app.py:2321`` 的 400，两条入口共用。"""
    add_embedded_instance(identity_client, "fi_mom", embedding=[1.0, 0.0, 0.0, 0.0])

    response = identity_client.post(
        "/api/v1/people/seed", data={"name": "   ", "face_instance_ids": "fi_mom"}
    )

    assert response.status_code == 400
    assert _error(response) == ("invalid_request", "name is required")


# ---- 预置建空间行（第 104 条的裁决，2026-09-24）-----------------------------------


def add_bare_cluster(client: TestClient, cluster_id: str, *, scope_id: str) -> None:
    """一行**什么都没有**的簇，直接插——绕开预置路由，单独钉「可见性由空间行决定」。"""
    with _db(client).connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, member_count, confidence, created_at, updated_at)"
            " VALUES (?, ?, 'pending', 1, 1.0, ?, ?)",
            (cluster_id, scope_id, NOW, NOW),
        )


def test_seeding_into_a_fresh_scope_registers_the_space_so_the_cluster_is_visible(
    identity_client: TestClient,
):
    """预置也要建空间行——1.0 的预置走 ``create_asset``，那里面就有 ensure（``db.py:1462``）。

    第 104 条：2.0 的 ``_store_seed_photo`` 直接 ``asset_repository.add``，于是往一个全新
    scope 预置身份、又不导入任何照片时，那个簇在审阅页里看不见。1.0 是建的
    （``app.py:2337``），这里补成一致。

    **只走 HTTP。** 同构构造的用例证明不了生产装配（第 107 条 M2）：装配点漏了
    ``container.spaces``，自己传协作者的集成用例照样是绿的。
    """
    add_embedded_instance(identity_client, "fi_mom", embedding=[1.0, 0.0, 0.0, 0.0])

    response = identity_client.post(
        "/api/v1/people/seed",
        data={"name": "妈妈", "scope_id": FRESH, "face_instance_ids": "fi_mom"},
    )

    assert response.status_code == 201, response.text
    space = identity_client.app.state.container.spaces.get(FRESH)
    assert space is not None
    assert (space.id, space.name, space.kind, space.status) == (FRESH, FRESH, "household", "active")
    body = identity_client.get("/api/v1/face-clusters", params={"scope_id": FRESH}).json()
    assert [item["id"] for item in body["clusters"]] == [response.json()["cluster_id"]]


def test_a_cluster_in_a_scope_without_a_space_row_is_invisible_on_the_review_page(
    client: TestClient,
):
    """反向那一半：同一个簇，只差一行空间行，``/face-clusters`` 就从看不见变成看得见。

    ``list_clusters`` 里的 ``memory_spaces`` 约束是**有意为之**（``faces.py:139``：内连 +
    ``ms.include_in_people = 1``），所以「少一行空间」在审阅页上的表现是「这个簇不存在」，
    「空」与「没有」从此被混成同一件事。``/people`` 不受影响，它用的是子查询（第 103 条）。

    定点变异量过这道门禁的边界：**把整条约束去掉**（M6b）这条立刻变红；只把 INNER JOIN
    换成 LEFT JOIN（M6）**不会**——``include_in_people = 1`` 那个谓词自己就够把没登记过的
    scope 挡掉（``NULL = 1`` 不成立）。两处合起来才是那条约束。

    它钉的是物理原因；预置路由自己那条反向门禁是定点变异（删掉 ``seed_person_identity``
    里的 ``container.spaces.ensure``（M5）→ 上面那条用例变红）。
    """
    add_bare_cluster(client, "cluster_orphan", scope_id=FRESH)

    assert client.get("/api/v1/face-clusters", params={"scope_id": FRESH}).json() == {"clusters": []}

    client.app.state.container.spaces.ensure(FRESH, FRESH)

    body = client.get("/api/v1/face-clusters", params={"scope_id": FRESH}).json()
    assert [item["id"] for item in body["clusters"]] == ["cluster_orphan"]
