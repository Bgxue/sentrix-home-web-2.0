"""实体列表派生判断的纯函数用例（S6 · T128a）。

这些函数是 1.0 ``_decorate_entities``（``db.py:4127-4137``）与 ``public_entity``
（``db.py:4674-4685``）里那几行判断的搬运。**搬的是边界，不是公式**——三条判据都
反直觉，而且每一条都能被「顺手改得更合理」改坏：

1. ``private_flag`` 判 ``is True`` 而不是真值；
2. ``single_sample`` 只看 ``member_count``，**不看簇的 status**；
3. 空簇列表下 ``all()`` 为真，所以「一个簇都没有的 pending 人物」是 ``single_sample``。

下面每一组都单列了「改坏了会怎样」的那一格。
"""

from __future__ import annotations

from sentrix.domain.identity.decoration import (
    PRIVATE_PLACE_NAME,
    is_reviewable,
    is_single_sample,
    private_place,
)
from sentrix.domain.identity.models import CLUSTER_STATUS_REJECTED, ClusterState


def _cluster(status: str = "pending", member_count: int = 1) -> ClusterState:
    return ClusterState(status=status, member_count=member_count)


# ---- is_reviewable · 人物 ---------------------------------------------------------


def test_a_confirmed_person_is_always_reviewable():
    """已确认的直接算数，哪怕一个簇都没有。"""
    assert is_reviewable(entity_type="person", status="confirmed", clusters=(), evidence_count=0)


def test_a_pending_person_is_reviewable_when_a_live_cluster_has_members():
    assert is_reviewable(
        entity_type="person",
        status="pending",
        clusters=(_cluster(status="pending", member_count=1),),
        evidence_count=0,
    )


def test_a_rejected_cluster_alone_does_not_make_a_person_reviewable():
    """被拒的簇不算数——它是用户已经否掉的判断，再拿它当「有东西可看」就是自相矛盾。"""
    assert not is_reviewable(
        entity_type="person",
        status="pending",
        clusters=(_cluster(status=CLUSTER_STATUS_REJECTED, member_count=5),),
        evidence_count=0,
    )


def test_an_empty_cluster_does_not_make_a_person_reviewable():
    """``member_count = 0`` 的簇是聚类留下的空壳：点进去什么都没有，所以不算「可看」。

    定点变异：把 ``cluster.member_count > 0`` 去掉，这条变红。
    """
    assert not is_reviewable(
        entity_type="person",
        status="pending",
        clusters=(_cluster(status="pending", member_count=0),),
        evidence_count=0,
    )


def test_one_live_cluster_is_enough_even_beside_rejected_ones():
    assert is_reviewable(
        entity_type="person",
        status="pending",
        clusters=(
            _cluster(status=CLUSTER_STATUS_REJECTED, member_count=9),
            _cluster(status="pending", member_count=1),
        ),
        evidence_count=0,
    )


# ---- is_reviewable · 非人物 -------------------------------------------------------


def test_a_non_person_is_reviewable_by_evidence_count_not_by_clusters():
    """非人物看**证据条数**。给它一堆簇也不会变「可看」——地点和物件没有脸。

    定点变异：把这一支也写成看簇的，这条与下一条一起变红。
    """
    assert is_reviewable(
        entity_type="place",
        status="pending",
        clusters=(),
        evidence_count=1,
    )
    assert not is_reviewable(
        entity_type="place",
        status="pending",
        clusters=(_cluster(status="pending", member_count=9),),
        evidence_count=0,
    )


def test_a_confirmed_non_person_still_needs_evidence():
    """非人物那一支**不看 status**——1.0 的 ``else`` 分支里只有 ``evidence_count > 0``。

    这一格容易想当然：人物「已确认就够」的印象会让人给非人物也开这个口子。
    """
    assert not is_reviewable(entity_type="place", status="confirmed", clusters=(), evidence_count=0)


# ---- is_single_sample ------------------------------------------------------------


def test_a_confirmed_person_is_never_single_sample():
    assert not is_single_sample(status="confirmed", clusters=(_cluster(member_count=1),))


def test_a_pending_person_is_single_sample_when_every_cluster_has_at_most_one_face():
    assert is_single_sample(
        status="pending",
        clusters=(_cluster(member_count=1), _cluster(member_count=0)),
    )


def test_two_faces_in_one_cluster_is_not_single_sample():
    assert not is_single_sample(status="pending", clusters=(_cluster(member_count=2),))


def test_a_person_with_no_clusters_at_all_is_single_sample():
    """**反直觉但正确**：空列表下 ``all()`` 为真，所以一个簇都还没有的 pending 人物
    判为孤证。1.0 就是这样，不改。

    定点变异：把 ``all(...)`` 写成 ``all(...) and clusters``，这条变红。
    """
    assert is_single_sample(status="pending", clusters=())


def test_a_rejected_cluster_still_counts_toward_single_sample():
    """判据里**没有**簇的 status：一个被拒的、有两张脸的簇照样让它不是孤证。

    这条是故意钉住那处「不一致」的——``is_reviewable`` 看 status，``is_single_sample``
    不看。两处都是 1.0 的行为；哪天有人把它们「统一」了，这条会红。
    """
    assert not is_single_sample(
        status="pending", clusters=(_cluster(status=CLUSTER_STATUS_REJECTED, member_count=2),)
    )


# ---- private_place ---------------------------------------------------------------


def test_a_place_without_the_flag_is_returned_unchanged():
    assert private_place("西湖", {}) == ("西湖", False)
    assert private_place("西湖", {"private_flag": False}) == ("西湖", False)


def test_a_private_place_uses_its_alias():
    assert private_place("某某小区", {"private_flag": True, "alias": "家"}) == ("家", True)


def test_a_private_place_without_an_alias_falls_back_to_the_constant():
    assert private_place("某某小区", {"private_flag": True}) == (PRIVATE_PLACE_NAME, True)
    # 空串也要落到兜底上：1.0 是 ``str(... or "私密地点")``，不是 ``get(..., 默认值)``。
    assert private_place("某某小区", {"private_flag": True, "alias": ""}) == (
        PRIVATE_PLACE_NAME,
        True,
    )


def test_only_a_real_boolean_true_makes_a_place_private():
    """``is True`` 不是真值判断。属性值是从 JSON 解出来的任意类型，``1`` 与 ``"true"``
    都能通过真值判断——1.0 只认真正的布尔 ``true``。

    定点变异：把 ``is not True`` 改成 ``not ...``（真值判断），这条变红。
    """
    for impostor in (1, "true", "1", [1], {"a": 1}):
        assert private_place("某某小区", {"private_flag": impostor}) == ("某某小区", False), impostor


def test_the_private_alias_is_stringified():
    """1.0 是 ``str(... or ...)``：别名不是串时照样转成串，不报错。"""
    assert private_place("某某小区", {"private_flag": True, "alias": 42}) == ("42", True)
