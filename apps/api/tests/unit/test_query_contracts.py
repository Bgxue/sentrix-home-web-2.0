"""查询契约的测试：约束派生、时间说法解析、媒体类型识别。

**边界比正例重要。** 三条会静默出错的形状各配了用例：

1. ``scope_id`` 在「跨相册」时必须返回 ``None``——写成 ``scope_ids[0]`` 会把
   「全部相册」悄悄变成「只查第一个相册」，而结果只是少几张照片；
2. 相对时间认不出来时必须返回 ``None``——把原文丢给严格的时间解析器，
   解析失败在 HARD 档下会把候选**全部排除**，用户看到的是「一张都没有」；
3. ``entity_resolver`` 解不出人名时不派生 person 约束——那与「这个人不存在」是两回事，
   而派生出来的 HARD 约束会排除一切。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sentrix.domain.retrieval.query_contracts import (
    Constraint,
    QueryParseDraft,
    QuerySpec,
    Strictness,
    build_query_spec,
    parse_media_type,
    resolve_time_expression,
)

NOW = datetime(2026, 9, 29)


# ---- Constraint / QuerySpec -----------------------------------------------------


def test_constraint_key_is_dimension_colon_value():
    assert Constraint("place", "杭州", Strictness.HARD, "asset_metadata").key == "place:杭州"


def test_scope_id_is_none_when_there_is_more_than_one_scope():
    """**写成 ``scope_ids[0]`` 会把「全部相册」变成「只查第一个相册」**，
    而结果只是少几张照片——没有任何报错。"""
    many = QuerySpec(
        query_id="q",
        scope_mode="all_authorized",
        scope_ids=[],
        viewer_id="owner",
        conversation_id="c",
        intent="answer",
        answer_target="general",
        constraints=[],
    )
    assert many.scope_id is None

    one = QuerySpec(
        query_id="q",
        scope_mode="single",
        scope_ids=["album1"],
        viewer_id="owner",
        conversation_id="c",
        intent="answer",
        answer_target="general",
        constraints=[],
    )
    assert one.scope_id == "album1"


def test_constraints_for_filters_by_dimension():
    spec = QuerySpec(
        query_id="q",
        scope_mode="single",
        scope_ids=["a"],
        viewer_id="owner",
        conversation_id="c",
        intent="answer",
        answer_target="general",
        constraints=[
            Constraint("time", "2023年", Strictness.HARD, "asset_metadata"),
            Constraint("place", "杭州", Strictness.SEMANTIC, "direct_or_possible"),
        ],
    )

    assert [item.dimension for item in spec.constraints_for("place")] == ["place"]
    assert spec.constraints_for("person") == []


# ---- build_query_spec -----------------------------------------------------------


def test_derives_hard_constraints_for_time_media_person():
    draft = QueryParseDraft(
        time_expression="2023年",
        media_expressions=["照片"],
        entity_names=["妈妈"],
    )

    spec = build_query_spec(
        draft,
        scope_id="album1",
        viewer_id="owner",
        conversation_id="c",
        entity_resolver=lambda name: "entity_1" if name == "妈妈" else None,
    )

    by_dimension = {item.dimension: item for item in spec.constraints}
    assert by_dimension["time"].strictness is Strictness.HARD
    assert by_dimension["media"].value == "image"
    assert by_dimension["person"].proof_policy == "confirmed_bridge"
    assert spec.entity_ids == ["entity_1"]


def test_an_unresolvable_person_does_not_produce_a_constraint():
    """**解不出人名时不派生 person 约束。** 那与「这个人不存在」是两回事——
    派生出来的 HARD 约束会排除一切候选，而表现是「按人搜一张都没有」。"""
    draft = QueryParseDraft(entity_names=["查无此人"])

    spec = build_query_spec(
        draft,
        scope_id="album1",
        viewer_id="owner",
        conversation_id="c",
        entity_resolver=lambda name: None,
    )

    assert [item.dimension for item in spec.constraints] == []
    assert spec.entity_ids == []


def test_an_unparseable_time_expression_does_not_produce_a_constraint():
    """同上：``build_query_spec`` 只在 ``parse_time_expression`` 认得时才派生。"""
    draft = QueryParseDraft(time_expression="上上上个月")

    spec = build_query_spec(draft, scope_id="album1", viewer_id="owner", conversation_id="c")

    assert spec.constraints == []


def test_semantic_conditions_are_never_hard():
    """语义条件不排除候选——那是两档严格度存在的理由。"""
    draft = QueryParseDraft(
        semantic_conditions=[
            {"dimension": "place", "value": "海边"},
            {"dimension": "看不懂的维度", "value": "x"},
        ]
    )

    spec = build_query_spec(draft, scope_id="album1", viewer_id="owner", conversation_id="c")

    assert all(item.strictness is Strictness.SEMANTIC for item in spec.constraints)
    # 认不出的维度归到 generic semantic，**不是**原样带进去
    assert [item.dimension for item in spec.constraints] == ["place", "semantic"]


def test_negative_conditions_are_hard_and_negated():
    draft = QueryParseDraft(
        negative_conditions=[{"dimension": "media", "value": "video", "source_text": "不要视频"}]
    )

    spec = build_query_spec(draft, scope_id="album1", viewer_id="owner", conversation_id="c")

    (constraint,) = spec.constraints
    assert constraint.negated is True
    assert constraint.strictness is Strictness.HARD


def test_an_empty_scope_means_every_album():
    spec = build_query_spec(QueryParseDraft(), scope_id="", viewer_id="owner", conversation_id="c")

    assert spec.scope_mode == "all_authorized"
    assert spec.scope_ids == []
    assert spec.scope_id is None


def test_a_missing_scope_falls_back_to_the_default_space():
    """1.0 的 ``scope or "home-default"``。**与空串是两回事**：
    空串是「全部相册」，``None`` 是「没传」。"""
    spec = build_query_spec(QueryParseDraft(), scope_id=None, viewer_id="owner", conversation_id="c")

    assert spec.scope_mode == "single"
    assert spec.scope_ids == ["home-default"]


# ---- resolve_time_expression ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("上个月", "2026年8月"),
        ("上上个月", "2026年7月"),
        ("去年", "2025年"),
        ("今年", "2026年"),
        ("前年", "2024年"),
        ("去年3月", "2025年3月"),
        ("去年十二月", "2025年12月"),
        ("去年夏天", "2025年6月-2025年8月"),
        ("去年冬天", "2025年12月-2026年2月"),
        ("这两年", "2025年-2026年"),
        ("2023", "2023年"),
        ("2023年10月", "2023年10月"),
    ],
)
def test_resolves_relative_time_phrases(value, expected):
    assert resolve_time_expression(value, now=NOW) == expected


@pytest.mark.parametrize("value", ["前几天", "", None, "某个时候", "很久以前"])
def test_unrecognised_time_phrases_return_none(value):
    """**不返回原文。** 1.0 的注释写死了理由：把认不出的说法原样丢给严格解析器，
    解析失败在 HARD 档下会把候选**全部排除**——丢掉这条约束只是少一层过滤，
    方向是安全的。"""
    assert resolve_time_expression(value, now=NOW) is None


def test_a_longer_phrase_can_match_a_shorter_one_as_a_substring():
    """**1.0 的行为，照抄，但记在这里。** 十二条分支用的都是 in（子串匹配），
    所以「上上上个月」（三个月前）会先撞上「上上个月」（两个月前）。

    这是缺陷，不是特性——但它**方向安全**：结果是「时间范围偏了一个月」，
    不是「候选被全删」。修它属于改产品行为，要单独决定；照抄则是本项目的默认。
    """
    assert resolve_time_expression("上上上个月", now=NOW) == "2026年7月"


def test_january_wraps_to_the_previous_december():
    """1 月的「上个月」是去年 12 月。写成 ``month - 1`` 会得到 0 月——
    而 ``parse_time_expression`` 收到 0 月会抛，或被静默吞掉。"""
    january = datetime(2026, 1, 15)

    assert resolve_time_expression("上个月", now=january) == "2025年12月"
    assert resolve_time_expression("最近一年", now=january) == "2025年12月-2026年1月"


# ---- parse_media_type -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("照片", "image"),
        ("视频", "video"),
        ("录像", "video"),
        ("录音", "audio"),
        ("文字", "text"),
        ("image", "image"),
    ],
)
def test_media_type_synonyms(value, expected):
    assert parse_media_type(value) == expected


@pytest.mark.parametrize("value", ["", None, "什么东西"])
def test_unknown_media_type_returns_none(value):
    assert parse_media_type(value) is None
