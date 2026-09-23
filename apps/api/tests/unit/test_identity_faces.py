"""人脸聚类纯规则。数值与分支逐条对回 1.0 ``backend/face_clustering.py`` /
``backend/face_embeddings.py``。
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from sentrix.domain.identity.faces import (
    DEFAULT_MATCH_STRATEGY,
    DEFAULT_MATCH_THRESHOLD,
    DEFAULT_MINIMUM_QUALITY,
    DEFAULT_PERCENTILE,
    DEFAULT_TOPK,
    PROTOTYPE_LIMIT,
    ClusterResult,
    FaceCluster,
    FaceClusterer,
    FacePrototype,
    FaceSample,
    compute_face_quality,
    cosine,
    normalize_embedding,
    pose_bucket,
)

PROBE = [1.0, 0.0]
# 一段四个成员与探针的余弦，升序，用于钉住各 match_strategy 取的是哪个下标。
FOUR_SIMS = (0.2, 0.5, 0.6, 0.9)


def unit(cos_sim: float, *, sine: float = 1.0) -> list[float]:
    """二维单位向量：与 ``[1.0, 0.0]`` 的点积正好是 ``cos_sim``。``sine=-1`` 换半平面。"""
    return [cos_sim, sine * math.sqrt(1.0 - cos_sim * cos_sim)]


def sample(
    sample_id: str,
    embedding: list[float] | None = None,
    quality: float = 0.9,
    pose: str = "frontal",
    **overrides: object,
) -> FaceSample:
    return FaceSample(
        id=sample_id,
        embedding=PROBE if embedding is None else embedding,
        quality=quality,
        pose_bucket=pose,
        **overrides,  # type: ignore[arg-type]
    )


def members_with(sims: tuple[float, ...]) -> list[FaceSample]:
    return [sample(f"m{index}", embedding=unit(sim), pose="unknown") for index, sim in enumerate(sims)]


# --- cosine ---------------------------------------------------------------


def test_cosine_is_zero_when_either_operand_is_empty():
    assert cosine([], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], []) == 0.0
    assert cosine([], []) == 0.0
    assert cosine(None, [1.0]) == 0.0
    assert cosine([1.0], None) == 0.0


def test_cosine_is_zero_when_lengths_differ():
    assert cosine([1.0, 2.0], [1.0]) == 0.0
    assert cosine([1.0], [1.0, 2.0, 3.0]) == 0.0


def test_cosine_is_a_raw_dot_product():
    """不归一化：入参自身长度会进结果，``[3,4]·[3,4]`` 是 25 而不是 1。"""
    assert cosine([3.0, 4.0], [3.0, 4.0]) == 25.0
    assert cosine([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == 32.0


def test_cosine_accepts_int_operands():
    assert cosine([1, 2], [3, 4]) == 11.0


# --- normalize_embedding --------------------------------------------------


def test_normalize_embedding_scales_to_unit_length():
    assert normalize_embedding([3.0, 4.0]) == pytest.approx([0.6, 0.8])


def test_normalize_embedding_returns_empty_list_for_a_zero_vector():
    assert normalize_embedding([0.0, 0.0]) == []
    assert normalize_embedding([]) == []
    assert normalize_embedding(None) == []


def test_normalize_embedding_accepts_int_operands():
    assert normalize_embedding([3, 4]) == pytest.approx([0.6, 0.8])


# --- FaceSample -----------------------------------------------------------


def test_face_sample_normalises_its_embedding_on_construction():
    assert FaceSample(id="a", embedding=[3.0, 4.0], quality=0.5).embedding == pytest.approx([0.6, 0.8])


def test_face_sample_clamps_quality_into_the_unit_interval():
    assert FaceSample(id="a", embedding=[1.0], quality=1.4).quality == 1.0
    assert FaceSample(id="a", embedding=[1.0], quality=-0.4).quality == 0.0
    assert FaceSample(id="a", embedding=[1.0], quality=0.42).quality == 0.42


def test_face_sample_defaults_are_the_1_0_field_defaults():
    item = FaceSample(id="a", embedding=[1.0], quality=0.5)
    assert item.pose_bucket == "unknown"
    assert item.model_name == "adaface"
    assert item.model_version == "unconfigured"
    assert item.protected_cluster is None


def test_face_sample_is_frozen():
    item = FaceSample(id="a", embedding=[1.0], quality=0.5)
    with pytest.raises(FrozenInstanceError):
        item.quality = 0.9  # type: ignore[misc]


# --- pose_bucket ----------------------------------------------------------


def test_pose_bucket_reads_yaw_and_treats_the_pm20_band_as_frontal():
    assert pose_bucket([0.0, 20.0, 0.0]) == "frontal"
    assert pose_bucket([0.0, -20.0, 0.0]) == "frontal"
    assert pose_bucket([0.0, 20.5, 0.0]) == "profile_right"
    assert pose_bucket([0.0, -20.5, 0.0]) == "profile_left"


def test_pose_bucket_ignores_pitch_and_roll():
    assert pose_bucket([80.0, 0.0, -80.0]) == "frontal"


def test_pose_bucket_is_frontal_when_pose_is_missing_or_too_short():
    assert pose_bucket(None) == "frontal"
    assert pose_bucket([]) == "frontal"
    assert pose_bucket([35.0]) == "frontal"


# --- compute_face_quality -------------------------------------------------


def test_compute_face_quality_is_one_when_every_signal_is_maxed():
    assert compute_face_quality(1.0, 0.08, 1.0, [0.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_compute_face_quality_weights_each_signal_exactly():
    # 0.40·1.0 + 0.20·(0.02/0.08) + 0.20·0.5 + 0.20·(1 - 45/90) = 0.40 + 0.05 + 0.10 + 0.10
    assert compute_face_quality(1.0, 0.02, 0.5, [0.0, 45.0, 0.0]) == pytest.approx(0.65)


def test_compute_face_quality_isolates_one_weight_at_a_time():
    """四个系数必须各就各位；全项满分或四项等值的样本分不出权重，这里只开一项。"""
    assert compute_face_quality(1.0, 0.0, 0.0, None) == pytest.approx(0.60)  # 0.40 + 姿态满分
    assert compute_face_quality(0.0, 0.08, 0.0, None) == pytest.approx(0.40)  # 0.20 + 姿态满分
    assert compute_face_quality(0.0, 0.0, 1.0, None) == pytest.approx(0.40)  # 0.20 + 姿态满分
    assert compute_face_quality(0.0, 0.0, 0.0, [0.0, 0.0, 0.0]) == pytest.approx(0.20)  # 只有姿态项
    # 面积项 0.20 与清晰度项 0.20 各自独立：两项对一个、零对一个。
    assert compute_face_quality(0.0, 0.08, 0.5, None) == pytest.approx(0.50)
    assert compute_face_quality(0.0, 0.04, 1.0, None) == pytest.approx(0.50)


def test_compute_face_quality_treats_a_missing_pose_as_frontal():
    assert compute_face_quality(1.0, 0.08, 1.0, None) == pytest.approx(1.0)
    assert compute_face_quality(1.0, 0.08, 1.0, []) == pytest.approx(1.0)


def test_compute_face_quality_floors_the_pose_term_at_0_15():
    # yaw 超过 76.5 就已经落到地板；170 先被 min(90, |yaw|) 截平，与 90 同值。
    assert compute_face_quality(1.0, 0.08, 1.0, [0.0, 80.0, 0.0]) == pytest.approx(0.83)
    assert compute_face_quality(1.0, 0.08, 1.0, [0.0, 90.0, 0.0]) == pytest.approx(0.83)
    assert compute_face_quality(1.0, 0.08, 1.0, [0.0, 170.0, 0.0]) == pytest.approx(0.83)
    assert compute_face_quality(1.0, 0.08, 1.0, [0.0, -80.0, 0.0]) == pytest.approx(0.83)


def test_compute_face_quality_never_scores_a_profile_face_as_absent():
    """侧脸是弱证据而不是没有证据：其余信号满分时也要显著大于 0。"""
    assert compute_face_quality(1.0, 0.08, 1.0, [0.0, 90.0, 0.0]) > 0.5


def test_compute_face_quality_clamps_every_input_before_weighting():
    assert compute_face_quality(1.5, 0.16, 1.5, [0.0, 0.0, 0.0]) == pytest.approx(1.0)
    assert compute_face_quality(-1.0, -0.16, -1.0, [0.0, 0.0, 0.0]) == pytest.approx(0.20)


# --- FaceClusterer 常量与默认值 -------------------------------------------


def test_module_constants_are_the_1_0_hardcoded_defaults():
    assert DEFAULT_MATCH_THRESHOLD == 0.30
    assert DEFAULT_MINIMUM_QUALITY == 0.30
    assert PROTOTYPE_LIMIT == 6
    assert DEFAULT_MATCH_STRATEGY == "all"
    assert DEFAULT_TOPK == 3
    assert DEFAULT_PERCENTILE == 0.1


def test_clusterer_defaults_come_from_the_constants():
    clusterer = FaceClusterer()
    assert clusterer.match_threshold == 0.30
    assert clusterer.minimum_quality == 0.30
    assert clusterer.prototype_limit == 6
    assert clusterer.match_strategy == "all"
    assert clusterer.topk == 3
    assert clusterer.percentile == 0.1


def test_clusterer_keeps_threshold_and_minimum_quality_as_constructor_parameters():
    clusterer = FaceClusterer(match_threshold=0.78, minimum_quality=0.55)
    assert clusterer.match_threshold == 0.78
    assert clusterer.minimum_quality == 0.55
    assert clusterer.match_strategy == "all"


# --- fit ------------------------------------------------------------------


def test_fit_returns_a_cluster_result():
    result = FaceClusterer().fit([sample("a")])
    assert isinstance(result, ClusterResult)
    assert isinstance(result.clusters[0], FaceCluster)
    assert result.labels == {"a": "cluster-1"}


def test_fit_numbers_new_clusters_from_one_in_creation_order():
    result = FaceClusterer().fit([sample("a"), sample("b", embedding=[0.0, 1.0])])
    assert [cluster.id for cluster in result.clusters] == ["cluster-1", "cluster-2"]


def test_fit_processes_samples_by_descending_quality():
    """输入顺序相反也必须先处理高质量样本，否则簇 id 会翻过来。"""
    result = FaceClusterer().fit([sample("a", quality=0.5), sample("z", embedding=[0.0, 1.0], quality=0.9)])
    assert result.labels["z"] == "cluster-1"
    assert result.labels["a"] == "cluster-2"


def test_fit_breaks_quality_ties_by_sample_id():
    result = FaceClusterer().fit([sample("b", embedding=[0.0, 1.0]), sample("a")])
    assert result.labels["a"] == "cluster-1"
    assert result.labels["b"] == "cluster-2"


def test_fit_labels_every_sample():
    result = FaceClusterer().fit(
        [sample("a"), sample("b", embedding=[0.0, 1.0]), sample("c", quality=0.1, embedding=[-1.0, 0.0])]
    )
    assert set(result.labels) == {"a", "b", "c"}
    assert len(result.labels) == 3


def test_fit_marks_sub_minimum_quality_clusters_as_noise():
    result = FaceClusterer().fit([sample("good"), sample("bad", quality=0.29, embedding=[0.0, 1.0])])
    by_id = {cluster.id: cluster for cluster in result.clusters}
    assert by_id["cluster-1"].noise is False
    assert by_id["cluster-2"].noise is True


def test_fit_does_not_flag_a_sample_exactly_at_the_quality_floor():
    result = FaceClusterer().fit([sample("floor", quality=0.30)])
    assert result.clusters[0].noise is False


def test_a_sample_below_the_quality_floor_never_joins_an_identical_cluster():
    """完全相同向量的低质量样本也必须另起一簇——质量门在相似度之前。"""
    result = FaceClusterer().fit([sample("high"), sample("low", quality=0.1)])
    assert result.labels["high"] == "cluster-1"
    assert result.labels["low"] == "cluster-2"


def test_minimum_quality_is_read_from_the_constructor():
    result = FaceClusterer(minimum_quality=0.95).fit([sample("a", quality=0.9)])
    assert result.clusters[0].noise is True


def test_sample_joins_the_best_scoring_cluster_not_the_first_created():
    """cluster-1 相似度 0.5、cluster-2 相似度 0.9，两个都过阈值时按分数排序取前者。"""
    result = FaceClusterer().fit(
        [
            sample("c1", embedding=unit(0.5)),
            sample("c2", embedding=unit(0.9, sine=-1.0)),
            sample("probe"),
        ]
    )
    assert result.labels["c1"] == "cluster-1"
    assert result.labels["c2"] == "cluster-2"
    assert result.labels["probe"] == "cluster-2"


def test_frontal_and_profile_views_share_one_multi_view_cluster():
    result = FaceClusterer(match_threshold=0.78).fit(
        [
            sample("frontal", quality=0.98, pose="frontal"),
            sample("left", embedding=unit(0.9135), quality=0.90, pose="profile_left"),
            sample("right", embedding=unit(0.9135, sine=-1.0), quality=0.88, pose="profile_right"),
        ]
    )
    assert len(result.clusters) == 1
    assert result.labels["frontal"] == result.labels["left"] == result.labels["right"]
    assert {item.pose_bucket for item in result.clusters[0].prototypes} == {
        "frontal",
        "profile_left",
        "profile_right",
    }


def test_low_quality_bridge_does_not_merge_two_identities():
    result = FaceClusterer(match_threshold=0.78, minimum_quality=0.30).fit(
        [
            sample("person_a", quality=0.98, pose="frontal"),
            sample("person_b", embedding=unit(0.669), quality=0.98, pose="frontal"),
            sample("bridge", embedding=unit(0.9135), quality=0.08, pose="unknown"),
        ]
    )
    assert result.labels["person_a"] != result.labels["person_b"]
    assert result.labels["bridge"] not in {result.labels["person_a"], result.labels["person_b"]}


# --- 模型 / protected 两道逐簇否决 ----------------------------------------


def test_cluster_with_a_different_model_name_is_skipped():
    result = FaceClusterer().fit(
        [
            sample("a", model_name="adaface", model_version="v1"),
            sample("b", model_name="magface", model_version="v1"),
        ]
    )
    assert result.labels["a"] != result.labels["b"]


def test_cluster_with_a_different_model_version_is_skipped():
    result = FaceClusterer().fit([sample("a", model_version="v1"), sample("b", model_version="v2")])
    assert result.labels["a"] != result.labels["b"]


def test_identical_model_identity_still_merges():
    result = FaceClusterer().fit(
        [sample("a", model_name="adaface", model_version="v1"), sample("b", model_version="v1")]
    )
    assert result.labels["a"] == result.labels["b"]


def test_sample_with_a_foreign_protected_value_is_skipped():
    result = FaceClusterer().fit(
        [sample("a", protected_cluster="person-1"), sample("b", protected_cluster="person-2")]
    )
    assert result.labels["a"] != result.labels["b"]


def test_sample_sharing_a_protected_value_merges():
    result = FaceClusterer().fit(
        [sample("a", protected_cluster="person-1"), sample("b", protected_cluster="person-1")]
    )
    assert result.labels["a"] == result.labels["b"]


def test_protected_sample_joins_a_cluster_with_no_protected_member():
    result = FaceClusterer().fit([sample("a"), sample("b", protected_cluster="person-1")])
    assert result.labels["a"] == result.labels["b"]


def test_unprotected_sample_joins_a_protected_cluster():
    result = FaceClusterer().fit([sample("a", protected_cluster="person-1"), sample("b")])
    assert result.labels["a"] == result.labels["b"]


# --- 姿态桶与 _same_view_members ------------------------------------------


def test_a_view_bucket_absent_from_the_cluster_matches_without_a_similarity_check():
    """1.0 的刻意的分支：同姿态桶一个成员都没有时直接收下，不再比相似度。

    ``opposite`` 与簇里 profile_right 成员的余弦是 -0.64（远低于阈值），
    只有「空桶直接命中」这条路径能让它并进来。
    """
    result = FaceClusterer().fit(
        [
            sample("a_frontal", quality=0.9, pose="frontal"),
            sample("b_side", embedding=unit(0.5), quality=0.9, pose="profile_right"),
            sample("c_opposite", embedding=unit(0.35, sine=-1.0), quality=0.9, pose="profile_left"),
        ]
    )
    assert len(result.clusters) == 1
    assert result.labels["c_opposite"] == "cluster-1"


def test_unknown_view_sample_must_match_every_member():
    """unknown 姿态用**全部**成员比对，profile_right 成员那一票必须算数。

    只看同桶（unknown）成员的实现会把 same_view 算成空表，从而直接放行。
    """
    result = FaceClusterer().fit(
        [
            sample("a_frontal", quality=0.9, pose="frontal"),
            sample("b_side", embedding=unit(0.5), quality=0.9, pose="profile_right"),
            sample("c_unknown", embedding=unit(0.35, sine=-1.0), quality=0.9, pose="unknown"),
        ]
    )
    assert len(result.clusters) == 2
    assert result.labels["c_unknown"] == "cluster-2"


def test_same_view_members_uses_all_members_for_an_unknown_sample():
    cluster = FaceCluster(
        id="cluster-1",
        members=[sample("a", pose="frontal"), sample("b", embedding=[0.0, 1.0], pose="profile_right")],
    )
    clusterer = FaceClusterer()
    assert clusterer._same_view_members(cluster, sample("probe", pose="unknown")) == cluster.members


def test_same_view_members_filters_by_the_sample_bucket():
    cluster = FaceCluster(
        id="cluster-1",
        members=[sample("a", pose="frontal"), sample("b", embedding=[0.0, 1.0], pose="profile_right")],
    )
    clusterer = FaceClusterer()
    assert [item.id for item in clusterer._same_view_members(cluster, sample("probe", pose="frontal"))] == [
        "a"
    ]


# --- _matches 的四个策略分派 ----------------------------------------------


def test_matches_all_strategy_requires_the_worst_member_to_pass():
    clusterer = FaceClusterer(match_threshold=0.4, match_strategy="all")
    assert clusterer._matches(sample("probe"), members_with(FOUR_SIMS)) is False
    assert (
        FaceClusterer(match_threshold=0.1, match_strategy="all")._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is True
    )


def test_matches_mean_strategy_averages_every_member():
    # (0.2 + 0.5 + 0.6 + 0.9) / 4 = 0.55
    assert (
        FaceClusterer(match_threshold=0.5, match_strategy="mean")._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is True
    )
    assert (
        FaceClusterer(match_threshold=0.6, match_strategy="mean")._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is False
    )


def test_matches_topk_strategy_averages_the_best_k():
    # k=3 → (0.5 + 0.6 + 0.9) / 3 = 0.6667
    assert (
        FaceClusterer(match_threshold=0.6, match_strategy="topk", topk=3)._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is True
    )
    assert (
        FaceClusterer(match_threshold=0.7, match_strategy="topk", topk=3)._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is False
    )


def test_matches_topk_strategy_clamps_k_into_range():
    # topk 超过成员数 → 取全部，均值 0.55；topk=0 → 至少取 1 个，即最大值 0.9。
    assert (
        FaceClusterer(match_threshold=0.6, match_strategy="topk", topk=99)._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is False
    )
    assert (
        FaceClusterer(match_threshold=0.8, match_strategy="topk", topk=0)._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is True
    )


def test_matches_p10_strategy_takes_the_percentile_index():
    # int(4 * 0.5) = 2 → 0.6
    assert (
        FaceClusterer(match_threshold=0.5, match_strategy="p10", percentile=0.5)._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is True
    )
    assert (
        FaceClusterer(match_threshold=0.7, match_strategy="p10", percentile=0.5)._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is False
    )


def test_matches_p10_strategy_falls_back_to_the_worst_member_for_two_or_fewer():
    # 两个成员时不用百分比下标（那会取到 0.9），退回 sims[0] = 0.2。
    assert (
        FaceClusterer(match_threshold=0.5, match_strategy="p10", percentile=0.5)._matches(
            sample("probe"), members_with((0.2, 0.9))
        )
        is False
    )
    # 三个成员时同一 percentile 走真分支：int(3 * 0.5) = 1 → 0.5。
    assert (
        FaceClusterer(match_threshold=0.5, match_strategy="p10", percentile=0.5)._matches(
            sample("probe"), members_with((0.2, 0.5, 0.9))
        )
        is True
    )


def test_matches_p10_strategy_uses_the_single_member_directly():
    assert (
        FaceClusterer(match_threshold=0.5, match_strategy="p10", percentile=0.5)._matches(
            sample("probe"), members_with((0.9,))
        )
        is True
    )


def test_matches_unknown_strategy_falls_back_to_the_worst_member():
    assert (
        FaceClusterer(match_threshold=0.4, match_strategy="zzz")._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is False
    )
    assert (
        FaceClusterer(match_threshold=0.1, match_strategy="zzz")._matches(
            sample("probe"), members_with(FOUR_SIMS)
        )
        is True
    )


def test_matches_default_strategy_is_all():
    clusterer = FaceClusterer(match_threshold=0.4)
    assert clusterer.match_strategy == "all"
    assert clusterer._matches(sample("probe"), members_with(FOUR_SIMS)) is False


# --- _update_prototypes ---------------------------------------------------


def test_each_view_bucket_keeps_its_own_prototype():
    cluster = FaceCluster(id="cluster-1")
    clusterer = FaceClusterer()
    clusterer._update_prototypes(cluster, sample("a", embedding=[1.0, 0.0], quality=0.5, pose="frontal"))
    clusterer._update_prototypes(cluster, sample("b", embedding=unit(0.5), quality=0.6, pose="profile_left"))
    assert [(item.pose_bucket, item.sample_id) for item in cluster.prototypes] == [
        ("profile_left", "b"),
        ("frontal", "a"),
    ]


def test_existing_prototype_is_replaced_in_place_by_a_higher_quality_sample():
    cluster = FaceCluster(id="cluster-1")
    clusterer = FaceClusterer()
    clusterer._update_prototypes(cluster, sample("a", embedding=[1.0, 0.0], quality=0.5))
    clusterer._update_prototypes(cluster, sample("b", embedding=unit(0.5), quality=0.6))

    assert len(cluster.prototypes) == 1
    assert cluster.prototypes[0].sample_id == "b"
    assert cluster.prototypes[0].quality == 0.6
    assert cluster.prototypes[0].embedding == pytest.approx(unit(0.5))

    clusterer._update_prototypes(cluster, sample("c", embedding=[1.0, 0.0], quality=0.4))
    assert len(cluster.prototypes) == 1
    assert cluster.prototypes[0].sample_id == "b"


def test_prototypes_are_sorted_and_truncated_to_the_limit():
    cluster = FaceCluster(id="cluster-1")
    clusterer = FaceClusterer(prototype_limit=2)
    clusterer._update_prototypes(cluster, sample("a", quality=0.5, pose="frontal"))
    clusterer._update_prototypes(cluster, sample("b", quality=0.9, pose="profile_right"))
    clusterer._update_prototypes(cluster, sample("c", quality=0.7, pose="profile_left"))

    assert [(item.quality, item.pose_bucket) for item in cluster.prototypes] == [
        (0.9, "profile_right"),
        (0.7, "profile_left"),
    ]


def test_prototypes_with_equal_quality_are_ordered_by_bucket():
    cluster = FaceCluster(id="cluster-1")
    clusterer = FaceClusterer()
    clusterer._update_prototypes(cluster, sample("z", quality=0.5, pose="profile_right"))
    clusterer._update_prototypes(cluster, sample("a", quality=0.5, pose="frontal"))
    assert [item.pose_bucket for item in cluster.prototypes] == ["frontal", "profile_right"]


# --- FaceCluster.best_score -----------------------------------------------


def test_best_score_is_zero_without_prototypes():
    assert FaceCluster(id="cluster-1").best_score(sample("probe")) == 0.0


def test_best_score_is_the_maximum_over_prototypes():
    cluster = FaceCluster(
        id="cluster-1",
        prototypes=[
            FacePrototype(sample_id="low", embedding=unit(0.2), quality=0.9, pose_bucket="frontal"),
            FacePrototype(sample_id="high", embedding=unit(0.9), quality=0.9, pose_bucket="profile_left"),
            FacePrototype(sample_id="mid", embedding=unit(0.5), quality=0.9, pose_bucket="profile_right"),
        ],
    )
    assert cluster.best_score(sample("probe")) == pytest.approx(0.9)
