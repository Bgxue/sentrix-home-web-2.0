"""整册导入：串行、可恢复、按后缀扫描。

GPU 是单卡，所以「串行」不是实现细节而是产品约束；「可恢复」是几小时跑批的基本要求：
一张坏图不该让整批归零。两条都在这里钉住。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sentrix.application.media.import_album import (
    AlbumImportRequest,
    ImportAlbum,
)
from sentrix.application.media.import_assets import ImportResult
from sentrix.domain.media.models import AssetStatus, MediaType


class StubImporter:
    """按文件名返回预置结果，并记录调用顺序（证明是串行的）。"""

    def __init__(self, *, failing: frozenset[str] = frozenset(), dedup: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._failing = failing
        self._dedup = dedup

    def execute(self, request):  # noqa: ANN001
        self.calls.append(request.file_name)
        if request.file_name in self._failing:
            raise RuntimeError(f"坏图: {request.file_name}")
        deduplicated = request.file_name in self._dedup
        return ImportResult(
            asset_id=f"asset_{len(self.calls)}",
            observation_id=None if deduplicated else "obs_1",
            media_type=MediaType.IMAGE,
            status=AssetStatus.PROCESSED,
            deduplicated=deduplicated,
            indexed=True,
            placed=True,
            event_id=None if deduplicated else "evt_1",
            lexical_terms=0 if deduplicated else 4,
        )


@pytest.fixture
def album(tmp_path: Path) -> Path:
    root = tmp_path / "album"
    root.mkdir()
    for name in ("b.jpg", "a.JPG", "c.png", "notes.txt"):
        (root / name).write_bytes(b"content-" + name.encode())
    return root


def test_scan_is_sorted_and_ignores_non_images(album: Path):
    importer = StubImporter()
    result = ImportAlbum(importer=importer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album))
    )

    assert importer.calls == ["a.JPG", "b.jpg", "c.png"]
    assert result.imported == 3
    assert result.events == 3
    assert result.lexical_terms == 12


def test_one_bad_photo_does_not_stop_the_batch(album: Path):
    importer = StubImporter(failing=frozenset({"b.jpg"}))
    result = ImportAlbum(importer=importer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album))
    )

    assert importer.calls == ["a.JPG", "b.jpg", "c.png"]  # 坏图之后继续
    assert result.imported == 2
    assert [item.file_name for item in result.failures] == ["b.jpg"]
    assert "坏图" in result.failures[0].reason


def test_failures_carry_the_exception_type(album: Path):
    importer = StubImporter(failing=frozenset({"c.png"}))

    result = ImportAlbum(importer=importer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album))
    )

    assert result.failures[0].reason.startswith("RuntimeError:")


def test_rerunning_the_same_album_deduplicates(album: Path):
    importer = StubImporter(dedup=frozenset({"a.JPG", "b.jpg", "c.png"}))

    result = ImportAlbum(importer=importer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album))
    )

    assert result.imported == 0
    assert result.deduplicated == 3
    assert result.events == 0
    assert result.scanned == 3


def test_limit_caps_the_batch(album: Path):
    importer = StubImporter()

    result = ImportAlbum(importer=importer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album), limit=1)
    )

    assert importer.calls == ["a.JPG"]
    assert result.imported == 1


def test_missing_directory_is_an_error_not_an_empty_result(tmp_path: Path):
    """目录不存在必须报错：空结果会让「路径写错了」看起来像「相册是空的」。"""
    with pytest.raises(NotADirectoryError):
        ImportAlbum(importer=StubImporter()).execute(
            AlbumImportRequest(scope_id="album1", directory=str(tmp_path / "gone"))
        )


# ---- 批末事件总结（1.0 ``process_ingest_batch`` 的收尾时机） ------------------------


class RecordingSummarizer:
    """记录被总结的事件 id 与调用顺序。"""

    def __init__(self, *, failing: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._failing = failing

    def __call__(self, event_id: str) -> str:
        self.calls.append(event_id)
        if event_id in self._failing:
            raise RuntimeError(f"总结挂了: {event_id}")
        return event_id


class PerFileEventImporter(StubImporter):
    """每个文件开一个自己的事件，用来验「一个事件只总结一次」。"""

    def execute(self, request):  # noqa: ANN001
        return dataclasses.replace(super().execute(request), event_id=f"evt_{request.file_name}")


def test_events_are_summarized_once_each_after_the_whole_batch(album: Path):
    """1.0 的时机：所有照片都进完之后，按事件 id 逐个总结（``app.py:647-653``）。"""
    summarizer = RecordingSummarizer()
    importer = StubImporter()
    album_import = ImportAlbum(importer=importer, summarize_event=summarizer)

    result = album_import.execute(AlbumImportRequest(scope_id="album1", directory=str(album)))

    # 三张图折进同一个事件 —— 总结只跑一次，且跑在导入之后。
    assert summarizer.calls == ["evt_1"]
    assert result.summarized == 1
    assert importer.calls == ["a.JPG", "b.jpg", "c.png"]


def test_summary_covers_every_distinct_event_and_nothing_else(album: Path):
    summarizer = RecordingSummarizer()

    result = ImportAlbum(importer=PerFileEventImporter(), summarize_event=summarizer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album))
    )

    assert summarizer.calls == ["evt_a.JPG", "evt_b.jpg", "evt_c.png"]
    assert result.summarized == 3


def test_one_failing_summary_does_not_stop_the_rest(album: Path):
    """与逐张那条同样的性质：一个事件的收尾失败不该让别的照片没有标题。"""
    summarizer = RecordingSummarizer(failing=frozenset({"evt_b.jpg"}))

    result = ImportAlbum(importer=PerFileEventImporter(), summarize_event=summarizer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album))
    )

    assert summarizer.calls == ["evt_a.JPG", "evt_b.jpg", "evt_c.png"]  # 失败之后继续
    assert result.summarized == 2
    assert result.imported == 3


def test_without_a_summarizer_events_keep_their_placeholder(album: Path):
    """不传总结器就退化成纯导入——事件留在 ``待总结事件``，与 1.0 没跑收尾时一样。"""
    result = ImportAlbum(importer=StubImporter()).execute(
        AlbumImportRequest(scope_id="album1", directory=str(album))
    )

    assert result.summarized == 0
    assert result.events == 3


# ---- S8a T506：整册导入放行视频 ------------------------------------------------------


def test_videos_are_scanned_alongside_photos(tmp_path: Path):
    """目录里的视频原先被静默跳过：`IMAGE_SUFFIXES` 只看图片，而结果里看不出少过东西。

    这一条钉的是 `SCAN_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES`：把视频那半边删掉，
    `b.mov` / `c.MP4` / `d.mkv` 立刻从 `calls` 里消失（大小写不敏感也一起钉住）。
    """
    root = tmp_path / "album"
    root.mkdir()
    for name in ("a.jpg", "b.mov", "c.MP4", "d.mkv", "notes.txt"):
        (root / name).write_bytes(b"content-" + name.encode())

    importer = StubImporter()
    result = ImportAlbum(importer=importer).execute(
        AlbumImportRequest(scope_id="album1", directory=str(root))
    )

    assert importer.calls == ["a.jpg", "b.mov", "c.MP4", "d.mkv"]
    assert result.imported == 4
