"""真 SQLite + 真迁移下的 S4 仓储：地点 / 事件 / 事实 / 词法投影。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import (
    SqliteAssetPlaceRepository,
)
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
XIAOSHAN = GpsPoint(30.1839, 120.2642)


@pytest.fixture
def assets(migrations_ready_db: Database) -> SqliteAssetRepository:
    return SqliteAssetRepository(migrations_ready_db)


@pytest.fixture
def observations(migrations_ready_db: Database) -> SqliteObservationRepository:
    return SqliteObservationRepository(migrations_ready_db)


@pytest.fixture
def places(migrations_ready_db: Database) -> SqliteAssetPlaceRepository:
    return SqliteAssetPlaceRepository(migrations_ready_db)


@pytest.fixture
def events(migrations_ready_db: Database) -> SqliteEventRepository:
    return SqliteEventRepository(migrations_ready_db)


def add_asset(repo: SqliteAssetRepository, asset_id: str = "asset_1", **overrides) -> Asset:
    fields: dict[str, object] = {
        "id": asset_id,
        "scope_id": "album1",
        "file_name": f"{asset_id}.jpg",
        "media_type": MediaType.IMAGE,
        "path": f"/tmp/{asset_id}.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 10,
        "status": AssetStatus.PROCESSED,
        "content_sha256": f"sha-{asset_id}",
        "captured_at": NOW,
        "captured_location": XIAOSHAN,
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    asset = Asset(**fields)  # type: ignore[arg-type]
    repo.add(asset)
    return asset


def add_observation(
    repo: SqliteObservationRepository, observation_id: str = "obs_1", **overrides
) -> Observation:
    fields: dict[str, object] = {
        "id": observation_id,
        "scope_id": "album1",
        "asset_id": "asset_1",
        "source_type": "import",
        "captured_at": NOW,
        "confidence": 0.65,
        "created_at": NOW,
        "updated_at": NOW,
        "caption": "一家人在公园里拍照",
        "place": "公园",
        "activity": "拍照",
        "event_type": "户外活动",
        "ocr_text": "",
        "objects": ("相机", "长椅"),
        "clothing": ("外套",),
        "revision": 1,
    }
    fields.update(overrides)
    observation = Observation(**fields)  # type: ignore[arg-type]
    repo.add(observation)
    return observation


# ---- 地点 ------------------------------------------------------------------------


def test_place_round_trips_all_two_slots(places: SqliteAssetPlaceRepository, assets):
    add_asset(assets)
    value = Place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
        gps=XIAOSHAN,
        confidence=0.9,
    )
    places.save(asset_id="asset_1", scope_id="album1", gps=XIAOSHAN, place=value)

    stored = places.get("asset_1")
    assert stored is not None
    assert stored.admin == AdminArea("浙江省", "杭州市", "萧山区")
    assert stored.nearest == NearestPlace("赵州镇", "大石桥村", 270.0)
    assert stored.precision is PlacePrecision.DISTRICT
    assert stored.source is PlaceSource.PYGEOCN
    assert stored.gps == XIAOSHAN
    assert stored.display() == "浙江省杭州市萧山区 · 赵州镇大石桥村附近"


def test_place_save_is_idempotent_and_corrective(places: SqliteAssetPlaceRepository, assets):
    """重复解析同一个 asset 只覆盖那一行——地点是属性，不是事件流。"""
    add_asset(assets)
    coarse = Place(
        admin=AdminArea("浙江省", "杭州市", ""),
        nearest=NearestPlace(),
        precision=PlacePrecision.CITY,
        source=PlaceSource.PYGEOCN,
        confidence=0.9,
    )
    fine = Place(
        admin=AdminArea("浙江省", "杭州市", "萧山区"),
        nearest=NearestPlace("赵州镇", "大石桥村", 270.0),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
        confidence=0.9,
    )
    places.save(asset_id="asset_1", scope_id="album1", gps=XIAOSHAN, place=coarse)
    places.save(asset_id="asset_1", scope_id="album1", gps=XIAOSHAN, place=fine)

    assert places.count() == 1
    stored = places.get("asset_1")
    assert stored is not None and stored.admin.district == "萧山区"


def test_place_absent_is_distinguishable_from_empty_resolution(places: SqliteAssetPlaceRepository, assets):
    """「没解析过」没有行；「解析过」有行。1.0 把两者都是空字符串。"""
    add_asset(assets)
    assert places.get("asset_1") is None
    assert places.count() == 0


def test_place_get_many_returns_only_known(places: SqliteAssetPlaceRepository, assets):
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2")
    value = Place(
        admin=AdminArea("浙江省"),
        nearest=NearestPlace(),
        precision=PlacePrecision.CITY,
        source=PlaceSource.PYGEOCN,
        confidence=0.9,
    )
    places.save(asset_id="asset_1", scope_id="album1", gps=None, place=value)

    found = places.get_many(["asset_1", "asset_2"])
    assert set(found) == {"asset_1"}


# ---- 事件 ------------------------------------------------------------------------


def test_event_create_and_fold(events: SqliteEventRepository):
    created = events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="浙江省杭州市萧山区",
        admin_district="萧山区",
        activity="拍照",
        summary="一家人在公园里拍照",
        confidence=0.65,
        aggregation_score=0.0,
        aggregation_breakdown={"selected": False, "ambiguity": False},
    )
    assert created.title == "待总结事件"
    assert created.revision == 1

    folded = events.fold_observation(
        "evt_1",
        time_start=NOW.replace(hour=14),
        time_end=NOW.replace(hour=14),
        place="",
        admin_district="",
        activity="",
        summary="孩子在放风筝",
        confidence=0.9,
        aggregation_score=0.72,
        aggregation_breakdown={"total": 0.72},
    )
    assert folded.time_end == NOW.replace(hour=14)
    assert folded.revision == 2
    assert folded.confidence == pytest.approx(0.9)
    assert "孩子在放风筝" in folded.summary
    assert folded.aggregation_score == pytest.approx(0.72)


def test_event_fold_never_lowers_confidence(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.9,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    folded = events.fold_observation(
        "evt_1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.1,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    assert folded.confidence == pytest.approx(0.9)


def test_event_summary_is_not_appended_twice(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="拍照",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    once = events.fold_observation(
        "evt_1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="拍照",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    assert once.summary == "拍照"


def test_event_merge_moves_observations_and_deletes_source(
    events: SqliteEventRepository, observations: SqliteObservationRepository, assets
):
    add_asset(assets, "asset_1")
    add_asset(assets, "asset_2")
    add_observation(observations, "obs_1", asset_id="asset_1")
    add_observation(observations, "obs_2", asset_id="asset_2")
    for event_id in ("evt_1", "evt_2"):
        events.create(
            event_id=event_id,
            scope_id="album1",
            time_start=NOW,
            time_end=NOW,
            place="",
            admin_district="",
            activity="",
            summary="",
            confidence=0.5,
            aggregation_score=0.0,
            aggregation_breakdown={},
        )
    events.attach("evt_1", "obs_1")
    events.attach("evt_2", "obs_2")

    events.merge("evt_2", "evt_1")

    assert events.get("evt_2") is None
    assert list(events.observation_ids("evt_1")) == ["obs_1", "obs_2"]


def test_event_merge_skips_already_shared_observations(
    events: SqliteEventRepository, observations: SqliteObservationRepository, assets
):
    add_asset(assets)
    add_observation(observations)
    for event_id in ("evt_1", "evt_2"):
        events.create(
            event_id=event_id,
            scope_id="album1",
            time_start=NOW,
            time_end=NOW,
            place="",
            admin_district="",
            activity="",
            summary="",
            confidence=0.5,
            aggregation_score=0.0,
            aggregation_breakdown={},
        )
    events.attach("evt_1", "obs_1")
    events.attach("evt_2", "obs_1")

    events.merge("evt_2", "evt_1")

    assert list(events.observation_ids("evt_1")) == ["obs_1"]


def test_event_merge_to_self_is_a_noop(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    events.merge("evt_1", "evt_1")
    assert events.get("evt_1") is not None


def test_event_list_is_scoped_and_active_only(events: SqliteEventRepository):
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    events.create(
        event_id="evt_2",
        scope_id="album2",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    assert [item.id for item in events.list_active("album1", limit=10)] == ["evt_1"]
    assert [item.id for item in events.list_by_scope("album1", limit=10)] == ["evt_1"]


def test_event_dissolve_removes_links_then_row(
    events: SqliteEventRepository, observations: SqliteObservationRepository, assets
):
    add_asset(assets)
    add_observation(observations)
    events.create(
        event_id="evt_1",
        scope_id="album1",
        time_start=NOW,
        time_end=NOW,
        place="",
        admin_district="",
        activity="",
        summary="",
        confidence=0.5,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )
    events.attach("evt_1", "obs_1")
    events.dissolve("evt_1")
    assert events.get("evt_1") is None
    assert list(events.observation_ids("evt_1")) == []


def test_event_fold_on_missing_event_raises(events: SqliteEventRepository):
    with pytest.raises(KeyError):
        events.fold_observation(
            "missing",
            time_start=NOW,
            time_end=NOW,
            place="",
            admin_district="",
            activity="",
            summary="",
            confidence=0.5,
            aggregation_score=0.0,
            aggregation_breakdown={},
        )
