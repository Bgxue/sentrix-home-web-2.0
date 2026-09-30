"""语义分组纯规则的用例（S6 · T129）。

对应 1.0 ``db.py:4144-4165``。**四级判定按顺序短路**，所以这里一格一格地钉「哪一级赢」：

| 顺序 | strategy | 什么时候赢 |
|---|---|---|
| 1 | ``semantic_primary`` | 属性有值**且**类型是 place / object / atmosphere |
| 2 | ``controlled_equivalence`` | 词表整串相等，其次子串命中 |
| 3 | ``semantic_concept`` | place / object 的概念词表子串命中 |
| 4 | ``exact_label`` | 兜底 |

**顺序本身就是行为**：把 2 与 3 调换，「湖边」会先被概念表的「湖」吃掉，归到
「滨水空间」而不是词表给的「滨水区域」。这一组用例的存在就是为了让那个调换变红。
"""

from __future__ import annotations

from sentrix.domain.identity.semantics import (
    STRATEGY_CONTROLLED_EQUIVALENCE,
    STRATEGY_EXACT_LABEL,
    STRATEGY_SEMANTIC_CONCEPT,
    STRATEGY_SEMANTIC_PRIMARY,
    group_entity_type,
    semantic_entity_key,
)


def _key(entity_type: str, name: str, primary: str | None = None):
    return semantic_entity_key(entity_type=entity_type, name=name, semantic_primary=primary)


# ---- 第 1 级：semantic_primary ----------------------------------------------------


def test_a_primary_property_wins_outright():
    assert _key("place", "某某湖", "滨水区域").strategy == STRATEGY_SEMANTIC_PRIMARY
    assert _key("place", "某某湖", "滨水区域").label == "滨水区域"


def test_a_primary_property_is_ignored_for_other_types():
    """**只有 place / object / atmosphere 三种类型用它。** 人也有同名属性，但那不是
    给分组用的——1.0 ``db.py:4152`` 的字面量集合。

    定点变异：去掉类型判断，这条变红。
    """
    assert _key("person", "妈妈", "滨水区域").strategy == STRATEGY_EXACT_LABEL
    assert _key("time", "2026-09", "滨水区域").strategy == STRATEGY_EXACT_LABEL


def test_an_empty_primary_falls_through():
    """空串与 None 一样算「没有」——1.0 是 ``if primary and ...``。"""
    assert _key("place", "西湖", "").strategy == STRATEGY_SEMANTIC_CONCEPT
    assert _key("place", "西湖", None).strategy == STRATEGY_SEMANTIC_CONCEPT


# ---- 第 2 级：controlled_equivalence -----------------------------------------------


def test_an_exact_equivalence_wins():
    key = _key("place", "湖边")

    assert key.strategy == STRATEGY_CONTROLLED_EQUIVALENCE
    assert key.label == "滨水区域"
    assert key.matched_label == "湖边"


def test_a_substring_equivalence_wins():
    """整串不在词表里、但词表的词是它的子串时，仍然归一——``matched_label`` 记的是
    **命中的那个词**，不是整串。"""
    key = _key("place", "湖边栈道")

    assert key.label == "滨水区域"
    assert key.matched_label == "湖边"


def test_an_exact_match_reports_the_whole_label_as_the_match():
    """整串命中等价表时，``matched_label`` 是**整个 label**，不是命中的子串。

    **一条被调查过的等价变异，记在这里。** 曾经试过把「子串命中」挪到「整串相等」前面
    （M3），**没有任何用例变红**。追下去发现那不是测试不够，是**两种顺序在这张词表上
    恒等**：整串命中要求 label 就是某个键，而那个键本身一定能被子串循环命中——除非有
    一个**更早**的键也是它的子串。逐对验证过 ``place`` 那 11 个键：这样的词对**一对都
    没有**（``湖边``/``水边``/``河边``/``河岸``/``湖畔`` 互不包含，
    ``博物馆``/``展厅``/``展览厅``/``展览馆``/``美术馆``/``画廊`` 同理），所以子串循环
    扫到那个键时一定先命中它自己。

    结论：**这两条规则在本表上不可分辨**。照搬 1.0 的顺序（整串优先）不变——它是
    1.0 的写法，而且词表一旦加了词，顺序就可能变得可观测。这条用例钉的是**结果**。
    """
    key = _key("place", "湖边")

    assert key.strategy == STRATEGY_CONTROLLED_EQUIVALENCE
    assert key.label == "滨水区域"
    assert key.matched_label == "湖边"


# ---- 第 3 级：semantic_concept ----------------------------------------------------


def test_a_place_concept_wins_over_the_exact_label():
    key = _key("place", "西湖")

    assert key.strategy == STRATEGY_SEMANTIC_CONCEPT
    assert key.label == "滨水空间"
    assert key.matched_label == "湖"


def test_an_object_concept_wins():
    key = _key("object", "巧克力蛋糕")

    assert key.strategy == STRATEGY_SEMANTIC_CONCEPT
    assert key.label == "食物与饮品"
    assert key.matched_label == "蛋糕"


def test_concepts_are_consulted_in_table_order():
    """**先命中的赢**，而表的顺序是 1.0 的字面量顺序。「水边」既是滨水空间（第 1 条）
    的词，也在等价表里——这里用两个都属于概念表的词验证顺序。

    ``水池`` 只属于滨水空间；``蛋糕`` 只属于食物与饮品。真正验证顺序的是能同时命中
    两条的词，而 1.0 的表里没有这种词——**所以这条只钉「第一条命中的那个」**，
    并用两条例证说明它不是靠运气。
    """
    assert _key("place", "小区水池").label == "滨水空间"
    assert _key("object", "桌上的蛋糕").label == "食物与饮品"


def test_an_unknown_type_gets_no_concepts():
    """``time`` 没有概念表——1.0 的 ``concepts`` 那一支对它是空元组。"""
    assert _key("time", "2026-09-23").strategy == STRATEGY_EXACT_LABEL


# ---- 第 4 级：exact_label ----------------------------------------------------------


def test_an_unknown_label_falls_back_to_itself():
    key = _key("place", "书房")

    assert key.strategy == STRATEGY_EXACT_LABEL
    assert key.label == "书房"
    assert key.matched_label == "书房"


def test_whitespace_is_stripped_before_matching():
    """1.0 是 ``re.sub(r"\\s+", "", ...)``——**所有**空白都去掉，不是只 strip 两端。
    不去的话「湖 边」匹配不上等价表。"""
    assert _key("place", " 湖 边 ").label == "滨水区域"


def test_an_empty_name_stays_empty():
    assert _key("place", "").label == ""


# ---- 组的 key 用的类型名 -----------------------------------------------------------


def test_emotion_is_grouped_as_atmosphere():
    """1.0 ``db.py:4199``：**组的 key** 把 emotion 当成 atmosphere。

    定点变异：让它原样返回，这条变红。
    """
    assert group_entity_type("emotion") == "atmosphere"


def test_other_types_pass_through_unchanged():
    for entity_type in ("place", "object", "time", "person"):
        assert group_entity_type(entity_type) == entity_type
