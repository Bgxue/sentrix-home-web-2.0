"""``EvaluateConditions`` 的测试：全假仓储。

**三条会静默出错的形状，各配一个用例：**

1. **多条观察取最好的那条**——只判第一条的话，一条没提到人物的观察会把整张图判死，
   而那张图明明在另一条观察里就有那个人；
2. **没有条件时零 IO**——纯语义查询是最常见的一档，它不该因为「顺便判一下」而多查库；
3. **查不到的 asset 要排除并且计数**——留着会让下游拿到一个不存在的 id。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.application.retrieval.evaluate_conditions import (
    RANK_EXACT,
    RANK_EXCLUDED,
    EvaluateConditions,
)
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.places.models import AdminArea, NearestPlace, Place, PlacePrecision, PlaceSource
from sentrix.domain.retrieval.conditions import Condition, Strictness

NOW = datetime(2026, 9, 29, tzinfo=UTC)
SCOPE = "album1"


def make_asset(asset_id: str, **overrides) -> Asset:
    base = dict(
        id=asset_id,
        scope_id=SCOPE,
        file_name=f"{asset_id}.jpg",
        media_type=MediaType.IMAGE,
        path=f"/tmp/{asset_id}.jpg",
        mime_type="image/jpeg",
        size_bytes=1,
        status=AssetStatus.PROCESSED,
        content_sha256=f"h_{asset_id}",
        captured_at=datetime(2023, 10, 5, 12, 0),
        captured_location=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return Asset(**base)


def make_observation(observation_id: str, asset_id: str, **overrides) -> Observation:
    base = dict(
        id=observation_id,
        scope_id=SCOPE,
        asset_id=asset_id,
        source_type="import",
        captured_at=datetime(2023, 10, 5, 12, 0),
        confidence=0.8,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return Observation(**base)


class FakeAssets:
    def __init__(self, assets: list[Asset]) -> None:
        self._by_id = {item.id: item for item in assets}
        self.gets: list[str] = []

    def get(self, asset_id: str) -> Asset | None:
        self.gets.append(asset_id)
        return self._by_id.get(asset_id)


class FakeObservations:
    def __init__(self, by_asset: dict[str, list[Observation]]) -> None:
        self._by_asset = by_asset
        self.calls: list[str] = []

    def list_by_asset(self, asset_id: str):
        self.calls.append(asset_id)
        return list(self._by_asset.get(asset_id, []))


class FakePlaces:
    def __init__(self, by_asset: dict[str, Place] | None = None) -> None:
        self._by_asset = by_asset or {}

    def get(self, asset_id: str) -> Place | None:
        return self._by_asset.get(asset_id)


def place_of(*, city: str) -> Place:
    return Place(
        admin=AdminArea(city=city),
        nearest=NearestPlace(),
        precision=PlacePrecision.CITY,
        source=PlaceSource.PYGEOCN,
    )


def build(assets, observations, places=None, **kwargs) -> EvaluateConditions:
    return EvaluateConditions(
        assets=FakeAssets(assets),
        observations=FakeObservations(observations),
        places=places,
        **kwargs,
    )


def test_without_conditions_nothing_is_excluded_and_nothing_is_read():
    """纯语义查询是最常见的一档。**它不该因为「顺便判一下」而多查库。**"""
    evaluator = build([make_asset("a")], {"a": [make_observation("o1", "a")]})

    outcome = evaluator.filter(["a"], [])

    assert outcome.kept == ("a",)
    assert outcome.excluded_count == 0
    assert evaluator._assets.gets == [], "无条件时不该去读资产"
    assert evaluator._observations.calls == [], "无条件时不该去读观察"


def test_a_hard_condition_excludes_the_non_matching_asset():
    assets = [make_asset("a", media_type=MediaType.IMAGE), make_asset("b", media_type=MediaType.VIDEO)]
    evaluator = build(assets, {"a": [make_observation("o1", "a")], "b": [make_observation("o2", "b")]})

    outcome = evaluator.filter(["a", "b"], [Condition(dimension="media", value="image")])

    assert outcome.kept == ("a",)
    assert outcome.excluded_count == 1


def test_an_asset_is_kept_when_any_observation_passes():
    """**这条是「按观察逐条判、取最好的那条」的全部意义。**

    一张图有多条观察（不同时间、不同富集版本）。只判第一条的话，一条没提到人物的
    观察会把整张图判死——而那张图明明在另一条观察里就有那个人。
    """
    assets = [make_asset("a")]
    observations = {
        "a": [
            make_observation("o1", "a"),  # 没有这个人
            make_observation("o2", "a", people=("妈妈",)),
        ]
    }
    evaluator = build(assets, observations)

    outcome = evaluator.filter(["a"], [Condition(dimension="person", value="妈妈")])

    assert outcome.kept == ("a",)
    assert outcome.excluded_count == 0


def test_an_asset_is_excluded_only_when_every_observation_fails():
    """反向对照：所有观察都不满足才排除。没有这条，上面那条可能只是「永远保留」。"""
    assets = [make_asset("a")]
    observations = {"a": [make_observation("o1", "a"), make_observation("o2", "a")]}
    evaluator = build(assets, observations)

    outcome = evaluator.filter(["a"], [Condition(dimension="person", value="妈妈")])

    assert outcome.kept == ()
    assert outcome.excluded_count == 1


def test_a_missing_asset_is_excluded_and_counted():
    """融合结果里的 id 在权威存储里查不到——排除它，**并且让它在计数上看得见**。"""
    evaluator = build([], {})

    outcome = evaluator.filter(["ghost"], [Condition(dimension="media", value="image")])

    assert outcome.kept == ()
    assert outcome.excluded_count == 1


def test_all_conditions_must_pass():
    """多条条件是与的关系：任何一条不满足就排除。"""
    assets = [make_asset("a", media_type=MediaType.IMAGE)]
    evaluator = build(assets, {"a": [make_observation("o1", "a")]})

    outcome = evaluator.filter(
        ["a"],
        [
            Condition(dimension="media", value="image"),  # 满足
            Condition(dimension="media", value="video"),  # 不满足
        ],
    )

    assert outcome.kept == ()
    assert outcome.excluded_count == 1


def test_a_semantic_condition_does_not_exclude_on_unknown():
    """两档严格度的差别在这里第一次产生可见后果：SEMANTIC 档下 UNKNOWN 放行。"""
    assets = [make_asset("a")]
    evaluator = build(assets, {"a": [make_observation("o1", "a")]})

    hard = evaluator.filter(["a"], [Condition(dimension="person", value="爷爷")])
    semantic = evaluator.filter(
        ["a"], [Condition(dimension="person", value="爷爷", strictness=Strictness.SEMANTIC)]
    )

    assert hard.kept == ()
    assert semantic.kept == ("a",)


def test_place_conditions_work_when_a_resolver_is_wired():
    assets = [make_asset("a"), make_asset("b")]
    evaluator = build(
        assets,
        {"a": [make_observation("o1", "a")], "b": [make_observation("o2", "b")]},
        places=FakePlaces({"a": place_of(city="杭州市"), "b": place_of(city="青岛市")}),
    )

    outcome = evaluator.filter(["a", "b"], [Condition(dimension="place", value="杭州")])

    assert outcome.kept == ("a",)


def test_place_conditions_degrade_to_unknown_without_a_resolver():
    """没接地点仓储时地点条件走 UNKNOWN 分支（不是 CONTRADICTED）——
    **这是有意的降级，不是等价实现**：接上之后它才真的会排除人。"""
    assets = [make_asset("a")]
    evaluator = build(assets, {"a": [make_observation("o1", "a")]})

    outcome = evaluator.filter(["a"], [Condition(dimension="place", value="杭州")])

    assert outcome.kept == ()
    verdict = outcome.verdicts[0]
    assert verdict.excluded is True
    assert verdict.results[0].status.value == "unknown"


def test_verdicts_say_which_condition_excluded():
    """被排除时必须说得出是哪一条——只有一个「排除了」的布尔值，排查时无从下手。"""
    assets = [make_asset("a", media_type=MediaType.VIDEO)]
    evaluator = build(assets, {"a": [make_observation("o1", "a")]})

    outcome = evaluator.filter(["a"], [Condition(dimension="media", value="image")])

    verdict = outcome.verdicts[0]
    assert verdict.excluded is True
    assert verdict.rank == RANK_EXCLUDED
    assert len(verdict.results) == 1
    assert verdict.results[0].status.value == "contradicted"


def test_a_passing_verdict_is_ranked_exact():
    assets = [make_asset("a", media_type=MediaType.IMAGE)]
    evaluator = build(assets, {"a": [make_observation("o1", "a")]})

    outcome = evaluator.filter(["a"], [Condition(dimension="media", value="image")])

    assert outcome.verdicts[0].rank == RANK_EXACT


@pytest.mark.parametrize("asset_ids", [(), ("a", "b", "c")])
def test_the_kept_order_follows_the_input_order(asset_ids):
    """**只排除、不重排。** 1.0 写死了「Later reranking decides ordering」——
    顺手重排会让分数不再可解释，而结果看起来只是「顺序有点怪」。"""
    assets = [make_asset(item) for item in asset_ids]
    observations = {item: [make_observation(f"o_{item}", item)] for item in asset_ids}
    evaluator = build(assets, observations)

    outcome = evaluator.filter(list(asset_ids), [Condition(dimension="media", value="image")])

    assert outcome.kept == asset_ids
