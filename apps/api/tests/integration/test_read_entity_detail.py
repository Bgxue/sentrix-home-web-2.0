"""实体详情用例的真 SQLite 用例（S6 · T128b）。

对应 1.0 ``db.py:4570-4627`` 的 ``get_entity_detail``（十二个键，2.0 搬九个）。
这里钉的是**每一块的边界**，不是「查得出来」：

- ``evidence_count`` 是**取回来的观察条数**，不是 ``entity_observations`` 的原始行数
  ——**1.0 同名不同义**（``:4601`` 对 ``:4083``），统一了就会有一边错；
- ``facts`` 按 **``canonical_name`` 名字**匹配而不是 id，且是「先取 1000 条再过滤」；
- ``clusters`` **不排 rejected**，而 ``properties`` **排** superseded——同一个响应里
  两种相反的规矩；
- ``evidence_files`` 里 id 撞车时**外观那条赢**（1.0 的 dict 后写覆盖先写）。
"""

from __future__ import annotations

import json

import pytest

from sentrix.application.identity.read_entity_detail import (
    EVIDENCE_KIND_APPEARANCE,
    EVIDENCE_KIND_OBSERVATION,
    ReadEntityDetail,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import SqliteAppearanceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.infrastructure.persistence.repositories.relationships import (
    SqliteRelationshipRepository,
)

NOW = "2026-09-23T10:00:00+00:00"
LATER = "2026-09-24T10:00:00+00:00"

SCOPE = "album1"


@pytest.fixture
def detail(migrations_ready_db: Database) -> dict[str, object]:
    """一个实体 + 两条观察 + 一条属性（active 与 superseded 各一）+ 三条事实 + 一个事件
    + 一个簇（rejected）+ 一条外观证据。外键开着，所以父数据按顺序落。"""
    db = migrations_ready_db
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, '主相册', 1, ?, ?)",
            (SCOPE, NOW, NOW),
        )
        for asset_id in ("asset_1", "asset_2"):
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at,"
                " updated_at) VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, SCOPE, f"{asset_id}.jpg", f"/tmp/{asset_id}.jpg", NOW, NOW),
            )
        for observation_id, asset_id, captured in (
            ("obs_1", "asset_1", NOW),
            ("obs_2", "asset_2", LATER),
        ):
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " caption, created_at, updated_at) VALUES (?, ?, ?, 'vlm', ?, ?, ?, ?)",
                (observation_id, SCOPE, asset_id, captured, f"{observation_id} 的描述", NOW, NOW),
            )
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('ent_1', ?, 'place', '西湖', 'confirmed', '', 0.0, ?, ?)",
            (SCOPE, NOW, NOW),
        )
        for observation_id in ("obs_1", "obs_2"):
            connection.execute(
                "INSERT INTO entity_observations (entity_id, observation_id, confidence, source,"
                " created_at) VALUES ('ent_1', ?, 0.5, 'observation_extraction', ?)",
                (observation_id, NOW),
            )
        for prop_id, key, value, status in (
            ("prop_live", "scene_type", '"湖边"', "active"),
            ("prop_old", "scene_type", '"旧值"', "superseded"),
        ):
            connection.execute(
                "INSERT INTO entity_properties (id, entity_id, property_key, value_json, source,"
                " confidence, status, evidence_ids_json, revision, created_at, updated_at)"
                " VALUES (?, 'ent_1', ?, ?, 'observation_extraction', 0.5, ?, '[]', 1, ?, ?)",
                (prop_id, key, value, status, NOW, NOW),
            )
        # 三条事实：一条主体命中、一条客体命中、一条两边都不沾。
        for fact_id, subject, obj in (
            ("fact_subject", "西湖", "杭州"),
            ("fact_object", "妈妈", "西湖"),
            ("fact_other", "爸爸", "上海"),
        ):
            connection.execute(
                "INSERT INTO facts (id, scope_id, subject, predicate, object, status, confidence,"
                " created_at, updated_at) VALUES (?, ?, ?, '在', ?, 'active', 0.5, ?, ?)",
                (fact_id, SCOPE, subject, obj, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO events (id, scope_id, title, event_type, place, created_at, updated_at)"
            " VALUES ('ev_1', ?, '游湖', 'outing', '西湖', ?, ?)",
            (SCOPE, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO event_observations (event_id, observation_id, created_at)"
            " VALUES ('ev_1', 'obs_1', ?)",
            (NOW,),
        )
        # 被拒的簇：它要出现在详情里（1.0 的 clusters 没有状态过滤）。
        connection.execute(
            "INSERT INTO face_clusters (id, scope_id, status, entity_id,"
            " representative_embedding_json, member_count, revision, created_at, updated_at)"
            " VALUES ('cluster_rejected', ?, 'rejected', 'ent_1', '[]', 1, 1, ?, ?)",
            (SCOPE, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
            " embedding_json, detection_confidence, quality, pose_json, pose_bucket,"
            " embedding_model, embedding_version, embedding_quality_signal, validity,"
            " area_ratio, sharpness, created_at)"
            " VALUES ('face_1', 'asset_1', 'obs_1', 'cluster_rejected', '[0,0,1,1]', '[]', 0.8, 0.9,"
            " '[]', 'frontal', 'buffalo_l', 'w600k_r50', 0.5, 'verified', 0.1, 0.0, ?)",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO person_appearance_evidence (id, person_id, face_instance_id,"
            " observation_id, asset_id, crop_bbox_json, clothing_json, confidence, model_name,"
            " status, created_at, updated_at)"
            " VALUES ('app_1', 'ent_1', 'face_1', 'obs_1', 'asset_2', '[0,0,1,1]',"
            " '[\"蓝色外套\"]', 0.7, 'vlm', 'confirmed', ?, ?)",
            (NOW, NOW),
        )
    return {
        "db": db,
        "entities": SqliteEntityRepository(db),
        "use_case": ReadEntityDetail(
            entities=SqliteEntityRepository(db),
            faces=SqliteFaceRepository(db),
            facts=SqliteFactRepository(db),
            events=SqliteEventRepository(db),
            observations=SqliteObservationRepository(db),
            assets=SqliteAssetRepository(db),
            appearances=SqliteAppearanceRepository(db),
            relationships=SqliteRelationshipRepository(db),
        ),
    }


def test_an_unknown_entity_is_none(detail):
    """1.0 ``db.py:4571-4573`` 返回 ``None``，由路由翻成 404。"""
    assert detail["use_case"].execute("ent_nope") is None


def test_evidence_count_is_the_fetched_observations_not_the_raw_rows(detail):
    """**同名不同义**：详情里的 ``evidence_count`` 是**取回来的观察条数**（上限 100），
    实体列表里那个是 ``entity_observations`` 的**原始行数**。**1.0 就是两个数**
    （``db.py:4601`` 对 ``:4083``），统一了必有一边错。

    两个数**什么时候**分叉：外键是开着的，「关联在、观察读不回来」造不出来；
    真正分叉的是那个 **100 的上限**。造 101 条，详情给 100、列表给 103。

    定点变异：把用例里的 ``len(observations)`` 换成 ``decoration.evidence_count``，
    这条立刻变红。
    """
    db = detail["db"]
    with db.connect() as connection, connection:
        connection.executemany(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
            " created_at, updated_at) VALUES (?, ?, 'asset_1', 'vlm', ?, ?, ?)",
            [(f"obs_extra_{index}", SCOPE, NOW, NOW, NOW) for index in range(101)],
        )
        connection.executemany(
            "INSERT INTO entity_observations (entity_id, observation_id, confidence, source,"
            " created_at) VALUES ('ent_1', ?, 0.5, 'observation_extraction', ?)",
            [(f"obs_extra_{index}", NOW) for index in range(101)],
        )

    result = detail["use_case"].execute("ent_1")
    listed = detail["entities"].decorate_entities(["ent_1"])["ent_1"]

    assert result.evidence_count == 100  # 取回来的：被 LIMIT 100 截住
    assert listed.evidence_count == 103  # 原始行数：2 条预置 + 101 条新插


def test_properties_exclude_superseded_but_history_keeps_them(detail):
    """同一个响应里两种相反的规矩：当前值排掉 superseded，历史全要。"""
    result = detail["use_case"].execute("ent_1")

    assert [item.id for item in result.properties] == ["prop_live"]
    assert {item.id for item in result.property_history} == {"prop_live", "prop_old"}


def test_facts_match_by_name_on_either_side(detail):
    """``subject`` 或 ``object`` 等于 ``canonical_name`` 就算，**不按 id**——
    2.0 的 ``facts`` 表存的就是名字。不沾边的那条不进结果。
    """
    result = detail["use_case"].execute("ent_1")

    assert {item.id for item in result.facts} == {"fact_subject", "fact_object"}


def test_a_rejected_cluster_still_appears_in_the_detail(detail):
    """``clusters`` 没有状态过滤（1.0 ``db.py:4580``）——与 ``properties`` 相反。

    定点变异：给簇查询加上 ``AND status != 'rejected'``，这条变红。
    """
    result = detail["use_case"].execute("ent_1")

    assert [item.cluster.id for item in result.clusters] == ["cluster_rejected"]
    assert [face.id for face in result.clusters[0].samples] == ["face_1"]


def test_events_come_from_the_event_ids_union(detail):
    """事件来自 ``event_ids`` 的三条来源并集，逐条取本体。"""
    result = detail["use_case"].execute("ent_1")

    assert [item.id for item in result.events] == ["ev_1"]
    assert result.events[0].title == "游湖"


def test_observations_are_newest_first_with_their_asset(detail):
    """``captured_at`` 降序，每条带 asset（1.0 是把 asset 整行挂上去）。"""
    result = detail["use_case"].execute("ent_1")

    assert [item.observation.id for item in result.observations] == ["obs_2", "obs_1"]
    assert result.observations[0].asset is not None
    assert result.observations[0].asset.file_name == "asset_2.jpg"


def test_evidence_files_cover_both_kinds(detail):
    """观察与外观证据合到一张表里，各带自己的 ``kind``。"""
    result = detail["use_case"].execute("ent_1")

    by_id = {item.evidence_id: item for item in result.evidence_files}
    assert by_id["obs_1"].kind == EVIDENCE_KIND_OBSERVATION
    assert by_id["obs_1"].file_name == "asset_1.jpg"
    assert by_id["app_1"].kind == EVIDENCE_KIND_APPEARANCE
    # 外观那条的 asset 是 asset_2（与它的 observation 指向的资产不同），
    # file_name 来自**联表**而不是 infer——所以这里必须是 asset_2.jpg。
    assert by_id["app_1"].file_name == "asset_2.jpg"


def test_appearance_evidence_keeps_empty_clothing_rows(detail):
    """``include_empty=True``（1.0 ``db.py:4609``）：空 clothing 的那些**也要**。

    1.0 在**别处**统计「模型判不出来的比例」时才要它们，实体详情这里同样传了 ``True``。
    定点变异：把 ``include_empty`` 去掉（默认 False），这条变红。
    """
    with detail["db"].connect() as connection, connection:
        connection.execute(
            "INSERT INTO person_appearance_evidence (id, person_id, face_instance_id,"
            " observation_id, asset_id, crop_bbox_json, clothing_json, confidence, model_name,"
            " status, created_at, updated_at)"
            " VALUES ('app_empty', 'ent_1', 'face_1', 'obs_1', 'asset_2', '[]', '[]', 0.3,"
            " 'vlm-empty', 'confirmed', ?, ?)",
            (NOW, NOW),
        )

    result = detail["use_case"].execute("ent_1")

    assert {item.id for item in result.appearance_evidence} == {"app_1", "app_empty"}


def test_the_appearance_file_name_is_joined_not_guessed(detail):
    """``file_name`` 来自**联表**（1.0 ``db.py:4535-4546`` 的 ``JOIN assets a``）。

    定点变异：把 ``appearance.py`` 的 JOIN 去掉（只剩 ``asset_id``），这条变红——
    兜底会给出 ``asset_2``，而正确答案是 ``asset_2.jpg``。
    """
    result = detail["use_case"].execute("ent_1")

    appearance = result.appearance_evidence[0]
    assert appearance.file_name == "asset_2.jpg"
    assert appearance.media_type == "image"
    assert appearance.clothing == ("蓝色外套",)


def test_the_avatar_ignores_rejected_clusters(detail):
    """头像仍然排除被拒的簇——与 ``clusters`` 那块**相反的规矩**。

    这个实体名下的唯一簇是被拒的，所以没有头像。定点变异：去掉头像查询里的
    ``fc.status != 'rejected'``，这条变红。
    """
    result = detail["use_case"].execute("ent_1")

    assert result.avatar_face_instance_id is None


def test_an_appearance_id_colliding_with_an_observation_wins(detail):
    """``evidence_files`` 是 dict 合并，**后写覆盖先写**——1.0 先放观察再放外观，
    所以撞车时外观那条赢。这条钉住那个顺序（调换两个循环就会红）。
    """
    with detail["db"].connect() as connection, connection:
        # 造一条 id 与观察相同的“外观证据”，看谁赢。
        connection.execute(
            "INSERT INTO person_appearance_evidence (id, person_id, face_instance_id,"
            " observation_id, asset_id, crop_bbox_json, clothing_json, confidence, model_name,"
            " status, created_at, updated_at)"
            " VALUES ('obs_1', 'ent_1', 'face_1', 'obs_1', 'asset_2', '[]', '[\"红\"]', 0.4,"
            " 'vlm-collide', 'confirmed', ?, ?)",
            (NOW, NOW),
        )

    result = detail["use_case"].execute("ent_1")

    by_id = {item.evidence_id: item for item in result.evidence_files}
    assert by_id["obs_1"].kind == EVIDENCE_KIND_APPEARANCE
    assert by_id["obs_1"].asset_id == "asset_2"


def test_relationships_cover_both_ends_and_ignore_status(detail):
    """``relationships``（S7a · T201）。1.0 ``db.py:4587`` 调
    ``list_relationships(entity_id)``（``:5590-5597``），三条边界一起钉：

    - **两端都算**：``subject_entity_id = ? OR object_entity_id = ?``；
    - **没有 status 过滤**：``retracted`` 的也返回——与同仓的
      ``list_person_relationships``（``:5601``，有 ``status != 'retracted'``）相反，
      也与同一响应里的 ``properties``（排 superseded）相反；
    - **``updated_at DESC``**，每行带 JOIN 出的 ``subject_name`` / ``object_name``。

    定点变异：给 ``list_for_entity`` 的 SQL 加 ``AND r.status != 'retracted'``，
    这条变红。
    """
    db = detail["db"]
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status, summary,"
            " confidence, created_at, updated_at)"
            " VALUES ('ent_2', ?, 'person', '妈妈', 'confirmed', '', 0.0, ?, ?)",
            (SCOPE, NOW, NOW),
        )
        for rel_id, subject, predicate, obj, status, updated in (
            ("rel_old", "ent_1", "出现在", "ent_2", "active", NOW),
            ("rel_new", "ent_2", "记录于", "ent_1", "retracted", LATER),
        ):
            connection.execute(
                "INSERT INTO relationships (id, scope_id, subject_entity_id, predicate,"
                " object_entity_id, status, confidence, evidence_ids_json, revision,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0.5, '[]', 1, ?, ?)",
                (rel_id, SCOPE, subject, predicate, obj, status, NOW, updated),
            )

    result = detail["use_case"].execute("ent_1")

    assert [item.id for item in result.relationships] == ["rel_new", "rel_old"]
    # 被撤回的那条也在——这一层不过滤 status。
    assert result.relationships[0].status == "retracted"
    # 两个方向都算：rel_old 里 ent_1 是 subject，rel_new 里 ent_1 是 object。
    newest, oldest = result.relationships
    assert (newest.subject_name, newest.object_name) == ("妈妈", "西湖")
    assert (oldest.subject_name, oldest.object_name) == ("西湖", "妈妈")


def test_the_property_value_round_trips_through_json(detail):
    """属性值是 JSON 解出来的（``value_json`` → ``value``），不是原始串。"""
    result = detail["use_case"].execute("ent_1")

    assert result.properties[0].value == "湖边"
    assert json.loads('"湖边"') == result.properties[0].value
