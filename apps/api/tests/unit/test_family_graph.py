"""家庭关系标签白名单（S7b1 · T302）。

对应 1.0 ``backend/family_graph.py``——43 行、没有 import，只有一张表和一支校验函数。

**这张表是家庭图谱唯一的把关点**：通用 ``relationships`` 的 ``predicate`` 是自由文本，
而家庭边**不能**离开这里。所以用例钉的不是「校验函数能跑」，是**表的形状**：
漏掉一个词条、或把某个反向写成单向，都不会报错——只会让某一类亲属关系永远建不出来
（或建出方向相反的边），而那要等到用户试到那一类关系时才发现。

``test_the_table_round_trips`` 是这套里最贵的一条：它遍历全表验
「``a`` 的合法反向里有 ``b``」⇔「``b`` 的合法反向里有 ``a``」。
"""

from __future__ import annotations

import pytest

from sentrix.domain.identity.family_graph import RELATION_INVERSES, validate_relationship_pair

# 1.0 ``family_graph.py:1-34`` 的词条数。**钉死这个数**：漏一行不会报错，
# 只会让那一类亲属关系在建边时抛「关系标签不匹配」——用户看到的是「建不了」。
EXPECTED_ENTRIES = 31


def test_the_table_has_every_entry() -> None:
    assert len(RELATION_INVERSES) == EXPECTED_ENTRIES


def test_the_table_round_trips() -> None:
    """**往返对称**：``a`` 的反向里有 ``b`` ⇔ ``b`` 的反向里有 ``a``。

    这不是「看起来对称」的装饰——``set_family_relationship`` **一次落两行**
    （``a --父亲--> b`` 与 ``b --女儿--> a``），所以任何一个单向的词条都会让
    那一类边**只落得下一半**：正向过了校验、反向过不了，整条边建不起来。
    而且校验是逐行调的，不会回头检查表本身。

    定点变异：把 ``"父亲": {"儿子", "女兒"}`` 里加一个 ``"儿子"`` **以外的**词，
    或把某个 ``{"X"}`` 改成 ``{"Y"}``，这条立刻红。
    """
    for predicate, inverses in RELATION_INVERSES.items():
        for inverse in inverses:
            assert inverse in RELATION_INVERSES, f"{predicate} 的反向 {inverse} 不是表里的键"
            assert predicate in RELATION_INVERSES[inverse], (
                f"{predicate} → {inverse} 在法律表里，但 {inverse} 的反向里没有 {predicate}"
            )


def test_a_gendered_inverse_pair_passes() -> None:
    """``父亲 → 女儿`` 合法：**孩子的性别决定用哪个反向词**。

    同一条 ``父亲`` 边，孩子是男是女要用不同的词——这就是这张表**一对多**的地方。
    """
    validate_relationship_pair("父亲", "女儿")
    validate_relationship_pair("父亲", "儿子")
    validate_relationship_pair("母亲", "女儿")


def test_a_wrong_gendered_inverse_is_rejected_with_the_1_0_message() -> None:
    """``女儿 → 母亲`` 合法，``女儿 → 父亲`` 也合法；但 ``父亲 → 母亲`` 不合法。

    报错话术是交付内容（调用方按消息区分两种失败），逐字照 1.0。
    """
    with pytest.raises(ValueError, match="关系标签不匹配: 父亲 -> 母亲"):
        validate_relationship_pair("父亲", "母亲")


def test_an_empty_label_is_a_different_message() -> None:
    """空标签与「不匹配」是**两句不同的话**——1.0 把它们分成两个判断。"""
    with pytest.raises(ValueError, match="关系标签不能为空"):
        validate_relationship_pair("", "女儿")
    with pytest.raises(ValueError, match="关系标签不能为空"):
        validate_relationship_pair("父亲", "")


def test_a_label_outside_the_table_is_rejected() -> None:
    """表外的标签（通用 ``relationships`` 那边随手能写的词）在这里被挡下。

    ``同事`` 是 ``person_graph.py`` 的 ``INVERSE_RELATION`` 里有、而**这张表没有**的词
    ——两张表粒度不同，1.0 就没统一过。这条同时钉住「它们没有被我合并」。
    """
    with pytest.raises(ValueError, match="关系标签不匹配: 同事 -> 同事"):
        validate_relationship_pair("同事", "同事")


def test_labels_are_stripped_before_lookup() -> None:
    """两边都先 ``strip()``（1.0 的第一行）。带空白的历史数据照样能过。"""
    validate_relationship_pair(" 父亲 ", " 女儿 ")


def test_the_symmetric_labels_map_to_themselves() -> None:
    """``朋友`` / ``密友`` / ``其他亲属`` 是自反的。

    写成 ``{"朋友"}`` 之外的东西会让「A 是 B 的朋友但 B 不是 A 的朋友」变成可能。
    """
    for label in ("朋友", "密友", "其他亲属"):
        assert RELATION_INVERSES[label] == {label}
