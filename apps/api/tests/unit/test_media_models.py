from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, VideoMetadata


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


def test_video_captured_datetime_is_naive_so_it_can_be_compared_with_event_times():
    """带偏移的 ffprobe 时间必须落成 **naive**，否则事件归属那条路会抛 TypeError。

    2026-10-07 实测：`album3-max-video10` 的 **10 个视频全部导入失败**，reason 全是
    ``can't subtract offset-naive and offset-aware datetimes``。链条是：ffprobe 报
    ``2017-09-27T03:36:15.000000Z`` → ``normalized_datetime`` 换成 ``+00:00`` →
    ``fromisoformat`` 给出 **aware** → 拿去减 ``events.time_start``（照片那条路来自
    EXIF，是 **naive**）→ 炸。**照片从不出错，只有视频会**，而 ``photo_import`` 只记
    ``partial``、整轮评测照常 ``run_valid=true``。

    1.0 同一个视频存的是 ``2017-09-27 11:36:15``（UTC+8 本地、无时区），这里照做。

    三层断言缺一不可：只看「naive」放得过「把时间点丢了」的实现；只看「时间点对」
    放得过「仍然 aware」的实现——而后者正是这次的 bug。
    """
    value = VideoMetadata(captured_at="2017-09-27T03:36:15.000000+00:00").captured_datetime()

    assert value is not None
    assert value.tzinfo is None, "必须 naive，否则减 events.time_start 会抛 TypeError"
    # 时间点没丢：转回 UTC 应等于原始的 UTC 时刻（本地化不等于移动时刻）。
    assert value.astimezone(UTC) == datetime(2017, 9, 27, 3, 36, 15, tzinfo=UTC)
    # 修复要保证的事：能与事件那条路的 naive 时间相减。
    # 11:36:15 - 10:00:00 = 1h36m15s = 5775 秒
    assert abs((value - datetime(2017, 9, 27, 10, 0, 0)).total_seconds()) == 5775


def test_a_naive_exif_time_is_left_alone():
    """本来就不带时区的（EXIF 那条路）原样返回——归一化不该动它。"""
    value = VideoMetadata(captured_at="2017-09-27T11:36:15").captured_datetime()

    assert value == datetime(2017, 9, 27, 11, 36, 15)
    assert value is not None and value.tzinfo is None
