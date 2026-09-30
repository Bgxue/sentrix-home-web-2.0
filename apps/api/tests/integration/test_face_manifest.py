"""``list_face_manifest``（T123 的第三条读路径）：一张照片里**每一张脸**，带局部编号。

它与 T122 的 ``list_photo_identities`` 是**两条查询、两种口径**，所以本文件的主线是
把它们放在**同一份数据上对拍**——同一张照片、同一批脸，一个说「图里有谁」，
另一个说「图里有几张脸、分别是谁或不知道是谁」。

**「未确认者不得猜姓名」的落点在这里**：名字要 cluster 与 entity **双确认**且实体有名字
三者同时成立才填。少任何一个合取项，模型就会拿到一个用户从没认可过的名字，
而它会把这个名字当成事实说出去。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.domain.identity.models import (
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    FACE_VALIDITY_VERIFIED,
)
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
SCOPE = "album-t123"
ASSET_ID = "asset_t123"


@pytest.fixture
def bundle(migrations_ready_db):
    assets = SqliteAssetRepository(migrations_ready_db)
    assets.add(
        Asset(
            id=ASSET_ID,
            scope_id=SCOPE,
            file_name="photo.jpg",
            media_type=MediaType.IMAGE,
            path=str(Path("photo.jpg")),
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256="hash-t123",
            captured_at=NOW,
            captured_location=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    # **观察必须先于脸存在**：``Database`` 开着 ``PRAGMA foreign_keys``，
    # 而 ``face_instances.observation_id`` 有 REFERENCES。
    SqliteObservationRepository(migrations_ready_db).add(
        Observation(
            id="obs_t123",
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
        "faces": SqliteFaceRepository(migrations_ready_db),
        "entities": SqliteEntityRepository(migrations_ready_db),
    }


def add_face(
    bundle, *, cluster_id: str | None, quality: float = 0.9, detection_confidence: float = 0.95
) -> str:
    return (
        bundle["faces"]
        .add_instance(
            asset_id=ASSET_ID,
            observation_id="obs_t123",
            cluster_id=cluster_id,
            bbox=(0.1, 0.2, 0.3, 0.4),
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
        .id
    )


def bind(
    bundle, *, entity_status: str, name: str, role: str | None = None, cluster_confirmed: bool = True
) -> str:
    """建一个簇、建一个实体、挂起来。返回 cluster_id。

    **``link_entity`` 与 ``confirm_cluster`` 不是同一件事**：前者只回填 ``entity_id``，
    簇的状态留在 ``pending``；后者才把簇转 ``confirmed``。名字那个合取项要的是
    **两个状态都确认**，所以「簇确认了但实体还 pending」这种组合要分开造得出来——
    它是三条腿里的第二条。
    """
    cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.9, scope_id=SCOPE)
    entity = bundle["entities"].create_person(name, status=entity_status, family_role=role, scope_id=SCOPE)
    if cluster_confirmed:
        bundle["faces"].confirm_cluster(cluster.id, entity.id)
    else:
        bundle["faces"].link_entity(cluster.id, entity.id)
    return cluster.id


# ---- 两条读路径的对照 ---------------------------------------------------------------


def test_the_manifest_sees_faces_that_the_identities_query_cannot(bundle):
    """**这张对拍就是本文件的主线。** 同一张照片、同一批脸：

    - ``list_photo_identities`` 只看见**绑到人**的那一张；
    - ``list_face_manifest`` 看见**全部三张**。

    两条都不是「更全的那条对」：前者的用途是「图里有谁」，后者是「图里有几张脸、
    哪些不知道是谁」。1.0 两个问题都问，所以两条查询都留着。
    """
    bound = bind(bundle, entity_status=ENTITY_STATUS_CONFIRMED, name="妈妈", role="母亲")
    unbound_cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.7, scope_id=SCOPE)

    add_face(bundle, cluster_id=bound)
    add_face(bundle, cluster_id=unbound_cluster.id)
    add_face(bundle, cluster_id=None)

    identities = bundle["faces"].list_photo_identities(ASSET_ID)
    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert len(identities) == 1
    assert len(manifest) == 3


def test_a_bound_face_appears_in_both(bundle):
    """绑到人的那张在两条里都在——两条查询对「谁在」这件事没有分歧。"""
    bound = bind(bundle, entity_status=ENTITY_STATUS_CONFIRMED, name="妈妈", role="母亲")
    add_face(bundle, cluster_id=bound)

    identities = bundle["faces"].list_photo_identities(ASSET_ID)
    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert [row.person_name for row in identities] == ["妈妈"]
    assert [row.person_name for row in manifest] == ["妈妈"]
    assert manifest[0].identity_status == "confirmed"


# ---- 「未确认者不得猜姓名」：三重合取各挡一条 ---------------------------------------


def test_a_pending_entity_gets_no_name_even_when_the_cluster_is_confirmed(bundle):
    """**三条腿里的第二条。** 簇已经 ``confirmed``，实体还是 ``pending``
    （``enroll_face`` 新建的人就是这个状态，用户还没认可这个名字）。

    这条最容易漏：只看簇状态的实现会在这里把名字放出去，而那是用户从没认可过的名字——
    模型拿到它会当成事实说出去。``_row_to_cluster`` 那边有一条同源的判据
    （「判据是**实体**状态而不是簇状态」），这里的三重合取是同一条纪律的第二次落点。
    """
    cluster = bind(bundle, entity_status=ENTITY_STATUS_PENDING, name="待确认人物簇 · cluster-x")
    add_face(bundle, cluster_id=cluster)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert manifest[0].identity_status == "unconfirmed"
    assert manifest[0].person_name == ""
    assert manifest[0].family_role == ""


def test_a_pending_cluster_gets_no_name_even_when_the_entity_is_confirmed(bundle):
    """**三条腿里的第三条。** 实体已经是确认的人，但簇还没确认（``link_entity`` 只回填
    归属、不转状态）。这一条挡的是「只看实体状态」的实现。"""
    cluster = bind(
        bundle,
        entity_status=ENTITY_STATUS_CONFIRMED,
        name="妈妈",
        role="母亲",
        cluster_confirmed=False,
    )
    add_face(bundle, cluster_id=cluster)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert manifest[0].identity_status == "unconfirmed"
    assert manifest[0].person_name == ""


def test_an_entity_without_a_name_gets_no_name_in_the_manifest(bundle):
    """**三条腿里剩下的那条：``and row["canonical_name"]``。**

    簇与实体都确认了，但实体的名字是空串（``create_person`` 允许建一个还没起名的
    确认实体）——这时同样不许算「已确认」：一个没有名字的确认，对模型来说与
    「不知道这是谁」没有区别，而 ``identity_status`` 是**提示词里那一格**。

    **判据要看 ``identity_status``，不能只看 ``person_name``。** 两条路在名字这一格上
    都算得出空串（``str("" or "")``），所以只断言名字的话，把这条腿删掉照样绿——
    定点变异实测抓到过这一点。可观察的差别在状态那一格。
    """
    cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.9, scope_id=SCOPE)
    _insert_entity(bundle["db"], entity_id="ent_blank", entity_type="person", name="")
    bundle["faces"].confirm_cluster(cluster.id, "ent_blank")
    add_face(bundle, cluster_id=cluster.id)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert manifest[0].identity_status == "unconfirmed"
    assert manifest[0].person_name == ""


def test_an_unbound_cluster_gets_no_name_in_the_manifest(bundle):
    """簇没绑实体 → 没有名字。这条由 LEFT JOIN 的 NULL 兜住。"""
    cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.7, scope_id=SCOPE)
    add_face(bundle, cluster_id=cluster.id)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert manifest[0].identity_status == "unconfirmed"
    assert manifest[0].person_name == ""


def test_a_face_without_a_cluster_still_gets_a_row(bundle):
    """**没有簇的脸也必须有一行**，否则模型看到的编号会跳号（face_1、face_3），
    然后开始猜 face_2 去哪了——与 ``render_numbered_preview`` 不给框的脸丢标签是同一条理由。"""
    add_face(bundle, cluster_id=None)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert [row.face_id for row in manifest] == ["face_1"]
    assert manifest[0].face_instance_id


# ---- 局部编号与排序 -----------------------------------------------------------------


def test_the_face_ids_are_numbered_from_one_in_quality_order(bundle):
    """``face_1`` / ``face_2``… 按 ``quality DESC, detection_confidence DESC, id`` 编。

    **编号要能被回指**：模型在「问题指向某个人但没给名字」时回一个 ``face_id``，
    调用方靠它找回具体那张脸。所以编号必须由**那条排序**决定，而且两次调用要一致
    （1.0 的三键排序里第三键 ``fi.id`` 就是为此存在的）。
    """
    bound = bind(bundle, entity_status=ENTITY_STATUS_CONFIRMED, name="妈妈")
    low = add_face(bundle, cluster_id=bound, quality=0.5, detection_confidence=0.99)
    mid = add_face(bundle, cluster_id=bound, quality=0.9, detection_confidence=0.5)
    high = add_face(bundle, cluster_id=bound, quality=0.9, detection_confidence=0.8)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert [(row.face_id, row.face_instance_id) for row in manifest] == [
        ("face_1", high),
        ("face_2", mid),
        ("face_3", low),
    ]


def test_ties_fall_back_to_the_face_id_key(bundle):
    """同分同置信度时按 ``fi.id`` 升序——**第三键不是装饰**。

    编号要进提示词、还要被模型回指，所以同一个问题问两次必须给出同一个编号。
    少了第三键，同分的行由 SQLite 的扫描顺序定，换个查询计划就可能变。

    **插入顺序故意与 id 顺序相反**（``zz`` → ``mm`` → ``aa``）：这样「按插入顺序出」
    与「按 id 出」是两个可分辨的答案。第一版用例比的是「两次调用结果相同」——
    那在同一次实现里恒真，什么也没验到；定点变异把第三键删掉照样绿，抓出来的就是它。
    """
    for name in ("face_zz", "face_mm", "face_aa"):
        add_face_with_id(bundle, name, quality=0.9, detection_confidence=0.95)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert [row.face_instance_id for row in manifest] == ["face_aa", "face_mm", "face_zz"]


def test_the_manifest_carries_the_bbox_and_confidence(bundle):
    """清单是要 JSON 进提示词的，这些格是它的内容。"""
    add_face(bundle, cluster_id=None, quality=0.77, detection_confidence=0.88)

    row = bundle["faces"].list_face_manifest(ASSET_ID)[0]

    assert row.bbox == (0.1, 0.2, 0.3, 0.4)
    assert row.quality == 0.77
    assert row.detection_confidence == 0.88
    assert row.asset_id == ASSET_ID


def test_another_asset_is_not_in_this_manifest(bundle):
    """**先造一张脸再查别的 asset**：不造的话「按 asset 过滤」这条被删掉也照样返回空表，
    用例什么也挡不住（定点变异抓到的第二处）。"""
    add_face(bundle, cluster_id=None)

    assert bundle["faces"].list_face_manifest(ASSET_ID) != []
    assert bundle["faces"].list_face_manifest("asset_nobody") == []


def test_the_manifest_does_not_filter_by_entity_type(bundle):
    """**这条钉的是一个已知的口径缺口，留给 T127 裁决。**

    1.0 的 ``_photo_face_manifest`` **没有** ``entity_type = 'person'`` 这条过滤
    （``tools.py:2337``），而 T122 的 ``list_photo_identities`` **有**。
    在 1.0 里这不成问题：非人物实体根本不存在。S6 的 T127 要落非人物实体，
    那时一个绑到「乌篷船」的簇会让这里**给出一个名字**。

    **今天不修**：改了就是偏离 1.0，而且 2.0 现在没有任何写入路径能造出非人物实体
    （本用例是直接写库造的）。这条用例钉的是**当前的真实行为**，好让 T127 动工时
    看得见它——而不是让它悄悄变。
    """
    cluster = bundle["faces"].create_cluster([0.0] * 4, confidence=0.9, scope_id=SCOPE)
    _insert_entity(bundle["db"], entity_id="ent_object", entity_type="object", name="乌篷船")
    bundle["faces"].confirm_cluster(cluster.id, "ent_object")
    add_face(bundle, cluster_id=cluster.id)

    manifest = bundle["faces"].list_face_manifest(ASSET_ID)

    assert manifest[0].person_name == "乌篷船", (
        "1.0 的清单不过滤 entity_type，这才是照抄的结果；"
        "如果这条红了，说明有人替 T127 改了它——那需要一条裁决，不是顺手改"
    )
    # 同一条 SQL 的邻居会把它挡掉：两条口径确实是相反的。
    assert bundle["faces"].list_photo_identities(ASSET_ID) == []


def _insert_entity(db, *, entity_id: str, entity_type: str, name: str, status: str = "confirmed") -> None:
    """直接写一个实体。S6 没有非人物实体与空名的写入路径（T127 才做），
    而这两条都要造得出来才测得到**过滤与合取**本身。"""
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
            " family_role, summary, confidence, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (entity_id, SCOPE, entity_type, name, status, None, "", 1.0, NOW.isoformat(), NOW.isoformat()),
        )


def add_face_with_id(bundle, instance_id: str, *, quality: float, detection_confidence: float) -> None:
    """写一行脸，**id 由用例指定**。

    排序那条用例需要「插入顺序 ≠ id 顺序」才能验到第三键 ``fi.id``——而
    ``add_instance`` 生成的 id 是随机十六进制，两个顺序可能碰巧相同，
    那样用例就静默地什么也没验到（第一版正是如此，定点变异抓到了它）。
    所以这一条按 id 造数据。
    """
    with bundle["db"].connect() as connection, connection:
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " detection_confidence, quality, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                instance_id,
                ASSET_ID,
                "obs_t123",
                None,
                "[0.1, 0.2, 0.3, 0.4]",
                detection_confidence,
                quality,
                NOW.isoformat(),
            ),
        )
