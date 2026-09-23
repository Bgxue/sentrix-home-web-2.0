"""确认一个人之后，事件侧跟着改的两件事：参与者投影与重分段。

对应 1.0 ``backend/db.py`` 的 ``_refresh_event_participants`` :5549、
``upsert_event_participant`` :2486、``resegment_events_for_confirmed_entity`` :5450、
``_merge_events`` :5482、``refresh_event_summary`` :2585。

**这一层钉的是内容，不是行数。** 「事件里多了一行参与者」这种计数断言在投影写错人、
写错角色、写错置信度时**照样是绿的**；所以每条断言都落到具体字段上：角色是
``visible_subject``、置信度是 0.75、证据是那条观察的 id、摘要正文里出现了名字。

**三处对照臂**（同一条判据的两侧都要有用例）：

- 没挂这条观察的事件一行都不该被碰（revision 与摘要都保持原样）；
- 未确认（pending）的实体哪怕有提及行，也不进投影；
- 重分段的两道门（6 小时窗、八分量 ``>= 0.50``）各有一个反例，且与正例共用同一份夹具。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sentrix.application.identity.review_cluster import ReviewCluster
from sentrix.application.memories.assign_events import ConfirmedPersonEvents
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.infrastructure.persistence.repositories.participants import (
    SqliteEventParticipantRepository,
)

HOME = "album1"
BASE_TIME = datetime(2017, 11, 5, 10, 0, tzinfo=UTC)

# 1.0 ``db.py:5570`` 的字面量。写在这里而不是从实现里 import：拿实现里的常数去断言
# 它自己，常数被改错时用例照样是绿的。
VISIBLE_SUBJECT_CONFIDENCE = 0.75


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (HOME, HOME, BASE_TIME.isoformat(), BASE_TIME.isoformat()),
        )
    return migrations_ready_db


def add_asset(db: Database, asset_id: str) -> None:
    SqliteAssetRepository(db).add(
        Asset(
            id=asset_id,
            scope_id=HOME,
            file_name=f"{asset_id}.jpg",
            media_type=MediaType.IMAGE,
            path=f"/tmp/{asset_id}.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256=asset_id,
            captured_at=BASE_TIME,
            captured_location=None,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
    )


def add_observation(
    db: Database,
    observation_id: str,
    *,
    asset_id: str,
    activity: str = "野餐",
    place: str = "公园",
    event_type: str = "户外",
) -> None:
    SqliteObservationRepository(db).add(
        Observation(
            id=observation_id,
            scope_id=HOME,
            asset_id=asset_id,
            source_type="import",
            captured_at=BASE_TIME,
            confidence=0.6,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
            caption="在公园野餐",
            activity=activity,
            place=place,
            event_type=event_type,
        )
    )


def add_event(
    db: Database,
    event_id: str,
    *,
    time_start: datetime,
    place: str = "公园",
    activity: str = "野餐",
) -> None:
    SqliteEventRepository(db).create(
        event_id=event_id,
        scope_id=HOME,
        time_start=time_start,
        time_end=time_start,
        place=place,
        admin_district="",
        activity=activity,
        summary="待总结事件",
        confidence=0.6,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )


def link_event(db: Database, event_id: str, observation_id: str) -> None:
    SqliteEventRepository(db).attach(event_id, observation_id)


def add_person(db: Database, entity_id: str, *, name: str, status: str = "confirmed") -> None:
    """直接插一行实体。

    不走 ``create_person`` 是因为它自己生成 id，而这里的用例要用固定的 ``ent_1`` /
    ``ent_2`` 在断言里指名道姓。写入的列与 ``create_person`` 完全一致。
    """
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, summary, confidence,"
            "  created_at, updated_at) VALUES (?, ?, 'person', ?, ?, '', 1.0, ?, ?)",
            (entity_id, HOME, name, status, BASE_TIME.isoformat(), BASE_TIME.isoformat()),
        )


@pytest.fixture
def entities(db: Database) -> SqliteEntityRepository:
    return SqliteEntityRepository(db)


@pytest.fixture
def participants(db: Database) -> SqliteEventParticipantRepository:
    return SqliteEventParticipantRepository(db)


@pytest.fixture
def projector(db: Database, confirmed_person_events) -> ConfirmedPersonEvents:
    return confirmed_person_events(db)


def query(db: Database, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def scalar(db: Database, sql: str, params: tuple[object, ...] = ()) -> object:
    with db.connect() as connection:
        row = connection.execute(sql, params).fetchone()
    return row[0] if row else None


# ---- 参与者投影 ------------------------------------------------------------------


def test_a_confirmed_person_becomes_a_visible_subject(
    db: Database,
    entities: SqliteEntityRepository,
    participants: SqliteEventParticipantRepository,
    projector: ConfirmedPersonEvents,
):
    """角色、置信度、证据三样都要对：1.0 写的是 ``visible_subject`` / 0.75 / 那条观察 id。"""
    add_asset(db, "asset_1")
    add_observation(db, "obs_1", asset_id="asset_1")
    add_event(db, "evt_1", time_start=BASE_TIME)
    link_event(db, "evt_1", "obs_1")
    add_person(db, "ent_1", name="妈妈")
    entities.link_confirmed_mention("ent_1", "obs_1")

    affected = projector.refresh_participants(["obs_1"])

    assert affected == ("evt_1",)
    rows = participants.list_participants("evt_1")
    assert [(row.person_id, row.role, row.confidence, row.evidence_ids) for row in rows] == [
        ("ent_1", "visible_subject", VISIBLE_SUBJECT_CONFIDENCE, ("obs_1",))
    ]
    assert rows[0].person_name == "妈妈"


def test_a_second_refresh_merges_evidence_instead_of_adding_a_row(
    db: Database,
    entities: SqliteEntityRepository,
    participants: SqliteEventParticipantRepository,
    projector: ConfirmedPersonEvents,
):
    """第二次刷新不新建行：证据合并、置信度取 MAX、``revision`` 逐次递增（1.0 的 upsert 语义）。

    ``revision == 3`` 不是笔误：1.0 的 ``_refresh_event_participants`` 是**逐观察**调
    ``upsert_event_participant`` 的（``db.py:5570-5571`` 那个双层循环里，每条观察各调一次），
    所以两次刷新一共三条证据、三次 upsert。把外层改成「先收集再写一次」会让这个数变小，
    但那不是 1.0 的行为——证据行是**按观察**记的，每次都得单独落。
    """
    add_asset(db, "asset_1")
    add_observation(db, "obs_1", asset_id="asset_1")
    add_observation(db, "obs_2", asset_id="asset_1")
    add_event(db, "evt_1", time_start=BASE_TIME)
    link_event(db, "evt_1", "obs_1")
    link_event(db, "evt_1", "obs_2")
    add_person(db, "ent_1", name="妈妈")
    entities.link_confirmed_mention("ent_1", "obs_1")
    entities.link_confirmed_mention("ent_1", "obs_2")

    projector.refresh_participants(["obs_1"])
    projector.refresh_participants(["obs_1", "obs_2"])

    rows = participants.list_participants("evt_1")
    assert len(rows) == 1
    assert rows[0].evidence_ids == ("obs_1", "obs_2")
    assert rows[0].confidence == VISIBLE_SUBJECT_CONFIDENCE
    assert rows[0].revision == 3


def test_an_unconfirmed_person_never_reaches_the_event_projection(
    db: Database,
    entities: SqliteEntityRepository,
    participants: SqliteEventParticipantRepository,
    projector: ConfirmedPersonEvents,
):
    """**对照臂**：pending 的实体不进投影。

    这一行提及是**直接插进去的**——``link_confirmed_mention`` 的资格闸门不允许未确认的
    实体写提及，所以这个状态走生产路径出不来。它要钉的是第二道闸门
    （``confirmed_people_for_observation`` 的 ``status = 'confirmed'``）：实体被降级之后
    历史提及还在（Task 84 的 ``_sync_face_entity_statuses`` 会把它写成 rejected），
    那时候投影必须停手——否则事件里会留下一个「已经不是这个人」的名字。
    """
    add_asset(db, "asset_1")
    add_observation(db, "obs_1", asset_id="asset_1")
    add_event(db, "evt_1", time_start=BASE_TIME)
    link_event(db, "evt_1", "obs_1")
    add_person(db, "ent_1", name="待确认人物簇 · cluster-1", status="pending")
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entity_mentions (id, entity_id, observation_id, confidence, created_at)"
            " VALUES ('mention_1', 'ent_1', 'obs_1', 0.9, ?)",
            (BASE_TIME.isoformat(),),
        )

    affected = projector.refresh_participants(["obs_1"])

    assert participants.list_participants("evt_1") == []
    # 事件本身仍然被刷新过：1.0 的循环不按「有没有人」跳过事件，摘要那一句要按当前
    # 参与者重写——没有人时它走的是退回占位的那条路。
    assert affected == ("evt_1",)


def test_only_the_events_that_hold_the_observation_are_touched(
    db: Database, entities: SqliteEntityRepository, projector: ConfirmedPersonEvents
):
    """**对照臂**：没挂这条观察的事件一行都不该被碰——revision 与摘要都保持原样。"""
    add_asset(db, "asset_1")
    add_asset(db, "asset_2")
    add_observation(db, "obs_1", asset_id="asset_1")
    add_observation(db, "obs_2", asset_id="asset_2")
    add_event(db, "evt_1", time_start=BASE_TIME, activity="野餐")
    add_event(db, "evt_2", time_start=BASE_TIME - timedelta(days=30), activity="做饭")
    link_event(db, "evt_1", "obs_1")
    link_event(db, "evt_2", "obs_2")
    add_person(db, "ent_1", name="妈妈")
    entities.link_confirmed_mention("ent_1", "obs_1")

    projector.refresh_participants(["obs_1"])

    assert query(db, "SELECT summary, revision FROM events WHERE id = 'evt_2'") == [
        {"summary": "待总结事件", "revision": 1}
    ]


def test_an_observation_in_no_event_writes_nothing(db: Database, projector: ConfirmedPersonEvents):
    """边界：观察还没归属到任何事件时，投影无事可做（不报错，也不凭空建一个事件）。"""
    add_asset(db, "asset_1")
    add_observation(db, "obs_1", asset_id="asset_1")

    assert projector.refresh_participants(["obs_1"]) == ()
    assert scalar(db, "SELECT COUNT(*) FROM events") == 0


def test_the_event_summary_names_the_confirmed_person(
    db: Database, entities: SqliteEntityRepository, projector: ConfirmedPersonEvents
):
    """摘要正文是 1.0 ``refresh_event_summary`` 的原文：``{名字}在{地点}参与{活动}``。

    只断言「摘要变了」是不够的——占位串换成另一句占位也是「变了」。
    """
    add_asset(db, "asset_1")
    add_observation(db, "obs_1", asset_id="asset_1", activity="野餐")
    add_event(db, "evt_1", time_start=BASE_TIME, activity="野餐", place="湖北省宜昌市夷陵区")
    link_event(db, "evt_1", "obs_1")
    add_person(db, "ent_1", name="妈妈")
    entities.link_confirmed_mention("ent_1", "obs_1")

    projector.refresh_participants(["obs_1"])

    assert scalar(db, "SELECT summary FROM events WHERE id = 'evt_1'") == "妈妈在湖北省宜昌市夷陵区参与野餐"
    assert scalar(db, "SELECT revision FROM events WHERE id = 'evt_1'") == 2


def test_two_people_in_one_event_are_both_named_in_the_summary(
    db: Database, entities: SqliteEntityRepository, projector: ConfirmedPersonEvents
):
    add_asset(db, "asset_1")
    add_observation(db, "obs_1", asset_id="asset_1", activity="野餐")
    add_event(db, "evt_1", time_start=BASE_TIME, activity="野餐", place="公园")
    link_event(db, "evt_1", "obs_1")
    add_person(db, "ent_1", name="妈妈")
    add_person(db, "ent_2", name="爸爸")
    entities.link_confirmed_mention("ent_1", "obs_1")
    entities.link_confirmed_mention("ent_2", "obs_1")

    projector.refresh_participants(["obs_1"])

    summary = str(scalar(db, "SELECT summary FROM events WHERE id = 'evt_1'"))
    # 名字的**拼接顺序**来自 ``list_participants`` 的 ``ORDER BY updated_at DESC``，最后
    # 落库的那个排前面——那是 1.0 的排序，不是一条需要钉住的语义。钉住的是「两个人都
    # 进了正文、且用的是 ``{名字}在{地点}参与{活动}`` 这个句式」。
    assert summary.endswith("在公园参与野餐")
    assert sorted(summary.removesuffix("在公园参与野餐").split("、")) == ["妈妈", "爸爸"]


# ---- 重分段 ----------------------------------------------------------------------


def seed_pair(
    db: Database,
    entities: SqliteEntityRepository,
    *,
    gap: timedelta,
    activity_two: str = "野餐",
    place_two: str = "公园",
    event_type_two: str = "户外",
) -> None:
    """两张同一个人物、时间差 ``gap`` 的照片，各自成一个事件。

    **两条用例共用这一份夹具**：正例只改 ``gap`` 之外的语义字段成对，反例把它们拆开，
    这样「合并与否」的差别只可能来自被改的那几个字段。
    """
    add_asset(db, "asset_1")
    add_asset(db, "asset_2")
    add_observation(db, "obs_1", asset_id="asset_1", activity="野餐", place="公园", event_type="户外")
    add_observation(
        db,
        "obs_2",
        asset_id="asset_2",
        activity=activity_two,
        place=place_two,
        event_type=event_type_two,
    )
    add_event(db, "evt_1", time_start=BASE_TIME, activity="野餐", place="公园")
    add_event(db, "evt_2", time_start=BASE_TIME + gap, activity=activity_two, place=place_two)
    link_event(db, "evt_1", "obs_1")
    link_event(db, "evt_2", "obs_2")
    add_person(db, "ent_1", name="妈妈")
    entities.link_confirmed_mention("ent_1", "obs_1")
    entities.link_confirmed_mention("ent_1", "obs_2")


def test_two_events_bridged_by_a_confirmed_person_become_one(
    db: Database,
    entities: SqliteEntityRepository,
    participants: SqliteEventParticipantRepository,
    projector: ConfirmedPersonEvents,
):
    """确认之后，同一小时同一地点的那两刀要合回来——这是这条路径存在的全部理由。

    断言的是**内容**：源事件没了、它的观察挂到目标事件上、参与者的证据合并成两条。
    只断言「事件数从 2 变成 1」的话，把观察写丢也是绿的。
    """
    seed_pair(db, entities, gap=timedelta(minutes=5))

    merged = projector.resegment("ent_1")

    assert merged == (("evt_1", "evt_2"),)
    assert scalar(db, "SELECT COUNT(*) FROM events") == 1
    assert scalar(db, "SELECT event_id FROM event_observations WHERE observation_id = 'obs_2'") == "evt_1"
    rows = participants.list_participants("evt_1")
    assert len(rows) == 1
    assert rows[0].evidence_ids == ("obs_1", "obs_2")


def test_the_source_event_leaves_no_participant_rows_behind(
    db: Database, entities: SqliteEntityRepository, projector: ConfirmedPersonEvents
):
    """外键的凭证：``event_participants.event_id`` 指向 ``events(id)``，连接上开着
    ``PRAGMA foreign_keys``。源事件被删而它的参与者行还在的话，那句 DELETE 会直接失败——
    所以本用例能绿，就是 ``rehome`` 真的搬过家（去掉它，合并那一步会炸在别名约束上，
    只 INSERT 不 DELETE 会炸在外键上）。
    """
    seed_pair(db, entities, gap=timedelta(minutes=5))
    projector.refresh_participants(["obs_1", "obs_2"])

    projector.resegment("ent_1")

    assert query(db, "SELECT * FROM event_participants WHERE event_id = 'evt_2'") == []
    assert query(db, "SELECT * FROM events WHERE id = 'evt_2'") == []


def test_events_more_than_six_hours_apart_are_left_alone(
    db: Database, entities: SqliteEntityRepository, projector: ConfirmedPersonEvents
):
    """第一道门**对照臂**：7 小时之外连打分都不打（1.0 ``db.py:5473``）。"""
    seed_pair(db, entities, gap=timedelta(hours=7))

    assert projector.resegment("ent_1") == ()
    assert scalar(db, "SELECT COUNT(*) FROM events") == 2


def test_a_pair_below_the_score_threshold_is_left_alone(
    db: Database, entities: SqliteEntityRepository, projector: ConfirmedPersonEvents
):
    """第二道门**对照臂**：活动 / 地点 / 事件类型全不一样，只剩时间与人物两个分量。

    这一对的实算总分是 0.4065（时间 ``0.25 * (1 - 5/360)`` = 0.2465，人物 ``0.20 * 0.8``
    = 0.16），落在 0.50 之下。**它必须与上面那条正例成对**：同一份夹具、只改语义字段，
    一条合并一条不合并，才说明判据是「语义对得上」而不是「有共同的人就合」。
    """
    seed_pair(
        db,
        entities,
        gap=timedelta(minutes=5),
        activity_two="做饭",
        place_two="厨房",
        event_type_two="室内",
    )

    assert projector.resegment("ent_1") == ()
    assert scalar(db, "SELECT COUNT(*) FROM events") == 2


def test_an_entity_with_a_single_event_is_not_resegmented(
    db: Database, entities: SqliteEntityRepository, projector: ConfirmedPersonEvents
):
    """边界：只涉一个事件时直接返回（1.0 的 ``if len(event_ids) < 2: return []``）。"""
    add_asset(db, "asset_1")
    add_observation(db, "obs_1", asset_id="asset_1")
    add_event(db, "evt_1", time_start=BASE_TIME)
    link_event(db, "evt_1", "obs_1")
    add_person(db, "ent_1", name="妈妈")
    entities.link_confirmed_mention("ent_1", "obs_1")

    assert projector.resegment("ent_1") == ()


def test_a_missing_entity_is_not_an_error(db: Database, projector: ConfirmedPersonEvents):
    assert projector.resegment("ent_missing") == ()


# ---- 与簇确认合起来 ---------------------------------------------------------------


def test_confirming_a_cluster_merges_the_two_events_its_photos_created(
    db: Database, entities: SqliteEntityRepository, confirmed_person_events
):
    """这一条是这条路径的**验收句**：确认之前两张照片在两个事件里，确认之后在一个里。

    它同时钉住顺序：提及写在投影之前。把 ``confirm`` 里那两句挪到写提及之前，
    参与者行不会出现（那时提及还不存在），摘要里也就没有名字。
    """
    seed_pair(db, entities, gap=timedelta(minutes=5))
    # 把「已确认的人」清掉：本用例要认的是这个簇自己的那个待确认实体，
    # 留着 ent_1 会让 confirm 走「同名并入」那一路，那是另一个分支。
    with db.connect() as connection, connection:
        connection.execute("DELETE FROM entity_mentions")
        connection.execute("DELETE FROM entities")

    faces = SqliteFaceRepository(db)
    cluster = faces.create_cluster([1.0, 0.0], confidence=0.9, scope_id=HOME)
    with db.connect() as connection, connection:
        for index, observation_id in enumerate(("obs_1", "obs_2"), start=1):
            connection.execute(
                "INSERT INTO face_instances"
                " (id, asset_id, observation_id, cluster_id, embedding_json, detection_confidence,"
                "  quality, pose_bucket, embedding_model, embedding_version, created_at)"
                " VALUES (?, ?, ?, ?, '[1.0, 0.0]', 0.9, 0.8, 'frontal', 'buffalo_l',"
                "  'w600k_r50', ?)",
                (f"ins_{index}", f"asset_{index}", observation_id, cluster.id, BASE_TIME.isoformat()),
            )
    pending = entities.create_person("待确认人物簇 · x", status="pending", scope_id=HOME)
    faces.link_entity(cluster.id, pending.id)

    assert scalar(db, "SELECT COUNT(*) FROM events") == 2
    review = ReviewCluster(
        faces=faces,
        entities=entities,
        facts=SqliteFactRepository(db),
        events=confirmed_person_events(db),
    )

    result = review.confirm(cluster.id, name="妈妈")

    assert scalar(db, "SELECT COUNT(*) FROM events") == 1
    only_event = str(scalar(db, "SELECT id FROM events"))
    rows = SqliteEventParticipantRepository(db).list_participants(only_event)
    assert [(row.person_id, row.role) for row in rows] == [(result.entity.id, "visible_subject")]
    assert scalar(db, "SELECT summary FROM events") == "妈妈在公园参与野餐"
