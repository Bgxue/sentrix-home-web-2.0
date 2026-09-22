"""检索域：查询契约、候选融合与排序规则。纯规则，零 IO。"""

from sentrix.domain.retrieval.fusion import (
    ANCHOR_BOOST,
    DEFAULT_CHANNEL_WEIGHTS,
    RRF_K,
    evidence_class_for,
    fuse,
    weighted_rrf_score,
)
from sentrix.domain.retrieval.models import CandidateHit, FusedCandidate

__all__ = [
    "ANCHOR_BOOST",
    "DEFAULT_CHANNEL_WEIGHTS",
    "RRF_K",
    "CandidateHit",
    "FusedCandidate",
    "evidence_class_for",
    "fuse",
    "weighted_rrf_score",
]
