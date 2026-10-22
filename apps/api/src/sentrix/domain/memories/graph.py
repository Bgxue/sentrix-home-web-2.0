"""图结构记忆的领域模型。

这些类型只描述已经提取出来的记忆如何形成一张可遍历的派生图，不知道关键帧、
Qdrant、SQLite 或任何模型。图的节点与边由 application 层从标准记忆端口生成，
持久化与遍历由 infrastructure 适配器负责。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GraphNode:
    """一条图节点投影。``attributes`` 只保存检索需要的可解释元数据。"""

    id: str
    scope_id: str
    kind: str
    label: str = ""
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """一条图边。

    遍历端把边视为无向邻接关系，但仍保留 source/target 的语义方向，方便审计和
    后续按关系类型做定向策略。
    """

    id: str
    scope_id: str
    source_id: str
    target_id: str
    relation: str
    weight: float = 1.0
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphBuildStats:
    scope_id: str
    node_count: int
    edge_count: int


@dataclass(frozen=True, slots=True)
class GraphStatus:
    scope_id: str
    available: bool
    node_count: int = 0
    edge_count: int = 0
    build_revision: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GraphExpansionHit:
    """从 seed asset 沿图走到的另一个媒体资产。"""

    asset_id: str
    score: float
    hops: int
    path: tuple[str, ...] = ()
    relations: tuple[str, ...] = ()
