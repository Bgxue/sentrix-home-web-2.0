"""真 SQLite + 真迁移下的 S4 仓储：地点 / 事件 / 事实 / 词法投影。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType
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
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository

NOW = datetime(2017, 10, 5, 12, 0, 0, tzinfo=UTC)
XIAOSHAN = GpsPoint(30.1839, 120.2642)


@pytest.fixture
def assets(migrations_ready_db: Database) -> SqliteAssetRepository:
    return SqliteAssetRepository(migrations_ready_db)


@pytest.fixture
def places(migrations_ready_db: Database) -> SqliteAssetPlaceRepository:
    return SqliteAssetPlaceRepository(migrations_ready_db)


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
