"""事件归属用例：真 SQLite + 真打分，含「视觉向量必须先写」这条硬约束。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sentrix.application.memories.assign_events import AssignEvents, EventVisualSimilarity
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.memories.events import WEIGHTS
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import (
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

BASE = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
XIAOSHAN = GpsPoint(30.1839, 120.2642)
MODEL = "chinese-clip-ViT-L-14"


@pytest.fixture
def repository_set(migrations_ready_db: Database):
    assets = SqliteAssetRepository(migrations_ready_db)
    observations = SqliteObservationRepository(migrations_ready_db)
    vectors = SqliteVectorRepository(migrations_ready_db)
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    events = SqliteEventRepository(migrations_ready_db)
    assign = AssignEvents(
        events=events,
        observations=observations,
        assets=assets,
        places=places,
        visual_similarity=EventVisualSimilarity(vectors=vectors, observations=observations, assets=assets),
        clock=lambda: BASE,
    )
    return {
        "assets": assets,
        "observations": observations,
        "vectors": vectors,
        "places": places,
        "events": events,
        "assign": assign,
    }


def seed(
    repos,
    *,
    asset_id: str,
    observation_id: str,
    captured_at: datetime = BASE,
    gps: GpsPoint | None = XIAOSHAN,
    caption: str = "一家人在公园里拍照",
    place: str = "公园",
    activity: str = "拍照",
    event_type: str = "户外活动",
    objects: tuple[str, ...] = ("相机",),
    district: str = "萧山区",
) -> Observation:
    repos["assets"].add(
        Asset(
            id=asset_id,
            scope_id="album1",
            file_name=f"{asset_id}.jpg",
            media_type=MediaType.IMAGE,
            path=f"/tmp/{asset_id}.jpg",
            mime_type="image/jpeg",
            size_bytes=1,
            status=AssetStatus.PROCESSED,
            content_sha256=f"sha-{asset_id}",
            captured_at=captured_at,
            captured_location=gps,
            created_at=captured_at,
            updated_at=captured_at,
        )
    )
    observation = Observation(
        id=observation_id,
        scope_id="album1",
        asset_id=asset_id,
        source_type="import",
        captured_at=captured_at,
        confidence=0.65,
        created_at=captured_at,
        updated_at=captured_at,
        caption=caption,
        place=place,
        activity=activity,
        event_type=event_type,
        objects=objects,
    )
    repos["observations"].add(observation)
    repos["places"].save(
        asset_id=asset_id,
        scope_id="album1",
        gps=gps,
        place=Place(
            admin=AdminArea("浙江省", "杭州市", district),
            nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
            precision=PlacePrecision.DISTRICT,
            source=PlaceSource.PYGEOCN,
            gps=gps,
            confidence=0.9,
        ),
    )
    return observation


def write_visual(repos, asset_id: str, vector: tuple[float, ...]) -> None:
    repos["vectors"].upsert(
        VectorRecord(
            scope_id="album1",
            space="visual",
            source_type="asset",
            source_id=asset_id,
            model_name=MODEL,
            vector=vector,
        )
    )


def test_first_observation_creates_an_event(repository_set):
    observation = seed(repository_set, asset_id="asset_1", observation_id="obs_1")

    result = repository_set["assign"].execute(observation)

    assert result.created is True
    assert result.score is None
    assert result.event.time_start == BASE
    assert result.event.admin_district == "萧山区"
    assert result.event.place == "浙江省杭州市萧山区 · 赵州镇大石桥村附近"
    assert repository_set["events"].observation_ids(result.event.id) == ["obs_1"]


def test_same_time_and_place_folds_into_the_existing_event(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    second = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(minutes=5),
    )
    created = repository_set["assign"].execute(first)
    write_visual(repository_set, "asset_1", (1.0, 0.0, 0.0))

    result = repository_set["assign"].execute(second)

    assert result.created is False
    assert result.event.id == created.event.id
    assert set(repository_set["events"].observation_ids(created.event.id)) == {"obs_1", "obs_2"}


def test_far_apart_in_time_creates_a_second_event(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    late = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(hours=9),
        gps=GpsPoint(31.2304, 121.4737),
    )
    repository_set["assign"].execute(first)

    result = repository_set["assign"].execute(late)

    assert result.created is True
    assert repository_set["events"].list_active("album1", limit=10).__len__() == 2


def _fold_pair(repos, *, write_vectors: bool):
    """两张同时间同地点的照片，第二张归并时返回它的打分。"""
    first = seed(repos, asset_id="asset_1", observation_id="obs_1")
    second = seed(repos, asset_id="asset_2", observation_id="obs_2", captured_at=BASE + timedelta(minutes=5))
    repos["assign"].execute(first)
    if write_vectors:
        write_visual(repos, "asset_1", (1.0, 0.0, 0.0))
        write_visual(repos, "asset_2", (1.0, 0.0, 0.0))
    return repos["assign"].execute(second)


def test_without_a_visual_vector_the_boost_is_zero_and_flagged_unavailable(repository_set):
    """向量没写 → ``available`` 为假。**不报错、不拆事件**，只是少 0.05 分。

    这正是 Task 56 那条顺序约束难被发现的原因：写反了整条链路照样绿。
    """
    result = _fold_pair(repository_set, write_vectors=False)

    assert result.created is False
    assert result.score is not None
    assert result.score.visual_available is False
    assert result.score.visual_boost == pytest.approx(0.0)


def test_visual_vector_written_first_gives_the_full_boost(repository_set):
    """向量先写 → ``visual_boost`` 满值，总分高于没写向量的那次。"""
    result = _fold_pair(repository_set, write_vectors=True)

    assert result.score is not None
    assert result.score.visual_available is True
    assert result.score.visual_boost == pytest.approx(1.0)
    # 5% 权重那一项真的进了总分，而不是只写在 breakdown 里：按展开式重算一遍总分。
    score = result.score
    expected = (
        WEIGHTS["time"] * score.time
        + WEIGHTS["location"] * score.location
        + WEIGHTS["visual_place"] * score.visual_place
        + WEIGHTS["event_type"] * score.event_type
        + WEIGHTS["activity"] * score.activity
        + WEIGHTS["objects"] * score.objects
        + WEIGHTS["person"] * score.person
        + WEIGHTS["visual_boost"] * score.visual_boost
        + score.spatio_temporal_bonus
    )
    assert score.total == pytest.approx(min(1.0, expected))


def test_semantic_conflict_without_bridge_creates_a_separate_event(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    conflicting = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(minutes=10),
        caption="一家人在室内聚餐",
        place="餐厅",
        activity="室内聚餐",
        event_type="室内聚餐",
        objects=(),
    )
    repository_set["assign"].execute(first)

    result = repository_set["assign"].execute(conflicting)

    assert result.created is True


def test_shared_object_bridges_a_semantic_conflict(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    bridging = seed(
        repository_set,
        asset_id="asset_2",
        observation_id="obs_2",
        captured_at=BASE + timedelta(minutes=10),
        caption="同一台相机拍的室内聚餐",
        place="餐厅",
        activity="室内聚餐",
        event_type="室内聚餐",
        objects=("相机",),
    )
    created = repository_set["assign"].execute(first)

    result = repository_set["assign"].execute(bridging)

    assert result.created is False
    assert result.event.id == created.event.id


def test_candidate_count_reports_ambiguity(repository_set):
    first = seed(repository_set, asset_id="asset_1", observation_id="obs_1")
    second = seed(
        repository_set, asset_id="asset_2", observation_id="obs_2", captured_at=BASE + timedelta(minutes=2)
    )
    late = seed(
        repository_set, asset_id="asset_3", observation_id="obs_3", captured_at=BASE + timedelta(minutes=4)
    )
    repository_set["assign"].execute(first)
    repository_set["assign"].execute(second)

    result = repository_set["assign"].execute(late)

    assert result.candidate_count >= 1


def test_observation_without_asset_raises(repository_set):
    from datetime import UTC as _UTC

    orphan = Observation(
        id="obs_x",
        scope_id="album1",
        asset_id="missing",
        source_type="import",
        captured_at=BASE,
        confidence=0.0,
        created_at=BASE,
        updated_at=BASE,
    )
    with pytest.raises(KeyError):
        repository_set["assign"].execute(orphan)
    assert _UTC is not None
