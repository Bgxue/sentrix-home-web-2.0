"""``decorate_entities`` 的真 SQLite 用例（S6 · T128a）。

对应 1.0 ``db.py:4056-4141`` 的 ``_decorate_entities``。纯判断部分（两个派生布尔、私密
投影）在 ``tests/unit/test_entity_decoration.py``；这里只验**取数**，也就是那七条聚合
的 SQL 有没有把该算的算进去。

每条还是钉一个「会说谎的数」，不是「SQL 能跑通」：

- ``cluster_count`` **不带状态过滤**，被拒的簇也算——漏掉它，「这个实体有几个簇」会比
  库里真实的行数少；
- ``photo_count`` 带 ``fc.status != 'rejected'`` 且是 ``COUNT(DISTINCT a.id)``——漏掉
  DISTINCT，一个簇里三张同源的脸会把一张照片数成三张；
- avatar 与 preview 都是 ``ROW_NUMBER() ... rn = 1``，**排序键写反了不会报错**，
  只会让每个实体的封面换一张，而且换得「看起来也合理」。
"""

from __future__ import annotations

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository

NOW = "2026-09-23T10:00:00+00:00"
LATER = "2026-09-24T10:00:00+00:00"


@pytest.fixture
def repos(migrations_ready_db: Database) -> dict[str, object]:
    """一个空间 + 两个资产 + 三条观察 + 两个实体。外键是开的，所以父数据先落。"""
    database = migrations_ready_db
    with database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', '主相册', 1, ?, ?)",
            (NOW, NOW),
        )
        for asset_id, captured in (("asset_1", NOW), ("asset_2", LATER)):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, 'album1', ?, 'image', ?, ?, ?)",
                (asset_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", captured, captured),
            )
        for observation_id, asset_id, captured in (
            ("obs_1", "asset_1", NOW),
            ("obs_2", "asset_2", LATER),
            ("obs_3", "asset_1", NOW),
            # 与 obs_1 同刻、不同资产——preview 的 id 次序要靠它才验得出来：
            # 两条观察指向同一个资产时，去掉次序也会「碰巧」给出同一个 asset_id。
            ("obs_4", "asset_2", NOW),
        ):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " created_at, updated_at) VALUES (?, 'album1', ?, 'vlm', ?, ?, ?)",
                (observation_id, asset_id, captured, captured, captured),
            )
        for entity_id, entity_type in (("ent_1", "person"), ("ent_2", "place")):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, 'album1', ?, ?, 'pending', '', 0.0, ?, ?)",
                (entity_id, entity_type, entity_id, NOW, NOW),
            )
    return {"entities": SqliteEntityRepository(database), "db": database}


def _add_cluster(db: Database, cluster_id: str, entity_id: str, *, status: str, member_count: int) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id,"
            " representative_embedding_json, member_count, revision, created_at, updated_at)"
            " VALUES (?, 'album1', ?, ?, '[]', ?, 1, ?, ?)",
            (cluster_id, status, entity_id, member_count, NOW, NOW),
        )


def _add_instance(
    db: Database,
    instance_id: str,
    cluster_id: str,
    *,
    asset_id: str,
    observation_id: str,
    detection_confidence: float,
    created_at: str,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
            " embedding_model, embedding_version, embedding_quality_signal, validity,"
            " area_ratio, sharpness, created_at)"
            " VALUES (?, ?, ?, ?, '[0,0,1,1]', '[]', ?, 0.9, '[]', 'frontal', 'buffalo_l',"
            " 'w600k_r50', 0.5, 'verified', 0.1, 0.0, ?)",
            (instance_id, asset_id, observation_id, cluster_id, detection_confidence, created_at),
        )


def _link_observation(db: Database, entity_id: str, observation_id: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_observations (entity_id, observation_id, confidence, source,"
            " created_at) VALUES (?, ?, 0.5, 'observation_extraction', ?)",
            (entity_id, observation_id, NOW),
        )


def _add_relationship(db: Database, rel_id: str, subject: str, obj: str, *, status: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO relationships (id, scope_id, subject_entity_id, predicate,"
            " object_entity_id, status, confidence, evidence_ids_json, revision,"
            " created_at, updated_at)"
            " VALUES (?, 'album1', ?, '出现在', ?, ?, 0.5, '[]', 1, ?, ?)",
            (rel_id, subject, obj, status, NOW, NOW),
        )


def _mention(db: Database, mention_id: str, entity_id: str, observation_id: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, confidence, created_at)"
            " VALUES (?, ?, ?, 0.5, ?)",
            (mention_id, entity_id, observation_id, NOW),
        )


def test_an_entity_with_no_rows_gets_zeroes_not_a_missing_entry(repos):
    """没有簇 / 提及 / 观察的实体照样有一条全零的记录——1.0 也是这个形状。

    定点变异：把返回那一段改成「只放有行的实体」，这条变红（``KeyError``）。
    """
    decoration = repos["entities"].decorate_entities(["ent_2"])["ent_2"]

    assert decoration.cluster_count == 0
    assert decoration.photo_count == 0
    assert decoration.mention_count == 0
    assert decoration.evidence_count == 0
    assert decoration.relationship_count == 0
    assert decoration.clusters == ()
    assert decoration.avatar_face_instance_id is None
    assert decoration.preview_asset_id is None


def test_an_empty_id_list_returns_an_empty_mapping_without_touching_the_database(repos):
    """空列表直接返回——1.0 是 ``if entities:`` 守卫。真去查会拼出 ``IN ()`` 的语法错。"""
    assert repos["entities"].decorate_entities([]) == {}


def test_cluster_count_includes_rejected_clusters(repos):
    """1.0 的 ``cluster_count`` 那条 ``COUNT(*)`` **没有状态过滤**。

    定点变异：给这条查询加上 ``AND status != 'rejected'``，这条变红。
    """
    db = repos["db"]
    _add_cluster(db, "cluster_live", "ent_1", status="pending", member_count=1)
    _add_cluster(db, "cluster_rejected", "ent_1", status="rejected", member_count=3)

    decoration = repos["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert decoration.cluster_count == 2
    assert [item.status for item in decoration.clusters] == ["pending", "rejected"]


def test_photo_count_is_distinct_assets_and_skips_rejected_clusters(repos):
    """同一张照片上的三张脸算**一张**照片；被拒的簇里的脸一张都不算。

    定点变异有两处，各自独立：去掉 ``DISTINCT`` 会数成 4，去掉
    ``fc.status != 'rejected'`` 会数成 2。
    """
    db = repos["db"]
    _add_cluster(db, "cluster_live", "ent_1", status="pending", member_count=3)
    _add_cluster(db, "cluster_rejected", "ent_1", status="rejected", member_count=1)
    for index in (1, 2, 3):
        _add_instance(
            db,
            f"face_{index}",
            "cluster_live",
            asset_id="asset_1",
            observation_id="obs_1",
            detection_confidence=0.5,
            created_at=NOW,
        )
    _add_instance(
        db,
        "face_rejected",
        "cluster_rejected",
        asset_id="asset_2",
        observation_id="obs_2",
        detection_confidence=0.9,
        created_at=NOW,
    )

    decoration = repos["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert decoration.photo_count == 1


def test_the_avatar_is_the_highest_confidence_face_of_a_live_cluster(repos):
    """检测置信度降序取第一；**被拒的簇不参与**，哪怕它的脸更清楚。

    定点变异：去掉 ``fc.status != 'rejected'``，头像会变成 ``face_rejected``。
    """
    db = repos["db"]
    _add_cluster(db, "cluster_live", "ent_1", status="pending", member_count=2)
    _add_cluster(db, "cluster_rejected", "ent_1", status="rejected", member_count=1)
    _add_instance(
        db,
        "face_low",
        "cluster_live",
        asset_id="asset_1",
        observation_id="obs_1",
        detection_confidence=0.4,
        created_at=NOW,
    )
    _add_instance(
        db,
        "face_high",
        "cluster_live",
        asset_id="asset_1",
        observation_id="obs_1",
        detection_confidence=0.95,
        created_at=LATER,
    )
    _add_instance(
        db,
        "face_rejected",
        "cluster_rejected",
        asset_id="asset_2",
        observation_id="obs_2",
        detection_confidence=0.99,
        created_at=NOW,
    )

    decoration = repos["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert decoration.avatar_face_instance_id == "face_high"


def test_the_avatar_tie_breaks_on_the_earlier_face(repos):
    """同分时取 ``created_at`` **更早**的（1.0 是 ``ORDER BY ... fi.created_at ASC``）。

    定点变异：把 ``ASC`` 写成 ``DESC``，这条变红而上一条仍然过——所以这条不是多余的。
    """
    db = repos["db"]
    _add_cluster(db, "cluster_live", "ent_1", status="pending", member_count=2)
    for instance_id, created in (("face_later", LATER), ("face_earlier", NOW)):
        _add_instance(
            db,
            instance_id,
            "cluster_live",
            asset_id="asset_1",
            observation_id="obs_1",
            detection_confidence=0.8,
            created_at=created,
        )

    decoration = repos["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert decoration.avatar_face_instance_id == "face_earlier"


def test_the_preview_is_the_newest_observation_of_the_entity(repos):
    """``ORDER BY o.captured_at DESC, o.id DESC``——最近的那条观察所指向的资产。

    定点变异：把 ``captured_at DESC`` 写成 ``ASC``，这条变红。
    """
    db = repos["db"]
    _link_observation(db, "ent_1", "obs_1")
    _link_observation(db, "ent_1", "obs_2")

    decoration = repos["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert decoration.preview_asset_id == "asset_2"
    assert decoration.preview_file_name == "asset_2.jpg"
    assert decoration.preview_media_type == "image"


def test_the_preview_tie_breaks_on_the_larger_observation_id(repos):
    """同一时刻的两条观察，取 **id 更大**的那条（1.0 的 ``o.id DESC``）。

    定点变异：去掉 ``o.id DESC``，取到的会变成先扫到的那条（``obs_1``）——所以两条
    观察必须指向**不同的资产**，否则去掉次序也照样给出同一个 ``asset_id``，
    这条用例就成了恒真。第一版就是这么写的，被变异测试抓了出来。
    """
    db = repos["db"]
    _link_observation(db, "ent_1", "obs_1")  # asset_1，与 obs_4 同刻
    _link_observation(db, "ent_1", "obs_4")  # asset_2，id 更大

    decoration = repos["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert decoration.preview_asset_id == "asset_2"
    assert decoration.preview_file_name == "asset_2.jpg"


def test_mentions_and_evidence_are_counted_separately(repos):
    """``mention_count`` 数 ``entity_mentions``，``evidence_count`` 数 ``entity_observations``。

    两张表容易混成一张：提及是「观察里认出了这个人」，证据是「这条观察与这个实体有关」。
    定点变异：把两条 COUNT 指向同一张表，这条变红。
    """
    db = repos["db"]
    _link_observation(db, "ent_1", "obs_1")
    _link_observation(db, "ent_1", "obs_2")
    _mention(db, "mention_1", "ent_1", "obs_1")

    decoration = repos["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert decoration.evidence_count == 2
    assert decoration.mention_count == 1


def test_relationship_count_counts_both_ends_and_skips_retracted(repos):
    """一条关系给**两端各记 1**，且**只排 ``retracted``**。

    1.0 ``db.py:4087-4098`` 是**两条**查询——按 ``subject_entity_id`` 一条、按
    ``object_entity_id`` 一条——再把结果相加。两个后果各自容易被「顺手优化」掉：

    - **两端都算**：``ent_1 → ent_2`` 让两个实体各得 1。只数一侧的话，被指向的那个
      实体永远显示 0 条关系；
    - **只排 ``retracted``**：``pending`` / ``superseded`` 都算——与 ``cluster_count``
      同口径，与 ``photo_count``（排 rejected）相反。

    定点变异：只留 subject 那一条查询，``ent_2`` 变成 0；把过滤换成 ``status = 'active'``，
    两个数都变成 1。
    """
    db = repos["db"]
    for rel_id, status in (
        ("rel_active", "active"),
        ("rel_pending", "pending"),
        ("rel_superseded", "superseded"),
        ("rel_retracted", "retracted"),
    ):
        _add_relationship(db, rel_id, "ent_1", "ent_2", status=status)

    both = repos["entities"].decorate_entities(["ent_1", "ent_2"])

    # 四条里被排除的只有 retracted。
    assert both["ent_1"].relationship_count == 3
    # 同三条，从 object 那一侧数出来——两端各记 1。
    assert both["ent_2"].relationship_count == 3


def test_decorating_one_entity_does_not_pick_up_anothers_rows(repos):
    """``IN (...)`` 的实体维度不能串。

    定点变异：把 ``WHERE entity_id IN (...)`` 整个去掉，这条会拿到全库的行数。
    """
    db = repos["db"]
    _add_cluster(db, "cluster_1", "ent_1", status="pending", member_count=5)
    _link_observation(db, "ent_1", "obs_1")
    _mention(db, "mention_2", "ent_2", "obs_2")

    both = repos["entities"].decorate_entities(["ent_1", "ent_2"])

    assert both["ent_1"].cluster_count == 1
    assert both["ent_2"].cluster_count == 0
    assert both["ent_1"].mention_count == 0
    assert both["ent_2"].mention_count == 1
