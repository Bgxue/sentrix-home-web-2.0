"""簇审阅用例：确认 / 拒绝 / 合并 / 拆分四件事在库上留下的痕迹。

对应 1.0 ``backend/db.py`` 的 ``confirm_face_cluster`` :5152、``_merge_cluster_into_person``
:5194、``reject_face_cluster`` :5579、``merge_face_clusters`` :4776、``split_face_instance``
:4797、``delete_person_candidate`` :5290。

**事件侧的那两件事（参与者投影 / 重分段）不在这里验。** 1.0 的 ``confirm`` 在同一个
函数里既写身份（entities / mentions）又刷事件参与者、重分段、重建人物画像；2.0 把它们
拆开：``ConfirmedPersonEvents`` 是 ``ReviewCluster`` 的一个**必填**协作者，在
``confirm`` 与 ``_merge_into_person`` 写完提及之后各调一次。它的行为与阈值在
``tests/integration/test_event_participants.py`` 里单独钉，这里只保证四件事本身的痕迹——
本文件的事件夹具是空的，所以那两次调用在这里都是空操作（``refresh_participants`` 查不到
任何事件、``resegment`` 数不出两个事件），不影响下面的断言。

**四件里有几件写审计**：merge 与 split 写（1.0 原文），confirm 走同名合并那一路时写的也是
``face_cluster_merge``。reject 不可能写——它删的正是那个实体的 ``entity_revisions`` 行。
"""

from __future__ import annotations

import json

import pytest

from sentrix.application.identity.review_cluster import (
    ClusterNotFoundError,
    InvalidReviewRequestError,
    ReviewCluster,
    ReviewConflictError,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository

NOW = "2026-09-23T10:00:00+00:00"

HOME = "album1"
OTHER = "album2"


@pytest.fixture
def seeded_db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        for scope_id, include in ((HOME, 1), (OTHER, 0)):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (scope_id, scope_id, include, NOW, NOW),
            )
        for asset_id, scope_id in (("asset_1", HOME), ("asset_2", OTHER)):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, scope_id, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
        for index in (1, 2, 3):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
                " VALUES (?, ?, ?, 'vlm', ?, ?)",
                (f"obs_{index}", HOME, "asset_1", NOW, NOW),
            )
    return migrations_ready_db


@pytest.fixture
def review(seeded_db: Database, confirmed_person_events) -> ReviewCluster:
    return ReviewCluster(
        faces=SqliteFaceRepository(seeded_db),
        entities=SqliteEntityRepository(seeded_db),
        facts=SqliteFactRepository(seeded_db),
        events=confirmed_person_events(seeded_db),
    )


# ---- 造数 ------------------------------------------------------------------------


def add_entity(
    db: Database,
    entity_id: str,
    *,
    name: str,
    status: str = "pending",
    scope_id: str = HOME,
    family_role: str | None = None,
    confidence: float = 0.0,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, family_role, summary,"
            "  confidence, created_at, updated_at)"
            " VALUES (?, ?, 'person', ?, ?, ?, '', ?, ?, ?)",
            (entity_id, scope_id, name, status, family_role, confidence, NOW, NOW),
        )


def add_cluster(
    db: Database,
    cluster_id: str,
    *,
    scope_id: str = HOME,
    status: str = "pending",
    entity_id: str | None = None,
    member_count: int = 0,
    revision: int = 1,
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, representative_embedding_json, member_count,"
            "  revision, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?)",
            (cluster_id, scope_id, status, entity_id, member_count, revision, NOW, NOW),
        )


def add_instance(
    db: Database,
    instance_id: str,
    *,
    cluster_id: str | None,
    observation_id: str = "obs_1",
    asset_id: str = "asset_1",
    embedding: tuple[float, ...] = (1.0, 0.0),
    detection_confidence: float = 0.9,
    quality: float = 0.8,
    pose_bucket: str = "frontal",
) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances"
            " (id, asset_id, observation_id, cluster_id, embedding_json, detection_confidence,"
            "  quality, pose_bucket, embedding_model, embedding_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'buffalo_l', 'w600k_r50', ?)",
            (
                instance_id,
                asset_id,
                observation_id,
                cluster_id,
                json.dumps(list(embedding)),
                detection_confidence,
                quality,
                pose_bucket,
                NOW,
            ),
        )


def add_prototype(db: Database, cluster_id: str, face_instance_id: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_prototypes"
            " (id, cluster_id, face_instance_id, pose_bucket, embedding_json, quality,"
            "  model_name, model_version, created_at, updated_at)"
            " VALUES (?, ?, ?, 'frontal', '[1.0, 0.0]', 0.8, 'buffalo_l', 'w600k_r50', ?, ?)",
            (f"proto_{face_instance_id}", cluster_id, face_instance_id, NOW, NOW),
        )


def add_fact(db: Database, fact_id: str, *, subject: str, predicate: str, value: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO facts"
            " (id, scope_id, subject, predicate, object, status, confidence, evidence_ids_json,"
            "  revision, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'active', 0.5, '[]', 1, ?, ?)",
            (fact_id, HOME, subject, predicate, value, NOW, NOW),
        )


def add_revision(db: Database, entity_id: str, field_name: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_revisions"
            " (id, entity_id, field_name, old_value, new_value, source, evidence_ids_json, created_at)"
            " VALUES (?, ?, ?, NULL, NULL, 'user', '[]', ?)",
            (f"rev_{field_name}", entity_id, field_name, NOW),
        )


def query(db: Database, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def scalar(db: Database, sql: str, params: tuple[object, ...] = ()) -> object:
    with db.connect() as connection:
        row = connection.execute(sql, params).fetchone()
    return row[0] if row else None


# ---- confirm ---------------------------------------------------------------------


def test_confirm_without_a_name_is_a_400(review: ReviewCluster, seeded_db: Database):
    """空名字不是「不改名」，是调用方出错。1.0 在路由层挡（``app.py:1977``），2.0 挡在用例里。"""
    add_cluster(seeded_db, "clu_1", entity_id=None)

    for bad_name in ("", "   "):
        with pytest.raises(InvalidReviewRequestError) as error:
            review.confirm("clu_1", name=bad_name)
        assert error.value.status_code == 400


def test_the_name_is_checked_before_the_cluster_lookup(review: ReviewCluster):
    """两者都错时报的是名字错（400），不是 404——1.0 的路由顺序就是这样。"""
    with pytest.raises(InvalidReviewRequestError):
        review.confirm("clu_missing", name="")


def test_confirm_of_a_missing_cluster_is_a_404(review: ReviewCluster):
    with pytest.raises(ClusterNotFoundError) as error:
        review.confirm("clu_missing", name="妈妈")
    assert error.value.status_code == 404


def test_confirm_marks_the_person_and_the_cluster(review: ReviewCluster, seeded_db: Database):
    """一次确认要同时改两行：实体转 confirmed（名字 / 角色 / ``confidence = MAX(…,1)``），
    簇转 confirmed 且 ``revision + 1``。

    ``MAX(confidence, 1)`` 的含义是「确认就是满置信」：用户按下的那一下比任何模型分数都强。
    """
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1", confidence=0.4)
    add_cluster(seeded_db, "clu_1", entity_id="ent_1", revision=3)

    result = review.confirm("clu_1", name="妈妈", family_role="母亲")

    assert result.merged_into is None
    assert result.entity.id == "ent_1"
    assert result.entity.canonical_name == "妈妈"
    assert result.entity.status == "confirmed"
    assert result.entity.family_role == "母亲"
    assert result.entity.confidence == 1.0
    cluster = query(seeded_db, "SELECT * FROM face_clusters WHERE id = 'clu_1'")
    assert cluster[0]["status"] == "confirmed"
    assert cluster[0]["entity_id"] == "ent_1"
    assert cluster[0]["revision"] == 4


def test_confirm_of_a_cluster_without_an_entity_creates_one(review: ReviewCluster, seeded_db: Database):
    """簇没有实体时（1.0 允许的中间态）另建一个，summary 是 1.0 的原文。"""
    add_cluster(seeded_db, "clu_1", entity_id=None)
    add_instance(seeded_db, "ins_1", cluster_id="clu_1")

    result = review.confirm("clu_1", name="爸爸")

    entity = query(seeded_db, "SELECT * FROM entities WHERE id = ?", (result.entity.id,))
    assert len(entity) == 1
    assert entity[0]["scope_id"] == HOME
    assert entity[0]["status"] == "confirmed"
    assert scalar(seeded_db, "SELECT summary FROM entities WHERE id = ?", (result.entity.id,)) == (
        "已确认人物，出现在 1 条观察中"
    )


def test_confirm_writes_a_mention_for_every_instance(review: ReviewCluster, seeded_db: Database):
    """簇内**每个实例**都写一条提及，置信度取该实例的检测置信度。

    两张脸落在同一条观察上时是两条提及（``UNIQUE(entity, observation, face_instance)``
    里带 ``face_instance_id`` 就是为了这个）：写成「每条观察一条」会丢掉一张脸。
    """
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1")
    add_instance(seeded_db, "ins_1", cluster_id="clu_1", observation_id="obs_1", detection_confidence=0.91)
    add_instance(seeded_db, "ins_2", cluster_id="clu_1", observation_id="obs_1", detection_confidence=0.92)
    add_instance(seeded_db, "ins_3", cluster_id="clu_1", observation_id="obs_2", detection_confidence=0.93)

    review.confirm("clu_1", name="妈妈")

    mentions = query(
        seeded_db,
        "SELECT face_instance_id, observation_id, confidence FROM entity_mentions WHERE entity_id = 'ent_1'",
    )
    assert {(item["face_instance_id"], item["observation_id"]) for item in mentions} == {
        ("ins_1", "obs_1"),
        ("ins_2", "obs_1"),
        ("ins_3", "obs_2"),
    }
    assert sorted(item["confidence"] for item in mentions) == [0.91, 0.92, 0.93]


def test_confirm_summary_counts_distinct_observations(review: ReviewCluster, seeded_db: Database):
    """summary 里的数字是**去重后**的观察条数，不是实例条数。"""
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1")
    for instance_id, observation_id in (("ins_1", "obs_1"), ("ins_2", "obs_1"), ("ins_3", "obs_3")):
        add_instance(seeded_db, instance_id, cluster_id="clu_1", observation_id=observation_id)

    review.confirm("clu_1", name="妈妈")

    assert scalar(seeded_db, "SELECT summary FROM entities WHERE id = 'ent_1'") == (
        "已确认人物，出现在 2 条观察中"
    )


def test_confirm_writes_the_family_role_fact(review: ReviewCluster, seeded_db: Database):
    """``family_role`` 非空才写事实，谓词是「家庭角色」，置信度 1.0，证据是那些观察。"""
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1")
    add_instance(seeded_db, "ins_1", cluster_id="clu_1", observation_id="obs_1")
    add_instance(seeded_db, "ins_2", cluster_id="clu_1", observation_id="obs_2")

    review.confirm("clu_1", name="妈妈", family_role="母亲")

    facts = query(seeded_db, "SELECT * FROM facts WHERE subject = '妈妈'")
    assert len(facts) == 1
    assert facts[0]["predicate"] == "家庭角色"
    assert facts[0]["object"] == "母亲"
    assert facts[0]["confidence"] == 1.0
    assert facts[0]["status"] == "active"
    assert sorted(json.loads(str(facts[0]["evidence_ids_json"]))) == ["obs_1", "obs_2"]


def test_confirm_without_a_family_role_writes_no_fact(review: ReviewCluster, seeded_db: Database):
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1")
    add_instance(seeded_db, "ins_1", cluster_id="clu_1")

    review.confirm("clu_1", name="妈妈")

    assert scalar(seeded_db, "SELECT COUNT(*) FROM facts") == 0


def test_confirm_folds_into_an_existing_person_with_the_same_name(review: ReviewCluster, seeded_db: Database):
    """同名已确认人物存在 → 不再建第二个人，而是把这个簇并进他。

    并进去的部分：实例改挂到对方的已确认簇、提及写给对方、源簇 rejected 且
    ``member_count`` 归零、写一条 ``face_cluster_merge`` 审计（来源 ``user_name_merge``）。
    返回里带 ``merged_into``，界面靠它提示「已并入某某」。
    """
    add_entity(seeded_db, "ent_old", name="妈妈", status="confirmed", scope_id=HOME)
    add_entity(seeded_db, "ent_new", name="待确认人物簇 · clu_2")
    add_cluster(seeded_db, "clu_old", status="confirmed", entity_id="ent_old", member_count=2)
    add_cluster(seeded_db, "clu_2", entity_id="ent_new", member_count=1)
    add_instance(seeded_db, "ins_old", cluster_id="clu_old")
    add_instance(seeded_db, "ins_2", cluster_id="clu_2", observation_id="obs_2", detection_confidence=0.7)

    result = review.confirm("clu_2", name="妈妈")

    assert result.merged_into == "ent_old"
    assert result.entity.id == "ent_old"
    assert scalar(seeded_db, "SELECT cluster_id FROM face_instances WHERE id = 'ins_2'") == "clu_old"
    assert scalar(seeded_db, "SELECT cluster_id FROM face_instances WHERE id = 'ins_2'") is not None
    assert query(
        seeded_db,
        "SELECT face_instance_id, confidence FROM entity_mentions WHERE entity_id = 'ent_old'",
    ) == [{"face_instance_id": "ins_2", "confidence": 0.7}]
    source = query(seeded_db, "SELECT * FROM face_clusters WHERE id = 'clu_2'")[0]
    assert (source["status"], source["member_count"], source["entity_id"]) == (
        "rejected",
        0,
        "ent_old",
    )
    merge_revisions = query(
        seeded_db,
        "SELECT * FROM entity_revisions WHERE entity_id = 'ent_old' AND field_name = 'face_cluster_merge'",
    )
    assert len(merge_revisions) == 1
    assert merge_revisions[0]["source"] == "user_name_merge"
    assert merge_revisions[0]["old_value"] == "clu_2"
    assert merge_revisions[0]["new_value"] == "clu_old"


def test_reconfirming_a_cluster_with_its_own_name_keeps_it(review: ReviewCluster, seeded_db: Database):
    """**修 1.0 的缺陷（用户裁决）**：对一个已确认的簇用它**自己的名字**再确认一次，
    簇必须原样留着。

    1.0 ``db.py:5157`` 的同名查询命中即合并，**没有排除「命中的就是本簇的实体」**：
    于是重复确认走进 ``_merge_cluster_into_person``，把自己置成 ``rejected``、
    ``member_count`` 归零——**这个人的脸全丢了**，而且从界面看是「这个簇消失了」。
    同一作者在 ``confirm_person_entity``（``db.py:5251``）里写了
    ``existing["entity_id"] != entity_id`` 这条守卫，``confirm_face_cluster`` 漏了。
    这不是新行为，是把 1.0 自己那条判据补齐。依据见 ``s5-deviations.md``。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_1", status="confirmed", entity_id="ent_1", member_count=1)
    add_instance(seeded_db, "ins_1", cluster_id="clu_1")

    result = review.confirm("clu_1", name="妈妈")

    assert result.merged_into is None
    assert result.entity.id == "ent_1"
    assert query(
        seeded_db, "SELECT status, member_count, entity_id FROM face_clusters WHERE id = 'clu_1'"
    ) == [{"status": "confirmed", "member_count": 1, "entity_id": "ent_1"}]
    assert scalar(seeded_db, "SELECT cluster_id FROM face_instances WHERE id = 'ins_1'") == "clu_1"
    # 自合并不该留下一条「并入了自己」的审计。
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entity_revisions") == 0


def test_reconfirming_through_one_of_its_own_aliases_keeps_it(review: ReviewCluster, seeded_db: Database):
    """同一个人的**别名**命中的还是它自己——同样不能自合并。

    别名那条路要走到 ``find_confirmed_person_by_name`` 的第二段（扫 aliases），
    与精确名是两条不同的代码路径，所以这条要单独钉。
    """
    add_entity(seeded_db, "ent_1", name="母亲", status="confirmed")
    SqliteEntityRepository(seeded_db).set_person_aliases("ent_1", ["妈妈"])
    add_cluster(seeded_db, "clu_1", status="confirmed", entity_id="ent_1", member_count=2)

    result = review.confirm("clu_1", name="妈妈")

    assert result.merged_into is None
    assert result.entity.id == "ent_1"
    assert query(seeded_db, "SELECT status, member_count FROM face_clusters WHERE id = 'clu_1'") == [
        {"status": "confirmed", "member_count": 2}
    ]


def test_a_different_cluster_with_the_same_name_still_merges(review: ReviewCluster, seeded_db: Database):
    """反向的一条：守卫只排除**本簇自己的实体**，别人的同名簇照样并。

    没有这一条，上面那条修复可以被实现成「同名一律不合并」而测试仍然全绿——
    而那样等于把同名合并整个功能关掉。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_1", status="confirmed", entity_id="ent_1", member_count=1)
    add_entity(seeded_db, "ent_2", name="待确认人物簇 · clu_2")
    add_cluster(seeded_db, "clu_2", entity_id="ent_2", member_count=1)

    result = review.confirm("clu_2", name="妈妈")

    assert result.merged_into == "ent_1"
    assert query(seeded_db, "SELECT status FROM face_clusters WHERE id = 'clu_2'") == [{"status": "rejected"}]
    assert query(seeded_db, "SELECT status FROM face_clusters WHERE id = 'clu_1'") == [
        {"status": "confirmed"}
    ]


# ---- reject ----------------------------------------------------------------------


def test_reject_of_a_confirmed_cluster_is_a_409(review: ReviewCluster, seeded_db: Database):
    """已确认的人不能被「拒绝」——那是删行，不是审阅；要用改名或合并。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_1", status="confirmed", entity_id="ent_1")

    with pytest.raises(ReviewConflictError) as error:
        review.reject("clu_1")

    assert error.value.status_code == 409
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entities") == 1
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters") == 1


def test_reject_of_a_missing_cluster_is_a_404(review: ReviewCluster):
    with pytest.raises(ClusterNotFoundError):
        review.reject("clu_missing")


def test_reject_deletes_the_candidate_and_keeps_the_faces(review: ReviewCluster, seeded_db: Database):
    """删除的范围与保留的范围都是契约。

    删：实体本身、它的簇、簇的原型、提及 / 属性 / 修订这些挂在实体上的派生行，
    以及用这个名字写的事实。
    留：**人脸实例**（它们是证据），只把 ``cluster_id`` 归 NULL——「这不是一个人」不等于
    「这些脸没存在过」。
    """
    add_entity(seeded_db, "ent_1", name="待确认人物簇 · clu_1")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1", member_count=2)
    add_instance(seeded_db, "ins_1", cluster_id="clu_1")
    add_instance(seeded_db, "ins_2", cluster_id="clu_1", observation_id="obs_2")
    add_prototype(seeded_db, "clu_1", "ins_1")
    add_revision(seeded_db, "ent_1", "canonical_name")
    add_fact(seeded_db, "fact_1", subject="待确认人物簇 · clu_1", predicate="家庭角色", value="母亲")

    review.reject("clu_1")

    assert scalar(seeded_db, "SELECT COUNT(*) FROM entities WHERE id = 'ent_1'") == 0
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_clusters WHERE id = 'clu_1'") == 0
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_prototypes") == 0
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entity_revisions WHERE entity_id = 'ent_1'") == 0
    assert scalar(seeded_db, "SELECT COUNT(*) FROM facts") == 0
    faces = query(seeded_db, "SELECT id, cluster_id FROM face_instances ORDER BY id")
    assert faces == [{"id": "ins_1", "cluster_id": None}, {"id": "ins_2", "cluster_id": None}]


def test_reject_refuses_a_confirmed_entity_even_when_the_cluster_is_not(
    review: ReviewCluster, seeded_db: Database
):
    """判据是**实体**状态不是簇状态：簇还是 pending 而实体已经确认，同样拒绝。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_1", status="pending", entity_id="ent_1")

    with pytest.raises(ReviewConflictError):
        review.reject("clu_1")


# ---- merge -----------------------------------------------------------------------


def test_merge_moves_the_instances_and_retires_the_source(review: ReviewCluster, seeded_db: Database):
    """合并只改 ``face_instances.cluster_id`` 一列，源簇退休，目标原型重算。

    审计写在**目标实体**上：old / new 是两个簇 id——它记的是「哪两个簇合成一个」，
    不是「谁被改名了」。
    """
    add_entity(seeded_db, "ent_1", name="妈妈")
    add_cluster(seeded_db, "clu_target", entity_id="ent_1")
    add_cluster(seeded_db, "clu_source", entity_id=None, status="pending")
    add_instance(seeded_db, "ins_1", cluster_id="clu_source")
    add_instance(seeded_db, "ins_2", cluster_id="clu_source", observation_id="obs_2", quality=0.4)

    result = review.merge(target_cluster_id="clu_target", source_cluster_id="clu_source")

    assert result.id == "clu_target"
    assert query(seeded_db, "SELECT id, cluster_id FROM face_instances ORDER BY id") == [
        {"id": "ins_1", "cluster_id": "clu_target"},
        {"id": "ins_2", "cluster_id": "clu_target"},
    ]
    source = query(seeded_db, "SELECT * FROM face_clusters WHERE id = 'clu_source'")[0]
    assert (source["status"], source["member_count"]) == ("rejected", 0)
    assert scalar(seeded_db, "SELECT member_count FROM face_clusters WHERE id = 'clu_target'") == 2
    assert scalar(seeded_db, "SELECT COUNT(*) FROM face_prototypes WHERE cluster_id = 'clu_target'") == 1
    merge_revisions = query(
        seeded_db, "SELECT * FROM entity_revisions WHERE field_name = 'face_cluster_merge'"
    )
    assert len(merge_revisions) == 1
    assert merge_revisions[0]["entity_id"] == "ent_1"
    assert merge_revisions[0]["source"] == "user_merge"
    assert merge_revisions[0]["old_value"] == "clu_source"
    assert merge_revisions[0]["new_value"] == "clu_target"


def test_merging_a_cluster_into_itself_changes_nothing(review: ReviewCluster, seeded_db: Database):
    """同一个簇合并到自己：返回它，不写审计、不改状态（1.0 的提前返回）。"""
    add_entity(seeded_db, "ent_1", name="妈妈")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1", status="confirmed", member_count=1)

    result = review.merge(target_cluster_id="clu_1", source_cluster_id="clu_1")

    assert result.id == "clu_1"
    assert scalar(seeded_db, "SELECT COUNT(*) FROM entity_revisions") == 0
    assert scalar(seeded_db, "SELECT status FROM face_clusters WHERE id = 'clu_1'") == "confirmed"


def test_merge_across_scopes_is_a_409(review: ReviewCluster, seeded_db: Database):
    """跨记忆空间合并 → 409。两个空间的人本来就不该是同一个人。"""
    add_cluster(seeded_db, "clu_home", scope_id=HOME)
    add_cluster(seeded_db, "clu_other", scope_id=OTHER)

    with pytest.raises(ReviewConflictError) as error:
        review.merge(target_cluster_id="clu_home", source_cluster_id="clu_other")

    assert error.value.status_code == 409


def test_merge_of_two_confirmed_people_is_a_409(review: ReviewCluster, seeded_db: Database):
    """两个都已确认的人不能自动合并：名字已经被人认过，静静并掉就是丢一个人。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_entity(seeded_db, "ent_2", name="姐姐", status="confirmed")
    add_cluster(seeded_db, "clu_1", status="confirmed", entity_id="ent_1")
    add_cluster(seeded_db, "clu_2", status="confirmed", entity_id="ent_2")

    with pytest.raises(ReviewConflictError) as error:
        review.merge(target_cluster_id="clu_1", source_cluster_id="clu_2")

    assert error.value.status_code == 409


def test_merge_of_a_rejected_or_missing_cluster_is_a_404(review: ReviewCluster, seeded_db: Database):
    add_entity(seeded_db, "ent_1", name="妈妈")
    add_cluster(seeded_db, "clu_1", entity_id="ent_1")
    add_cluster(seeded_db, "clu_dead", status="rejected")

    for target, source in (("clu_1", "clu_missing"), ("clu_missing", "clu_1"), ("clu_1", "clu_dead")):
        with pytest.raises(ClusterNotFoundError):
            review.merge(target_cluster_id=target, source_cluster_id=source)


def test_merge_across_two_confirmed_clusters_of_the_same_person_is_allowed(
    review: ReviewCluster, seeded_db: Database
):
    """同一个人的两个簇可以合——1.0 的判据是**实体不同**且都确认，不是「都确认」。"""
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_1", status="confirmed", entity_id="ent_1")
    add_cluster(seeded_db, "clu_2", status="confirmed", entity_id="ent_1")

    result = review.merge(target_cluster_id="clu_1", source_cluster_id="clu_2")

    assert result.id == "clu_1"


# ---- split -----------------------------------------------------------------------


def test_split_creates_a_pending_cluster_for_the_face(review: ReviewCluster, seeded_db: Database):
    """拆出来的脸进一个新簇，新簇带自己的 pending 实体；两个簇的原型都要重算。

    审计写在**原簇的实体**上（``face_cluster_split``），证据是那张脸的 id——
    「哪张脸被拆出去了」必须能查到，否则用户看到的两个簇无法解释。
    """
    add_entity(seeded_db, "ent_1", name="妈妈", status="confirmed")
    add_cluster(seeded_db, "clu_1", status="confirmed", entity_id="ent_1", member_count=2)
    add_instance(seeded_db, "ins_1", cluster_id="clu_1")
    add_instance(seeded_db, "ins_2", cluster_id="clu_1", observation_id="obs_2", quality=0.3)

    new_cluster = review.split("clu_1", face_instance_id="ins_2")

    assert new_cluster.id != "clu_1"
    assert scalar(seeded_db, "SELECT cluster_id FROM face_instances WHERE id = 'ins_2'") == new_cluster.id
    assert scalar(seeded_db, "SELECT member_count FROM face_clusters WHERE id = 'clu_1'") == 1
    assert scalar(seeded_db, "SELECT member_count FROM face_clusters WHERE id = ?", (new_cluster.id,)) == 1
    entity = query(seeded_db, "SELECT * FROM entities WHERE id = ?", (new_cluster.entity_id,))
    assert len(entity) == 1
    assert entity[0]["status"] == "pending"
    assert entity[0]["canonical_name"] == f"待确认人物簇 · {new_cluster.id}"
    assert entity[0]["summary"] == "由 buffalo_l embedding 生成，等待用户确认"
    split_revisions = query(
        seeded_db, "SELECT * FROM entity_revisions WHERE field_name = 'face_cluster_split'"
    )
    assert len(split_revisions) == 1
    assert split_revisions[0]["entity_id"] == "ent_1"
    assert split_revisions[0]["source"] == "user_split"
    assert split_revisions[0]["old_value"] == "clu_1"
    assert split_revisions[0]["new_value"] == new_cluster.id
    assert json.loads(str(split_revisions[0]["evidence_ids_json"])) == ["ins_2"]


def test_split_of_an_instance_from_another_cluster_is_a_404(review: ReviewCluster, seeded_db: Database):
    """``face_instance_id`` 必须属于这个簇。不带这条判据，拆分就成了「把任意一张脸
    从任意一个簇里夺过来」。"""
    add_cluster(seeded_db, "clu_1")
    add_cluster(seeded_db, "clu_2")
    add_instance(seeded_db, "ins_other", cluster_id="clu_2")

    with pytest.raises(ClusterNotFoundError) as error:
        review.split("clu_1", face_instance_id="ins_other")

    assert error.value.status_code == 404
    assert scalar(seeded_db, "SELECT cluster_id FROM face_instances WHERE id = 'ins_other'") == "clu_2"


def test_split_of_a_missing_cluster_or_instance_is_a_404(review: ReviewCluster, seeded_db: Database):
    add_cluster(seeded_db, "clu_1")

    with pytest.raises(ClusterNotFoundError):
        review.split("clu_missing", face_instance_id="ins_1")
    with pytest.raises(ClusterNotFoundError):
        review.split("clu_1", face_instance_id="ins_missing")
