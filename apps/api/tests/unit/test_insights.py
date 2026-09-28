"""核心人物排序与代表照选择。

移植自 1.0 ``backend/person_insights.py:21-112``。用例分三组：**常量**（权重与门槛——
它们是产品行为）、**排序的确定性**（四段排序键的最后一段就是为它存在的）、
以及**两处副作用**（1.0 的 docstring 自称 pure，实际这两处会改写入参）。
"""

from __future__ import annotations

import pytest

from sentrix.domain.identity.faces import cosine as bare_cosine
from sentrix.domain.identity.insights import (
    CONFIRMED_BOOST,
    CORE_PEOPLE_LIMIT,
    DUPLICATE_THRESHOLD,
    SELECTION_VERSION,
    SINGLE_DAY_PENALTY,
    WEIGHT_DATE,
    WEIGHT_EVENT,
    core_person_score,
    cosine,
    rank_core_people,
    select_representatives,
)


def person(person_id: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "person_id": person_id,
        "date_count": 0,
        "event_count": 0,
        "member_count": 0,
        "co_person_count": 0,
        "scene_count": 0,
        "quality": 0.0,
        "confirmed": False,
    }
    row.update(overrides)
    return row


# ---- 常量（产品行为，不是实现细节）------------------------------------------------


def test_weights_sum_to_one_before_boost_and_penalty() -> None:
    """六项权重加起来正好是 1.0——**这是刻意的**：没有加成与惩罚时，
    分数就是各项归一化后的加权平均，满分的人恰好得 1.0。
    改任何一个权重都会破坏这个性质，所以这条钉住总和而不只是逐个值。"""
    from sentrix.domain.identity import insights

    total = (
        insights.WEIGHT_DATE
        + insights.WEIGHT_EVENT
        + insights.WEIGHT_COVERAGE
        + insights.WEIGHT_CO_PERSON
        + insights.WEIGHT_SCENE
        + insights.WEIGHT_QUALITY
    )
    assert total == pytest.approx(1.0)


def test_the_frozen_constants_match_1_0() -> None:
    """1.0 ``person_insights.py:32-40,64,79`` 的原值。"""
    assert (WEIGHT_DATE, WEIGHT_EVENT) == (0.30, 0.20)
    assert CONFIRMED_BOOST == 0.20
    assert SINGLE_DAY_PENALTY == 0.35
    assert CORE_PEOPLE_LIMIT == 10
    assert DUPLICATE_THRESHOLD == 0.94
    assert SELECTION_VERSION == "representative-v1"


# ---- core_person_score ------------------------------------------------------------


def test_a_maxed_person_scores_exactly_one() -> None:
    row = person(
        "p1", date_count=8, event_count=6, member_count=20, co_person_count=4, scene_count=4, quality=1.0
    )
    assert core_person_score(row) == 1.0


def test_single_day_is_penalised() -> None:
    """只出现过一天的人物被降权——那是「来串门的」而不是「家里人」。

    **断言的是分项而不是分数差。** 低分人物的惩罚会被钳位吃掉（见下一条），
    拿分数差去反推惩罚是否生效，在低分段会得出错误结论。
    """
    row = person("p1", date_count=1)
    core_person_score(row)
    assert row["score_breakdown"]["single_day_penalty"] == SINGLE_DAY_PENALTY


def test_two_days_are_not_penalised() -> None:
    row = person("p1", date_count=2)
    core_person_score(row)
    assert row["score_breakdown"]["single_day_penalty"] == 0.0


def test_the_penalty_can_be_absorbed_by_the_clamp() -> None:
    """钳位会把低分人物的惩罚吃掉——`date_count=1`、质量满分的人总分是
    0.30*0.125 + 0.15 − 0.35 = −0.1625，钳到 0.0，与「没有惩罚但本来就低分」
    在分数上完全一样。

    **这不是缺陷**（那种人本来就该得 0 分），但它意味着分数差不能用来判断
    惩罚有没有生效——那要看 `score_breakdown`。
    """
    row = person("p1", date_count=1, quality=1.0)
    assert core_person_score(row) == 0.0
    assert row["score_breakdown"]["single_day_penalty"] == SINGLE_DAY_PENALTY


def test_confirmed_gets_a_boost() -> None:
    plain = person("p1", date_count=4, quality=0.5)
    confirmed = person("p2", date_count=4, quality=0.5, confirmed=True)
    assert core_person_score(confirmed) - core_person_score(plain) == pytest.approx(CONFIRMED_BOOST)


def test_the_score_is_clamped_to_the_unit_interval() -> None:
    """加成可以把总分推过 1.0，钳位是最后一道。"""
    row = person(
        "p1",
        date_count=8,
        event_count=6,
        member_count=20,
        co_person_count=4,
        scene_count=4,
        quality=1.0,
        confirmed=True,
    )
    assert core_person_score(row) == 1.0


def test_missing_keys_read_as_zero_not_as_an_error() -> None:
    """1.0 全用 ``row.get(...) or 0``：字段缺失是常态（新入库的人物还没有场景数）。"""
    assert core_person_score({"person_id": "p1"}) == 0.0


# ---- rank_core_people -------------------------------------------------------------


def test_the_last_sort_key_makes_ties_deterministic() -> None:
    """分数与天数都相同时按 ``person_id`` 升序——**少了这一段，同一份输入两次跑出来的
    顺序可以不同**，而排序结果决定谁进 core、谁被送去做画像。"""
    rows = [person("pb", date_count=2, quality=0.5), person("pa", date_count=2, quality=0.5)]
    assert [item["person_id"] for item in rank_core_people(rows)] == ["pa", "pb"]


def test_confirmed_sorts_before_unconfirmed_even_with_a_lower_score() -> None:
    rows = [person("high", date_count=3, quality=1.0), person("confirmed", confirmed=True)]
    assert [item["person_id"] for item in rank_core_people(rows)][0] == "confirmed"


def test_core_requires_confirmed_or_two_days() -> None:
    """排进前十**不等于** core：还要已确认、或跨过两天（``:65``）。
    这是一道单独的判据——只看排名会把来串门的人也算成核心人物。"""
    rows = [person("one_day", date_count=1, quality=1.0)]
    assert rank_core_people(rows)[0]["tier"] == "incidental"


def test_limit_is_capped_at_ten_however_large_it_is_passed() -> None:
    """``min(limit, 10)`` 的硬上限：它是导入后模型调用总量的来源。"""
    rows = [person(f"p{index:02d}", date_count=3) for index in range(15)]
    core = [item for item in rank_core_people(rows, limit=99) if item["tier"] == "core"]
    assert len(core) == CORE_PEOPLE_LIMIT


# ---- select_representatives -------------------------------------------------------


def candidate(face_id: str, event: str, asset: str, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "face_instance_id": face_id,
        "event_id": event,
        "asset_id": asset,
        "quality": 0.5,
        "body_visibility": 0.5,
        "captured_at": "2026-01-01",
    }
    item.update(overrides)
    return item


def test_each_event_contributes_at_most_per_event_photos() -> None:
    """代表照要覆盖不同场合，不是同一次出行里最能看的十二张。"""
    candidates = [candidate(f"f{i}", "same-event", f"a{i}") for i in range(6)]
    chosen = select_representatives(candidates, {}, per_event=2)
    assert len(chosen) == 2


def test_near_duplicates_are_skipped_by_cosine() -> None:
    """同一套衣服、同一个角度的连拍：去重比排序更决定观感。"""
    identical = [1.0, 0.0, 0.0]
    candidates = [candidate("f1", "e1", "a1"), candidate("f2", "e2", "a2")]
    vectors = {"a1": identical, "a2": [0.99, 0.1, 0.0]}
    assert len(select_representatives(candidates, vectors)) == 1


def test_a_candidate_without_a_vector_is_not_deduplicated_away() -> None:
    """没向量的人不该被丢掉——去重是**加强**不是准入。"""
    candidates = [candidate("f1", "e1", "a1"), candidate("f2", "e2", "a2")]
    assert len(select_representatives(candidates, {"a1": [1.0, 0.0]}, limit=1)) == 1


def test_the_selection_records_why_it_was_made() -> None:
    chosen = select_representatives([candidate("f1", "e1", "a1", quality=0.8)], {})
    assert chosen[0]["selection_json"]["version"] == SELECTION_VERSION
    assert chosen[0]["selection_json"]["quality"] == 0.8
    assert chosen[0]["selection_json"]["event_coverage"] == 1


# ---- 两处副作用（1.0 原样，钉住而不是修掉）------------------------------------------


def test_core_person_score_writes_back_a_breakdown() -> None:
    """**1.0 的模块 docstring 说这是 pure function，它不是。**
    钉住这一条是为了让「顺手修成返回新 dict」变成一次**有意识的**改动——
    改它会同时改变所有依赖 `row["score_breakdown"]` 的调用方。"""
    row = person("p1", date_count=4)
    core_person_score(row)
    assert set(row["score_breakdown"]) == {
        "date_score",
        "event_score",
        "coverage_score",
        "co_person_score",
        "scene_score",
        "quality_score",
        "confirmed_boost",
        "single_day_penalty",
    }


def test_rank_core_people_mutates_the_rows_it_was_given() -> None:
    rows = [person("p1", date_count=4)]
    rank_core_people(rows)
    assert "score_breakdown" in rows[0], "rank 会经 core_person_score 写回原行（1.0 原样）"


def test_select_representatives_mutates_the_candidates_it_was_given() -> None:
    candidates = [candidate("f1", "e1", "a1")]
    select_representatives(candidates, {})
    assert "selection_json" in candidates[0], "选中项会被写回原字典（1.0 原样）"


# ---- cosine 与 faces.cosine 不是一回事 ----------------------------------------------


def test_this_cosine_normalises_but_the_faces_one_does_not() -> None:
    """两个同名函数语义不同：``faces.cosine`` 是裸点积（入参已归一化），
    这一个自己算模长。未归一化的向量喂给前者，得到的**不是**余弦。"""
    unnormalised = [3.0, 4.0]
    assert cosine(unnormalised, unnormalised) == pytest.approx(1.0)
    assert bare_cosine(unnormalised, unnormalised) == pytest.approx(25.0)


def test_cosine_returns_zero_for_a_zero_vector() -> None:
    """零向量返回 0.0 而不是抛 ZeroDivisionError——与 1.0 一致。"""
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0
