"""MemorySpace 仓储。2.0 是空库，只需 ensure/list。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.infrastructure.persistence.database import Database


@dataclass(frozen=True, slots=True)
class MemorySpace:
    id: str
    name: str
    kind: str
    status: str


class SqliteSpaceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def ensure(self, scope_id: str, name: str, kind: str = "household") -> str:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
                " VALUES (?, ?, ?, 'active', ?, ?)"
                " ON CONFLICT(id) DO NOTHING",
                (scope_id, name, kind, now, now),
            )
        return scope_id

    def list_all(self) -> list[MemorySpace]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, kind, status FROM memory_spaces ORDER BY created_at"
            ).fetchall()
        return [MemorySpace(row["id"], row["name"], row["kind"], row["status"]) for row in rows]

    def get(self, scope_id: str) -> MemorySpace | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id, name, kind, status FROM memory_spaces WHERE id = ?", (scope_id,)
            ).fetchone()
        return MemorySpace(row["id"], row["name"], row["kind"], row["status"]) if row else None
