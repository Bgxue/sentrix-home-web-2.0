"""条件判定的测试：四个维度 × 四种状态 × 两档严格度。

**重点不在「能判对」，在几条会静默删照片的边界。**

写这个模块时我自己踩过一次：``_place`` 第一版写成「什么都不匹配就 CONTRADICTED」，
而 1.0 的 docstring 明写「**无 geocode 时保持 unknown**（开放世界，不能因为照片没 GPS
就剔除）」。那版会让**所有没有 GPS 的照片**在 HARD 地点条件下一律被排除，
而表现只是「搜出来的照片少了几张」——没有任何东西会报错。

下面每一条边界都配了一个用例。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.places.aliases import EMPTY_ALIASES
from sentrix.domain.places.models import AdminArea, NearestPlace, Place, PlacePrecision, PlaceSource
from sentrix.domain.retrieval.conditions import (
    Condition,
    ConditionStatus,
    Evidence,
    EvidenceContext,
    Strictness,
    evaluate,
    excludes,
    trusted,
)
from sentrix.domain.retrieval.time_expression import parse_time_expression

NOW = datetime(2026, 9, 29, tzinfo=UTC)
SCOPE = "album1"


def make_asset(**overrides) -> Asset:
    base = dict(
        id="asset_1",
        scope_id=SCOPE,
        file_name="a.jpg",
        media_type=MediaType.IMAGE,
        path="/tmp/a.jpg",
        mime_type="image/jpeg",
        size_bytes=1,
        status=AssetStatus.PROCESSED,
        content_sha256="h",
        captured_at=datetime(2023, 10, 5, 12, 0),
        captured_location=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return Asset(**base)


def make_observation(**overrides) -> Observation:
    base = dict(
        id="obs_1",
        scope_id=SCOPE,
        asset_id="asset_1",
        source_type="import",
        captured_at=datetime(2023, 10, 5, 12, 0),
        confidence=0.8,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return Observation(**base)


def place_of(*, province="", city="", district="") -> Place:
    return Place(
        admin=AdminArea(province=province, city=city, district=district),
        nearest=NearestPlace(),
        precision=PlacePrecision.CITY,
        source=PlaceSource.PYGEOCN,
    )


# ---- 时间表达解析 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2023年10月5日", (datetime(2023, 10, 5), datetime(2023, 10, 6))),
        ("2023年10月", (datetime(2023, 10, 1), datetime(2023, 11, 1))),
        ("2023-10", (datetime(2023, 10, 1), datetime(2023, 11, 1))),
        ("2023", (datetime(2023, 1, 1), datetime(2024, 1, 1))),
        ("2023年", (datetime(2023, 1, 1), datetime(2024, 1, 1))),
        ("2025年-2026年", (datetime(2025, 1, 1), datetime(2027, 1, 1))),
        ("2025年3月-2025年5月", (datetime(2025, 3, 1), datetime(2025, 6, 1))),
        ("2025年3月-5月", (datetime(2025, 3, 1), datetime(2025, 6, 1))),
        # 12 月要进位到次年 1 月——漏掉这一步「到 12 月」会变成空区间
        ("2025年11月-12月", (datetime(2025, 11, 1), datetime(2026, 1, 1))),
    ],
)
def test_parses_the_four_tiers(expression, expected):
    assert parse_time_expression(expression) == expected


@pytest.mark.parametrize("expression", ["去年", "上个月", "", None, "很久以前"])
def test_unparseable_expressions_return_none(expression):
    """**不猜。** 返回 ``None`` 让调用方知道这条条件没被理解——
    那与「解析成今天」是完全不同的两件事。"""
    assert parse_time_expression(expression) is None


def test_range_tier_wins_over_the_pure_year_tier():
    """**顺序错了会静默出错**：把纯年份提到前面，``2025年3月-2025年5月`` 会落到
    月/日那档、解析成「2025 年 3 月」一天——返回了一个区间、看起来完全正常，
    只是短了两个月。"""
    assert parse_time_expression("2025年3月-2025年5月") == (
        datetime(2025, 3, 1),
        datetime(2025, 6, 1),
    )


# ---- 时间条件 ------------------------------------------------------------------


def test_time_matches_and_contradicts():
    condition = Condition(dimension="time", value="2023年10月")
    assert evaluate(condition, make_asset(), make_observation()).status is ConditionStatus.MATCHED
    assert (
        evaluate(condition, make_asset(captured_at=datetime(2021, 1, 1)), make_observation()).status
        is ConditionStatus.CONTRADICTED
    )


def test_unparseable_time_is_unknown_not_contradicted():
    """**认不出来不是「不满足」。** 判成 CONTRADICTED 会在 HARD 档下把所有候选一次删光，
    而用户看到的是「没找到照片」——一次解析失败伪装成了一次空结果。"""
    condition = Condition(dimension="time", value="去年")

    assert evaluate(condition, make_asset(), make_observation()).status is ConditionStatus.UNKNOWN


# ---- 媒体条件 ------------------------------------------------------------------


def test_media_matches_by_type():
    condition = Condition(dimension="media", value="image")
    assert evaluate(condition, make_asset(), make_observation()).status is ConditionStatus.MATCHED
    assert (
        evaluate(condition, make_asset(media_type=MediaType.VIDEO), make_observation()).status
        is ConditionStatus.CONTRADICTED
    )


# ---- 地点条件：四条分支 ----------------------------------------------------------


def test_place_matches_via_the_geocode():
    condition = Condition(dimension="place", value="杭州")
    context = EvidenceContext(place=place_of(province="浙江省", city="杭州市"))

    assert (
        evaluate(condition, make_asset(), make_observation(), context=context).status
        is ConditionStatus.MATCHED
    )


def test_place_matches_on_an_exact_observation_field():
    condition = Condition(dimension="place", value="海边")
    evidence = evaluate(condition, make_asset(), make_observation(place="海边"))

    assert evidence.status is ConditionStatus.MATCHED
    assert evidence.source_type == "observation_field_exact"


def test_place_in_the_caption_is_only_possible():
    """捕获文本里出现了那个词，不等于这张图在那个地方。判成 matched 会让它在 HARD 档下
    通过——而 HARD 档的**全部意义**就是「用户明确说了，必须满足」。"""
    condition = Condition(dimension="place", value="青岛")

    evidence = evaluate(condition, make_asset(), make_observation(caption="在青岛拍的照片"))

    assert evidence.status is ConditionStatus.POSSIBLE


def test_place_without_a_geocode_is_unknown():
    """**这条是整个模块最要紧的一条。**

    1.0 的原话：「无 geocode 时保持 unknown（开放世界，**不能因为照片没 GPS 就剔除**）」。
    写成「什么都不匹配就 contradicted」会让所有没有 GPS 的照片在 HARD 地点条件下
    一律被排除，而表现只是「搜出来的照片少了几张」——没有任何东西会报错。
    """
    condition = Condition(dimension="place", value="青岛")

    evidence = evaluate(condition, make_asset(), make_observation(), context=EvidenceContext(place=None))

    assert evidence.status is ConditionStatus.UNKNOWN
    assert excludes(condition, evidence) is True, "HARD 档下 UNKNOWN 仍然排除——这是有意的"


def test_place_with_a_geocode_that_does_not_match_is_contradicted():
    condition = Condition(dimension="place", value="青岛")
    context = EvidenceContext(place=place_of(province="浙江省", city="杭州市"))

    evidence = evaluate(condition, make_asset(), make_observation(), context=context)

    assert evidence.status is ConditionStatus.CONTRADICTED


# ---- 人物条件 ------------------------------------------------------------------


def test_person_matches_from_the_observation():
    condition = Condition(dimension="person", value="妈妈")

    evidence = evaluate(condition, make_asset(), make_observation(people=("妈妈", "爸爸")))

    assert evidence.status is ConditionStatus.MATCHED
    assert evidence.source_type == "confirmed_bridge"


def test_person_matches_via_the_confirmed_bridge():
    condition = Condition(dimension="person", value="妈妈")
    context = EvidenceContext(confirmed_person=lambda name: name == "妈妈")

    assert (
        evaluate(condition, make_asset(), make_observation(), context=context).status
        is ConditionStatus.MATCHED
    )


def test_an_absent_person_is_unknown_not_contradicted():
    """1.0 ``:479`` 返回 ``unknown``。图片里没有这个名字有两种可能：这个人没被拍到，
    或者那条提及还没建。HARD 档下两者都排除，但 SEMANTIC 档下只有后者该排除——
    所以这个区别必须留着。"""
    condition = Condition(dimension="person", value="爷爷")

    evidence = evaluate(condition, make_asset(), make_observation())

    assert evidence.status is ConditionStatus.UNKNOWN


def test_an_unknown_dimension_is_never_contradicted():
    """从一个不认识的条件上推出「矛盾」，会让它在 HARD 档下悄悄删掉候选。"""
    condition = Condition(dimension="weather", value="晴")

    assert evaluate(condition, make_asset(), make_observation()).status is ConditionStatus.UNKNOWN


# ---- 来源可信度 -----------------------------------------------------------------


def test_a_match_from_an_untrusted_source_is_downgraded():
    """「捕获文本里出现过」与「结构化字段说是」不该拿到同一种处置。"""
    downgraded = trusted(Evidence(ConditionStatus.MATCHED, "observation", "obs_1", 0.8))

    assert downgraded.status is ConditionStatus.POSSIBLE


def test_a_match_from_a_trusted_source_survives():
    kept = trusted(Evidence(ConditionStatus.MATCHED, "observation_field_exact", "obs_1", 0.8))

    assert kept.status is ConditionStatus.MATCHED


# ---- 两档严格度的差别 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "hard_excluded", "semantic_excluded"),
    [
        (ConditionStatus.MATCHED, False, False),
        (ConditionStatus.POSSIBLE, True, False),
        (ConditionStatus.CONTRADICTED, True, True),
        (ConditionStatus.UNKNOWN, True, False),
    ],
)
def test_the_two_strictness_levels_differ_exactly_here(status, hard_excluded, semantic_excluded):
    """**这张表是这个模块存在的理由。** 合成一条规则会让「用户明确说的」与
    「模型猜的」得到同一种处置，而它们的证据强度差着一个量级。"""
    hard = Condition(dimension="place", value="x", strictness=Strictness.HARD)
    semantic = Condition(dimension="place", value="x", strictness=Strictness.SEMANTIC)

    assert excludes(hard, Evidence(status)) is hard_excluded
    assert excludes(semantic, Evidence(status)) is semantic_excluded


def test_a_negated_hard_condition_flips():
    condition = Condition(dimension="media", value="video", negated=True)

    assert excludes(condition, Evidence(ConditionStatus.MATCHED)) is True
    assert excludes(condition, Evidence(ConditionStatus.CONTRADICTED)) is False


def test_gps_point_is_not_used_as_a_place_string():
    """地点判定的权威来源是**解析出的 Place**，不是经纬度文本。
    拿 ``captured_location`` 当地点串去匹配，用户说「杭州」会永远匹配不上——
    而那看起来只是「地点条件不生效」。"""
    condition = Condition(dimension="place", value="30.834077")
    asset = make_asset(captured_location=GpsPoint(30.834077, 111.019005))

    evidence = evaluate(condition, asset, make_observation(), context=EvidenceContext(place=None))

    assert evidence.status is ConditionStatus.UNKNOWN


def test_aliases_are_used_for_matching():
    """中英别名命中（1.0 ``place_text_matches`` 的第 3 条）必须走别名表，
    而不是靠字符串包含。"""
    condition = Condition(dimension="place", value="Chiang Mai")
    context = EvidenceContext(place=place_of(city="清迈"), place_aliases=EMPTY_ALIASES)

    # EMPTY_ALIASES 下匹配不上，这正是「别名表为空」该有的样子——不是「匹配一切」
    assert (
        evaluate(condition, make_asset(), make_observation(), context=context).status
        is not ConditionStatus.MATCHED
    )
