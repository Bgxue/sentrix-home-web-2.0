"""方案漂移：库里**有**向量，却没有一条属于当前方案。

**为什么需要它。** 换嵌入方案（`domain/retrieval/scheme.py`）之后必须重建索引，
否则查询向量与库里向量不同源——而**这件事本身不会报错**。2026-10-03 实测：删掉当前
方案的全部向量、模拟「切了方案但忘记重建」之后，两条向量通道仍然报
`status=ok / candidate_count=0`，响应里 `degraded` 是 `False`、`reason` 是空串，
检索返回 2 条而不是 3 条——**看起来完全正常，只是结果变差**。

这正是 1.0 `backend/embeddings/scheme.py` 开头那段事故注释描述的事
（「检索时好时坏，而且**没有任何一处会报错**」）。1.0 为此写了
`consistency.assert_ready`（不匹配就抛 `ConsistencyError`，注释明说「刻意做成异常
而不是日志——检索静默返回空比报错难查得多」），**但那东西从来没接进运行时**，
只有两个重建脚本在结尾打印一行提示。这里做的是把它接上。

**判据只有一条，两个数都从现成接口拿**：这个 space 里有多少条向量
（`model_name=None` 是不按模型过滤）对阵有多少条属于当前方案。差集就是别的方案
留下的。**不需要知道「本该有多少」**——那要再查资产数与观察数，代价大且要区分
「scope 本来就没东西」，而这里两个数为 0 时自然不报，正好把那种情况放过去。
"""

from __future__ import annotations

from sentrix.domain.retrieval.ports import VectorRepository


def scheme_drift_reason(vectors: VectorRepository, *, space: str, model_name: str) -> str:
    """当前方案在这个 space 里一条向量都没有、而 space 并非空时，给一句能照着做的原因。

    返回空串表示没问题（包括「这个 space 本来就是空的」）。
    """
    if vectors.count(space=space, model_name=model_name) > 0:
        return ""
    stored = vectors.count(space=space)
    if stored == 0:
        return ""
    # 带上补救动作。1.0 ``consistency.py`` 的异常消息就是这么写的——一条只说
    # 「不匹配」的报错，读的人还得自己去翻是哪个脚本能修。
    return (
        f"scheme_out_of_sync:{space} 里有 {stored} 条向量，"
        f"没有一条属于当前方案 {model_name}；先重建再检索"
        f"（scripts/backfill_text_vectors.py / scripts/backfill_visual_vectors.py）"
    )
