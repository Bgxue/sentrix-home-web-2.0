"""融合之后的**条件排除**：把不满足硬条件的候选从结果里去掉。

对应 1.0 ``backend/evidence_retrieval.py:325-380`` 的 ``_evaluate_fused`` +
``:414-445`` 的 ``_evaluate``。**这是「硬条件」真正生效的地方。**

## 为什么单独一个用例

``RetrieveEvidence`` 现在只拿四条召回通道。条件判定要用到资产、观察、地点、人物
四个仓储——直接塞进去会让那个编排类从「四条通道 + 一次融合」变成「四条通道 +
一次融合 + 四个仓储」，而它读起来就不再是「怎么把通道合成一份排名」了。

## 三条照抄 1.0，每条都会静默出错

**一、按观察逐条判，取最好的那条。** 一张图可能有多条观察（不同时间、不同富集版本），
1.0 ``_evaluate_fused`` 对每条都判、取 ``rank`` 最高的那条（``:337-341``）。
只判第一条的话，一条没提到人物的观察会把整张图判死——
**而那张图明明在另一条观察里就有那个人**。

**二、``rank`` 的含义**（``:445``）：``0`` 被排除 / ``3`` exact / ``2`` strong / ``1`` 其余。
``excluded`` 排在最前，所以「有一条观察通过」永远赢过「有一条观察被排除」。

**三、只排除、不重排。** 1.0 的注释写死了：「Later reranking decides ordering;
scope and explicit media type remain the only hard boundaries.」
条件判定只做减法——排序仍由融合那一步的分数决定。**顺手重排会让分数不再可解释**，
而结果看起来只是「顺序有点怪」。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sentrix.domain.media.models import Asset, Observation
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.ports import AssetPlaceRepository
from sentrix.domain.retrieval.conditions import (
    Condition,
    ConditionStatus,
    Evidence,
    EvidenceContext,
    evaluate,
    excludes,
    trusted,
)

# 1.0 ``_evaluate`` 的 ``rank``：被排除的最靠前（0），然后按证据档位 3 / 2 / 1。
RANK_EXCLUDED = 0
RANK_EXACT = 3
RANK_STRONG = 2
RANK_APPROXIMATE = 1


@dataclass(frozen=True, slots=True)
class ConditionVerdict:
    """一张图的判定结果。``results`` 逐条列出——**被排除时必须说得出是哪一条**。"""

    asset_id: str
    excluded: bool
    rank: int
    results: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ConditionOutcome:
    kept: tuple[str, ...] = ()
    excluded_count: int = 0
    verdicts: tuple[ConditionVerdict, ...] = field(default_factory=tuple)


class EvaluateConditions:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        places: AssetPlaceRepository | None = None,
        aliases: PlaceAliases | None = None,
        confirmed_person: Callable[[str], bool] | None = None,
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._places = places
        self._aliases = aliases
        self._confirmed_person = confirmed_person

    def filter(
        self,
        asset_ids: Sequence[str],
        conditions: Sequence[Condition],
        *,
        scope_id: str | None = None,
    ) -> ConditionOutcome:
        if not conditions:
            # 没有条件就没有排除。**不要在这里返回「全都排除」或「全都保留」以外的
            # 第三种行为**——空条件是最常见的一档（纯语义查询），它的代价必须是零 IO。
            return ConditionOutcome(kept=tuple(asset_ids))

        kept: list[str] = []
        verdicts: list[ConditionVerdict] = []
        excluded_count = 0
        for asset_id in asset_ids:
            verdict = self._judge(asset_id, conditions, scope_id=scope_id)
            verdicts.append(verdict)
            if verdict.excluded:
                excluded_count += 1
            else:
                kept.append(asset_id)
        return ConditionOutcome(kept=tuple(kept), excluded_count=excluded_count, verdicts=tuple(verdicts))

    def _judge(
        self, asset_id: str, conditions: Sequence[Condition], *, scope_id: str | None
    ) -> ConditionVerdict:
        asset = self._assets.get(asset_id)
        if asset is None:
            # 融合结果里的 id 在权威存储里查不到——**排除它，并且这是要看得见的**
            # （``excluded_count`` 会计上它）。留着会让下游拿到一个不存在的 asset。
            return ConditionVerdict(asset_id, excluded=True, rank=RANK_EXCLUDED)

        # 地点按资产查一次，四条观察共用。**必须是这个资产的解析结果**——
        # 传 ``None`` 会让地点条件一律 UNKNOWN，而 UNKNOWN 在 HARD 档下同样排除候选，
        # 于是「地点条件」会表现为「把所有照片都删了」。
        context = self._context(asset_id)
        best: ConditionVerdict | None = None
        for observation in self._observations_for(asset_id):
            candidate = self._judge_one(asset, observation, conditions, context)
            if best is None or candidate.rank > best.rank:
                best = candidate
        return best or ConditionVerdict(asset_id, excluded=True, rank=RANK_EXCLUDED)

    def _judge_one(
        self,
        asset: Asset,
        observation: Observation,
        conditions: Sequence[Condition],
        context: EvidenceContext,
    ) -> ConditionVerdict:
        results: list[Evidence] = []
        excluded = False
        for condition in conditions:
            evidence = trusted(evaluate(condition, asset, observation, context=context))
            results.append(evidence)
            if excludes(condition, evidence):
                excluded = True
        return ConditionVerdict(
            asset_id=asset.id,
            excluded=excluded,
            rank=RANK_EXCLUDED if excluded else _rank_of(results),
            results=tuple(results),
        )

    def _observations_for(self, asset_id: str) -> Sequence[Observation]:
        return self._observations.list_by_asset(asset_id)

    def _context(self, asset_id: str) -> EvidenceContext:
        """判一条约束需要的外部事实。``places`` 没接时 ``place`` 恒为 ``None``——
        那让地点条件走 UNKNOWN 分支（不是 CONTRADICTED），与「这张图没解析出地点」
        是同一条路。**这是有意的降级，不是等价实现**：接上之后地点条件才真的会排除人。"""
        return EvidenceContext(
            place=self._places.get(asset_id) if self._places is not None else None,
            place_aliases=self._aliases if self._aliases is not None else EvidenceContext().place_aliases,
            confirmed_person=self._confirmed_person,
        )


def _rank_of(results: Sequence[Evidence]) -> int:
    """1.0 ``:427-429`` 的档位：全 possible → strong；都是确定命中 → exact；否则 approximate。

    ``unknown`` 也算进「有一处不确定」——它进 approximate，不进 exact。
    """
    if not results:
        return RANK_EXACT
    if all(item.status is ConditionStatus.POSSIBLE for item in results):
        return RANK_STRONG
    if all(item.status is ConditionStatus.MATCHED for item in results):
        return RANK_EXACT
    return RANK_APPROXIMATE
