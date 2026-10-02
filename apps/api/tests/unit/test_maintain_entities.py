"""非人物实体抽取（S6 · T127）：纯规则 + 用例两半。

这份用例要钉三件事，第三件是这次最要紧的：

1. **词表与边界的每一个数**（``time_semantics`` 的六段与四季、``object_category`` 的
   子串匹配与类别顺序）——它们是产品行为，改一个数字就是改归类；
2. **回退链**：1.0 首选语义词表，2.0 没有，所以走的是 1.0 自己的回退那一支
   （``observation.place`` / ``observation.objects`` / ``captured_at``）；
3. **建关系**（S7a · T204 把 S6 的收窄补回来了）。1.0 那个函数最后会给每个实体建两条
   （「出现在」/「记录于」）。**这三条断言必须有**：S6 那次收窄没有门禁替它把关，
   当时的原话是「哪天有人顺手补全，只会多出几行数据，不会有任何东西报错」——
   T204 正是那个「顺手补全」，所以现在反过来由用例钉住补全后的形状。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.media.maintain_entities import MaintainObservationEntities
from sentrix.domain.identity.extraction import (
    EVENT_RELATIONS,
    UNKNOWN_PLACE,
    build_entity_specs,
    object_category,
    time_semantics,
)
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.participants import (
    SqliteEventParticipantRepository,
)
from sentrix.infrastructure.persistence.repositories.relationships import (
    SqliteRelationshipRepository,
)

SCOPE = "album-t127"


# ---- time_semantics：六段与四季的每一个边界 ------------------------------------------


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "凌晨"),
        (4, "凌晨"),
        (5, "清晨"),
        (7, "清晨"),
        (8, "上午"),
        (11, "上午"),
        (12, "午后"),
        (16, "午后"),
        (17, "傍晚"),
        (19, "傍晚"),
        (20, "夜晚"),
        (23, "夜晚"),
    ],
)
def test_part_of_day_boundaries(hour: int, expected: str) -> None:
    """1.0 ``db.py:256-267`` 的六段，逐个边界。**每一个 ``<`` 都取在边界值上**：
    写成 ``<=`` 就整体错一格，而那不报错，只是所有照片的时段标签偏了。
    """
    assert time_semantics(datetime(2019, 7, 22, hour, tzinfo=UTC))["part_of_day"] == expected


@pytest.mark.parametrize(
    ("month", "expected"),
    [
        (1, "冬"),
        (2, "冬"),
        (3, "春"),
        (4, "春"),
        (5, "春"),
        (6, "夏"),
        (7, "夏"),
        (8, "夏"),
        (9, "秋"),
        (10, "秋"),
        (11, "秋"),
        (12, "冬"),
    ],
)
def test_season_boundaries(month: int, expected: str) -> None:
    assert time_semantics(datetime(2019, month, 15, 12, tzinfo=UTC))["season"] == expected


def test_the_calendar_labels_are_the_exif_values() -> None:
    """``date`` / ``year`` / ``month`` 是**原样派生**，不是本地化过的串。"""
    labels = time_semantics(datetime(2019, 7, 22, 9, tzinfo=UTC))

    assert labels["date"] == "2019-07-22"
    assert labels["year"] == 2019
    assert labels["month"] == 7


# ---- object_category ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("生日蛋糕", "食物"),
        ("自行车", "交通工具"),
        ("一只橘猫", "宠物"),
        ("手机", "电子设备"),
        ("毕业证", "文具与出版物"),
        ("运动鞋", "服饰"),
        ("一块石头", "其他"),
        ("", "其他"),
    ],
)
def test_object_category_matches_by_substring(label: str, expected: str) -> None:
    """**子串而不是等值**：名字是模型写的自由文本（「生日蛋糕」「一只橘猫」），
    等值匹配永远命中不了。"""
    assert object_category(label) == expected


def test_the_category_order_is_part_of_the_judgement() -> None:
    """一个词同时出现在两个类别里时取**先出现**的那个（1.0 的 ``for ... in`` 语义）。

    「猫」在宠物里、「衣」在服饰里——要造一个两边都命中的词才测得到顺序。
    「猫咪的书」同时命中「猫」（宠物）与「书」（文具与出版物），
    而**宠物排在前面**，所以结果是宠物。

    **这条用例存在的理由**：把那两个字换成 dict 的插入顺序之外的东西，
    结果会变而不报错——顺序是判据的一部分，不是实现的偶然。
    """
    assert object_category("猫咪的书") == "宠物"
    # 反过来确认它确实也命中文具那一类（否则上面那条测的不是顺序）
    assert "书" in "猫咪的书"


# ---- build_entity_specs：回退链 ------------------------------------------------------


def test_the_place_spec_falls_back_to_the_unknown_name() -> None:
    """1.0 ``db.py:3789`` 的 ``place_name = selected_scene or visual_place or "其他或不确定"``。
    2.0 没有语义词表那一支，所以没有自由文本地点时就是那个兜底名——
    **没有地点信息的照片会共用这一个实体**，那是 1.0 的行为。"""
    specs = build_entity_specs(place="", objects=(), captured_at=None)

    assert [(s.entity_type, s.name) for s in specs] == [("place", UNKNOWN_PLACE)]


def test_objects_become_one_spec_each() -> None:
    specs = build_entity_specs(place="家中餐厅", objects=("蛋糕", "蜡烛"), captured_at=None)

    assert [(s.entity_type, s.name) for s in specs] == [
        ("place", "家中餐厅"),
        ("object", "蛋糕"),
        ("object", "蜡烛"),
    ]


def test_without_a_capture_time_there_is_no_time_spec() -> None:
    """1.0 写成 ``("time", captured_day, ...) if captured_day else ("time", [], "")``——
    **没有拍摄时间就产出一个空名字列表、等于不建**。这里直接跳过，形状等价。"""
    specs = build_entity_specs(place="x", objects=(), captured_at=None)

    assert not [s for s in specs if s.entity_type == "time"]


def test_the_capture_day_becomes_the_time_name() -> None:
    specs = build_entity_specs(place="x", objects=(), captured_at=datetime(2019, 7, 22, 9, tzinfo=UTC))

    assert [(s.entity_type, s.name) for s in specs if s.entity_type == "time"] == [("time", "2019-07-22")]


def test_atmosphere_is_not_built() -> None:
    """**这一条钉的是那次留白。** ``atmosphere`` 的两个来源（语义词表与 ``emotions``）
    2.0 都没有，而 1.0 在词表不可用时同样产不出名字——所以「不建」正是 1.0 在同样
    输入下的行为。哪天有人把 `emotions` 搬进来，这条会红，那时应当同时改计划。"""
    specs = build_entity_specs(
        place="家中餐厅", objects=("蛋糕",), captured_at=datetime(2019, 7, 22, 9, tzinfo=UTC)
    )

    assert "atmosphere" not in {s.entity_type for s in specs}
    assert "emotion" not in {s.entity_type for s in specs}


def test_every_entity_type_has_an_event_relation_name() -> None:
    """``EVENT_RELATIONS`` 少一条的后果是那个类型在 ``event_entities`` 上退化成
    「关联实体」——不报错，只是事件详情里那句话不对。"""
    assert set(EVENT_RELATIONS) == {"place", "time", "object", "atmosphere"}


# ---- 用例 ----------------------------------------------------------------------------


@pytest.fixture
def bundle(migrations_ready_db):
    # **外键链是 asset → observation → entity_observations**，``Database`` 开着
    # ``PRAGMA foreign_keys``，所以三层都得先有行。这里只落满足外键的那两层，
    # 观察的内容由各用例自己那份 ``observation()`` 描述。
    assets = SqliteAssetRepository(migrations_ready_db)
    assets.add(
        Asset(
            id="asset_1",
            scope_id=SCOPE,
            file_name="photo.jpg",
            media_type=MediaType.IMAGE,
            path=str(Path("photo.jpg")),
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256="h",
            captured_at=datetime(2019, 7, 22, 9, tzinfo=UTC),
            captured_location=None,
            created_at=datetime(2019, 7, 22, 9, tzinfo=UTC),
            updated_at=datetime(2019, 7, 22, 9, tzinfo=UTC),
        )
    )
    SqliteObservationRepository(migrations_ready_db).add(observation())
    return {
        "db": migrations_ready_db,
        "assets": SqliteAssetRepository(migrations_ready_db),
        "entities": SqliteEntityRepository(migrations_ready_db),
        "events": SqliteEventParticipantRepository(migrations_ready_db),
        "relationships": SqliteRelationshipRepository(migrations_ready_db),
    }


def observation(**overrides: object) -> Observation:
    fields: dict[str, object] = {
        "id": "obs_1",
        "scope_id": SCOPE,
        "asset_id": "asset_1",
        "source_type": "import",
        "captured_at": datetime(2019, 7, 22, 9, tzinfo=UTC),
        "confidence": 0.65,
        "created_at": datetime(2019, 7, 22, 9, tzinfo=UTC),
        "updated_at": datetime(2019, 7, 22, 9, tzinfo=UTC),
        "place": "家中餐厅",
        "objects": ("生日蛋糕", "蜡烛"),
    }
    fields.update(overrides)
    return Observation(**fields)  # type: ignore[arg-type]


def seed_event(bundle, event_id: str = "event_1") -> str:
    """落一个事件行。``event_entities.event_id`` 有 REFERENCES，没有它那条写入直接
    ``IntegrityError``——**这是「事件必须先存在」在夹具上的体现**，不是绕开约束。"""
    with bundle["db"].connect() as connection, connection:
        connection.execute(
            "INSERT INTO events (id, scope_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (
                event_id,
                SCOPE,
                "测试事件",
                datetime(2019, 7, 22, 9, tzinfo=UTC).isoformat(),
                datetime(2019, 7, 22, 9, tzinfo=UTC).isoformat(),
            ),
        )
    return event_id


def build(bundle) -> MaintainObservationEntities:
    return MaintainObservationEntities(
        entities=bundle["entities"],
        events=bundle["events"],
        assets=bundle["assets"],
        relationships=bundle["relationships"],
    )


def rows(bundle, table: str, where: str = "1=1", params: tuple[object, ...] = ()) -> list:
    with bundle["db"].connect() as connection:
        return connection.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()


def test_it_creates_the_three_entity_types(bundle) -> None:
    result = build(bundle).execute(observation())

    types = sorted(entity.entity_type for entity in result.entities)
    assert types == ["object", "object", "place", "time"]


def test_it_is_idempotent(bundle) -> None:
    """**可重跑**：同一条观察跑两次不产生第二套实体。

    导入流水线真的会重跑（换模型、补全、失败重试），而重复的实体不会报错——
    它们只是让「这个地方出现在多少张照片里」这个统计凭空翻倍。
    """
    build(bundle).execute(observation())
    second = build(bundle).execute(observation())

    assert len(rows(bundle, "entities")) == 4, "第二次不该再建实体"
    assert len(rows(bundle, "entity_observations")) == 4, "关联行也不该翻倍"
    assert len(second.entities) == 4


def test_a_rerun_raises_the_link_confidence_instead_of_doing_nothing(bundle) -> None:
    """**``ON CONFLICT`` 那一支是 ``DO UPDATE SET confidence = MAX(...)``，不是 ``DO NOTHING``。**

    两者在**行数**上完全一样（都只留一行），差别只在置信度那一列——所以只断言行数的
    话，把它们互换是一个**观察不到**的改动。1.0 ``db.py:3833-3836`` 要的是前者：
    同一条观察重跑时，这条关联的置信度应当跟着抬高（重跑常常是因为换了更好的模型）。
    """
    build(bundle).execute(observation(confidence=0.4))
    build(bundle).execute(observation(confidence=0.9))

    links = rows(bundle, "entity_observations")
    assert len(links) == 4
    assert all(row["confidence"] == pytest.approx(0.9) for row in links)


def test_it_links_the_observation_once(bundle) -> None:
    """``entity_observations`` 是「这条观察提到了它」——1.0 ``db.py:3831`` 的
    ``ON CONFLICT ... DO UPDATE``。**T127 之前这张表一个写入者都没有。**"""
    build(bundle).execute(observation())

    links = rows(bundle, "entity_observations")
    assert {row["observation_id"] for row in links} == {"obs_1"}
    assert all(row["source"] == "observation_extraction" for row in links)


def test_object_entities_get_label_and_category(bundle) -> None:
    """1.0 ``db.py:3891-3899`` 的前两条属性。``category`` 走那六个类别的词表。"""
    build(bundle).execute(observation())

    cake = next(e for e in rows(bundle, "entities") if e["canonical_name"] == "生日蛋糕")
    props = {
        row["property_key"]: row["value_json"]
        for row in rows(bundle, "entity_properties", "entity_id = ?", (cake["id"],))
    }

    assert props["label"] == '"生日蛋糕"'
    assert props["category"] == '"食物"'


def test_time_entities_get_the_calendar_properties(bundle) -> None:
    """1.0 ``db.py:3925-3934``：四个标量 + 一个多值的 ``part_of_day``。"""
    build(bundle).execute(observation())

    time_entity = next(e for e in rows(bundle, "entities") if e["entity_type"] == "time")
    props = {
        row["property_key"]: row["value_json"]
        for row in rows(bundle, "entity_properties", "entity_id = ?", (time_entity["id"],))
    }

    assert props["date"] == '"2019-07-22"'
    assert props["year"] == "2019"
    assert props["month"] == "7"
    assert props["season"] == '"夏"'
    assert props["part_of_day"] == '["上午"]'


def test_place_entities_get_geo_and_scene_type(bundle) -> None:
    """地点这一类在 2.0 的输入下写**两条**属性：

    - ``geo`` ← asset 的 GPS（1.0 ``:3839-3844``，来源标记 ``asset_gps``）；
    - ``scene_type`` ← 自由文本地点（无语义词表时 1.0 的 ``scene_type`` 就等于它）。

    **不写 ``visual_place_descriptions``**：1.0 那个条件是
    ``visual_place and (visual_place != scene_type or has_semantic_place)``，
    而 2.0 的输入代进去恒为假——那是 1.0 在同样输入下的行为。
    """
    with bundle["db"].connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET gps_latitude = ?, gps_longitude = ? WHERE id = ?",
            (30.2741, 120.1551, "asset_1"),
        )
    build(bundle).execute(observation())

    place = next(e for e in rows(bundle, "entities") if e["entity_type"] == "place")
    props = {
        row["property_key"]: (row["value_json"], row["source"])
        for row in rows(bundle, "entity_properties", "entity_id = ?", (place["id"],))
    }

    assert props["scene_type"][0] == '"家中餐厅"'
    assert props["scene_type"][1] == "observation_extraction"
    assert props["geo"][0] == '{"latitude": 30.2741, "longitude": 120.1551}'
    assert props["geo"][1] == "asset_gps"
    assert "visual_place_descriptions" not in props, "那个合取在 2.0 的输入下恒为假"


def test_it_links_entities_to_the_event_with_chinese_relations(bundle) -> None:
    """1.0 ``db.py:3935-3939``：``event_entities`` 上每类一个中文关系名。
    **T127 之前这张表也是零行**（``participants.py`` 的注释写着「等 S6 有了写入者再开口」）。"""
    build(bundle).execute(observation(), event_id=seed_event(bundle))

    relations = {row["relation"] for row in rows(bundle, "event_entities")}
    assert relations == {"地点", "包含物件", "时间"}


def test_without_an_event_there_is_no_event_link(bundle) -> None:
    build(bundle).execute(observation())

    assert rows(bundle, "event_entities") == []


def _entity_id(bundle, name: str) -> str:
    found = rows(bundle, "entities", "canonical_name = ?", (name,))
    assert len(found) == 1, f"{name} 应该是唯一实体，实际 {len(found)} 行"
    return str(found[0]["id"])


def test_it_links_every_entity_to_the_place_and_the_time(bundle) -> None:
    """**S7a · T204 把 S6 的收窄补回来了。** 1.0 ``db.py:3943-3952``。

    这次观察有 place（家中餐厅）、captured_at（2019-07-22）与两个物件，一共四个实体：
    ``出现在`` 从每个非 place 的实体连到 place（三个：两个物件 + time），
    ``记录于`` 从每个非 time 的实体连到 time（三个：place + 两个物件），合计六条。

    **自环是这里挡的**：place 不连自己、time 不连自己——``create_relationship``
    没有自环校验（1.0 也没有），漏掉那两行判断就会多出两条 ``出现在`` / ``记录于``
    的自环，而且不会有任何东西报错。
    """
    build(bundle).execute(observation(), event_id=seed_event(bundle))

    found = rows(bundle, "relationships")
    by_predicate = {item["predicate"] for item in found}

    assert by_predicate == {"出现在", "记录于"}
    assert len([item for item in found if item["predicate"] == "出现在"]) == 3
    assert len([item for item in found if item["predicate"] == "记录于"]) == 3
    # **方向**也要钉：只数条数的话，把两个谓词对调不会被抓到（对调后两边还是 3 条）。
    assert {item["object_entity_id"] for item in found if item["predicate"] == "出现在"} == {
        _entity_id(bundle, "家中餐厅")
    }
    assert {item["object_entity_id"] for item in found if item["predicate"] == "记录于"} == {
        _entity_id(bundle, "2019-07-22")
    }
    # 一条自环都没有。
    assert all(item["subject_entity_id"] != item["object_entity_id"] for item in found)
    # 两条边都是「机器看出来的」，status 走仓储默认值。
    assert {item["status"] for item in found} == {"pending"}
    assert {item["confidence"] for item in found} == {0.65}


def test_the_relationship_evidence_includes_the_event_when_there_is_one(bundle) -> None:
    """``evidence`` 是 ``[observation_id, event_id]``；**没有 event 时只挂观察**
    （1.0 ``db.py:3944`` 的三元表达式）。这一行在写属性与挂事件那两处已经存在，
    关系这两条复用它——所以两处的证据集必须一致。
    """
    build(bundle).execute(observation())  # 不给 event_id

    found = rows(bundle, "relationships")

    assert found
    assert {tuple(json.loads(item["evidence_ids_json"])) for item in found} == {("obs_1",)}


def test_a_rerun_merges_the_relationship_instead_of_adding_rows(bundle) -> None:
    """重跑同一条观察：同三元组走 ``create`` 的合并分支——**行数不变、``revision + 1``**。

    定点变异：把 ``evidence_ids`` 从每次调用改成常量、或给 ``create`` 换一个每次都不同的
    ``predicate``，这条都会红（前者恒真，后者会变成十二条）。
    """
    event_id = seed_event(bundle)
    build(bundle).execute(observation(), event_id=event_id)
    build(bundle).execute(observation(), event_id=event_id)

    found = rows(bundle, "relationships")

    assert len(found) == 6
    assert {item["revision"] for item in found} == {2}


def test_a_rerun_raises_the_confidence_instead_of_adding_rows(bundle) -> None:
    """``find_or_create_entities`` 命中已有实体时抬置信度（1.0 的 ``MAX(confidence, ?)``）。"""
    build(bundle).execute(observation(confidence=0.4))
    build(bundle).execute(observation(confidence=0.9))

    place = next(e for e in rows(bundle, "entities") if e["entity_type"] == "place")
    assert place["confidence"] == pytest.approx(0.9)


# ---- maintain_property_values：合并而不是改口 ---------------------------------------
#
# 这三条直接打端口方法，不走抽取用例。理由：**T127 的常见路径很少走到合并那一支**
# （地点/物件/时间的属性各自都是单值，`part_of_day` 每个时间实体又只写一个值），
# 而它是这次新加的三个端口方法里语义最微妙的一个——`s5-deviations.md` 第 46 条记过
# 它在 1.0「本切片没有调用者」，2.0 又没有别的调用者，所以它只被这份用例守着。


def test_multi_value_properties_merge_and_keep_order(bundle) -> None:
    """1.0 ``db.py:4735`` 的 ``dict.fromkeys(existing + values)``：**并集、保序、去重、
    丢空项**。写成 ``set`` 会打乱顺序，而这份列表要进提示词。"""
    entity = bundle["entities"].create_person("妈妈", scope_id=SCOPE)

    bundle["entities"].maintain_property_values(entity.id, "semantic_details", ["甲", "乙"])
    result = bundle["entities"].maintain_property_values(
        entity.id, "semantic_details", ["乙", "丙", "", None]
    )

    assert result.value == ["甲", "乙", "丙"]


def test_a_user_value_is_never_merged_over(bundle) -> None:
    """1.0 ``db.py:4731-4732``：``source == 'user'`` 就原样返回、一个字节都不写。"""
    entity = bundle["entities"].create_person("妈妈", scope_id=SCOPE)
    bundle["entities"].set_property(entity.id, "semantic_details", ["用户写的"])

    result = bundle["entities"].maintain_property_values(entity.id, "semantic_details", ["模型写的"])

    assert result.value == ["用户写的"]


def test_merging_does_not_start_a_pending_revision(bundle) -> None:
    """**这条是它必须与 ``maintain_property`` 分开存在的理由。**

    多值属性是**累积的证据**：走 ``maintain_property`` 的话，每张照片多一条就会新起
    一条 ``pending`` 的「改口」行（值不同 → 派生新值等人确认），界面会堆满永远没人
    确认的修订。1.0 在这里是**就地 UPDATE**。
    """
    entity = bundle["entities"].create_person("妈妈", scope_id=SCOPE)
    bundle["entities"].maintain_property_values(entity.id, "semantic_details", ["甲"])
    bundle["entities"].maintain_property_values(entity.id, "semantic_details", ["乙"])

    rows_ = [r for r in rows(bundle, "entity_properties", "entity_id = ?", (entity.id,))]

    assert len(rows_) == 1, "合并就地更新，不该多出一行"
    assert rows_[0]["status"] == "active"
    assert rows_[0]["value_json"] == '["甲", "乙"]'
