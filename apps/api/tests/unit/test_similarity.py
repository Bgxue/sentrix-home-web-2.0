"""余弦相似度的边界行为。"""

from __future__ import annotations

import pytest

from sentrix.domain.retrieval.similarity import cosine_similarity, dimension_matches


def test_identical_vectors_score_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_opposite_vectors_score_minus_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_orthogonal_vectors_score_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_scale_does_not_matter():
    assert cosine_similarity([1.0, 1.0], [10.0, 10.0]) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ([], [1.0]),
        ([1.0], []),
        ([1.0, 2.0], [1.0]),
    ],
)
def test_length_mismatch_scores_zero(left, right):
    """维度不一致返回 0.0 而不是抛异常；是否跳过该候选由调用方决定。"""
    assert cosine_similarity(left, right) == 0.0


def test_zero_vector_scores_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_dimension_matches():
    assert dimension_matches([1.0] * 768, 768) is True
    assert dimension_matches([1.0] * 512, 768) is False
