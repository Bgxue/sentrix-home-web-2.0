from __future__ import annotations

from pydantic import BaseModel

from sentrix.api.schemas.media import AssetSummary


class SearchHit(BaseModel):
    """一条命中。``asset`` 是可打开的原图本身——产品硬约束：查询必须返回原图。"""

    asset: AssetSummary
    score: float
    # 通道 -> 该通道里的名次。S2 只有 visual_ann 一条，结构先按六路留好。
    channels: dict[str, int]
    raw_scores: dict[str, float]


class SearchResponse(BaseModel):
    query: str
    # 实际服务的后端：qdrant 或 sqlite_exact。
    backend: str
    degraded: bool
    degraded_reason: str
    # 权威存储里的向量条数。用来区分「还没导入过」和「搜不到」。
    authoritative_vectors: int
    # 被结构化条件排除掉的候选数。**必须给出来**：没有它，「条件把所有候选都排除了」
    # 与「本来就没有候选」在响应上长得一模一样，而这两件事该做的处置相反
    # （前者是用户的条件太窄，后者是库里没有）。
    excluded_count: int = 0
    items: list[SearchHit]
