import dataclasses
from datetime import UTC, datetime

from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType


class FakeAssets:
    def __init__(self) -> None:
        self.rows: dict[str, Asset] = {}

    def add(self, asset):
        self.rows[asset.id] = asset

    def get(self, asset_id):
        return self.rows.get(asset_id)

    def list_by_scope(self, scope_id, limit):
        return [a for a in self.rows.values() if a.scope_id == scope_id][:limit]

    def find_by_hash(self, scope_id, content_sha256):
        return next(
            (a for a in self.rows.values() if a.scope_id == scope_id and a.content_sha256 == content_sha256),
            None,
        )

    def update_status(self, asset_id, status):
        # Asset 是 frozen+slots dataclass，没有 __dict__，必须用 dataclasses.replace
        self.rows[asset_id] = dataclasses.replace(self.rows[asset_id], status=status)


class FakeObservations:
    def __init__(self) -> None:
        self.rows = {}

    def add(self, observation):
        self.rows[observation.id] = observation

    def get(self, observation_id):
        return self.rows.get(observation_id)


class FakeStorage:
    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}

    def store(self, asset_id, file_name, content):
        from pathlib import Path

        self.stored[asset_id] = content
        return Path(f"/media/{asset_id}_{file_name}")

    def read(self, asset_id):
        return self.stored[asset_id]

    def path_for(self, asset):
        from pathlib import Path

        return Path(asset.path)


class FakeMetadata:
    def __init__(self, captured_at=None, gps=None, device=None) -> None:
        self._captured_at, self._gps, self._device = captured_at, gps, device

    def read(self, path):
        return self._captured_at, self._gps, self._device


def build(metadata=None):
    assets, observations, storage = FakeAssets(), FakeObservations(), FakeStorage()
    use_case = ImportAssets(
        assets=assets,
        observations=observations,
        storage=storage,
        metadata=metadata or FakeMetadata(),
    )
    return use_case, assets, observations, storage


def test_import_creates_asset_and_observation():
    use_case, assets, observations, storage = build()
    request = ImportRequest(
        scope_id="album1",
        file_name="IMG_0001.HEIC",
        content_type="image/heic",
        content=b"raw bytes",
    )

    result = use_case.execute(request)

    assert result.status is AssetStatus.PROCESSED
    assert result.media_type is MediaType.IMAGE
    asset = assets.get(result.asset_id)
    assert asset is not None
    assert asset.size_bytes == len(b"raw bytes")
    assert storage.stored[result.asset_id] == b"raw bytes"
    assert observations.get(result.observation_id) is not None


def test_identical_content_in_same_scope_is_deduplicated():
    use_case, assets, _, storage = build()
    request = ImportRequest(
        scope_id="album1",
        file_name="a.jpg",
        content_type="image/jpeg",
        content=b"same",
    )

    first = use_case.execute(request)
    second = use_case.execute(request)

    assert second.deduplicated is True
    assert second.asset_id == first.asset_id
    assert len(assets.rows) == 1
    assert len(storage.stored) == 1


def test_same_content_in_different_scopes_is_kept_separately():
    """1.0 的去重按 scope 隔离（fix(api): scope dedup in /api/ingest）。"""
    use_case, assets, _, _ = build()
    use_case.execute(
        ImportRequest(scope_id="album1", file_name="a.jpg", content_type="image/jpeg", content=b"same")
    )
    use_case.execute(
        ImportRequest(scope_id="album2", file_name="a.jpg", content_type="image/jpeg", content=b"same")
    )

    assert len(assets.rows) == 2


def test_metadata_is_carried_onto_the_asset():
    gps = GpsPoint(18.7883, 98.9853)
    captured = datetime(2018, 4, 1, 21, 5, 47, tzinfo=UTC)
    use_case, assets, _, _ = build(FakeMetadata(captured, gps, "iPhone X"))

    result = use_case.execute(
        ImportRequest(scope_id="album1", file_name="a.jpg", content_type="image/jpeg", content=b"x")
    )

    asset = assets.get(result.asset_id)
    assert asset.captured_at == captured
    assert asset.captured_location == gps
