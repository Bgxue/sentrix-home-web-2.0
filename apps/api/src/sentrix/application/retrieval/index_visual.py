"""把一张 Asset 变成视觉记忆：嵌入 → 写权威存储 → 镜像到派生索引。

1.0 的时机是「导入即向量化」（``backend/pipeline.py`` 的 ``_commit_fast_image``），
2.0 保持同样时机：用户在 Web 上导入完照片，就应该能立刻搜到它。

两条来自 1.0 的硬性要求：

- **权威先写、镜像后写**。SQLite 里的 memory_vectors 是事实源，Qdrant 只是索引；
  镜像写失败不该让导入失败，但必须留下可见痕迹（返回 ``mirrored=False`` + 原因）。
- **空向量不写**。1.0 拿到空向量照样 upsert，于是「这张图没有任何视觉向量」在
  整条链路上不可见——numpy ABI 冲突期间 223 张图就是这么丢的。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sentrix.domain.media.models import Asset, MediaType
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import (
    AssetVisualIndexer,
    PreparedVisual,
    VectorIndex,
    VectorIndexUnavailable,
    VectorRecord,
    VectorRepository,
    VisualEmbedder,
    VisualIndexOutcome,
)

log = logging.getLogger("sentrix.retrieval.index_visual")

SOURCE_TYPE = "asset"


class IndexAssetVisual(AssetVisualIndexer):
    def __init__(
        self,
        *,
        embedder: VisualEmbedder,
        vectors: VectorRepository,
        index: VectorIndex | None = None,
    ) -> None:
        self._embedder = embedder
        self._vectors = vectors
        self._index = index

    def prepare(self, asset: Asset) -> PreparedVisual:
        """第一段：**只算不写**。幂等判据（``has_vector``）也在这一段——它是一次读。

        每一条早退在 ``commit`` 里都有一一对应的 ``VisualIndexOutcome``，所以
        两段合起来与原先的单段逐字等价。
        """
        if asset.media_type is not MediaType.IMAGE:
            return PreparedVisual(skip_reason="not_an_image")

        if self._vectors.has_vector(
            space=scheme.VISUAL_SPACE,
            source_type=SOURCE_TYPE,
            source_id=asset.id,
            model_name=self._embedder.model_id,
        ):
            # 幂等：重复导入同一张图不该再跑一次推理。
            return PreparedVisual(skip_reason="already_indexed")

        vector = self._embedder.embed_image(Path(asset.path))
        if not vector:
            # 到这里说明适配器已经 log.exception 过了；这里只负责不让它变成一次
            # 「导入成功但没有向量」的静默成功。
            return PreparedVisual(skip_reason="embedding_unavailable")
        return PreparedVisual(vector=tuple(float(value) for value in vector))

    def commit(self, asset: Asset, prepared: PreparedVisual) -> VisualIndexOutcome:
        """第二段：嵌入之外的全部——建记录、写权威存储、镜像派生索引。

        **写库只在这一半**，所以它必须留在串行侧（见 ``AssetVisualIndexer``）。
        """
        if prepared.skip_reason:
            # ``already_indexed`` 是「早就有」，与「刚写上」必须分得开。
            copied = prepared.skip_reason == "already_indexed"
            return VisualIndexOutcome(indexed=copied, mirrored=copied, reason=prepared.skip_reason)
        if len(prepared.vector) != self._embedder.dimension:
            return VisualIndexOutcome(
                indexed=False,
                reason=f"dimension_mismatch:{len(prepared.vector)}!={self._embedder.dimension}",
            )

        record = VectorRecord(
            scope_id=asset.scope_id,
            space=scheme.VISUAL_SPACE,
            source_type=SOURCE_TYPE,
            source_id=asset.id,
            model_name=self._embedder.model_id,
            vector=tuple(float(value) for value in prepared.vector),
            metadata={"file_name": asset.file_name},
        )
        self._vectors.upsert(record)
        mirrored, reason = self._mirror(record)
        return VisualIndexOutcome(indexed=True, mirrored=mirrored, reason=reason)

    def index(self, asset: Asset) -> VisualIndexOutcome:
        """两段的复合。**行为与拆分前逐字一致**；不需要并行的调用方用它。"""
        return self.commit(asset, self.prepare(asset))

    def _mirror(self, record: VectorRecord) -> tuple[bool, str]:
        """把同一条向量写进派生索引。只吞 ``VectorIndexUnavailable``——那是预期内的失败。

        其它异常一律向上抛：索引适配器里的代码错误不该被降级逻辑掩盖成
        「索引暂时不可用」。
        """
        if self._index is None:
            return False, "no_index_configured"
        try:
            self._index.upsert([record])
        except VectorIndexUnavailable as error:
            log.warning("派生向量索引不可用，权威存储已写入: %s", error)
            return False, str(error)
        return True, ""
