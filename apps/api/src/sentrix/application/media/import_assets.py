"""导入用例：把一次上传变成 Asset + 地点 + 视觉向量 + Observation + 事件 + 词法投影。

业务规则（都来自 1.0 的既有行为）：
- 内容 SHA-256 去重，**按 scope 隔离**，不跨相册去重
- 去重命中时不重复落盘
- 拍摄时间与 GPS 来自 EXIF，抽不到就留空，**不阻断导入**

S2 追加一条：**导入即向量化**（1.0 ``pipeline.py`` 的 ``_commit_fast_image`` 同样
时机）。``visual_indexer`` 是可选协作者——不传就退化成纯导入，这样单元测试与不关心
视觉记忆的调用方都不必构造嵌入器。

S4 追加一条**顺序是硬约束**（``s4-deviations.md`` §2.3）：

    存储 → EXIF/GPS → 地点解析 → 视觉向量 → 观察 → 语义富集 → 事件归属 → 词法投影

- **视觉向量先于事件归属**：``score_event_candidate`` 的 ``visual_boost``（权重 0.05）
  与 ``split_guard`` 都读 ``memory_vectors`` 里 ``space='visual' & source_type='asset'``
  的向量。顺序写反**不报错**——``_event_visual_similarity`` 返回 ``(0.0, False)``，
  ``visual_boost`` 恒 0、``split_guard`` 因 ``not visual_available`` 更易命中，事件
  照分照建，只是分错了。1.0 在 ``pipeline.py:315-325`` 用注释钉过这件事，
  ``tests/integration/test_import_pipeline.py`` 用调用序断言钉住它。
- **地点解析先于事件归属**：事件的 ``place`` / ``admin_district`` 取的是 ``asset_places``
  那一行（``AssignEvents._create`` / ``_fold``），解析没做完时事件里就是空的。
- **词法投影挂在观察写入之后逐条写**：1.0 的 ``refresh_from_observation`` 存在但生产
  零调用，2.0 不做懒补建。

协作者全是可选的：不传就退化成纯导入（本文件的既有调用方与单元测试不必构造整条链）。

**去重命中时只重跑幂等的那几步**（视觉向量 + 地点）。事件归属不重跑：它不是幂等的，
对同一条 Observation 再跑一次可能给它开出第二个事件。1.0 有过 223 张图没有任何视觉
向量却毫无征兆的记录，重新导入是使用者唯一能想到的补救动作，它必须真的有效——
但有效的那部分只包括「重算一遍属性」。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.ports import (
    AssetObserver,
    AssetRepository,
    MediaStorage,
    MetadataReader,
    ObservationRepository,
)
from sentrix.domain.places.ports import AssetPlaceIndex
from sentrix.domain.retrieval.ports import AssetVisualIndexer, LexicalIndex


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
    placed: bool = False
    event_id: str | None = None
    lexical_terms: int = 0


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
        place_indexer: AssetPlaceIndex | None = None,
        observer: AssetObserver | None = None,
        event_assigner: Callable[[Observation], str] | None = None,
        lexical_index: LexicalIndex | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._storage = storage
        self._metadata = metadata
        self._visual_indexer = visual_indexer
        self._place_indexer = place_indexer
        self._observer = observer
        self._event_assigner = event_assigner
        self._lexical_index = lexical_index
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
                placed=self._index_place(existing),
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

        placed = self._index_place(asset)
        indexed = self._index_visual(asset)

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

        observation = self._observe(asset, observation) or observation
        event_id = self._assign_event(observation)
        lexical_terms = self._project_lexical(observation)

        return ImportResult(
            asset_id=asset_id,
            observation_id=observation.id,
            media_type=media_type,
            status=asset.status,
            deduplicated=False,
            indexed=indexed,
            placed=placed,
            event_id=event_id,
            lexical_terms=lexical_terms,
        )

    # ---- 可选协作者 ------------------------------------------------------------

    def _index_visual(self, asset: Asset) -> bool:
        if self._visual_indexer is None:
            return False
        return self._visual_indexer.index(asset).indexed

    def _index_place(self, asset: Asset) -> bool:
        if self._place_indexer is None:
            return False
        return self._place_indexer.index(asset)

    def _observe(self, asset: Asset, observation: Observation) -> Observation | None:
        if self._observer is None:
            return None
        return self._observer.observe(asset, observation)

    def _assign_event(self, observation: Observation) -> str | None:
        if self._event_assigner is None:
            return None
        return self._event_assigner(observation)

    def _project_lexical(self, observation: Observation) -> int:
        if self._lexical_index is None:
            return 0
        return self._lexical_index.refresh_from_observation(observation)
