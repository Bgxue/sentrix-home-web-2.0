"""``query_photo_people``（T122）：两条新读路径 + 工具本身。真 SQLite，不假件。

这个工具在 1.0 被调了 887 次，而它要回答的那句话只有两个来源：

1. ``list_photo_identities``——这张照片里**绑到人**的脸（``face_instances ⋈ face_clusters
   ⋈ entities``）；
2. ``latest_people_snapshot``——最近一次观察的 ``people_json`` 里**没有 entity_id**
   的那几条 = 未确认同行者。

**这份用例的主体是两个「过滤前有、过滤后没有」的对照。** 两条读路径的正确性都不在
「拿到了什么」，而在**排除了什么**：没绑簇的脸、簇没绑实体的脸、绑到非人物实体的脸，
少排一条就会给模型一个不存在的人；而快照那一边，少读一位 ``entity_id`` 就会把已经
认出来的人再数一遍（1.0 ``tools.py:2582-2588`` 的注释写的就是这件事）。

**一条关于变异的事实，记在这里免得下次误判。** 14 条定点变异里 12 条变红，
两条（把 ``JOIN face_clusters`` / ``JOIN entities`` 改成 ``LEFT JOIN``）**怎么改都不红**
——它们是**等价变异**，不是用例漏了。实测确认过：那条 SQL 有三重互相覆盖的过滤，
``WHERE ... AND e.entity_type = 'person'`` 在没匹配到实体时 ``e.entity_type`` 是 NULL，
而 ``NULL = 'person'`` 不为真，于是**光这一条就把「没绑簇的」与「簇没绑实体的」都挡掉了**，
两个 INNER JOIN 各自单独换成 LEFT JOIN 都观察不到差别。
把 WHERE 那一句也拿掉之后，未绑定的那张脸立刻出现、用例立刻红。
所以这里验的是**行为**（不该有的人不出现），验不到**哪一句在挡**——三条里留一条就够。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.assistant.tools import AssistantToolbox
from sentrix.domain.assistant.models import PersonProfile
from sentrix.domain.assistant.result_set import ResultSet
from sentrix.domain.identity.models import (
    ENTITY_STATUS_CONFIRMED,
    FACE_VALIDITY_VERIFIED,
)
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.result_sets import SqliteResultSetRepository

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
SCOPE = "album-t122"
ASSET_ID = "asset_t122"
OBSERVATION_ID = "obs_t122"


# ---- 夹具：一张照片，四种脸 ---------------------------------------------------------


@pytest.fixture
def bundle(migrations_ready_db):
    """真仓储 + 一条 asset。脸与实体由各用例自己造——它们是判据本身。"""
    assets = SqliteAssetRepository(migrations_ready_db)
    path = Path("photo.jpg")
    assets.add(
        Asset(
            id=ASSET_ID,
            scope_id=SCOPE,
            file_name="photo.jpg",
            media_type=MediaType.IMAGE,
            path=str(path),
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256="hash-t122",
            captured_at=NOW,
            captured_location=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    observations = SqliteObservationRepository(migrations_ready_db)
    # **观察必须先于脸存在。** ``Database`` 是开着 ``PRAGMA foreign_keys`` 的，
    # 而 ``face_instances.observation_id`` 有 REFERENCES——先落脸会直接 IntegrityError。
    observations.add(
        Observation(
            id=OBSERVATION_ID,
            scope_id=SCOPE,
            asset_id=ASSET_ID,
            source_type="import",
            captured_at=NOW,
            confidence=1.0,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return {
        "db": migrations_ready_db,
        "assets": assets,
        "faces": SqliteFaceRepository(migrations_ready_db),
        "observations": observations,
        "entities": SqliteEntityRepository(migrations_ready_db),
        "result_sets": SqliteResultSetRepository(migrations_ready_db),
    }


def add_face(
    bundle,
    *,
    cluster_id: str | None,
    quality: float = 0.9,
    detection_confidence: float = 0.95,
    bbox: Sequence[float] = (0.1, 0.2, 0.3, 0.4),
) -> str:
    """落一条人脸实例，返回它真正的 id。

    **不去改 id。** 实例 id 是仓储生成的（``face_xxx``），用例断言时用返回值——
    改库里的主键会让「读路径读到了哪一条」这件事变得依赖测试的写法。
    """
    record = bundle["faces"].add_instance(
        asset_id=ASSET_ID,
        observation_id=OBSERVATION_ID,
        cluster_id=cluster_id,
        bbox=bbox,
        embedding=[0.0] * 4,
        detection_confidence=detection_confidence,
        quality=quality,
        pose=[0.0] * 3,
        pose_bucket_name="front",
        embedding_model="buffalo_l",
        embedding_version="1",
        embedding_quality_signal=quality,
        validity=FACE_VALIDITY_VERIFIED,
        area_ratio=0.05,
        sharpness=100.0,
    )
    return record.id


@pytest.fixture
def bound_person(bundle):
    """一个簇 + 一个**已确认的人物实体**，挂在一起。返回 (entity, cluster)。"""
    cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.9, scope_id=SCOPE)
    entity = bundle["entities"].create_person(
        "妈妈",
        status=ENTITY_STATUS_CONFIRMED,
        family_role="母亲",
        scope_id=SCOPE,
    )
    bundle["faces"].link_entity(cluster.id, entity.id)
    return entity, cluster


def _add_object_entity(db, *, entity_id: str) -> None:
    """一个 ``entity_type != 'person'`` 的实体（S6 还没有写入路径，T127 才做）。

    直接写库不是偷懒：**这正是要测的那条过滤**（``e.entity_type = 'person'``）。
    等 T127 落了非人物实体的写入侧，这条夹具可以换成那条路径，判据不用动。
    """
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
            " family_role, summary, confidence, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                entity_id,
                SCOPE,
                "object",
                "乌篷船",
                "confirmed",
                None,
                "",
                1.0,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )


# ---- 读路径 1：list_photo_identities -----------------------------------------------


def test_only_faces_bound_to_a_person_come_back(bundle, bound_person):
    """**过滤前有四张脸，过滤后只剩一张。这条就是这份用例的主体。**

    四张脸各挡一条错法：

    - ``face_person``：簇绑到人物实体 → 唯一该出现的那张；
    - ``face_no_cluster``：``cluster_id IS NULL``（「这张脸还算不上某个人」）→ 必须没有；
    - 有簇但簇**没绑实体**的那张 → 必须没有；
    - ``face_object``：簇绑到一个 **``entity_type='object'``** 的实体 → 必须没有。

    后两条由两个 INNER JOIN 与 ``entity_type`` 那条 WHERE 分别挡掉，**三条判据缺一条
    这张表里就会出现一个不该有的人**。用「四张脸进、一张脸出」来验，而不是只验
    「那张该在的在」——后者在三处过滤全被删掉时依然绿。
    """
    entity, cluster = bound_person
    _add_object_entity(bundle["db"], entity_id="ent_object")
    object_cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.8, scope_id=SCOPE)
    bundle["faces"].link_entity(object_cluster.id, "ent_object")

    unbound_cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.7, scope_id=SCOPE)
    person_face = add_face(bundle, cluster_id=cluster.id)
    add_face(bundle, cluster_id=None)
    add_face(bundle, cluster_id=unbound_cluster.id)
    add_face(bundle, cluster_id=object_cluster.id)

    rows = bundle["faces"].list_photo_identities(ASSET_ID)

    assert [row.face_instance_id for row in rows] == [person_face]
    assert rows[0].entity_id == entity.id
    assert rows[0].person_name == "妈妈"
    assert rows[0].family_role == "母亲"
    assert rows[0].cluster_id == cluster.id


def test_a_cluster_without_a_face_yields_nothing(bundle, bound_person):
    """反方向：没有脸的簇不该凭空造出一行。这条挡的是「按簇出而不是按实例出」。"""
    _entity, _cluster = bound_person

    assert bundle["faces"].list_photo_identities(ASSET_ID) == []


def test_the_identity_carries_the_bbox_which_t123_needs(bundle, bound_person):
    """``bbox`` 是 T123 那一半：``_inspect_photo`` 拿它裁目标人物的定位图。

    T122 自己一个都不用——**正因为它不用，才更要有一条用例挡着**：没人用的字段
    最容易被当成多余的顺手删掉，而删掉之后 T123 会在另一个文件里悄悄少一块，
    两边都不报错。
    """
    _entity, cluster = bound_person
    add_face(bundle, cluster_id=cluster.id, bbox=(1.0, 2.0, 3.0, 4.0))

    rows = bundle["faces"].list_photo_identities(ASSET_ID)

    assert rows[0].bbox == (1.0, 2.0, 3.0, 4.0)
    assert rows[0].quality == 0.9
    assert rows[0].detection_confidence == 0.95
    assert rows[0].mention_confidence is None, "没有 entity_mentions 行时是 None，不是 0.0"


def test_the_order_is_quality_then_confidence(bundle, bound_person):
    """1.0 ``ORDER BY fi.quality DESC, fi.detection_confidence DESC``——只有这两个键。"""
    _entity, cluster = bound_person
    low = add_face(bundle, cluster_id=cluster.id, quality=0.5, detection_confidence=0.99)
    mid = add_face(bundle, cluster_id=cluster.id, quality=0.9, detection_confidence=0.5)
    high = add_face(bundle, cluster_id=cluster.id, quality=0.9, detection_confidence=0.8)

    rows = bundle["faces"].list_photo_identities(ASSET_ID)

    assert [row.face_instance_id for row in rows] == [high, mid, low]


# ---- 读路径 2：latest_people_snapshot ----------------------------------------------


def _observation(observation_id: str, *, updated_at: datetime, people: tuple[str, ...] = ()):
    return Observation(
        id=observation_id,
        scope_id=SCOPE,
        asset_id=ASSET_ID,
        source_type="import",
        captured_at=NOW,
        confidence=1.0,
        created_at=NOW,
        updated_at=updated_at,
        caption="",
        ocr_text="",
        people=people,
    )


def _write_snapshot(db, observation_id: str, payload: object) -> None:
    """直接写 ``people_json``——**读取侧要测的正是 ``add()`` 写不出来的那一位**。

    ``ObservationRepository.add`` 会把 ``people`` 元组重新序列化成
    ``{"name":…, "status":"confirmed"}``（**没有 entity_id**，因为
    ``Observation.people`` 是 ``tuple[str, ...]``）；带 entity_id 的那一份由命名流程的
    ``_append_person_snapshot`` 追加。所以构造这条输入只能走 SQL——
    这也正是 §8.3 那条裁决要的东西：读侧旁路，不动模型。
    """
    with db.connect() as connection, connection:
        connection.execute(
            "UPDATE observations SET people_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), observation_id),
        )


def test_entity_id_survives_the_read_path(bundle):
    """**这一条是整个 T122 的理由。** 快照里绑定与未绑定各一条，读回来必须分得开。"""
    _write_snapshot(
        bundle["db"],
        "obs_t122",
        [
            {"entity_id": "ent_1", "name": "妈妈", "status": "confirmed"},
            {"name": "同伴"},
            "只说了个称呼的人",
        ],
    )

    snapshot = bundle["observations"].latest_people_snapshot(ASSET_ID)

    assert [(entry.name, entry.entity_id) for entry in snapshot] == [
        ("妈妈", "ent_1"),
        ("同伴", ""),
        ("只说了个称呼的人", ""),
    ]


def test_the_latest_observation_wins(bundle):
    """1.0 ``ORDER BY updated_at DESC LIMIT 1``——一张照片可以有多条观察（换过模型、补全过）。"""
    bundle["observations"].add(_observation("obs_old", updated_at=datetime(2026, 1, 1, tzinfo=UTC)))
    bundle["observations"].add(_observation("obs_new", updated_at=datetime(2026, 12, 1, tzinfo=UTC)))
    _write_snapshot(bundle["db"], "obs_old", [{"name": "旧"}])
    _write_snapshot(bundle["db"], "obs_new", [{"name": "新"}, {"name": "另一个"}])

    snapshot = bundle["observations"].latest_people_snapshot(ASSET_ID)

    assert [entry.name for entry in snapshot] == ["新", "另一个"]


def test_no_observation_is_an_empty_snapshot(bundle):
    """没有观察行与「有观察但没人」返回同一个结果——1.0 也是这么收的（``people_values = []``）。

    两条都断言：``asset_t122`` 有一条观察而 ``people_json`` 是空表，``asset_absent``
    连观察都没有。**调用方分不开这两件事，1.0 也没打算让它分开。**
    """
    assert bundle["observations"].latest_people_snapshot(ASSET_ID) == ()
    assert bundle["observations"].latest_people_snapshot("asset_absent") == ()


def test_the_snapshot_is_not_deduplicated(bundle):
    """**去重会把「未确认同行者」这个数改小。** ``Observation.people`` 去重是因为它产出的是
    给人看的名字表；这里产出的是计数，而 1.0 是在**原始列表**上数的那些没有 entity_id 的条目。"""
    _write_snapshot(bundle["db"], "obs_t122", [{"name": "同伴"}, {"name": "同伴"}, "同伴"])

    snapshot = bundle["observations"].latest_people_snapshot(ASSET_ID)

    assert len(snapshot) == 3


# ---- 工具本身 ----------------------------------------------------------------------


class StubRetrieval:
    def execute(self, request):  # pragma: no cover - 这个用例不调它
        raise AssertionError("本用例不该走检索")


class StubPeople:
    def lookup(self, name: str, scope_id: str) -> PersonProfile | None:
        return None


def build_toolbox(bundle) -> AssistantToolbox:
    return AssistantToolbox(
        retrieval=StubRetrieval(),
        assets=bundle["assets"],
        result_sets=bundle["result_sets"],
        people=StubPeople(),
        memory_facts=None,
        faces=bundle["faces"],
        observations=bundle["observations"],
        photo_inspector=None,
        person_crops=None,
    )


def seed_result_set(bundle, *, scope_id: str = SCOPE, asset_ids: tuple[str, ...] = (ASSET_ID,)) -> str:
    bundle["result_sets"].add(
        ResultSet(id="rs_t122", scope_id=scope_id, query="海边", asset_ids=asset_ids, created_at=NOW)
    )
    return "rs_t122"


def test_the_unconfirmed_count_subtracts_the_bound_faces(bundle, bound_person):
    """**这条是那个减法的全部意义。** 快照里有 3 个「说不上名字的人」，其中 1 个后来被认出来了
    （命名流程把已确认的人**追加**进同一份快照），所以未确认是 3 - 1 = 2，不是 3。

    不减的话，每一个被认出来的人都会在摘要里再多报一次「未确认同行者」——
    而模型会把那句话原样转述给用户。
    """
    _entity, cluster = bound_person
    add_face(bundle, cluster_id=cluster.id)
    _write_snapshot(
        bundle["db"],
        "obs_t122",
        [{"name": "同伴甲"}, {"name": "同伴乙"}, {"entity_id": "ent_1", "name": "妈妈"}],
    )
    seed_result_set(bundle)

    outcome = build_toolbox(bundle).execute(
        "query_photo_people",
        {"result_set_id": "rs_t122", "asset_handle": "photo_1"},
        scope_id=SCOPE,
        now=NOW,
    )

    assert outcome.ok
    assert outcome.observation["described_people_count"] == 2
    assert outcome.observation["unconfirmed_people_count"] == 1
    assert outcome.model_visible["summary"] == "妈妈；另有 1 名未确认身份同行者"


def test_the_count_never_goes_negative(bundle):
    """绑定的脸比快照里「说不上名字的人」还多时，结果是 0 不是负数。

    会真的发生：快照是导入期写的，那张脸可能是**之后**才被认出来的，那时快照里那条
    已经从「没名字」变成了带 entity_id 的一条。
    """
    _write_snapshot(bundle["db"], "obs_t122", [{"entity_id": "ent_1", "name": "妈妈"}])
    seed_result_set(bundle)
    cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.9, scope_id=SCOPE)
    entity = bundle["entities"].create_person("爸爸", status=ENTITY_STATUS_CONFIRMED, scope_id=SCOPE)
    bundle["faces"].link_entity(cluster.id, entity.id)
    add_face(bundle, cluster_id=cluster.id)
    add_face(bundle, cluster_id=cluster.id)

    outcome = build_toolbox(bundle).execute(
        "query_photo_people",
        {"result_set_id": "rs_t122", "asset_handle": "photo_1"},
        scope_id=SCOPE,
        now=NOW,
    )

    assert outcome.observation["unconfirmed_people_count"] == 0
    assert "未确认" not in outcome.model_visible["summary"], "没有未确认的人时那句尾巴整个不出现"
    # **同一个人有两张脸就是两条。** 这不是没去重，是 1.0 的口径：``people`` 是照着
    # `identities` 逐行生成的，而那个列表的粒度是**人脸实例**（``tools.py:2589-2593``）。
    # 一张合影里同一个人被检出两次，摘要里就会念两次名字。
    # **去重是在改产品行为**——它同时会把 ``people`` 的长度改掉，而那个长度是证据。
    assert outcome.model_visible["summary"] == "爸爸、爸爸"
    assert len(outcome.model_visible["people"]) == 2


def test_nobody_confirmed_says_so_instead_of_leaving_it_blank(bundle):
    """空摘要会让模型以为这个字段坏了。1.0 写死的那句话就是为此（``tools.py:2602``）。"""
    seed_result_set(bundle)

    outcome = build_toolbox(bundle).execute(
        "query_photo_people",
        {"result_set_id": "rs_t122", "asset_handle": "photo_1"},
        scope_id=SCOPE,
        now=NOW,
    )

    assert outcome.model_visible["summary"] == "没有已确认姓名"
    assert outcome.model_visible["people"] == []


def test_the_asset_id_stays_internal(bundle, bound_person):
    """``observation`` 与 ``model_visible`` 的差集就是这个工具的信息边界：
    ``asset_id`` 与 ``result_set_id`` 是内部标识，不进模型可见的那一份。"""
    _entity, cluster = bound_person
    add_face(bundle, cluster_id=cluster.id)
    seed_result_set(bundle)

    outcome = build_toolbox(bundle).execute(
        "query_photo_people",
        {"result_set_id": "rs_t122", "asset_handle": "photo_1"},
        scope_id=SCOPE,
        now=NOW,
    )

    assert outcome.observation["asset_id"] == ASSET_ID
    assert "asset_id" not in outcome.model_visible
    assert outcome.model_visible["people"] == [
        {"person_name": "妈妈", "membership": "母亲", "identity_status": "bound"}
    ]


def test_a_handle_outside_the_result_set_is_a_bad_handle(bundle):
    seed_result_set(bundle)

    outcome = build_toolbox(bundle).execute(
        "query_photo_people",
        {"result_set_id": "rs_t122", "asset_handle": "photo_9"},
        scope_id=SCOPE,
        now=NOW,
    )

    assert outcome.failure_reason == "bad_handle"


def test_a_result_set_from_another_scope_is_refused(bundle):
    """两个失败码分开是 1.0 的：合起来会让「handle 打错了」与「结果集过期了」
    在轨迹里长得一样。"""
    seed_result_set(bundle, scope_id="another-album")

    outcome = build_toolbox(bundle).execute(
        "query_photo_people",
        {"result_set_id": "rs_t122", "asset_handle": "photo_1"},
        scope_id=SCOPE,
        now=NOW,
    )

    assert outcome.failure_reason == "scope_mismatch"


def test_an_unknown_result_set_is_reported_as_such(bundle):
    outcome = build_toolbox(bundle).execute(
        "query_photo_people",
        {"result_set_id": "rs_nope", "asset_handle": "photo_1"},
        scope_id=SCOPE,
        now=NOW,
    )

    assert outcome.failure_reason == "unknown_result_set"
