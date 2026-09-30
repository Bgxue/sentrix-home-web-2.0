"""``semantic_profiles`` / ``semantic_claims`` 的**只读**仓储（S6 · T130）。

这两张表从 T127 建起来之后一直没有读者，这里是第一个。列集与 1.0 ``db.py:811-849``
逐字一致（迁移 0008 的注释里写着），所以 ``_COLUMNS`` 按表定义列全，不用 ``SELECT *``：
1.0 用 ``SELECT *`` 是因为它的表是运行期 ``ALTER`` 长出来的，2.0 一次建对。

**一处 1.0 的名字谎言，照搬不改**：``person_id`` 列在这个域里**存的是实体 id**，
不是「人」——1.0 的 ``get_entity_detail`` 与 ``/api/knowledge`` 都拿非人物实体的 id
去查过它，调用点不判类型。改名会让新老库的行对不上。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from sentrix.domain.identity.models import SemanticClaimRecord, SemanticProfileRecord
from sentrix.infrastructure.persistence.database import Database

_PROFILE_COLUMNS = (
    "id, scope_id, person_id, summary_zh, activity_summary_zh, place_summary_zh,"
    " appearance_summary_zh, preference_summary_zh, event_memory_json, patterns_json,"
    " evidence_ids_json, revision, created_at, updated_at"
)
_CLAIM_COLUMNS = (
    "id, scope_id, person_id, dimension, predicate, value_text, normalized_value_text,"
    " normalization_version, support_count, value_entity_id, valid_from, valid_to,"
    " supporting_event_ids_json, evidence_ids_json, status, confidence, confidence_source,"
    " supersedes_claim_id, revision, created_at, updated_at"
)

# 1.0 ``db.py:2711`` 的状态白名单。它是 ``include_history=False``（默认）那一支——
# 也就是唯一被 2.0 用到的取值。
ACTIVE_CLAIM_STATUSES = ("active", "pending")


def _timestamp(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _json_list(raw: object) -> list[object]:
    """解一个 ``*_json`` 列。1.0 的 ``_decode`` 解不开时回落空表，这里一样。"""
    try:
        value = json.loads(str(raw) if raw is not None else "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _strings(raw: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _json_list(raw))


def _row_to_profile(row: sqlite3.Row) -> SemanticProfileRecord:
    return SemanticProfileRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        person_id=row["person_id"],
        summary_zh=row["summary_zh"],
        activity_summary_zh=row["activity_summary_zh"],
        place_summary_zh=row["place_summary_zh"],
        appearance_summary_zh=row["appearance_summary_zh"],
        preference_summary_zh=row["preference_summary_zh"],
        event_memory=tuple(_json_list(row["event_memory_json"])),
        patterns=tuple(_json_list(row["patterns_json"])),
        evidence_ids=_strings(row["evidence_ids_json"]),
        revision=row["revision"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


def _row_to_claim(row: sqlite3.Row) -> SemanticClaimRecord:
    return SemanticClaimRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        person_id=row["person_id"],
        dimension=row["dimension"],
        predicate=row["predicate"],
        value_text=row["value_text"],
        normalized_value_text=row["normalized_value_text"],
        normalization_version=row["normalization_version"],
        support_count=row["support_count"],
        value_entity_id=row["value_entity_id"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        supporting_event_ids=_strings(row["supporting_event_ids_json"]),
        evidence_ids=_strings(row["evidence_ids_json"]),
        status=row["status"],
        confidence=row["confidence"],
        confidence_source=row["confidence_source"],
        supersedes_claim_id=row["supersedes_claim_id"],
        revision=row["revision"],
        created_at=_timestamp(row["created_at"]),
        updated_at=_timestamp(row["updated_at"]),
    )


class SqliteSemanticRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_profile(self, person_id: str) -> SemanticProfileRecord | None:
        """1.0 ``db.py:2614-2616``。``person_id`` 在库里是 UNIQUE，所以至多一行。"""
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_PROFILE_COLUMNS} FROM semantic_profiles WHERE person_id = ?",
                (person_id,),
            ).fetchone()
        return _row_to_profile(row) if row else None

    def list_claims(self, person_id: str | None = None, *, limit: int = 500) -> Sequence[SemanticClaimRecord]:
        """1.0 ``db.py:2710-2716``。**恒带状态白名单**，排序 ``updated_at DESC``。

        两个分支的 SQL 形状与 1.0 一一对应：给了 ``person_id`` 按它过滤，没给就是全表
        （1.0 那个分支的 ``WHERE 1 = 1`` 只是为了让状态子句能直接拼上去）。
        """
        marks = ",".join("?" * len(ACTIVE_CLAIM_STATUSES))
        with self._database.connect() as connection:
            if person_id:
                rows = connection.execute(
                    f"SELECT {_CLAIM_COLUMNS} FROM semantic_claims"
                    f" WHERE person_id = ? AND status IN ({marks})"
                    " ORDER BY updated_at DESC LIMIT ?",
                    (person_id, *ACTIVE_CLAIM_STATUSES, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {_CLAIM_COLUMNS} FROM semantic_claims"
                    f" WHERE status IN ({marks})"
                    " ORDER BY updated_at DESC LIMIT ?",
                    (*ACTIVE_CLAIM_STATUSES, limit),
                ).fetchall()
        return [_row_to_claim(row) for row in rows]
