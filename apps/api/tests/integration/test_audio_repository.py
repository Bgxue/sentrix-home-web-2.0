"""音频探测结果的落库（S8c T521）。真 SQLite + 真迁移。

**为什么用真库**：这一层的全部风险都在「写到哪个键、会不会把同一列上别的东西抹掉」，
拿假仓储测等于什么都不测。键名与视频那两个同一套体例（``video_metadata`` /
``video_probe_error`` → ``audio_metadata`` / ``audio_probe_error``）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, AudioMetadata, MediaType
from sentrix.domain.media.ports import AudioProbe
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.audio import SqliteAudioRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)

PROBED = AudioMetadata(
    captured_at="2026-08-02T14:55:56+08:00",
    latitude=22.4846,
    longitude=114.5438,
    captured_location="22.484600,114.543800",
    duration_sec=5.0,
    codec="aac",
    sample_rate=44100,
    channels=2,
    bit_rate=128000,
    creation_source="format.creation_time",
)


def _asset(asset_id: str, media_type: MediaType = MediaType.AUDIO) -> Asset:
    return Asset(
        id=asset_id,
        scope_id="album1",
        file_name="clip.m4a" if media_type is MediaType.AUDIO else "a.jpg",
        media_type=media_type,
        path=f"/media/{asset_id}",
        mime_type="audio/mp4" if media_type is MediaType.AUDIO else "image/jpeg",
        size_bytes=1,
        status=AssetStatus.PROCESSED,
        content_sha256=asset_id,
        captured_at=NOW,
        captured_location=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _payload(db: Database, asset_id: str) -> dict:
    with db.connect() as connection:
        row = connection.execute("SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)).fetchone()
    return json.loads(row["metadata_json"])


def test_save_probe_writes_the_audio_metadata_key(migrations_ready_db: Database):
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_audio"))

    SqliteAudioRepository(migrations_ready_db).save_probe("asset_audio", AudioProbe(metadata=PROBED))

    payload = _payload(migrations_ready_db, "asset_audio")
    assert payload["audio_probe_error"] == ""
    assert payload["audio_metadata"]["codec"] == "aac"
    assert payload["audio_metadata"]["sample_rate"] == 44100
    assert payload["audio_metadata"]["channels"] == 2
    assert payload["audio_metadata"]["bit_rate"] == 128000
    assert payload["audio_metadata"]["duration_sec"] == pytest.approx(5.0)
    # 视频的键一个都不该出现——音频写自己的那一份。
    assert "video_metadata" not in payload


def test_save_probe_merges_other_metadata_keys(migrations_ready_db: Database):
    """``metadata_json`` 上可能压着别的东西（1.0 同一列放过反地理/事实 id），
    合并写不是整包覆盖。"""
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_audio"))
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "UPDATE assets SET metadata_json = ? WHERE id = ?",
            ('{"reverse_geocode": {"city": "邯郸市"}}', "asset_audio"),
        )

    SqliteAudioRepository(migrations_ready_db).save_probe("asset_audio", AudioProbe(metadata=PROBED))

    payload = _payload(migrations_ready_db, "asset_audio")
    assert payload["reverse_geocode"] == {"city": "邯郸市"}
    assert payload["audio_metadata"]["codec"] == "aac"


def test_a_failed_probe_keeps_the_reason_and_no_metadata(migrations_ready_db: Database):
    """探测失败：``audio_probe_error`` 有值、``audio_metadata`` **不写**——否则
    「探测过但没读出来」与「读出来是空的」在库里长得一样。"""
    SqliteAssetRepository(migrations_ready_db).add(_asset("asset_audio"))

    SqliteAudioRepository(migrations_ready_db).save_probe(
        "asset_audio", AudioProbe(probe_error="ValueError: ffprobe found no audio stream")
    )

    payload = _payload(migrations_ready_db, "asset_audio")
    assert payload["audio_probe_error"] == "ValueError: ffprobe found no audio stream"
    assert "audio_metadata" not in payload


def test_save_probe_rejects_an_unknown_asset(migrations_ready_db: Database):
    with pytest.raises(KeyError):
        SqliteAudioRepository(migrations_ready_db).save_probe("asset_missing", AudioProbe())
