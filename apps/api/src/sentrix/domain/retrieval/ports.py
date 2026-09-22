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

from sentrix.domain.media.models import Asset, Observation


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


@dataclass(frozen=True, slots=True)
class LexicalTerm:
    """``observation_search_terms`` 的一行。``field_type`` 见 domain/retrieval/lexical.py。"""

    observation_id: str
    asset_id: str
    scope_id: str
    field_type: str
    normalized_value: str
    confidence: float
    source_type: str
    source_revision: int


class LexicalIndex(Protocol):
    """词法投影的写入侧。**派生表**：可整表重建，每一行追得回某条 Observation 的某个 revision。

    1.0 把这两个方法放在 ``RetrievalIndex`` 一个类里（``retrieval_indexes.py:105-294``）。
    2.0 只声明 S4 真正会调的两件事：按 observation 增量刷新、按 scope 全量重建。
    1.0 的 ``search``（LIKE 子串回退）不移植——那是 FTS5 缺席时的降级路径，而
    ``make ci`` 已经在实测 FTS5 可用（``sqlite3`` 自带），留一条永不执行的降级分支
    只会让人以为它被验证过。
    """

    def refresh_from_observation(self, observation: Observation) -> int: ...

    def rebuild_scope(self, scope_id: str) -> int: ...

    def count(self, scope_id: str | None = None) -> int: ...


class LexicalSearch(Protocol):
    """词法投影的读取侧。``token_hits`` 是 ``asset_id → 命中 token 数``。"""

    def search_fts(self, query: str, *, scope_id: str | None, limit: int) -> Sequence[tuple[str, int]]: ...

    def exact_assets(self, whole_query: str, *, scope_id: str | None) -> frozenset[str]: ...


class VectorRepository(Protocol):
    """权威向量存储。SQLite 是唯一实现，也是索引重建的数据来源。"""

    def upsert(self, record: VectorRecord) -> None: ...

    def has_vector(self, *, space: str, source_type: str, source_id: str, model_name: str) -> bool: ...

    def get(
        self,
        *,
        space: str,
        source_type: str,
        source_id: str,
        model_name: str | None = None,
    ) -> VectorRecord | None:
        """取一条权威向量。``model_name`` 为空时取最近更新的一条。

        事件归并读它算视觉相似度（1.0 ``db.py:2082-2110`` 的那条 SQL：
        ``ORDER BY updated_at DESC LIMIT 1``）。
        """
        ...

    def search_exact(
        self,
        *,
        space: str,
        vector: Sequence[float],
        limit: int,
        scope_id: str | None = None,
        model_name: str | None = None,
    ) -> Sequence[VectorHit]: ...

    def count(self, *, space: str, model_name: str | None = None) -> int: ...


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


@dataclass(frozen=True, slots=True)
class VisualIndexOutcome:
    """一次视觉索引的结果。三个字段让「没索引上」和「为什么」都能一路传到接口。"""

    indexed: bool
    mirrored: bool = False
    reason: str = ""


class AssetVisualIndexer(Protocol):
    """把一张 Asset 变成视觉记忆。导入用例依赖它，不依赖具体的嵌入器/索引。"""

    def index(self, asset: Asset) -> VisualIndexOutcome: ...


class VisualEmbedder(Protocol):
    """跨模态视觉嵌入：同一空间里的文本查询向量与图像向量。

    只管两个方向，不带设备、批次、模型元数据——``model_id`` 与 ``dimension``
    是索引兼容性校验的全部依据。

    这两个成员声明成只读 property 而不是裸属性：适配器的实现是 property，
    mypy 的 ``arg-type`` 不允许一个只读 property 去满足「可写变量」的协议约定。
    """

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_image(self, path: Path) -> Sequence[float]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...
