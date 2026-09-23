"""全局重聚类：整表重算、认领不确定的脸、撤掉孤儿候选。

对应 1.0 ``backend/db.py:4956-5132`` 的 ``recluster_faces`` / ``_attach_uncertain_faces``
/ ``_sync_face_entity_statuses``。

**这一段验的是「改了哪些行」，不是「跑完了」。** 重聚类是整表重算：它会改归属、撤簇、
转实体的状态，每一件都在库里留痕——只断言 ``result.instances == 4`` 的话，
把四张脸全挂到一个新簇、旧簇全留着也一样是绿的。

**两条对照臂**（判据两侧都要有）：

- 已确认的簇必须在分组里被**保留**（换 id 会让提及、事件参与者、画像全部指向一个
  被撤掉的簇）；
- 认领不确定的脸有两道门（相似度 0.75、领先第二名 0.08），各有一个反例。

**还有一条「不自动跑」的结构断言**：导入链上的四个模块一个都不许提 recluster。
用户裁决③要的是显式维护入口——「忘记接线」与「偷偷接上」都不该只靠读代码发现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.identity.recluster import (
    FACE_ATTACH_MARGIN,
    FACE_ATTACH_MINIMUM_MEMBERS,
    FACE_ATTACH_THRESHOLD,
    ReclusterFaces,
)
from sentrix.domain.identity.models import RETIRED_CANDIDATE_SUMMARY
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

HOME = "album1"
NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
# 两张正脸方向一致的单位向量；``FAR`` 与它正交。
FRONT = (1.0, 0.0)
FAR = (0.0, 1.0)


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (HOME, HOME, NOW.isoformat(), NOW.isoformat()),
        )
        for index in (1, 2, 3, 4, 5):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (
                    f"asset_{index}",
                    HOME,
                    f"asset_{index}.jpg",
                    f"/tmp/asset_{index}.jpg",
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at,"
                " updated_at) VALUES (?, ?, ?, 'import', ?, ?)",
                (f"obs_{index}", HOME, f"asset_{index}", NOW.isoformat(), NOW.isoformat()),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at,"
                " updated_at) VALUES (?, ?, ?, 'import', ?, ?)",
                (f"obs_{index}_u", HOME, f"asset_{index}", NOW.isoformat(), NOW.isoformat()),
            )
    return migrations_ready_db


@pytest.fixture
def faces(db: Database) -> SqliteFaceRepository:
    return SqliteFaceRepository(db)


@pytest.fixture
def entities(db: Database) -> SqliteEntityRepository:
    return SqliteEntityRepository(db)


@pytest.fixture
def vectors(db: Database) -> SqliteVectorRepository:
    return SqliteVectorRepository(db)


@pytest.fixture
def recluster(
    faces: SqliteFaceRepository, entities: SqliteEntityRepository, vectors: SqliteVectorRepository
) -> ReclusterFaces:
    return ReclusterFaces(faces=faces, entities=entities, vectors=vectors)


def add_cluster(
    db: Database,
    cluster_id: str,
    *,
    status: str = "pending",
    entity_id: str | None = None,
    member_count: int = 0,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, representative_embedding_json, member_count,"
            "  created_at, updated_at) VALUES (?, ?, ?, ?, '[]', ?, ?, ?)",
            (cluster_id, HOME, status, entity_id, member_count, NOW.isoformat(), NOW.isoformat()),
        )


def add_instance(
    db: Database,
    instance_id: str,
    *,
    cluster_id: str | None,
    embedding: tuple[float, ...] = FRONT,
    quality: float = 0.9,
    validity: str = "verified",
    index: int = 1,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, embedding_json, detection_confidence,"
            "  quality, pose_bucket, embedding_model, embedding_version, embedding_quality_signal,"
            "  validity, created_at) VALUES (?, ?, ?, ?, ?, 0.9, ?, 'frontal', 'buffalo_l',"
            "  'w600k_r50', 0.9, ?, ?)",
            (
                instance_id,
                f"asset_{index}",
                f"obs_{index}",
                cluster_id,
                "[" + ",".join(str(value) for value in embedding) + "]",
                quality,
                validity,
                NOW.isoformat(),
            ),
        )


def add_vector(vectors: SqliteVectorRepository, instance_id: str, *, cluster_id: str | None) -> None:
    metadata: dict[str, object] = {"quality": 0.9}
    if cluster_id is not None:
        metadata["cluster_id"] = cluster_id
    vectors.upsert(
        VectorRecord(
            scope_id=HOME,
            space="visual",
            source_type="face_instance",
            source_id=instance_id,
            model_name="buffalo_l",
            vector=FRONT,
            metadata=metadata,
        )
    )


def add_person(db: Database, entity_id: str, *, name: str, status: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at) VALUES (?, ?, 'person', ?, ?, '', 0.5, ?, ?)",
            (entity_id, HOME, name, status, NOW.isoformat(), NOW.isoformat()),
        )


def cluster_row(db: Database, cluster_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT status, member_count, entity_id FROM face_clusters WHERE id = ?", (cluster_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def instance_cluster(db: Database, instance_id: str) -> str | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT cluster_id FROM face_instances WHERE id = ?", (instance_id,)
        ).fetchone()
    return row["cluster_id"] if row else None


def vector_cluster(vectors: SqliteVectorRepository, instance_id: str) -> object:
    record = vectors.get(space="visual", source_type="face_instance", source_id=instance_id)
    return record.metadata.get("cluster_id") if record else None


# ---- 整表重算 ----------------------------------------------------------------------


def seed_two_clusters(db: Database, vectors: SqliteVectorRepository) -> None:
    """两个 pending 簇、各两张脸，四张脸的向量完全一样——重聚类该把它们并成一个。"""
    add_cluster(db, "clu_1", member_count=2)
    add_cluster(db, "clu_2", member_count=2)
    for instance_id, cluster_id, index in (
        ("ins_1", "clu_1", 1),
        ("ins_2", "clu_1", 2),
        ("ins_3", "clu_2", 3),
        ("ins_4", "clu_2", 4),
    ):
        add_instance(db, instance_id, cluster_id=cluster_id, index=index)
        add_vector(vectors, instance_id, cluster_id=cluster_id)


def test_two_clusters_of_the_same_face_are_merged_into_one(
    db: Database,
    faces: SqliteFaceRepository,
    vectors: SqliteVectorRepository,
    recluster: ReclusterFaces,
):
    """四张一模一样的脸分在两个簇里，重算之后只剩一个簇、成员数是 4。

    落选的那个簇**不是被删掉**，而是转 ``rejected`` 且成员数归零——历史归属要可查。
    """
    seed_two_clusters(db, vectors)

    result = recluster.execute(scope_id=HOME)

    assert (result.instances, result.clusters, result.noise) == (4, 1, 0)
    assert result.retired_clusters == 1
    assert cluster_row(db, "clu_1") == {"status": "pending", "member_count": 4, "entity_id": None}
    assert cluster_row(db, "clu_2") == {"status": "rejected", "member_count": 0, "entity_id": None}
    assert [instance_cluster(db, f"ins_{index}") for index in (1, 2, 3, 4)] == ["clu_1"] * 4
    assert faces.list_prototypes("clu_1")


def test_the_vector_metadata_follows_the_new_cluster(
    db: Database, vectors: SqliteVectorRepository, recluster: ReclusterFaces
):
    """向量 metadata 里的 ``cluster_id`` 也要改——留着旧簇 id 的投影比没有投影更坏。

    它不报错，只是让任何按 metadata 找簇的下游查到一堆已经被撤掉的 id。
    """
    seed_two_clusters(db, vectors)

    recluster.execute(scope_id=HOME)

    assert [vector_cluster(vectors, f"ins_{index}") for index in (1, 2, 3, 4)] == ["clu_1"] * 4


def test_a_confirmed_cluster_survives_with_its_own_id(
    db: Database, vectors: SqliteVectorRepository, recluster: ReclusterFaces
):
    """**对照臂**：分组里有已确认的簇时，保留的是**它**，不是成员顺序里的第一个。

    换掉一个已确认簇的 id，提及 / 事件参与者 / 画像会全部指向一个被撤掉的簇——
    那种断裂不报错，只是那个人从此查不到自己的照片。
    """
    add_cluster(db, "clu_1", member_count=2)
    add_person(db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(db, "clu_2", status="confirmed", entity_id="ent_1", member_count=2)
    for instance_id, cluster_id, index in (
        ("ins_1", "clu_1", 1),
        ("ins_2", "clu_1", 2),
        ("ins_3", "clu_2", 3),
        ("ins_4", "clu_2", 4),
    ):
        add_instance(db, instance_id, cluster_id=cluster_id, index=index)
        add_vector(vectors, instance_id, cluster_id=cluster_id)

    recluster.execute(scope_id=HOME)

    assert cluster_row(db, "clu_2") == {
        "status": "confirmed",
        "member_count": 4,
        "entity_id": "ent_1",
    }
    assert cluster_row(db, "clu_1")["status"] == "rejected"
    assert [instance_cluster(db, f"ins_{index}") for index in (1, 2, 3, 4)] == ["clu_2"] * 4


def test_an_empty_scope_changes_nothing(db: Database, recluster: ReclusterFaces):
    """一张可用的脸都没有时**什么都不做**：那时「把所有簇撤掉」不是重聚类，是清库。"""
    add_cluster(db, "clu_1", member_count=0)

    result = recluster.execute(scope_id=HOME)

    assert result.instances == 0
    assert result.retired_clusters == 0
    assert cluster_row(db, "clu_1") == {"status": "pending", "member_count": 0, "entity_id": None}


def test_the_quality_gate_keeps_low_quality_faces_out_of_the_rebuild(
    db: Database,
    faces: SqliteFaceRepository,
    vectors: SqliteVectorRepository,
    recluster: ReclusterFaces,
):
    """质量门在 ``execute`` 的参数上：默认 0.30，抬到 0.95 时那两张脸整批不参与。"""
    seed_two_clusters(db, vectors)

    result = recluster.execute(scope_id=HOME, minimum_quality=0.95)

    assert result.instances == 0
    assert cluster_row(db, "clu_1")["member_count"] == 2


# ---- 认领不确定的脸 ----------------------------------------------------------------


def seed_confirmed_pair(db: Database, faces: SqliteFaceRepository, vectors: SqliteVectorRepository) -> None:
    """一个已确认、两张脸的簇，原型就是 ``FRONT``。"""
    add_cluster(db, "clu_1", status="confirmed", member_count=2)
    add_instance(db, "ins_1", cluster_id="clu_1", index=1)
    add_instance(db, "ins_2", cluster_id="clu_1", index=2)
    add_vector(vectors, "ins_1", cluster_id="clu_1")
    add_vector(vectors, "ins_2", cluster_id="clu_1")
    faces.refresh_prototypes("clu_1")


def test_an_uncertain_face_joins_the_confirmed_cluster_it_resembles(
    db: Database,
    faces: SqliteFaceRepository,
    vectors: SqliteVectorRepository,
    recluster: ReclusterFaces,
):
    """一张不确定的脸（``validity='uncertain'`` 且没有簇）向量几乎一样 → 被认领。

    1.0 ``_attach_uncertain_faces``：**只认领、绝不新建簇**——单独立簇会造出一堆只有
    一张脸的候选。
    """
    seed_confirmed_pair(db, faces, vectors)
    add_instance(db, "ins_u", cluster_id=None, validity="uncertain", index=5)
    add_vector(vectors, "ins_u", cluster_id=None)

    result = recluster.execute(scope_id=HOME)

    assert result.attached_uncertain == 1
    assert instance_cluster(db, "ins_u") == "clu_1"
    assert cluster_row(db, "clu_1")["member_count"] == 3


def test_an_uncertain_face_that_resembles_nobody_is_left_alone(
    db: Database,
    faces: SqliteFaceRepository,
    vectors: SqliteVectorRepository,
    recluster: ReclusterFaces,
):
    """**对照臂一**：相似度低于 0.75 就不认领。"""
    seed_confirmed_pair(db, faces, vectors)
    add_instance(db, "ins_u", cluster_id=None, embedding=FAR, validity="uncertain", index=5)
    add_vector(vectors, "ins_u", cluster_id=None)

    result = recluster.execute(scope_id=HOME)

    assert result.attached_uncertain == 0
    assert instance_cluster(db, "ins_u") is None


def test_a_cluster_with_a_single_member_is_not_an_attach_target(
    db: Database,
    faces: SqliteFaceRepository,
    vectors: SqliteVectorRepository,
    recluster: ReclusterFaces,
):
    """**对照臂二**：认领目标至少两张脸（``>= 2``）——一张脸的簇还没有稳定的代表向量。"""
    add_cluster(db, "clu_1", status="confirmed", member_count=1)
    add_instance(db, "ins_1", cluster_id="clu_1", index=1)
    add_vector(vectors, "ins_1", cluster_id="clu_1")
    faces.refresh_prototypes("clu_1")
    add_instance(db, "ins_u", cluster_id=None, validity="uncertain", index=5)
    add_vector(vectors, "ins_u", cluster_id=None)

    result = recluster.execute(scope_id=HOME)

    assert FACE_ATTACH_MINIMUM_MEMBERS == 2
    assert result.attached_uncertain == 0
    assert instance_cluster(db, "ins_u") is None


def test_the_attach_threshold_and_margin_are_the_ones_1_0_used():
    """两个常数是**产品判据**，不是可调参数：0.75 与 0.08（1.0 ``db.py:5051`` / ``:5101``）。"""
    assert FACE_ATTACH_THRESHOLD == 0.75
    assert FACE_ATTACH_MARGIN == 0.08


# ---- 撤掉孤儿候选 ------------------------------------------------------------------


def test_a_candidate_whose_cluster_is_gone_is_retired(
    db: Database, vectors: SqliteVectorRepository, recluster: ReclusterFaces
):
    """簇没了之后，那个候选人物不能再留在人物列表里显示成「待命名成员」。

    **转状态而不是删行**：这个人在确认之前的所有痕迹都要留在库里可查。
    断言的是 status **与那句 summary 的原文**——只断言 status 的话，把文案换掉
    （那是存在 ``entities.summary`` 里的用户可见内容）也照样绿。
    """
    seed_two_clusters(db, vectors)
    add_cluster(db, "clu_9", member_count=0)
    add_person(db, "ent_orphan", name="待确认人物簇 · clu_9", status="pending")

    result = recluster.execute(scope_id=HOME)

    with db.connect() as connection:
        row = connection.execute("SELECT status, summary FROM entities WHERE id = 'ent_orphan'").fetchone()
    assert dict(row) == {"status": "rejected", "summary": RETIRED_CANDIDATE_SUMMARY}
    assert result.retired_candidates == 1


def test_a_cluster_with_no_instances_at_all_is_not_retired(
    db: Database, vectors: SqliteVectorRepository, recluster: ReclusterFaces
):
    """1.0 的 ``stale_ids`` 只从**实例**反查（``db.py:5019-5021``）——一个一张脸都不挂的
    簇根本不在那次重算的视野里，它的状态保持原样。

    这是**照抄行为**，不是遗漏：2.0 的实现同样只撤「有实例、但实例都被并走了」的簇。
    真正的清理动作落在实体那一侧（``retire_unclustered_candidates``）——那个候选人物
    会因为没有活着的簇被转成 rejected。
    """
    seed_two_clusters(db, vectors)
    add_cluster(db, "clu_empty", member_count=0)

    result = recluster.execute(scope_id=HOME)

    assert result.retired_clusters == 1
    assert cluster_row(db, "clu_1")["member_count"] == 4
    assert cluster_row(db, "clu_empty") == {
        "status": "pending",
        "member_count": 0,
        "entity_id": None,
    }


def test_a_confirmed_person_is_never_retired(
    db: Database,
    faces: SqliteFaceRepository,
    vectors: SqliteVectorRepository,
    recluster: ReclusterFaces,
):
    """**对照臂**：已确认的人不会因为一次聚类重算而消失。

    它的簇在重算里被保住（``keep_id``），实体也因此有活着的簇——两道保险。
    这里单独断言实体状态：把 SQL 里的 ``status != 'confirmed'`` 去掉，这条会红。
    """
    seed_confirmed_pair(db, faces, vectors)
    add_person(db, "ent_1", name="妈妈", status="confirmed")

    recluster.execute(scope_id=HOME)

    with db.connect() as connection:
        row = connection.execute("SELECT status FROM entities WHERE id = 'ent_1'").fetchone()
    assert row["status"] == "confirmed"


# ---- 「不自动跑」是结构事实 ----------------------------------------------------------


IMPORT_CHAIN = (
    "application/media/import_assets.py",
    "application/media/import_album.py",
    "application/identity/enroll_face.py",
    "api/routes/media.py",
)


def test_nothing_on_the_import_path_can_trigger_the_global_pass():
    """**导入链上的模块一个都不许提 recluster。**

    这不是形式化检查：``recluster_faces`` 做的是整表重算，挂在导入链上意味着每导一批
    照片都可能重写全库的身份归属。用户裁决③要的是「显式维护入口」，而「有人顺手把它接
    进导入」正是这条裁决最容易失效的方式——那时没有任何一条行为断言会红。
    """
    root = Path(__file__).resolve().parents[2] / "src" / "sentrix"
    offenders = [
        relative for relative in IMPORT_CHAIN if "recluster" in (root / relative).read_text(encoding="utf-8")
    ]

    assert offenders == []
