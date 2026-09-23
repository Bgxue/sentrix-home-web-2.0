"""迁移 0006 的 schema：身份表族 + 观察的人物快照列。

对齐 ``test_timeline_schema.py`` 的理由：``test_migrations.py`` 验的是迁移**执行器**
（应用一次、失败回滚、没有 .sql 时报错），它在 0006 写出来之前就是绿的，验不了 0006
建了什么。这里断言的是「建出来的东西真的能用」——三条 UNIQUE 约束、一处可空性、
一列在 1.0 需要整表重建才拿到的可空外键。

``test_face_instances_cluster_id_is_nullable`` 是这套里最贵的一条：1.0 为把
``cluster_id`` 从 NOT NULL 改成可空，写了一整段 ``CREATE TABLE _migrated`` /
``INSERT SELECT`` / ``DROP`` / ``RENAME``（``db.py:1257-1311``），因为 SQLite 不能
就地摘掉 NOT NULL。2.0 的迁移是编号的、一次建对，这条断言把「一次建对」钉住——
写成 NOT NULL 时它立刻红，而不是等到真有一张低置信度人脸要落库才炸。
"""

from __future__ import annotations

import sqlite3

import pytest

from sentrix.infrastructure.persistence.database import Database

EXPECTED_TABLES = {
    "entities",
    "entity_revisions",
    "entity_properties",
    "face_clusters",
    "face_instances",
    "face_prototypes",
    "person_appearance_evidence",
    "entity_mentions",
    "entity_observations",
    "relationships",
    "event_participants",
    "event_entities",
}

# 2026-09-23 用户裁决解的冻（person_moments / person_portrait_revisions）与仍冻的
# person_insight_runs 都在这条断言里：三张表的去留是一个决定，不能只断言去的那半。
UNFROZEN_BY_RULING = {"person_moments", "person_portrait_revisions"}
STILL_FROZEN = {"person_insight_runs"}


@pytest.fixture
def identity_db(migrations_ready_db: Database):
    """迁移就绪的库 + 一行最小可引用的父数据（assets / observations / events）。

    外键是打开的（``Database.connect`` 每次都 ``PRAGMA foreign_keys = ON``），
    所以 UNIQUE 与 FK 两类断言都必须有真父行才谈得上「约束在生效」。
    """
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES ('asset_1', 'album1', 'a.jpg', 'image', '/tmp/a.jpg', '2026-01-01', '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
            " VALUES ('obs_1', 'album1', 'asset_1', 'vlm', '2026-01-01', '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO events (id, scope_id, title, created_at, updated_at)"
            " VALUES ('evt_1', 'album1', '事件', '2026-01-01', '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO memory_spaces (id, name, created_at, updated_at)"
            " VALUES ('album1', '测试空间', '2026-01-01', '2026-01-01')"
        )
    return migrations_ready_db


def _insert_entity(connection: sqlite3.Connection, entity_id: str, name: str) -> None:
    connection.execute(
        "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, created_at, updated_at)"
        " VALUES (?, 'album1', 'person', ?, 'pending', '2026-01-01', '2026-01-01')",
        (entity_id, name),
    )


def _insert_cluster(connection: sqlite3.Connection, cluster_id: str) -> None:
    connection.execute(
        "INSERT INTO face_clusters (id, scope_id, status, created_at, updated_at)"
        " VALUES (?, 'album1', 'pending', '2026-01-01', '2026-01-01')",
        (cluster_id,),
    )


def _insert_face_instance(connection: sqlite3.Connection, instance_id: str, cluster_id: str | None) -> None:
    connection.execute(
        "INSERT INTO face_instances"
        " (id, asset_id, observation_id, cluster_id, created_at)"
        " VALUES (?, 'asset_1', 'obs_1', ?, '2026-01-01')",
        (instance_id, cluster_id),
    )


def test_0006_creates_the_identity_tables(identity_db: Database):
    with identity_db.connect() as connection:
        names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master")}

    assert names >= EXPECTED_TABLES


def test_portrait_tables_follow_the_ruling(identity_db: Database):
    """解冻的两张要建，冻结的那张不许建。

    只断言「解冻的两张在」会让「顺手把 person_insight_runs 也建了」静默通过——
    那等于绕开裁决把那张表的冻结撤了，而 frozen.md 还写着它冻结。
    """
    with identity_db.connect() as connection:
        names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master")}

    assert names >= UNFROZEN_BY_RULING
    assert not (names & STILL_FROZEN)


def test_face_instances_cluster_id_is_nullable(identity_db: Database):
    """1.0 为这一列写过一次整表重建；2.0 必须一次建对。

    可空不是为了好看：低置信度人脸要能作为**证据**落库而不被塞进某个簇。
    """
    with identity_db.connect() as connection:
        columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(face_instances)")}

    assert columns["cluster_id"]["notnull"] == 0


def test_low_confidence_face_lands_without_a_cluster(identity_db: Database):
    """承上条：可空性要在真写入里成立，而不只是 PRAGMA 里那个 0。"""
    with identity_db.connect() as connection, connection:
        _insert_face_instance(connection, "inst_orphan", None)
        stored = connection.execute(
            "SELECT cluster_id FROM face_instances WHERE id = 'inst_orphan'"
        ).fetchone()

    assert stored["cluster_id"] is None


def test_face_prototypes_reject_a_second_row_for_the_same_pose_bucket(identity_db: Database):
    """UNIQUE(cluster_id, pose_bucket) 是「同桶只留最好那张」在 SQL 层的凭证。

    只写在写入方的字典逻辑里，别的写入路径绕过去就没人拦——所以这条直接撞约束。
    """
    with identity_db.connect() as connection, connection:
        _insert_cluster(connection, "clu_1")
        _insert_face_instance(connection, "inst_1", "clu_1")
        _insert_face_instance(connection, "inst_2", "clu_1")
        connection.execute(
            "INSERT INTO face_prototypes"
            " (id, cluster_id, face_instance_id, pose_bucket, created_at, updated_at)"
            " VALUES ('pro_1', 'clu_1', 'inst_1', 'frontal', '2026-01-01', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO face_prototypes"
                " (id, cluster_id, face_instance_id, pose_bucket, created_at, updated_at)"
                " VALUES ('pro_2', 'clu_1', 'inst_2', 'frontal', '2026-01-01', '2026-01-01')"
            )


def test_entity_mentions_reject_a_duplicate_but_allow_many_null_face_instances(identity_db: Database):
    """UNIQUE 里的 face_instance_id 可空，而 SQLite 的 UNIQUE 允许多个 NULL。

    「同一条观察里同一个实体被提了两次」要被拦，但「同一实体在同一条观察里有多条
    没有对应人脸实例的提及」不能被拦——1.0 的 DDL 就是这个语义，照搬要照搬到这里。
    """
    with identity_db.connect() as connection, connection:
        _insert_entity(connection, "ent_1", "妈妈")
        _insert_face_instance(connection, "inst_1", None)
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id, created_at)"
            " VALUES ('men_1', 'ent_1', 'obs_1', 'inst_1', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id, created_at)"
                " VALUES ('men_2', 'ent_1', 'obs_1', 'inst_1', '2026-01-01')"
            )
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id, created_at)"
            " VALUES ('men_3', 'ent_1', 'obs_1', NULL, '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id, created_at)"
            " VALUES ('men_4', 'ent_1', 'obs_1', NULL, '2026-01-01')"
        )
        rows = connection.execute(
            "SELECT count(*) AS n FROM entity_mentions WHERE entity_id = 'ent_1'"
        ).fetchone()

    assert rows["n"] == 3


def test_person_appearance_evidence_is_unique_per_person_face_and_model(identity_db: Database):
    with identity_db.connect() as connection, connection:
        _insert_entity(connection, "ent_1", "妈妈")
        _insert_face_instance(connection, "inst_1", None)
        connection.execute(
            "INSERT INTO person_appearance_evidence"
            " (id, person_id, face_instance_id, observation_id, asset_id, model_name, created_at, updated_at)"
            " VALUES ('app_1', 'ent_1', 'inst_1', 'obs_1', 'asset_1', 'vlm-a', '2026-01-01', '2026-01-01')"
        )
        # 换一个模型名是合法的：同一张脸可以被两个模型各描述一次。
        connection.execute(
            "INSERT INTO person_appearance_evidence"
            " (id, person_id, face_instance_id, observation_id, asset_id, model_name, created_at, updated_at)"
            " VALUES ('app_2', 'ent_1', 'inst_1', 'obs_1', 'asset_1', 'vlm-b', '2026-01-01', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO person_appearance_evidence"
                " (id, person_id, face_instance_id, observation_id, asset_id,"
                "  model_name, created_at, updated_at)"
                " VALUES ('app_3', 'ent_1', 'inst_1', 'obs_1', 'asset_1',"
                " 'vlm-a', '2026-01-01', '2026-01-01')"
            )


def test_observations_gained_people_json(identity_db: Database):
    with identity_db.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(observations)")}

    assert "people_json" in columns


def test_entities_carries_the_three_state_columns_inline(identity_db: Database):
    """1.0 的三列是运行期 ALTER 补的（``db.py:1240-1243``），2.0 一次建对。

    列的**默认值**也要钉：``name_state`` 的 ``anonymous`` 与 ``identity_state`` 的
    ``clustered`` 是簇审阅状态机的初态，写错不会报错，只会让「待命名」判定失效。
    """
    with identity_db.connect() as connection, connection:
        _insert_entity(connection, "ent_1", "妈妈")
        row = connection.execute(
            "SELECT identity_state, role_state, name_state FROM entities WHERE id = 'ent_1'"
        ).fetchone()

    assert (row["identity_state"], row["role_state"], row["name_state"]) == (
        "clustered",
        "unknown",
        "anonymous",
    )


def test_event_participants_references_entities(identity_db: Database):
    """0005 不建这张表的理由就是它 REFERENCES entities；这条钉住那条依赖真的闭合了。"""
    with (
        identity_db.connect() as connection,
        connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            "INSERT INTO event_participants"
            " (id, event_id, person_id, role, created_at, updated_at)"
            " VALUES ('par_1', 'evt_1', 'ent_missing', 'visible_subject', '2026-01-01', '2026-01-01')"
        )
