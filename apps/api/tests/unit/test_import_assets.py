import dataclasses
from datetime import UTC, datetime
from pathlib import Path

from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, VideoMetadata


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


# ---- S8a：视频不再静默空转 ----------------------------------------------------------
#
# 这一组只验用例本身的判定：探测到了什么、失败被记到哪里、图片那条路有没有被带跑。
# 真 ffprobe 的抽取口径在 tests/integration/test_video_metadata.py，
# 装配点接线（build_import_assets 有没有把三件接上）在 tests/contract/test_video_routes.py。


class FakeVideoMetadata:
    """返回一条预置元数据；``error`` 非空时按真适配器的契约抛 ``ValueError``。"""

    def __init__(self, metadata=None, error: str = "") -> None:
        self._metadata = metadata
        self._error = error
        self.calls: list[str] = []

    def read(self, path):
        self.calls.append(str(path))
        if self._error:
            raise ValueError(self._error)
        return self._metadata


class FakeVideoTranscoder:
    """``target`` 给 None 表示「不用转」；``error`` 非空表示转码失败。"""

    def __init__(self, target=None, error: str = "") -> None:
        self._target, self._error = target, error
        self.calls: list[str] = []

    def browser_preview(self, asset_id, source, codec):
        self.calls.append(codec)
        if self._error:
            raise RuntimeError(self._error)
        return self._target


class FakeVideoRepository:
    def __init__(self) -> None:
        self.probes: dict[str, object] = {}

    def save_probe(self, asset_id, probe):
        self.probes[asset_id] = probe

    def probe(self, asset_id):
        return self.probes.get(asset_id)


VIDEO_METADATA = VideoMetadata(
    captured_at="2026-08-02T14:55:56+08:00",
    latitude=22.4846,
    longitude=114.5438,
    captured_location="22.484600,114.543800",
    duration_sec=11.735,
    codec="hevc",
    device="Apple iPhone 14 Plus",
)


def build_video(*, metadata_reader, transcoder=None):
    assets, observations, storage = FakeAssets(), FakeObservations(), FakeStorage()
    videos = FakeVideoRepository()
    use_case = ImportAssets(
        assets=assets,
        observations=observations,
        storage=storage,
        metadata=FakeMetadata(),
        video_metadata=metadata_reader,
        video_transcoder=transcoder,
        video_repository=videos,
    )
    return use_case, assets, videos


def test_video_import_reads_metadata_and_records_the_probe():
    """T505：视频要读 ffprobe 元数据、写进 metadata_json，**不能收下了什么都不发生**。"""
    reader = FakeVideoMetadata(VIDEO_METADATA)
    use_case, assets, videos = build_video(
        metadata_reader=reader, transcoder=FakeVideoTranscoder(target=Path("/derived/preview.mp4"))
    )

    result = use_case.execute(
        ImportRequest(scope_id="album1", file_name="clip.mov", content_type=None, content=b"mov")
    )

    assert reader.calls, "视频必须真的走一次探测"
    assert result.video_probed is True
    probe = videos.probes[result.asset_id]
    assert probe.metadata is VIDEO_METADATA
    assert probe.browser_preview_path == "/derived/preview.mp4"
    # 拍摄时间/GPS 接到与 EXIF 同一条路上：否则视频的 captured_at 只会是导入时刻。
    asset = assets.get(result.asset_id)
    assert asset.captured_at == datetime(2026, 8, 2, 6, 55, 56, tzinfo=UTC)
    assert asset.captured_location == GpsPoint(22.4846, 114.5438)


def test_video_probe_failure_is_recorded_not_swallowed():
    """探测失败照收，但原因要落在资产上——「收下了但没读出来」必须看得见。"""
    use_case, _, videos = build_video(metadata_reader=FakeVideoMetadata(error="ffprobe failed: boom"))

    result = use_case.execute(
        ImportRequest(scope_id="album1", file_name="clip.mov", content_type=None, content=b"mov")
    )

    assert result.video_probed is False
    probe = videos.probes[result.asset_id]
    assert probe.metadata is None
    assert "ffprobe failed: boom" in probe.probe_error


def test_transcode_failure_keeps_the_import_and_records_the_reason():
    """转码是增强：失败回落原片，但失败原因要留下来（1.0 的 browser_preview_error）。"""
    use_case, _, videos = build_video(
        metadata_reader=FakeVideoMetadata(VIDEO_METADATA),
        transcoder=FakeVideoTranscoder(error="ffmpeg exploded"),
    )

    result = use_case.execute(
        ImportRequest(scope_id="album1", file_name="clip.mov", content_type=None, content=b"mov")
    )

    assert result.video_probed is True
    probe = videos.probes[result.asset_id]
    assert probe.browser_preview_path == ""
    assert "ffmpeg exploded" in probe.browser_preview_error


def test_already_playable_video_is_not_transcoded():
    """H.264 直出：转码器返回 None 不是失败，preview_path 为空、也没有 error。"""
    transcoder = FakeVideoTranscoder(target=None)
    use_case, _, videos = build_video(
        metadata_reader=FakeVideoMetadata(VIDEO_METADATA), transcoder=transcoder
    )

    result = use_case.execute(
        ImportRequest(scope_id="album1", file_name="clip.mp4", content_type=None, content=b"mp4")
    )

    probe = videos.probes[result.asset_id]
    assert transcoder.calls == ["hevc"]
    assert probe.browser_preview_path == ""
    assert probe.browser_preview_error == ""


def test_image_import_never_touches_the_video_chain():
    """图片走 EXIF，一步都不进视频那条路——否则「图片也被 ffprobe 了一遍」不会报错。"""
    reader = FakeVideoMetadata(VIDEO_METADATA)
    transcoder = FakeVideoTranscoder(target=Path("/derived/preview.mp4"))
    use_case, _, videos = build_video(metadata_reader=reader, transcoder=transcoder)

    result = use_case.execute(
        ImportRequest(scope_id="album1", file_name="a.jpg", content_type="image/jpeg", content=b"jpg")
    )

    assert result.video_probed is False
    assert reader.calls == []
    assert transcoder.calls == []
    assert videos.probes == {}


def test_video_import_without_the_probe_chain_still_works():
    """协作者全缺时退化成旧行为（不报错）——与其余可选协作者同一形状。"""
    use_case, _, _ = build_video(metadata_reader=None)

    result = use_case.execute(
        ImportRequest(scope_id="album1", file_name="clip.mov", content_type=None, content=b"mov")
    )

    assert result.media_type is MediaType.VIDEO
    assert result.video_probed is False
