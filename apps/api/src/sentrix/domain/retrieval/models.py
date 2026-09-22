"""检索领域模型：候选命中与融合结果。纯数据与规则，零 IO。

分值不是裸 float。1.0 的 ``retrieval/base.py`` 把这件事写成了契约（P0-19）：
``CandidateHit`` 必须同时带 ``raw_score`` / ``score_kind`` / ``higher_is_better`` /
``rank``，因为跨通道的原始分数不可比——余弦相似度 0.3 与词法命中次数 1 放一起比
大小没有任何意义。融合只消费 ``rank``，原始分数只用于展示与探针校准。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CandidateHit:
    """一条通道召回的一条候选。``rank`` 从 1 开始，与 RRF 公式一致。"""

    asset_id: str
    channel: str
    rank: int
    raw_score: float
    score_kind: str = "cosine_similarity"
    higher_is_better: bool = True

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError(f"rank 从 1 开始，收到 {self.rank}")


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """融合后的候选。``channels`` 记录它被哪些通道以第几名召回。"""

    asset_id: str
    score: float
    channels: Mapping[str, int] = field(default_factory=dict)
    raw_scores: Mapping[str, float] = field(default_factory=dict)
    evidence_class: str = "semantic"
