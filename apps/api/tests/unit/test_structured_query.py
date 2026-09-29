"""结构化查询筛选条件的派生：``QuerySpec`` → ``StructuredFilter``。

**几条会静默给出错数的边界，各配一个用例：**

1. **时间边界用 ``isoformat`` 而不是 ``str()``**——``captured_at`` 列存的是 ISO 串，
   而 ``str(datetime)`` 用空格分隔日期与时间，两者的字典序不同，``>=`` 会静默给出
   错误的边界；
2. **只有 HARD 的地点约束进 WHERE**——SEMANTIC 档的地点不该排除候选，
   而 WHERE 一旦加上就是排除；
3. **``scope_id=None`` 表示全部相册**——写成空串会让 ``WHERE scope_id = ''``
   一条都不匹配，而结果只是「一张都没有」。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sentrix.domain.retrieval.query_contracts import Constraint, QuerySpec, Strictness
from sentrix.domain.retrieval.structured_query import (
    DEFAULT_GROUP_BY,
    derive_filter,
    hard_conditions,
    normalize_group_by,
)

DEFAULT = Strictness.HARD


def make_spec(*constraints: Constraint, entity_ids: list[str] | None = None) -> QuerySpec:
    return QuerySpec(
        query_id="q",
        scope_mode="single",
        scope_ids=["album1"],
        viewer_id="owner",
        conversation_id="c",
        intent="answer",
        answer_target="general",
        constraints=list(constraints),
        entity_ids=entity_ids or [],
    )


def test_derives_time_bounds_as_iso_strings():
    """**ISO 串，不是 ``str(datetime)``。** 前者是 ``2023-10-01T00:00:00``，
    后者是 ``2023-10-01 00:00:00``——与库里存的格式一致的只有前者，
    而用后者去比大小会静默得到错误的边界（空格 < 'T'）。"""
    spec = make_spec(Constraint("time", "2023年10月", DEFAULT, "asset_metadata"))

    result = derive_filter(spec, scope_id="album1")

    assert result.time_start == "2023-10-01T00:00:00"
    assert result.time_end == "2023-11-01T00:00:00"


def test_an_unparseable_time_constraint_is_dropped():
    """解析不出来就不加时间条件——**不是**加一个空区间。空区间会匹配零行。"""
    spec = make_spec(Constraint("time", "上上上个月", DEFAULT, "asset_metadata"))

    assert derive_filter(spec).time_start is None


def test_media_constraints_split_by_negation():
    spec = make_spec(
        Constraint("media", "image", DEFAULT, "asset_metadata"),
        Constraint("media", "video", DEFAULT, "asset_metadata", negated=True),
    )

    result = derive_filter(spec)

    assert result.media_include == ("image",)
    assert result.media_exclude == ("video",)


def test_a_hard_place_constraint_enters_the_where_clause():
    spec = make_spec(Constraint("place", "杭州", DEFAULT, "asset_metadata"))

    assert derive_filter(spec).place == "杭州"


def test_a_semantic_place_constraint_also_enters_the_where_clause():
    """**严格度在这里不起作用。** 1.0 的 ``_place`` 全程没看过 ``strictness``，
    而 1.0 的工具层把地点条件构造成 ``semantic_required``——按严格度筛的话，
    每一次带地点的统计都会悄悄退化成整个相册的统计，返回值看起来还很正常。

    两档严格度的差别在**融合之后逐候选判定**上（``conditions.excludes``），
    对 count/first/last 这类问题，``filters.place`` 就是问题本身。"""
    spec = make_spec(Constraint("place", "杭州", Strictness.SEMANTIC, "direct_or_possible"))

    assert derive_filter(spec).place == "杭州"


def test_the_first_place_constraint_wins():
    """「第一个胜」而不是「后面覆盖前面」。1.0 遍历时碰到第一个非空值就返回；
    两条约束并存时，谁生效不该取决于 spec 的构造顺序。"""
    spec = make_spec(
        Constraint("place", "杭州", DEFAULT, "asset_metadata"),
        Constraint("place", "上海", DEFAULT, "asset_metadata"),
    )

    assert derive_filter(spec).place == "杭州"


def test_the_first_parseable_time_constraint_wins():
    """认不出来的那条跳过（1.0 的 ``continue``），但认出来的第一条就定下来。"""
    spec = make_spec(
        Constraint("time", "2023年", DEFAULT, "asset_metadata"),
        Constraint("time", "2021年", DEFAULT, "asset_metadata"),
    )

    assert derive_filter(spec).time_start == "2023-01-01T00:00:00"


def test_an_unparseable_time_constraint_does_not_block_a_later_one():
    spec = make_spec(
        Constraint("time", "上上上个月", DEFAULT, "asset_metadata"),
        Constraint("time", "2023年", DEFAULT, "asset_metadata"),
    )

    assert derive_filter(spec).time_start == "2023-01-01T00:00:00"


def test_a_negated_place_constraint_does_not_enter_the_positive_clause():
    """否定的地点约束不该当成「要匹配这个地点」。"""
    spec = make_spec(Constraint("place", "杭州", DEFAULT, "asset_metadata", negated=True))

    assert derive_filter(spec).place == ""


def test_scope_id_is_passed_through_including_none():
    """``None`` 表示全部相册。**它必须原样传下去**——在这里兜底成某个空间，
    会让「跨相册查询」变成「只查那一个空间」，而结果只是少几张照片。"""
    spec = make_spec()

    assert derive_filter(spec, scope_id=None).scope_id is None
    assert derive_filter(spec, scope_id="album1").scope_id == "album1"


def test_entity_ids_are_deduplicated():
    spec = make_spec(entity_ids=["entity_1", "entity_1", "entity_2"])

    assert derive_filter(spec).entity_ids == ("entity_1", "entity_2")


def test_applied_records_what_was_filtered():
    """``filters_applied`` 是要回给模型看的——它得知道「刚才那次统计筛了什么」。"""
    spec = make_spec(
        Constraint("time", "2023年", DEFAULT, "asset_metadata"),
        Constraint("media", "image", DEFAULT, "asset_metadata"),
    )

    result = derive_filter(spec)

    assert result.applied["media"] == ("image",)
    assert result.applied["time"][0].startswith("2023-01-01")


def test_nothing_applied_when_there_are_no_constraints():
    assert derive_filter(make_spec()).applied == {}


# ---- hard_conditions ------------------------------------------------------------


def test_hard_conditions_keeps_only_hard_ones():
    spec = make_spec(
        Constraint("media", "image", DEFAULT, "asset_metadata"),
        Constraint("place", "杭州", Strictness.SEMANTIC, "direct_or_possible"),
    )

    result = hard_conditions(spec)

    assert [item.dimension for item in result] == ["media"]
    assert result[0].value == "image"


def test_hard_conditions_preserves_negation_and_key():
    spec = make_spec(Constraint("media", "video", DEFAULT, "asset_metadata", negated=True))

    (condition,) = hard_conditions(spec)

    assert condition.negated is True
    assert condition.key == "media:video"


# ---- normalize_group_by ---------------------------------------------------------


@pytest.mark.parametrize("value", ["month", "year", "date", "media", "place"])
def test_known_group_keys_survive(value):
    assert normalize_group_by(value) == value


@pytest.mark.parametrize("value", ["", None, "   ", "quarter", "看不懂"])
def test_unknown_group_keys_fall_back_to_month(value):
    """**不抛。** 模型经常把每个可选字段都填上，一个认不出的 group_by 把整条查询
    变成错误，代价远大于按默认分组返回。"""
    assert normalize_group_by(value) == DEFAULT_GROUP_BY


def test_now_is_not_read_inside_the_module():
    """时间条件判的是「用户说的那段时间」，与「现在」无关——
    所以这个模块里不该出现 ``datetime.now()``。相对时间的解析在
    ``resolve_time_expression``（它显式收 ``now`` 参数），不在这里。"""
    import inspect

    from sentrix.domain.retrieval import structured_query

    source = inspect.getsource(structured_query)

    assert "datetime.now" not in source
    assert datetime is not None  # 显式引用，免得上面那行被优化掉的错觉
