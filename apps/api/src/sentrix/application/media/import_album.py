"""整册导入：把一个目录里的照片串行走一遍完整导入链。

对应 1.0 ``pipeline.process_batch`` 与批量导入端点的时序，三条性质来自 1.0 的实测：

- **串行**：单卡 3090 上视觉嵌入与 VLM 观察吃同一块 GPU（规划时实测约 5.7GB / 24GB）。
  并发打 8100/8101 只会把队列排到超时，不会更快，而且会把显存推到别的进程失败。
- **可恢复**：一张失败不中断整批，失败的文件名与原因随结果一起返回。重跑同一批时
  已导入的照片走 SHA-256 去重（只重跑视觉向量与地点，不重复建事件，见 ``ImportAssets``）。
- **每张最多 3 次模型调用**：那个上限属于观察器（首轮 / 补全 / 中文化，见
  ``infrastructure/media/vlm_observer.py``）。批末的事件总结另算：**每个事件一次**
  （第 48 条），整册 53 张折成 17 个事件就是 17 次——不是每张一次。

2.0 **不管理模型进程的生命周期**：8100/8101 由部署方起，这里只是它们的客户端。

**事件总结在批末，不是在每张之后**——这是 1.0 的时机，不是这里挑的。1.0 的共享
批量路径（``app.py:616-660`` 的 ``process_ingest_batch``）走的是
``commit_semantic_image(summarize_event=False)``，逐张**不总结**；等所有照片都
``complete`` 之后，``claim_ingest_batch_summary(batch_id)`` → ``batch_event_ids(batch_id)``
→ 逐个 ``_summarize_event_worker(event_id)``（``app.py:647-653``，同样的收尾也在
``_process_ingest_asset_group(finalize_batch=True)``，``app.py:577-581``）。
1.0 只总结**这一批碰过的事件**，不是整个 scope 的全部事件——所以这里收集的是本次
循环里出现过的 ``event_id``，去重后逐个总结。

逐张那条路（``pipeline.process()``，``pipeline.py:355-356``）的 ``summarize_event=True``
是另一个入口的默认值，整册导入不走它。**一张照片折叠进已有事件时，前面那张照片
触发的总结不会被重跑**——1.0 亦然（它按事件 id 总结，一个事件只跑一次）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sentrix.application.media.import_assets import ImportAssets, ImportRequest

log = logging.getLogger("sentrix.media.import_album")

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
    summarized: int = 0
    failures: tuple[AlbumFailure, ...] = field(default_factory=tuple)

    @property
    def scanned(self) -> int:
        return self.imported + self.deduplicated + len(self.failures)


class ImportAlbum:
    def __init__(
        self,
        *,
        importer: ImportAssets,
        summarize_event: Callable[[str], object] | None = None,
    ) -> None:
        self._importer = importer
        # 不传就退化成纯导入（与 ``ImportAssets`` 的其余可选协作者同一个形状）：
        # 事件留在 ``待总结事件`` / ``待判断`` 两个占位值上，与 1.0 没跑收尾时一样。
        self._summarize_event = summarize_event

    def execute(self, request: AlbumImportRequest) -> AlbumImportResult:
        root = Path(request.directory)
        if not root.is_dir():
            raise NotADirectoryError(str(root))

        paths = sorted(
            path for path in root.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )[: max(1, int(request.limit))]

        imported = deduplicated = events = lexical_terms = 0
        event_ids: list[str] = []
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
                if result.event_id not in event_ids:  # 保序去重：一个事件只总结一次
                    event_ids.append(result.event_id)
            lexical_terms += result.lexical_terms

        return AlbumImportResult(
            scope_id=request.scope_id,
            directory=str(root),
            imported=imported,
            deduplicated=deduplicated,
            events=events,
            lexical_terms=lexical_terms,
            summarized=self._summarize_events(event_ids),
            failures=tuple(failures),
        )

    def _summarize_events(self, event_ids: list[str]) -> int:
        """批末逐个总结。**一个事件的失败不中断整批**——与逐张那条同样的性质。

        总结失败不回滚任何东西：事件仍在，只是 title/event_type 还是占位值，
        重跑同一个 scope（``SummarizeEvent.execute`` 幂等，1.0 亦然）就能补上。
        """
        if self._summarize_event is None:
            return 0
        summarized = 0
        for event_id in event_ids:
            try:
                self._summarize_event(event_id)
            except Exception as error:  # noqa: BLE001 - 一个事件总结失败不该让整个相册归零
                log.warning("事件总结失败，事件保留占位内容: %s: %s", event_id, error)
                continue
            summarized += 1
        return summarized
