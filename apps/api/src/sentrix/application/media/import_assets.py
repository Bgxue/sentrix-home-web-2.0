"""导入用例：把一次上传变成 Asset + Observation（+ 视觉向量）。

业务规则（都来自 1.0 的既有行为）：
- 内容 SHA-256 去重，**按 scope 隔离**，不跨相册去重
- 去重命中时不重复落盘
- 拍摄时间与 GPS 来自 EXIF，抽不到就留空，**不阻断导入**

S2 追加一条：**导入即向量化**（1.0 ``pipeline.py`` 的 ``_commit_fast_image`` 同样
时机）。``visual_indexer`` 是可选协作者——不传就退化成纯导入，这样单元测试与不关心
视觉记忆的调用方都不必构造嵌入器。

去重命中时**仍然调一次 indexer**：索引本身是幂等的（已有向量直接返回），所以这条
路径的真实作用是「补上第一次没能写成的向量」。1.0 有过 223 张图没有任何视觉向量
却毫无征兆的记录，重新导入是使用者唯一能想到的补救动作，它必须真的有效。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.ports import (
    AssetRepository,
    MediaStorage,
    MetadataReader,
    ObservationRepository,
)
from sentrix.domain.retrieval.ports import AssetVisualIndexer


@dataclass(frozen=True, slots=True)
class ImportRequest:
    scope_id: str
    file_name: str
    content_type: str | None
    content: bytes


@dataclass(frozen=True, slots=True)
class ImportResult:
    asset_id: str
    observation_id: str | None
    media_type: MediaType
    status: AssetStatus
    deduplicated: bool
    indexed: bool = False


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ImportAssets:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        storage: MediaStorage,
        metadata: MetadataReader,
        visual_indexer: AssetVisualIndexer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._storage = storage
        self._metadata = metadata
        self._visual_indexer = visual_indexer
        self._clock = clock

    def execute(self, request: ImportRequest) -> ImportResult:
        content_sha256 = hashlib.sha256(request.content).hexdigest()
        media_type = MediaType.from_upload(request.content_type, request.file_name)

        existing = self._assets.find_by_hash(request.scope_id, content_sha256)
        if existing is not None:
            return ImportResult(
                asset_id=existing.id,
                observation_id=None,
                media_type=existing.media_type,
                status=existing.status,
                deduplicated=True,
                indexed=self._index_visual(existing),
            )

        asset_id = _new_id("asset")
        stored_path = self._storage.store(asset_id, request.file_name, request.content)

        captured_at, gps, device = (None, None, None)
        if media_type is MediaType.IMAGE:
            captured_at, gps, device = self._metadata.read(stored_path)

        now = self._clock()
        asset = Asset(
            id=asset_id,
            scope_id=request.scope_id,
            file_name=request.file_name,
            media_type=media_type,
            path=str(stored_path),
            mime_type=request.content_type,
            size_bytes=len(request.content),
            status=AssetStatus.PROCESSED,
            content_sha256=content_sha256,
            captured_at=captured_at or now,
            captured_location=gps,
            created_at=now,
            updated_at=now,
        )
        self._assets.add(asset)

        observation = Observation(
            id=_new_id("obs"),
            scope_id=request.scope_id,
            asset_id=asset_id,
            source_type="import",
            captured_at=asset.captured_at,
            confidence=0.0,
            created_at=now,
            updated_at=now,
        )
        self._observations.add(observation)

        return ImportResult(
            asset_id=asset_id,
            observation_id=observation.id,
            media_type=media_type,
            status=asset.status,
            deduplicated=False,
            indexed=self._index_visual(asset),
        )

    def _index_visual(self, asset: Asset) -> bool:
        if self._visual_indexer is None:
            return False
        return self._visual_indexer.index(asset).indexed
