"""Asset / Observation 仓储。SQL 只出现在这一层。"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime

from sentrix.domain.media.models import (
    Asset,
    AssetStatus,
    GpsPoint,
    MediaType,
    Observation,
    ObservationFact,
)
from sentrix.domain.media.ports import ObservationSemantics
from sentrix.infrastructure.persistence.database import Database

_ASSET_COLUMNS = (
    "id, scope_id, file_name, media_type, path, mime_type, size_bytes, status,"
    " content_sha256, captured_at, gps_latitude, gps_longitude, created_at, updated_at"
)


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_asset(row: sqlite3.Row) -> Asset:
    latitude, longitude = row["gps_latitude"], row["gps_longitude"]
    return Asset(
        id=row["id"],
        scope_id=row["scope_id"],
        file_name=row["file_name"],
        media_type=MediaType(row["media_type"]),
        path=row["path"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        status=AssetStatus(row["status"]),
        content_sha256=row["content_sha256"] or "",
        captured_at=_parse_datetime(row["captured_at"]),
        captured_location=(
            GpsPoint(latitude, longitude) if latitude is not None and longitude is not None else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class SqliteAssetRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, asset: Asset) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, mime_type,"
                " size_bytes, status, content_sha256, captured_at, gps_latitude, gps_longitude,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset.id,
                    asset.scope_id,
                    asset.file_name,
                    asset.media_type.value,
                    asset.path,
                    asset.mime_type,
                    asset.size_bytes,
                    asset.status.value,
                    asset.content_sha256,
                    asset.captured_at.isoformat() if asset.captured_at else None,
                    asset.captured_location.latitude if asset.captured_location else None,
                    asset.captured_location.longitude if asset.captured_location else None,
                    asset.created_at.isoformat(),
                    asset.updated_at.isoformat(),
                ),
            )

    def get(self, asset_id: str) -> Asset | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_ASSET_COLUMNS} FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        return _row_to_asset(row) if row else None

    def list_by_scope(self, scope_id: str, limit: int = 200) -> Sequence[Asset]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_ASSET_COLUMNS} FROM assets WHERE scope_id = ?"
                " ORDER BY COALESCE(captured_at, created_at) DESC LIMIT ?",
                (scope_id, limit),
            ).fetchall()
        return [_row_to_asset(row) for row in rows]

    def find_by_hash(self, scope_id: str, content_sha256: str) -> Asset | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_ASSET_COLUMNS} FROM assets WHERE scope_id = ? AND content_sha256 = ? LIMIT 1",
                (scope_id, content_sha256),
            ).fetchone()
        return _row_to_asset(row) if row else None

    def update_status(self, asset_id: str, status: AssetStatus) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE assets SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status.value, asset_id),
            )


_OBSERVATION_COLUMNS = (
    "id, scope_id, asset_id, source_type, captured_at, confidence, caption, place, activity,"
    " event_type, ocr_text, objects_json, clothing_json, people_json, facts_json, revision,"
    " created_at, updated_at"
)


def _json_facts(value: str | None) -> tuple[ObservationFact, ...]:
    """模型自报的候选事实。与 ``people_json`` 一样，**这里只解析，不校验**：
    字段齐不齐是 ``is_writable_fact`` 的判据，落库层再判一次会让那条规则有一半走不到。"""
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(
        ObservationFact(
            subject=str(item.get("subject") or ""),
            predicate=str(item.get("predicate") or ""),
            object=str(item.get("object") or ""),
            confidence=float(item.get("confidence") or 0.0),
        )
        for item in parsed
        if isinstance(item, dict)
    )


def _json_people(value: str | None) -> tuple[str, ...]:
    """``people_json`` → 名字元组。

    写入侧（``entities.py`` 的 ``_append_person_snapshot``）存的是
    ``{"entity_id":…, "name":…, "status":…}``；1.0 把它整条 ``str()`` 进了词法投影
    （见 ``domain/retrieval/lexical.py`` 的字段表注释），2.0 只取名字。
    """
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    names: list[str] = []
    for item in parsed:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else str(item).strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _json_list(value: str | None) -> tuple[str, ...]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if isinstance(item, (str, int, float)))


def row_to_observation(row: sqlite3.Row) -> Observation:
    return Observation(
        id=row["id"],
        scope_id=row["scope_id"],
        asset_id=row["asset_id"],
        source_type=row["source_type"],
        captured_at=_parse_datetime(row["captured_at"]),
        confidence=row["confidence"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        caption=row["caption"] or "",
        place=row["place"],
        activity=row["activity"],
        event_type=row["event_type"],
        ocr_text=row["ocr_text"] or "",
        objects=_json_list(row["objects_json"]),
        clothing=_json_list(row["clothing_json"]),
        people=_json_people(row["people_json"]),
        facts=_json_facts(row["facts_json"]),
        revision=row["revision"],
    )


class SqliteObservationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, observation: Observation) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " confidence, caption, place, activity, event_type, ocr_text, objects_json,"
                " clothing_json, people_json, facts_json, revision, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    observation.id,
                    observation.scope_id,
                    observation.asset_id,
                    observation.source_type,
                    observation.captured_at.isoformat() if observation.captured_at else None,
                    observation.confidence,
                    observation.caption,
                    observation.place,
                    observation.activity,
                    observation.event_type,
                    observation.ocr_text,
                    json.dumps(list(observation.objects), ensure_ascii=False),
                    json.dumps(list(observation.clothing), ensure_ascii=False),
                    json.dumps(
                        [{"name": name, "status": "confirmed"} for name in observation.people],
                        ensure_ascii=False,
                    ),
                    json.dumps([dataclasses.asdict(item) for item in observation.facts], ensure_ascii=False),
                    observation.revision,
                    observation.created_at.isoformat(),
                    observation.updated_at.isoformat(),
                ),
            )

    def get(self, observation_id: str) -> Observation | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_OBSERVATION_COLUMNS} FROM observations WHERE id = ?", (observation_id,)
            ).fetchone()
        return row_to_observation(row) if row else None

    def list_by_asset(self, asset_id: str) -> Sequence[Observation]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_OBSERVATION_COLUMNS} FROM observations WHERE asset_id = ?"
                " ORDER BY created_at DESC",
                (asset_id,),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def list_scope(self, scope_id: str, *, limit: int) -> Sequence[Observation]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_OBSERVATION_COLUMNS} FROM observations WHERE scope_id = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (scope_id, limit),
            ).fetchall()
        return [row_to_observation(row) for row in rows]

    def enrich(self, observation_id: str, *, semantics: ObservationSemantics) -> Observation:
        """写入 VLM 语义并把 ``revision`` 加一。

        ``revision`` 自增而不是由调用方给：它是**版本号**，两个并发富集如果各自带一个
        自认为正确的号，投影的过期判断就会退化成比大小。自增在 SQL 里是原子的。
        """
        with self._database.connect() as connection, connection:
            connection.execute(
                "UPDATE observations SET caption = ?, place = ?, activity = ?, event_type = ?,"
                " ocr_text = ?, objects_json = ?, clothing_json = ?, facts_json = ?,"
                " confidence = MAX(confidence, ?), revision = revision + 1,"
                " updated_at = datetime('now') WHERE id = ?",
                (
                    semantics.caption,
                    semantics.place,
                    semantics.activity,
                    semantics.event_type,
                    semantics.ocr_text,
                    json.dumps(list(semantics.objects), ensure_ascii=False),
                    json.dumps(list(semantics.clothing), ensure_ascii=False),
                    json.dumps([dataclasses.asdict(item) for item in semantics.facts], ensure_ascii=False),
                    float(semantics.confidence),
                    observation_id,
                ),
            )
        enriched = self.get(observation_id)
        if enriched is None:
            raise KeyError(observation_id)
        return enriched
