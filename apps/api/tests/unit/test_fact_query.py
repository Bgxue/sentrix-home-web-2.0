"""``query_memory_facts`` 的参数校验与 spec 派生。

**校验顺序是契约的一部分**，所以下面的用例是逐条钉顺序的，不是逐条钉「会报错」。
同一个畸形输入在不同顺序下会得到不同的 ``reason``，而 ``reason`` 是要回给模型看的。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.retrieval.fact_query import (
    FactRejection,
    FactRequest,
    fact_query_spec,
    parse_fact_arguments,
)
from sentrix.domain.retrieval.query_contracts import Strictness

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


def parse(**arguments) -> FactRequest | FactRejection:
    return parse_fact_arguments(arguments)


def request(**overrides) -> FactRequest:
    fields = {"operation": "count", "subject": "asset"}
    fields.update(overrides)
    result = parse(**fields)
    assert isinstance(result, FactRequest)
    return result


def spec_of(req: FactRequest, *, entity_resolver=None):
    return fact_query_spec(
        req,
        scope_id="album1",
        viewer_id="owner",
        conversation_id="c",
        query_id="q",
        now=NOW,
        entity_resolver=entity_resolver,
    )


# ---- 正常路径 -------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["count", "exists", "first", "last", "group"])
def test_every_operation_parses(operation):
    result = parse(
        operation=operation, subject="asset", **({"group_by": "month"} if operation == "group" else {})
    )

    assert isinstance(result, FactRequest)
    assert result.operation.value == operation


@pytest.mark.parametrize("subject", ["asset", "person_appearance", "processing"])
def test_every_subject_parses(subject):
    extra = {"filters": {"person": "妈妈"}} if subject == "person_appearance" else {}
    result = parse(operation="count", subject=subject, **extra)

    assert isinstance(result, FactRequest)
    assert result.subject.value == subject


def test_filters_default_to_empty():
    result = request()

    assert (result.time, result.media, result.place, result.person) == ("", "", "", "")


def test_filters_are_stripped():
    result = request(filters={"place": "  杭州  ", "media": " IMAGE "})

    assert result.place == "杭州"
    assert result.media == "image"


def test_a_non_mapping_argument_object_is_treated_as_empty():
    result = parse_fact_arguments(None)  # type: ignore[arg-type]

    assert result == FactRejection("unsupported_operation", "unsupported_operation")


# ---- 校验顺序 -------------------------------------------------------------------


def test_an_unknown_top_level_argument_is_rejected_first():
    """多余参数排在最前面：模型自己发明了一个 ``scope_id``，比它同时把
    operation 写错更需要先说出来。"""
    result = parse(operation="nonsense", subject="nonsense", scope_id="album9")

    assert result == FactRejection("unsupported_filter", "unsupported_argument")


def test_filters_must_be_an_object():
    result = parse(operation="count", subject="asset", filters=["time"])

    assert result == FactRejection("unsupported_filter", "filters_must_be_object")


def test_an_unknown_filter_key_is_rejected_before_the_operation():
    result = parse(operation="nonsense", subject="asset", filters={"color": "红"})

    assert result == FactRejection("unsupported_filter", "unsupported_filter")


def test_the_operation_is_checked_before_the_subject():
    result = parse(operation="nonsense", subject="nonsense")

    assert result == FactRejection("unsupported_operation", "unsupported_operation")


def test_the_subject_is_checked_before_the_media():
    result = parse(operation="count", subject="nonsense", filters={"media": "audio"})

    assert result == FactRejection("unsupported_subject", "unsupported_subject")


def test_media_is_checked_before_the_group():
    result = parse(operation="group", subject="asset", group_by="nonsense", filters={"media": "audio"})

    assert result == FactRejection("unsupported_filter", "unsupported_media")


# ---- 媒体 -----------------------------------------------------------------------


@pytest.mark.parametrize("media", ["audio", "text", "pdf"])
def test_unsupported_media_is_rejected(media):
    """``audio`` / ``text`` 是契约层认得的媒体类型，**但结构化统计不认**——
    1.0 的 ``_STRUCTURED_FACT_FILTERS`` 只放 image / video，2.0 照旧。
    让它们通过会让 SQL 里出现一个永远匹配不到的值，而结果只是「0 张」。"""
    result = parse(operation="count", subject="asset", filters={"media": media})

    assert result == FactRejection("unsupported_filter", "unsupported_media")


def test_an_empty_media_is_not_an_error():
    assert request(filters={"media": ""}).media == ""


# ---- group_by -------------------------------------------------------------------


@pytest.mark.parametrize("group_by", ["month", "place", "media"])
def test_asset_groups(group_by):
    assert request(operation="group", subject="asset", group_by=group_by).group_by == group_by


def test_status_is_only_a_group_for_processing():
    """两个白名单不共用：``status`` 问的是导入流水线，不是「相册里有什么」；
    ``month`` 反过来对 processing 也没意义。"""
    assert request(operation="group", subject="processing", group_by="status").group_by == "status"

    rejected = parse(operation="group", subject="asset", group_by="status")
    assert rejected == FactRejection("unsupported_filter", "unsupported_group_by")

    rejected = parse(operation="group", subject="processing", group_by="month")
    assert rejected == FactRejection("unsupported_filter", "unsupported_group_by")


def test_a_group_by_on_count_is_cleared_not_rejected():
    """**不是模型说错了话，是它多填了一个字段。** 小模型经常把 schema 里每个可选的
    都填上；把 ``{"count", group_by="month"}`` 判成错误，会让一次本来能精确回答的
    查询变成一句「不支持」。"""
    result = request(operation="count", subject="asset", group_by="month")

    assert result.group_by == ""


def test_a_bogus_group_by_on_count_is_also_cleared():
    """连认不出的键也一样清空——反正它不会被用到，报错没有意义。"""
    assert request(operation="count", subject="asset", group_by="季度").group_by == ""


def test_group_requires_a_group_by():
    result = parse(operation="group", subject="asset")

    assert result == FactRejection("unsupported_filter", "unsupported_group_by")


# ---- 人物 -----------------------------------------------------------------------


def test_person_appearance_requires_a_person():
    result = parse(operation="count", subject="person_appearance")

    assert result == FactRejection("unresolved_person", "person_required")


def test_processing_rejects_a_person():
    """「还在处理的照片里有多少张是妈妈」不是一个能回答的问题——处理状态与人物
    属于两条互不相交的数据。"""
    result = parse(operation="count", subject="processing", filters={"person": "妈妈"})

    assert result == FactRejection("unsupported_filter", "processing_does_not_support_person")


def test_processing_rejects_a_person_before_group_validation():
    result = parse(operation="group", subject="processing", group_by="month", filters={"person": "妈妈"})

    assert result == FactRejection("unsupported_filter", "unsupported_group_by")


def test_the_person_is_kept_on_the_request():
    assert (
        request(operation="count", subject="person_appearance", filters={"person": "妈妈"}).person == "妈妈"
    )


# ---- QuerySpec 派生 -------------------------------------------------------------


def test_place_becomes_a_semantic_condition():
    """**SEMANTIC 档，不是 HARD。** 这是 1.0 工具层的行为。

    照抄它并不显眼，但它是「地点条件不看严格度」那条修正的由来——
    ``derive_filter`` 若按 HARD 筛，这条地点条件会被整个丢掉，于是每一次带地点的
    统计都悄悄退化成整个相册的统计，而 ``count=53`` 看起来完全正常。
    """
    spec = spec_of(request(operation="count", subject="asset", filters={"place": "杭州"}))

    (place,) = spec.constraints_for("place")
    assert place.value == "杭州"
    assert place.strictness is Strictness.SEMANTIC


def test_media_becomes_a_hard_constraint():
    spec = spec_of(request(operation="count", subject="asset", filters={"media": "image"}))

    (media,) = spec.constraints_for("media")
    assert media.value == "image"
    assert media.strictness is Strictness.HARD


def test_a_relative_time_is_resolved_against_now():
    """模型契约：相对时间原样传进来，由工具确定性换算。"""
    spec = spec_of(request(operation="count", subject="asset", filters={"time": "去年"}))

    (time,) = spec.constraints_for("time")
    assert time.value == "2025年"


def test_an_unrecognised_time_is_dropped_not_passed_through():
    """换算不出来就**不加时间条件**。原样塞进去会得到一条判不出真假的条件——
    宁可放宽召回，也不要悄悄给一条错的时间区间。"""
    spec = spec_of(request(operation="count", subject="asset", filters={"time": "小时候"}))

    assert spec.constraints_for("time") == []


def test_a_longer_relative_phrase_falls_into_a_shorter_one():
    """**钉住一个既有缺陷，不是断言它是对的。**

    ``resolve_time_expression`` 逐条做的是 ``"上上个月" in v`` 这样的子串判断，
    所以 ``上上上上个月``（四个「上」）会被当成 ``上上个月`` 解析成两个月前。
    用户说的是「上上上上个月」，拿到的是两个月前那个月——数字看着正常，只是错的。

    同一个毛病在 ``parse_time_expression`` 里也有一份（``上上上个月`` 命中
    ``上上个月``），那条已经记过。修法是锚定匹配（``re.search(r"(上+)个月")``
    之后按「上」的个数推移），不在 T121c 的范围里。
    """
    spec = spec_of(request(operation="count", subject="asset", filters={"time": "上上上上个月"}))

    (time,) = spec.constraints_for("time")
    assert time.value == "2026年7月"  # 两个月前，不是四个月前


def test_an_unresolvable_person_produces_no_person_constraint():
    """解不出人名**不派生 person 约束**——那与「这个人不存在」是两回事：
    前者是这条约束没法判（HARD 档下会排除一切），后者是确实不是他。
    1.0 也是这个行为（``if entity_id:``）。"""
    spec = spec_of(
        request(operation="count", subject="person_appearance", filters={"person": "查无此人"}),
        entity_resolver=lambda name: None,
    )

    assert spec.constraints_for("person") == []
    assert spec.entity_ids == []


def test_a_resolvable_person_lands_in_entity_ids():
    spec = spec_of(
        request(operation="count", subject="person_appearance", filters={"person": "妈妈"}),
        entity_resolver=lambda name: "entity_1" if name == "妈妈" else None,
    )

    assert spec.entity_ids == ["entity_1"]


def test_the_scope_is_the_album_the_caller_passed():
    spec = spec_of(request())

    assert spec.scope_id == "album1"
    assert spec.scope_mode == "single"


def test_the_spec_carries_nothing_the_tool_did_not_ask_for():
    """**``operation`` 与 ``group_by`` 故意不进 QuerySpec。** 1.0 把它们塞进
    ``draft.answer_type`` / ``draft.structured``，由 ``StructuredMemoryExecutor.execute()``
    读回来挑操作；2.0 由 ``FactRequest`` 自己带着，application 层直接分派。

    这条用例钉住的是「别再加回去」：``build_query_spec`` 根本不读这两个字段，
    写进去就是一份没有读者的配置——而这份仓库已经删过好几处这种死配置了
    （``RetrievalConfig.matched_source_types`` 是最近的一处）。
    """
    spec = spec_of(request(operation="group", subject="asset", group_by="place"))

    assert not hasattr(spec, "structured")
    assert not hasattr(spec, "answer_type")
