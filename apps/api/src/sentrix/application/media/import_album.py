"""整册导入：把一个目录里的照片串行走一遍完整导入链。

对应 1.0 ``pipeline.process_batch`` 与批量导入端点的时序，三条性质来自 1.0 的实测：

- **串行**：单卡 3090 上视觉嵌入与 VLM 观察吃同一块 GPU（规划时实测约 5.7GB / 24GB）。
  并发打 8100/8101 只会把队列排到超时，不会更快，而且会把显存推到别的进程失败。
- **可恢复**：一张失败不中断整批，失败的文件名与原因随结果一起返回。重跑同一批时
  已导入的照片走 SHA-256 去重（只重跑视觉向量与地点，不重复建事件，见 ``ImportAssets``）。
- **每张最多 3 次模型调用**：那个上限属于观察器（首轮 / 补全 / 中文化，见
  ``infrastructure/media/vlm_observer.py``）。本用例自己不额外调模型。

2.0 **不管理模型进程的生命周期**：8100/8101 由部署方起，这里只是它们的客户端。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sentrix.application.media.import_assets import ImportAssets, ImportRequest

# 与 ``MediaType.from_upload`` 认得的图片后缀一致。目录扫描按后缀，不看内容类型——
# 整册导入拿到的是磁盘上的文件，没有 upload 的 content-type 可依。
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"})

DEFAULT_ALBUM_LIMIT = 200


@dataclass(frozen=True, slots=True)
class AlbumImportRequest:
    scope_id: str
    directory: str
    limit: int = DEFAULT_ALBUM_LIMIT


@dataclass(frozen=True, slots=True)
class AlbumFailure:
    file_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class AlbumImportResult:
    scope_id: str
    directory: str
    imported: int = 0
    deduplicated: int = 0
    events: int = 0
    lexical_terms: int = 0
    failures: tuple[AlbumFailure, ...] = field(default_factory=tuple)

    @property
    def scanned(self) -> int:
        return self.imported + self.deduplicated + len(self.failures)


class ImportAlbum:
    def __init__(self, *, importer: ImportAssets) -> None:
        self._importer = importer

    def execute(self, request: AlbumImportRequest) -> AlbumImportResult:
        root = Path(request.directory)
        if not root.is_dir():
            raise NotADirectoryError(str(root))

        paths = sorted(
            path for path in root.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )[: max(1, int(request.limit))]

        imported = deduplicated = events = lexical_terms = 0
        failures: list[AlbumFailure] = []
        for path in paths:
            try:
                result = self._importer.execute(
                    ImportRequest(
                        scope_id=request.scope_id,
                        file_name=path.name,
                        content_type=None,
                        content=path.read_bytes(),
                    )
                )
            except Exception as error:  # 一张失败不中断整批——整册导入是几小时的事
                failures.append(AlbumFailure(file_name=path.name, reason=f"{type(error).__name__}: {error}"))
                continue
            if result.deduplicated:
                deduplicated += 1
            else:
                imported += 1
            if result.event_id is not None:
                events += 1
            lexical_terms += result.lexical_terms

        return AlbumImportResult(
            scope_id=request.scope_id,
            directory=str(root),
            imported=imported,
            deduplicated=deduplicated,
            events=events,
            lexical_terms=lexical_terms,
            failures=tuple(failures),
        )
