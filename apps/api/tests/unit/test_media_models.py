from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType


def test_asset_requires_known_media_type():
    with pytest.raises(ValueError, match="media_type"):
        Asset(
            id="asset_1",
            scope_id="home-default",
            file_name="a.bin",
            media_type="application",
            path="/tmp/a.bin",
            mime_type=None,
            size_bytes=1,
            status=AssetStatus.QUEUED,
            content_sha256="x",
            captured_at=None,
            captured_location=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def test_gps_point_rejects_out_of_range_latitude():
    with pytest.raises(ValueError, match="latitude"):
        GpsPoint(latitude=91.0, longitude=0.0)


def test_media_type_from_mime_prefers_mime_over_filename():
    assert MediaType.from_upload("image/heic", "photo.jpg") is MediaType.IMAGE
    assert MediaType.from_upload("audio/mpeg", None) is MediaType.AUDIO
    assert MediaType.from_upload(None, "clip.MOV") is MediaType.VIDEO


def test_asset_is_immutable():
    asset = Asset(
        id="asset_1",
        scope_id="home-default",
        file_name="a.jpg",
        media_type=MediaType.IMAGE,
        path="/tmp/a.jpg",
        mime_type="image/jpeg",
        size_bytes=1,
        status=AssetStatus.QUEUED,
        content_sha256="x",
        captured_at=None,
        captured_location=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(FrozenInstanceError):
        asset.status = AssetStatus.PROCESSED  # type: ignore[misc]
