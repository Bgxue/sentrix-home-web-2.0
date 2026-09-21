"""Asset / Observation 仓储。SQL 只出现在这一层。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation
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


class SqliteObservationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, observation: Observation) -> None:
        with self._database.connect() as connection, connection:
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " confidence, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    observation.id,
                    observation.scope_id,
                    observation.asset_id,
                    observation.source_type,
                    observation.captured_at.isoformat() if observation.captured_at else None,
                    observation.confidence,
                    observation.created_at.isoformat(),
                    observation.updated_at.isoformat(),
                ),
            )

    def get(self, observation_id: str) -> Observation | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT id, scope_id, asset_id, source_type, captured_at, confidence,"
                " created_at, updated_at FROM observations WHERE id = ?",
                (observation_id,),
            ).fetchone()
        if not row:
            return None
        return Observation(
            id=row["id"],
            scope_id=row["scope_id"],
            asset_id=row["asset_id"],
            source_type=row["source_type"],
            captured_at=_parse_datetime(row["captured_at"]),
            confidence=row["confidence"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
