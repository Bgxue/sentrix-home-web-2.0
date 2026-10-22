"""SQLite 记忆图适配器。

图表是派生镜像，权威数据仍在 events / observations / assets / entities 等表里。
替换或重建图只影响这组三张表，不会改变产品主链路的写入语义。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sentrix.domain.memories.graph import (
    GraphBuildStats,
    GraphEdge,
    GraphExpansionHit,
    GraphNode,
    GraphStatus,
)
from sentrix.infrastructure.persistence.database import Database


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


class SqliteMemoryGraphRepository:
    """按 scope 原子替换、按有限 hop 做邻接遍历。"""

    def __init__(self, database: Database) -> None:
        self._database = database

    def replace_scope(
        self,
        scope_id: str,
        *,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        build_revision: str,
    ) -> GraphBuildStats:
        timestamp = datetime.now(UTC).isoformat()
        node_rows = [
            (
                node.id,
                scope_id,
                node.kind,
                node.label,
                _json(dict(node.attributes)),
                build_revision,
                timestamp,
                timestamp,
            )
            for node in nodes
        ]
        edge_rows = [
            (
                edge.id,
                scope_id,
                edge.source_id,
                edge.target_id,
                edge.relation,
                float(edge.weight),
                _json(dict(edge.attributes)),
                build_revision,
                timestamp,
                timestamp,
            )
            for edge in edges
        ]
        with self._database.connect() as connection, connection:
            # 边先于节点删除，保持派生表内部始终自洽，即使未来加上 FK 也不需要改顺序。
            connection.execute("DELETE FROM memory_graph_edges WHERE scope_id = ?", (scope_id,))
            connection.execute("DELETE FROM memory_graph_nodes WHERE scope_id = ?", (scope_id,))
            connection.executemany(
                "INSERT INTO memory_graph_nodes"
                " (id, scope_id, node_type, label, attributes_json, build_revision, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                node_rows,
            )
            connection.executemany(
                "INSERT INTO memory_graph_edges"
                " (id, scope_id, source_id, target_id, relation, weight, attributes_json,"
                "  build_revision, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                edge_rows,
            )
            connection.execute(
                "INSERT INTO memory_graph_builds(scope_id, build_revision, node_count, edge_count, built_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(scope_id) DO UPDATE SET build_revision=excluded.build_revision,"
                " node_count=excluded.node_count, edge_count=excluded.edge_count, built_at=excluded.built_at",
                (scope_id, build_revision, len(node_rows), len(edge_rows), timestamp),
            )
        return GraphBuildStats(scope_id=scope_id, node_count=len(node_rows), edge_count=len(edge_rows))

    def status(self, scope_id: str) -> GraphStatus:
        with self._database.connect() as connection:
            build = connection.execute(
                "SELECT build_revision, node_count, edge_count FROM memory_graph_builds WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
        if build is None:
            return GraphStatus(scope_id=scope_id, available=False, reason="graph_not_built")
        return GraphStatus(
            scope_id=scope_id,
            available=True,
            node_count=int(build[1]),
            edge_count=int(build[2]),
            build_revision=str(build[0]),
        )

    def quality(self, scope_id: str) -> Mapping[str, object]:
        """图自身的结构审计。

        这里不能读取 PhotoBench 的身份/QA GT；那是 evaluator 的职责。所谓「可验边」
        在 2.0 里因此是当前关系契约的 rule-reference：检查端点是否存在、节点类型是否
        符合关系契约、是否有重复边。响应明确带上 ``reference_mode``，前端不会把它显示
        成独立真实世界准确率。
        """

        status = self.status(scope_id)
        if not status.available:
            return {
                "available": False,
                "scope_id": scope_id,
                "reason": status.reason or "graph_not_built",
                "reference_mode": "current_2_0_relation_contract",
            }
        with self._database.connect() as connection:
            node_rows = connection.execute(
                "SELECT id, node_type FROM memory_graph_nodes WHERE scope_id = ?", (scope_id,)
            ).fetchall()
            edge_rows = connection.execute(
                "SELECT source_id, target_id, relation, attributes_json"
                " FROM memory_graph_edges WHERE scope_id = ?",
                (scope_id,),
            ).fetchall()

        node_types = {str(row[0]): str(row[1]) for row in node_rows}
        allowed = {
            "event_contains_observation": {("event", "observation")},
            "event_contains_asset": {("event", "asset")},
            "event_has_entity": {("event", "entity")},
            "observation_of_asset": {("observation", "asset")},
            "observation_mentions_entity": {("observation", "entity")},
            "observation_mentions_concept": {("observation", "concept")},
            "fact_evidence": {("fact", "observation")},
            "fact_about_entity": {("fact", "entity")},
            "event_temporal_next": {("event", "event")},
        }
        total = len(edge_rows)
        endpoint_valid = 0
        contract_valid = 0
        attribute_supported = 0
        duplicate_counts = Counter((str(row[0]), str(row[1]), str(row[2])) for row in edge_rows)
        for row in edge_rows:
            source_id, target_id, relation = str(row[0]), str(row[1]), str(row[2])
            source_kind, target_kind = node_types.get(source_id), node_types.get(target_id)
            if source_kind and target_kind:
                endpoint_valid += 1
                relation_ok = (relation in allowed and (source_kind, target_kind) in allowed[relation]) or (
                    relation.startswith("entity_relationship:") and source_kind == target_kind == "entity"
                )
                if relation_ok:
                    contract_valid += 1
            try:
                attributes = json.loads(row[3] or "{}")
            except (TypeError, json.JSONDecodeError):
                attributes = {}
            if isinstance(attributes, dict) and attributes:
                attribute_supported += 1
        duplicate_extra = sum(max(0, count - 1) for count in duplicate_counts.values())

        def rate(numerator: int, denominator: int) -> float | None:
            return round(numerator / denominator, 4) if denominator else None

        connected_nodes: set[str] = set()
        for row in edge_rows:
            if str(row[0]) in node_types and str(row[1]) in node_types:
                connected_nodes.update((str(row[0]), str(row[1])))
        reference_count = contract_valid
        reference_precision = rate(contract_valid, total)
        reference_recall = rate(contract_valid, reference_count)
        reference_f1 = (
            round(
                2 * reference_precision * reference_recall / (reference_precision + reference_recall),
                4,
            )
            if reference_precision is not None
            and reference_recall is not None
            and reference_precision + reference_recall
            else None
        )
        return {
            "available": True,
            "scope_id": scope_id,
            "build_revision": status.build_revision,
            "node_count": len(node_rows),
            "edge_count": total,
            "reference_mode": "current_2_0_relation_contract",
            "reference_edge_count": reference_count,
            "evaluable_predicted_edge_count": total,
            "reference_edge_true_positive_count": contract_valid,
            "reference_edge_precision": reference_precision,
            # rule-reference 的分母就是当前关系契约生成的可验证边；它不是独立 GT。
            "reference_edge_recall": reference_recall,
            "reference_edge_f1": reference_f1,
            "valid_edge_rate": rate(endpoint_valid, total),
            "edge_consistency_rate": rate(contract_valid, total),
            "edge_attribute_support_rate": rate(attribute_supported, total),
            "edge_evidence_support_rate": None,
            "duplicate_edge_rate": rate(duplicate_extra, total),
            "node_connected_rate": rate(len(connected_nodes), len(node_rows)),
            "unverifiable_edge_ratio": rate(total - contract_valid, total),
            "independent_ground_truth_available": False,
            "note": "当前 2.0 图没有独立边 GT；P/R/F1 是关系契约 rule-reference，不是独立准确率。",
        }

    def expand_assets(
        self,
        seed_asset_ids: Sequence[str],
        *,
        scope_id: str,
        max_hops: int,
        limit: int,
    ) -> Sequence[GraphExpansionHit]:
        seeds = tuple(dict.fromkeys(f"asset:{asset_id}" for asset_id in seed_asset_ids if asset_id))
        if not seeds or max_hops < 1 or limit < 1:
            return ()

        status = self.status(scope_id)
        if not status.available:
            return ()

        # 每层最多保留有限 frontier，避免错误或过于常见的概念把整张图拖进内存。
        frontier: dict[str, tuple[float, int, tuple[str, ...], tuple[str, ...]]] = {
            node_id: (1.0, 0, (node_id,), ()) for node_id in seeds
        }
        best_hops: dict[str, int] = {node_id: 0 for node_id in seeds}
        candidates: dict[str, GraphExpansionHit] = {}

        with self._database.connect() as connection:
            for _ in range(max_hops):
                if not frontier:
                    break
                frontier_ids = tuple(frontier)
                placeholders = ",".join("?" for _ in frontier_ids)
                rows = connection.execute(
                    "SELECT source_id, target_id, relation, weight FROM memory_graph_edges"
                    f" WHERE scope_id = ? AND (source_id IN ({placeholders})"
                    f" OR target_id IN ({placeholders}))"
                    " ORDER BY id LIMIT 10000",
                    (scope_id, *frontier_ids, *frontier_ids),
                ).fetchall()
                neighbor_ids = {str(row[1]) if str(row[0]) in frontier else str(row[0]) for row in rows}
                if not neighbor_ids:
                    break
                neighbor_placeholders = ",".join("?" for _ in neighbor_ids)
                node_rows = connection.execute(
                    "SELECT id, node_type FROM memory_graph_nodes"
                    f" WHERE scope_id = ? AND id IN ({neighbor_placeholders})",
                    (scope_id, *neighbor_ids),
                ).fetchall()
                node_types = {str(row[0]): str(row[1]) for row in node_rows}
                next_frontier: dict[str, tuple[float, int, tuple[str, ...], tuple[str, ...]]] = {}

                for row in rows:
                    source_id, target_id = str(row[0]), str(row[1])
                    relation = str(row[2])
                    weight = float(row[3] or 0.0)
                    for current_id, neighbor_id in ((source_id, target_id), (target_id, source_id)):
                        current = frontier.get(current_id)
                        if current is None or neighbor_id == current_id:
                            continue
                        current_score, current_hops, current_path, current_relations = current
                        next_hops = current_hops + 1
                        old_hops = best_hops.get(neighbor_id)
                        if old_hops is not None and old_hops <= next_hops:
                            continue
                        best_hops[neighbor_id] = next_hops
                        safe_weight = min(1.0, max(0.0, weight))
                        next_score = current_score * safe_weight * (0.72 if next_hops > 1 else 1.0)
                        state = (
                            next_score,
                            next_hops,
                            (*current_path, neighbor_id),
                            (*current_relations, relation),
                        )
                        next_frontier[neighbor_id] = state
                        if node_types.get(neighbor_id) == "asset" and neighbor_id not in seeds:
                            asset_id = neighbor_id.removeprefix("asset:")
                            existing = candidates.get(asset_id)
                            hit = GraphExpansionHit(
                                asset_id=asset_id,
                                score=next_score,
                                hops=next_hops,
                                path=state[2],
                                relations=state[3],
                            )
                            if existing is None or hit.score > existing.score:
                                candidates[asset_id] = hit
                # Keep the most promising 512 frontier nodes for the next hop.
                frontier = dict(sorted(next_frontier.items(), key=lambda item: (-item[1][0], item[0]))[:512])

        return tuple(
            sorted(
                candidates.values(),
                key=lambda item: (-item.score, item.hops, item.asset_id),
            )[:limit]
        )
