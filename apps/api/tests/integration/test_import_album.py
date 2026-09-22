"""整册导入：串行、可恢复、按后缀扫描。

GPU 是单卡，所以「串行」不是实现细节而是产品约束；「可恢复」是几小时跑批的基本要求：
一张坏图不该让整批归零。两条都在这里钉住。
"""

from __future__ import annotations

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
