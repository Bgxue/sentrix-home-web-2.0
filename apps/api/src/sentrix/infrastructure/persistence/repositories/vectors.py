"""memory_vectors 的 SQLite 仓储。SQL 只出现在这一层。

精确回退（``search_exact``）是**功能**不是兜底：Qdrant 目录被别的进程锁住、
collection 还没建、或者维度对不上时，检索必须还能给出正确答案，只是慢。
1.0 的同类回退存在但把降级写进了没人看的日志；这里由调用方（application 层）
拿 ``VectorIndexStatus`` 决定要不要告诉用户。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sentrix.domain.retrieval.ports import VectorHit, VectorRecord
from sentrix.domain.retrieval.similarity import cosine_similarity
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, space, source_type, source_id, model_name, dimension,"
    " vector_json, metadata_json, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_list(raw: str | None) -> list[float]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [float(item) for item in value]


def _decode_map(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return dict(value) if isinstance(value, dict) else {}


class SqliteVectorRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def upsert(self, record: VectorRecord) -> None:
        if not record.vector:
            raise ValueError("拒绝写入空向量：1.0 的空向量会被静默 upsert，事后无人发现")
        timestamp = _now()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO memory_vectors ("
                f" {_COLUMNS}"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(space, source_type, source_id, model_name) DO UPDATE SET"
                " scope_id = excluded.scope_id, dimension = excluded.dimension,"
                " vector_json = excluded.vector_json, metadata_json = excluded.metadata_json,"
                " updated_at = excluded.updated_at",
                (
                    f"vec_{uuid.uuid4().hex[:12]}",
                    record.scope_id,
                    record.space,
                    record.source_type,
                    record.source_id,
                    record.model_name,
                    record.dimension,
                    json.dumps([float(value) for value in record.vector]),
                    json.dumps(dict(record.metadata), ensure_ascii=False, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )

    def has_vector(self, *, space: str, source_type: str, source_id: str, model_name: str) -> bool:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM memory_vectors WHERE space = ? AND source_type = ?"
                " AND source_id = ? AND model_name = ? LIMIT 1",
                (space, source_type, source_id, model_name),
            ).fetchone()
        return row is not None

    def count(self, *, space: str) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM memory_vectors WHERE space = ?", (space,)
            ).fetchone()
        return int(row[0]) if row else 0

    def search_exact(
        self,
        *,
        space: str,
        vector: Sequence[float],
        limit: int,
        scope_id: str | None = None,
        model_name: str | None = None,
    ) -> Sequence[VectorHit]:
        if not vector or limit < 1:
            return []
        rows = self._fetch(space=space, scope_id=scope_id, model_name=model_name)
        scored: list[VectorHit] = []
        for row in rows:
            candidate = _decode_list(row["vector_json"])
            # 维度不一致的行直接跳过：拿 768 维查询去和 512 维旧向量算余弦，
            # 结果恒为 0.0，会以「相似度很低」的形态混进结果里而不是被识别为脏数据。
            if len(candidate) != len(vector):
                continue
            scored.append(
                VectorHit(
                    source_id=row["source_id"],
                    scope_id=row["scope_id"],
                    source_type=row["source_type"],
                    model_name=row["model_name"],
                    score=cosine_similarity(vector, candidate),
                    metadata=_decode_map(row["metadata_json"]),
                )
            )
        # 同分按 source_id 兜底，保证同一份数据每次给出同一个名次。
        scored.sort(key=lambda item: (-item.score, item.source_id))
        return scored[:limit]

    def _fetch(self, *, space: str, scope_id: str | None, model_name: str | None) -> list[sqlite3.Row]:
        sql = f"SELECT {_COLUMNS} FROM memory_vectors WHERE space = ?"
        params: list[Any] = [space]
        if scope_id:
            sql += " AND scope_id = ?"
            params.append(scope_id)
        if model_name:
            sql += " AND model_name = ?"
            params.append(model_name)
        with self._database.connect() as connection:
            return list(connection.execute(sql, tuple(params)).fetchall())
