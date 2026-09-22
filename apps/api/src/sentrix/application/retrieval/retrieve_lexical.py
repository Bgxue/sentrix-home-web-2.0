"""词法召回用例：查询 → FTS5 倒排命中 → 命中计数排序 → ``CandidateHit``。

对应 1.0 ``backend/retrieval/lexical.py`` 的 ``LexicalRetriever.retrieve``。

1.0 里这个类还带一个 ``_ensure_populated``：FTS 表为空时**懒补建**整张投影
（``lexical.py:34-48``）。2.0 **不搬**——它是 1.0 那个「生产不写投影」缺陷的补丁，
而不是设计的一部分：

- 1.0 的投影只在 maintenance 脚本与 `scope_finalize` 里建，日常导入不写；于是
  ``_ensure_populated`` 必须存在，否则第一次查询恒为空；
- 2.0 把投影挂到导入路径上逐条写（用户裁决 #4），FTS 表在每次导入后就是最新的。

保留懒补建等于给「导入没写投影」留一条永远不会红的退路：真出了这个问题，
用户会看到「第一次查慢、之后正常」，而不是「查不到」。**在检索路径上做整表重建
也是错的**——一次查询触发 96 万行写入，没有任何超时保护。
"""

from __future__ import annotations

from collections.abc import Sequence

from sentrix.domain.retrieval.lexical import score_token_hits
from sentrix.domain.retrieval.models import CandidateHit
from sentrix.domain.retrieval.ports import LexicalSearch

CHANNEL = "lexical"


class RetrieveLexical:
    def __init__(self, *, index: LexicalSearch) -> None:
        self._index = index

    def execute(
        self,
        *,
        whole_query: str,
        facets: Sequence[str] = (),
        scope_id: str | None,
        limit: int,
    ) -> list[CandidateHit]:
        """整串与每个切面各自独立匹配，再按 asset 合并。

        1.0 ``lexical.py:56-72``：``queries = [whole_query] + [facet.surface_text …]``，
        去重保序；每个 query 单独查一次 FTS，同一个 asset 的**命中数累加**，
        ``matched_text`` 记第一次命中的那个 query。
        """
        queries = list(dict.fromkeys(item for item in (whole_query, *facets) if item))
        if not queries:
            return []

        scores: dict[str, float] = {}
        matched: dict[str, str] = {}
        # ``limit * 4`` 是 1.0 的过取倍数（``lexical.py:63``）：命中数相同的候选在
        # 单条 query 内被截断时，跨 query 合并后名次会变，过取一层留出余量。
        per_query_limit = max(limit, 1) * 4
        for surface in queries:
            rows = self._index.search_fts(surface, scope_id=scope_id, limit=per_query_limit)
            token_hits = {asset_id: count for asset_id, count in rows}
            whole = surface
            scored = score_token_hits(
                token_hits,
                whole_query=whole,
                exact_assets=self._index.exact_assets(whole, scope_id=scope_id),
            )
            for asset_id, score in scored:
                scores[asset_id] = scores.get(asset_id, 0.0) + score
                matched.setdefault(asset_id, surface)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return [
            CandidateHit(
                asset_id=asset_id,
                channel=CHANNEL,
                rank=index + 1,
                raw_score=score,
                # 跨通道的原始分数不可比：命中次数与余弦相似度量纲不同，
                # 融合只消费 rank。见 domain/retrieval/models.py 的模块注释。
                score_kind="token_hits",
                higher_is_better=True,
            )
            for index, (asset_id, score) in enumerate(ranked)
        ]
