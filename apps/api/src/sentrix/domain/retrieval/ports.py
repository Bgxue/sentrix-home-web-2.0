"""检索域端口：权威向量存储、派生向量索引、视觉嵌入器。

三者的分工是 1.0 已经验证过的契约，2.0 原样保留：

- ``VectorRepository`` 是**权威**（SQLite 里的 memory_vectors），写入必须先到它；
- ``VectorIndex`` 是**派生镜像**（Qdrant），可重建、可缺席；
- 索引缺席或锁被占用时，检索必须回退到权威存储，并把「正在降级」这件事报出去。

1.0 的教训是降级被写进了日志但没人看：Qdrant 目录锁被 8091 独占时，第二个进程
只打印一行 ``vector search degrades to SQLite full scans``，接口照常返回空结果。
所以这里的 ``VectorIndexStatus`` 是端口的一部分，降级原因必须能一路传到 HTTP 响应。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """一条待写入的向量。``(space, source_type, source_id, model_name)`` 唯一。"""

    scope_id: str
    space: str
    source_type: str
    source_id: str
    model_name: str
    vector: tuple[float, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def dimension(self) -> int:
        return len(self.vector)


@dataclass(frozen=True, slots=True)
class VectorHit:
    source_id: str
    scope_id: str
    source_type: str
    model_name: str
    score: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorIndexStatus:
    """索引健康度。``degraded`` 为真时必须是可行动的信息，不是一个布尔摆设。"""

    backend: str
    available: bool
    degraded: bool = False
    reason: str = ""


class VectorRepository(Protocol):
    """权威向量存储。SQLite 是唯一实现，也是索引重建的数据来源。"""

    def upsert(self, record: VectorRecord) -> None: ...

    def has_vector(self, *, space: str, source_type: str, source_id: str, model_name: str) -> bool: ...

    def search_exact(
        self,
        *,
        space: str,
        vector: Sequence[float],
        limit: int,
        scope_id: str | None = None,
        model_name: str | None = None,
    ) -> Sequence[VectorHit]: ...

    def count(self, *, space: str) -> int: ...


class VectorIndexUnavailable(RuntimeError):
    """派生索引不可用（目录被占用、权限、磁盘满）。

    这是**预期内**的失败，所以必须是一个调用方能按类型捕获的异常，而不是
    「返回 0 / 返回空列表」——后者在调用点看不出区别，1.0 的静默降级就是这么来的。
    """


class VectorIndex(Protocol):
    """派生向量索引。缺席不是错误，但必须被看见。

    ``upsert`` / ``search`` / ``count`` 在索引不可用时抛 ``VectorIndexUnavailable``，
    由 application 层决定是回退到权威存储还是把错误暴露出去——适配器不替它决定。
    """

    def upsert(self, records: Sequence[VectorRecord]) -> int: ...

    def search(
        self,
        *,
        space: str,
        vector: Sequence[float],
        limit: int,
        scope_id: str | None = None,
        model_name: str | None = None,
    ) -> Sequence[VectorHit]: ...

    def count(self, *, space: str, model_name: str | None = None) -> int: ...

    def status(self) -> VectorIndexStatus: ...


class VisualEmbedder(Protocol):
    """跨模态视觉嵌入：同一空间里的文本查询向量与图像向量。

    只管两个方向，不带设备、批次、模型元数据——``model_id`` 与 ``dimension``
    是索引兼容性校验的全部依据。
    """

    model_id: str
    dimension: int

    def embed_image(self, path: Path) -> Sequence[float]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...
