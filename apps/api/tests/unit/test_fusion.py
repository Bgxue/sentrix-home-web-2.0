"""融合排序规则的单元测试。

这些断言钉的是 `docs/refactor/behaviour-baseline.md` 里冻结的数值：RRF_K=60、
anchor +1.0、六路通道权重。1.0 的这套权重来自 R7 消融实验（chinese-clip 视觉
r10=0.887、词法 0.373、文本 0.158），平权 RRF 会让弱通道把强通道稀释掉，所以
改任何一个数字都必须先改基线文档。
"""

from sentrix.domain.retrieval.fusion import (
    ANCHOR_BOOST,
    DEFAULT_CHANNEL_WEIGHTS,
    RRF_K,
    evidence_class_for,
    fuse,
    weighted_rrf_score,
)
from sentrix.domain.retrieval.models import CandidateHit


def hit(asset_id: str, channel: str, rank: int, score: float = 1.0) -> CandidateHit:
    return CandidateHit(asset_id=asset_id, channel=channel, rank=rank, raw_score=score)


def test_frozen_constants_match_behaviour_baseline():
    assert RRF_K == 60
    assert ANCHOR_BOOST == 1.0
    assert DEFAULT_CHANNEL_WEIGHTS == {
        "visual_ann": 2.5,
        "lexical": 1.0,
        "text_ann": 0.5,
        "metadata": 1.0,
        "entity": 1.0,
        "adjacency": 0.5,
    }


def test_weighted_rrf_matches_cormack_formula():
    """weight/(k+rank)，k=60。rank 从 1 开始，所以 rank=1 时是 weight/61。"""
    assert weighted_rrf_score({"visual_ann": 1}, DEFAULT_CHANNEL_WEIGHTS) == 2.5 / 61
    assert weighted_rrf_score({"lexical": 1}, DEFAULT_CHANNEL_WEIGHTS) == 1.0 / 61


def test_visual_weight_dominates_lexical_at_equal_rank():
    channels = {
        "visual_ann": [hit("asset_visual", "visual_ann", 1)],
        "lexical": [hit("asset_lexical", "lexical", 1)],
    }

    fused = fuse(channels)

    assert [c.asset_id for c in fused] == ["asset_visual", "asset_lexical"]
    # 反向验证：权重真的生效了。若 fuse() 退回平权 RRF，两条分数会相等，上面那条
    # 断言就只剩「插入顺序」在决定名次——那正是本测试要挡住的恒真形态。
    assert fused[0].score > fused[1].score


def test_multi_channel_agreement_outranks_single_channel():
    """同一张图出现在两条通道上，RRF 自然累加。"""
    channels = {
        "visual_ann": [hit("asset_both", "visual_ann", 2), hit("asset_visual_only", "visual_ann", 1)],
        "lexical": [hit("asset_both", "lexical", 1)],
    }

    fused = fuse(channels)

    assert fused[0].asset_id == "asset_both"
    assert fused[0].channels == {"visual_ann": 2, "lexical": 1}


def test_anchor_channels_get_deterministic_boost():
    channels = {
        "visual_ann": [hit("asset_visual", "visual_ann", 1)],
        "metadata": [hit("asset_anchor", "metadata", 1)],
    }

    fused = fuse(channels)

    by_id = {c.asset_id: c for c in fused}
    assert by_id["asset_anchor"].evidence_class == "anchor"
    assert by_id["asset_visual"].evidence_class == "semantic"
    # 1.0/61 = 0.01639，anchor 再加 1.0 —— 元数据精确命中永远压在语义命中之上。
    assert by_id["asset_anchor"].score == 1.0 / 61 + ANCHOR_BOOST
    assert fused[0].asset_id == "asset_anchor"


def test_expander_channel_is_classified_separately():
    assert evidence_class_for("adjacency") == "expander"
    assert evidence_class_for("entity") == "anchor"
    assert evidence_class_for("lexical") == "semantic"
    assert evidence_class_for("visual_ann") == "semantic"


def test_channel_insertion_order_does_not_change_the_result():
    forward = fuse(
        {
            "visual_ann": [hit("asset_b", "visual_ann", 1)],
            "lexical": [hit("asset_a", "lexical", 1)],
        }
    )
    backward = fuse(
        {
            "lexical": [hit("asset_a", "lexical", 1)],
            "visual_ann": [hit("asset_b", "visual_ann", 1)],
        }
    )

    # visual 权重更高，所以两条路径下 asset_b 都该在前。
    assert [c.asset_id for c in forward] == ["asset_b", "asset_a"]
    assert [c.asset_id for c in backward] == ["asset_b", "asset_a"]


def test_ties_break_on_asset_id_not_channel_insertion_order():
    """同分时按 asset_id 稳定排序。

    1.0 直接 sort(reverse=True)，同分名次取决于 dict 的插入顺序，也就是调用方
    传通道的先后——同一份数据换个调用顺序就换一套结果。检索必须可复现。
    """
    forward = fuse(
        {
            "metadata": [hit("asset_z", "metadata", 1)],
            "entity": [hit("asset_y", "entity", 1)],
        }
    )
    backward = fuse(
        {
            "entity": [hit("asset_y", "entity", 1)],
            "metadata": [hit("asset_z", "metadata", 1)],
        }
    )

    scores = {c.asset_id: c.score for c in forward}
    assert scores["asset_y"] == scores["asset_z"], "构造前提：两条必须同分"
    assert [c.asset_id for c in forward] == ["asset_y", "asset_z"]
    assert [c.asset_id for c in backward] == ["asset_y", "asset_z"]


def test_rank_comes_from_the_hit_not_the_list_position():
    """命中自报的 rank 才是权威，列表下标不算数。

    1.0 用 ``enumerate`` 取下标：命中列表一旦没排好，分数与自报名次就悄悄对不上，
    且没有任何征兆。这里把同一个 asset 放两次、顺序颠倒，断言用的是 rank=2。
    """
    fused = fuse({"visual_ann": [hit("asset_b", "visual_ann", 2), hit("asset_b", "visual_ann", 1)]})

    assert fused[0].channels == {"visual_ann": 1}
    assert fused[0].score == 2.5 / 61


def test_empty_input_yields_no_candidates():
    assert fuse({}) == []
    assert fuse({"visual_ann": []}) == []
