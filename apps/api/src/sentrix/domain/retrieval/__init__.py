"""检索域：查询契约、候选融合、向量端口与相似度。纯规则，零 IO。"""

from sentrix.domain.retrieval.fusion import (
    ANCHOR_BOOST,
    DEFAULT_CHANNEL_WEIGHTS,
    RRF_K,
    evidence_class_for,
    fuse,
    weighted_rrf_score,
)
from sentrix.domain.retrieval.models import CandidateHit, FusedCandidate
from sentrix.domain.retrieval.ports import (
    AssetVisualIndexer,
    VectorHit,
    VectorIndex,
    VectorIndexStatus,
    VectorIndexUnavailable,
    VectorRecord,
    VectorRepository,
    VisualEmbedder,
    VisualIndexOutcome,
)
from sentrix.domain.retrieval.similarity import cosine_similarity, dimension_matches

__all__ = [
    "ANCHOR_BOOST",
    "DEFAULT_CHANNEL_WEIGHTS",
    "RRF_K",
    "AssetVisualIndexer",
    "CandidateHit",
    "FusedCandidate",
    "VectorHit",
    "VectorIndex",
    "VectorIndexStatus",
    "VectorIndexUnavailable",
    "VectorRecord",
    "VectorRepository",
    "VisualEmbedder",
    "VisualIndexOutcome",
    "cosine_similarity",
    "dimension_matches",
    "evidence_class_for",
    "fuse",
    "weighted_rrf_score",
]
