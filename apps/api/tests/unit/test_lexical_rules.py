"""词法召回的纯规则：预分词、命中计分、锚点。对应 1.0 ``retrieval_indexes.py:31-80/296-334``。"""

from __future__ import annotations

import pytest

from sentrix.domain.retrieval.lexical import (
    ANCHOR_CONFIDENCE,
    ANCHOR_FIELD_TYPES,
    ANCHOR_SOURCE_TYPES,
    EXACT_BOOST,
    anchor_terms,
    file_stem_is_indexable,
    fts_content,
    normalize_text,
    pre_tokenize,
    query_tokens,
    score_token_hits,
    time_anchor_tokens,
)


def test_normalize_folds_whitespace_and_lowercases():
    assert normalize_text("  Chiang   Mai  ") == "chiang mai"
    assert normalize_text(None) == ""


def test_single_cjk_character_is_never_a_token():
    """1.0 Phase R P0-1：一个「色」字不能算一条证据。"""
    assert pre_tokenize("色") == ()


def test_two_character_cjk_yields_only_the_whole_string():
    """两个汉字只有一个相邻对，而那个 bigram 与整串是同一个词——去重后只剩一个。"""
    assert pre_tokenize("毛绒") == ("毛绒",)


def test_long_cjk_yields_whole_plus_every_bigram():
    tokens = pre_tokenize("浅黄色毛绒睡衣")
    assert "浅黄色毛绒睡衣" in tokens
    assert "浅黄" in tokens
    assert "睡衣" in tokens
    assert all(len(token) > 1 for token in tokens)


def test_latin_words_and_numbers_are_tokens():
    tokens = pre_tokenize("Chiang Mai 2017")
    assert {"chiang", "mai", "2017", "chiang mai 2017"} <= set(tokens)


def test_fts_content_is_space_joined_pretokens():
    assert fts_content("浅黄色") == "浅黄 浅黄色 黄色"


def test_query_tokens_of_single_character_is_empty():
    assert query_tokens("色") == ()


def test_score_counts_distinct_tokens_and_boosts_exact():
    """命中数恰好为 1 且整串完全相等时 +2.0；命中两个 token 时**没有** boost。"""
    exact = frozenset({"asset_a"})
    scored = dict(score_token_hits({"asset_a": 1, "asset_b": 2}, whole_query="毛绒", exact_assets=exact))
    assert scored["asset_a"] == 1 + EXACT_BOOST
    assert scored["asset_b"] == 2


def test_score_without_exact_hit_has_no_boost():
    scored = score_token_hits({"asset_a": 1}, whole_query="毛绒", exact_assets=frozenset())
    assert scored == [("asset_a", 1.0)]


def test_score_is_stable_on_ties_by_asset_id():
    """1.0 用 ``sort(reverse=True)`` 只排分数，同分顺序取决于字典插入序。"""
    forward = score_token_hits({"b": 2, "a": 2}, whole_query="", exact_assets=frozenset())
    backward = score_token_hits({"a": 2, "b": 2}, whole_query="", exact_assets=frozenset())
    assert forward == backward == [("a", 2.0), ("b", 2.0)]


def test_empty_whole_query_disables_boost():
    scored = score_token_hits({"asset_a": 1}, whole_query="", exact_assets=frozenset({"asset_a"}))
    assert scored == [("asset_a", 1.0)]


def test_file_stem_excludes_event_covers_and_keyframes():
    """1.0 ``:245``：事件封面与视频关键帧的文件名不是用户内容。"""
    assert file_stem_is_indexable("IMG_1234.HEIC") is True
    assert file_stem_is_indexable("event_event_abc.jpg") is False
    assert file_stem_is_indexable("kf_0007.jpg") is False
    assert file_stem_is_indexable("") is False


@pytest.mark.parametrize(
    ("captured", "expected"),
    [
        ("2017-10-05T12:30:00", ("2017", "2017-10", "2017-10-05")),
        # ``len(captured) >= 7`` 是 1.0 的门槛，``2017-10`` 正好 7 个字符要放行；
        # 它的 ``captured[:10]`` 仍是自己，去重后只剩两段。
        ("2017-10", ("2017", "2017-10")),
        ("2017-1", ()),
        ("", ()),
    ],
)
def test_time_anchor_tokens(captured: str, expected: tuple[str, ...]):
    assert time_anchor_tokens(captured) == expected


# ---- asset 级锚点（1.0 ``retrieval_indexes.py:225-245``） ---------------------------------

PLACE_TEXT = "浙江省 杭州市 萧山区 赵州镇 大石桥村"


def test_anchor_terms_cover_geo_time_and_file():
    """三类锚点各自的值与来源（``source_type`` 是「这行从哪来」的凭证）。"""
    terms = anchor_terms(
        file_name="IMG_1234.HEIC", captured_at="2017-10-05T12:30:00+00:00", place_text=PLACE_TEXT
    )

    assert {term.field_type for term in terms} == set(ANCHOR_FIELD_TYPES)
    assert {term.confidence for term in terms} == {ANCHOR_CONFIDENCE}
    assert {(term.field_type, term.source_type) for term in terms} == {
        (field_type, ANCHOR_SOURCE_TYPES[field_type]) for field_type in ANCHOR_FIELD_TYPES
    }
    assert {term.normalized_value for term in terms} == {
        "浙江省",
        "杭州市",
        "萧山区",
        "赵州镇",
        "大石桥村",
        "2017",
        "2017-10",
        "2017-10-05",
        "img_1234",
    }


def test_anchor_terms_never_contain_coordinates():
    """``geo`` 取的每一段都来自 ``Place.searchable_text()``，坐标进不来。

    1.0 把 ``label`` 整串写进索引，而它在 PyGeoCN 那条路上可能退化成坐标串
    （``_event_display_place`` 专门用正则挡这件事）。
    """
    terms = anchor_terms(file_name="", captured_at="", place_text="浙江省 杭州市 萧山区")

    assert [term.normalized_value for term in terms] == ["浙江省", "杭州市", "萧山区"]


def test_anchor_terms_skip_event_covers_and_keyframes():
    """事件封面与视频关键帧的文件名不是用户内容（1.0 ``:245``）。"""
    assert anchor_terms(file_name="event_event_abc.jpg", captured_at="", place_text="") == ()
    assert anchor_terms(file_name="kf_0007.jpg", captured_at="", place_text="") == ()
    assert anchor_terms(file_name="", captured_at="", place_text="") == ()


def test_anchor_terms_skip_a_capture_date_that_is_too_short():
    assert anchor_terms(file_name="", captured_at="2017-1", place_text="") == ()
