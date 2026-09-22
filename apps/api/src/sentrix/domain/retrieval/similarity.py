"""向量相似度。纯数学，零 IO，放 domain 而不是仓储里。

1.0 把它写进 ``MemoryStore._cosine``（db.py:3515 附近），于是「怎么算相似」这件事
只存在于 SQLite 适配器内部，回退路径与主路径无法共用同一份判定。抽出来后，
精确回退与任何未来的内存索引用的是同一个函数。
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度；长度不等、任一为空、或任一模长为 0 时返回 0.0。

    返回 0.0 而不是抛异常：向量维度不一致是数据问题，检索应当跳过这条候选继续，
    而不是让整次查询崩掉——但调用方必须能看见它（见 ``dimension_matches``）。
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def dimension_matches(vector: Sequence[float], expected: int) -> bool:
    """查询向量与索引维度是否一致——不一致时检索必须停下来说清楚，不能返回空。"""
    return len(vector) == expected
