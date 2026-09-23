"""``events`` / ``event_observations`` 仓储。SQL 只出现在这一层。

对应 1.0 ``backend/db.py`` 的 ``create_event`` / ``merge_observation_into_event`` /
``_merge_events`` / ``list_events`` / ``get_event``。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.memories.events import EventRecord
from sentrix.infrastructure.persistence.database import Database

_COLUMNS = (
    "id, scope_id, title, event_type, time_start, time_end, place, admin_district,"
    "activity, summary, confidence, aggregation_score, aggregation_breakdown_json,"
    " status, revision, created_at"
)

# 1.0 ``merge_observation_into_event`` 新建事件时的两个占位值（``db.py:2310`` 附近）。
DEFAULT_EVENT_TITLE = "待总结事件"
DEFAULT_EVENT_TYPE = "待判断"
DEFAULT_EVENT_PLACE = "其他或不确定"


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _scope_clause(scope_id: str, place: str) -> tuple[str, tuple[object, ...]]:
    clause = " WHERE scope_id = ? AND status = 'active'"
    params: list[object] = [scope_id]
    if place:
        clause += " AND (place LIKE ? OR admin_district LIKE ?)"
        params.extend((f"%{place}%", f"%{place}%"))
    return clause, tuple(params)


def _row_to_event(row: sqlite3.Row) -> EventRecord:
    try:
        breakdown = json.loads(row["aggregation_breakdown_json"] or "{}")
    except json.JSONDecodeError:
        breakdown = {}
    return EventRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        title=row["title"],
        event_type=row["event_type"],
        time_start=_parse_datetime(row["time_start"]),
        time_end=_parse_datetime(row["time_end"]),
        place=row["place"],
        admin_district=row["admin_district"],
        activity=row["activity"],
        summary=row["summary"],
        confidence=row["confidence"],
        aggregation_score=row["aggregation_score"],
        aggregation_breakdown=breakdown if isinstance(breakdown, dict) else {},
        status=row["status"],
        revision=row["revision"],
        created_at=_parse_datetime(row["created_at"]),
    )


class SqliteEventRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        *,
        event_id: str,
        scope_id: str,
        time_start: datetime | None,
        time_end: datetime | None,
        place: str,
        admin_district: str,
        activity: str,
        summary: str,
        confidence: float,
        aggregation_score: float,
        aggregation_breakdown: dict[str, object],
    ) -> EventRecord:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO events (id, scope_id, title, event_type, time_start, time_end,"
                " place, admin_district, activity, summary, confidence, aggregation_score,"
                " aggregation_breakdown_json, status, revision, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',1,?,?)",
                (
                    event_id,
                    scope_id,
                    DEFAULT_EVENT_TITLE,
                    DEFAULT_EVENT_TYPE,
                    time_start.isoformat() if time_start else None,
                    time_end.isoformat() if time_end else None,
                    place or DEFAULT_EVENT_PLACE,
                    admin_district,
                    activity,
                    summary or DEFAULT_EVENT_TITLE,
                    float(confidence),
                    float(aggregation_score),
                    json.dumps(aggregation_breakdown, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        created = self.get(event_id)
        assert created is not None
        return created

    def get(self, event_id: str) -> EventRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(f"SELECT {_COLUMNS} FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def list_active(self, scope_id: str, *, limit: int) -> Sequence[EventRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM events WHERE status = 'active' AND scope_id = ?"
                " ORDER BY time_start DESC LIMIT ?",
                (scope_id, limit),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_by_scope(
        self, scope_id: str, *, limit: int, offset: int = 0, place: str = ""
    ) -> Sequence[EventRecord]:
        clause, params = _scope_clause(scope_id, place)
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM events{clause}"
                " ORDER BY COALESCE(time_start, created_at) DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def count_by_scope(self, scope_id: str, *, place: str = "") -> int:
        clause, params = _scope_clause(scope_id, place)
        with self._database.connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS n FROM events{clause}", params).fetchone()
        return int(row["n"]) if row else 0

    def apply_summary(
        self,
        event_id: str,
        *,
        title: str,
        event_type: str,
        activity: str,
        summary: str,
    ) -> EventRecord:
        """写入事件投影（标题 / 类型 / 活动 / 摘要）并把 ``revision`` 加一。

        1.0 ``_persist_event_summary`` 的 UPDATE 就是这四列；置信度不在其中——
        1.0 也没有把确定性摘要的那 0.45 写回事件。
        """
        if self.get(event_id) is None:
            raise KeyError(event_id)
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE events SET title = ?, event_type = ?, activity = ?, summary = ?,"
                " revision = revision + 1, updated_at = ? WHERE id = ?",
                (
                    title,
                    event_type,
                    activity,
                    summary,
                    datetime.now(UTC).isoformat(),
                    event_id,
                ),
            )
        updated = self.get(event_id)
        assert updated is not None
        return updated

    def observation_ids(self, event_id: str) -> Sequence[str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT observation_id FROM event_observations WHERE event_id = ? ORDER BY observation_id",
                (event_id,),
            ).fetchall()
        return [row["observation_id"] for row in rows]

    def events_for_observations(self, observation_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """这些观察各自落在哪些事件上（``event_observations`` 的反向查询）。

        1.0 ``_refresh_event_participants`` 是把每条观察单独查一次再在 Python 里分组；
        这里一次 ``IN`` 查完，分组结果一样。观察按 id 升序去重——一条观察在一个事件里
        只可能有一行（主键 ``(event_id, observation_id)``），同一条观察落在两个事件上
        倒是可能的（1.0 允许的中间态），那种情况它出现在两个分组里，各自算一次。
        """
        unique = list(dict.fromkeys(observation_ids))
        if not unique:
            return {}
        placeholders = ",".join("?" for _ in unique)
        with self._database.connect() as connection:
            rows = connection.execute(
                "SELECT event_id, observation_id FROM event_observations"
                f" WHERE observation_id IN ({placeholders})"
                " ORDER BY event_id, observation_id",
                tuple(unique),
            ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["event_id"], []).append(row["observation_id"])
        return {event_id: tuple(items) for event_id, items in grouped.items()}

    def attach(self, event_id: str, observation_id: str) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO event_observations (event_id, observation_id, created_at)"
                " VALUES (?,?,?)",
                (event_id, observation_id, datetime.now(UTC).isoformat()),
            )

    def fold_observation(
        self,
        event_id: str,
        *,
        time_start: datetime | None,
        time_end: datetime | None,
        place: str,
        admin_district: str,
        activity: str,
        summary: str,
        confidence: float,
        aggregation_score: float,
        aggregation_breakdown: dict[str, object],
    ) -> EventRecord:
        """把一条新观察折进已有事件：扩时间窗、补地点/活动/摘要、抬置信度、revision+1。

        1.0 的对应 UPDATE 在 ``db.py:2296-2305``。两处保留的语义：
        ``confidence = MAX(confidence, ?)``（只抬不降）与 ``summary`` 的追加拼接
        （新 caption 不在旧摘要里才追加）。
        """
        current = self.get(event_id)
        if current is None:
            raise KeyError(event_id)
        merged_activity = current.activity or activity
        merged_summary = current.summary
        if summary and summary not in merged_summary:
            merged_summary = f"{merged_summary}；{summary}" if merged_summary else summary
        starts = [value for value in (current.time_start, time_start) if value is not None]
        ends = [value for value in (current.time_end, time_end) if value is not None]
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE events SET time_start = ?, time_end = ?, place = ?, admin_district = ?,"
                " activity = ?, summary = ?, confidence = MAX(confidence, ?),"
                " aggregation_score = ?, aggregation_breakdown_json = ?,"
                " revision = revision + 1, updated_at = ? WHERE id = ?",
                (
                    min(starts).isoformat() if starts else None,
                    max(ends).isoformat() if ends else None,
                    place or current.place,
                    admin_district or current.admin_district,
                    merged_activity,
                    merged_summary,
                    float(confidence),
                    float(aggregation_score),
                    json.dumps(aggregation_breakdown, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    event_id,
                ),
            )
        updated = self.get(event_id)
        assert updated is not None
        return updated

    def merge(self, source_event_id: str, target_event_id: str) -> None:
        """把 ``source`` 的观察与时间窗并入 ``target``，然后**删掉** source。

        1.0 的 ``_merge_events``（``db.py:5482``）把 source 标成 ``merged`` 而不删，
        于是同一批照片会同时挂在一个 ``active`` 事件和一个 ``merged`` 事件上，
        ``list_events`` 只查 active 所以表面正常，但任何按 ``event_id`` 直接取数的
        地方都会拿到半份。2.0 直接删行——``event_observations`` 上有外键，
        删除前先把关联搬走。见 ``s4-deviations.md`` §2 第 9 项。
        """
        if source_event_id == target_event_id:
            return
        source = self.get(source_event_id)
        target = self.get(target_event_id)
        if source is None or target is None:
            raise KeyError(source_event_id if source is None else target_event_id)
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE OR IGNORE event_observations SET event_id = ? WHERE event_id = ?",
                (target_event_id, source_event_id),
            )
            # 上面是 OR IGNORE：两事件共有的观察已经在 target 上，这里把剩下的删掉。
            connection.execute("DELETE FROM event_observations WHERE event_id = ?", (source_event_id,))
            connection.execute("DELETE FROM events WHERE id = ?", (source_event_id,))
            starts = [value for value in (target.time_start, source.time_start) if value is not None]
            ends = [value for value in (target.time_end, source.time_end) if value is not None]
            connection.execute(
                "UPDATE events SET time_start = ?, time_end = ?,"
                " confidence = MAX(confidence, ?), revision = revision + 1, updated_at = ?"
                " WHERE id = ?",
                (
                    min(starts).isoformat() if starts else None,
                    max(ends).isoformat() if ends else None,
                    source.confidence,
                    datetime.now(UTC).isoformat(),
                    target_event_id,
                ),
            )

    def dissolve(self, event_id: str) -> None:
        with self._database.connect() as connection, connection:
            connection.execute("DELETE FROM event_observations WHERE event_id = ?", (event_id,))
            connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
