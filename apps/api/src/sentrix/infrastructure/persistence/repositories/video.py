"""视频血缘仓储。SQL 只出现在这一层。

对应 1.0 ``backend/db.py`` 的 ``list_derived_assets``（:2383）与
``cleanup_video_derivatives``（:2435），加上 2.0 单开的探测结果读写
（1.0 把 ``video_metadata`` / ``browser_preview_path`` 直接摊在 ``assets.metadata_json`` 上，
写完就没有回读的地方——2.0 的 ``GET /videos/{id}`` 要把它拿出来，所以有了成对的读写）。

**元数据为什么留在 ``metadata_json`` 而不是摊成列。** 迁移 0010 的表头注释定了这条：
每类媒体自己的元数据形状不同（EXIF / 视频 / 音频），摊成列会让列数随媒体类型膨胀，
而视频这几个字段都不参与检索过滤。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from sentrix.domain.media.models import DerivedAsset, VideoMetadata
from sentrix.domain.media.ports import VideoProbe
from sentrix.infrastructure.persistence.database import Database

# 写进 ``assets.metadata_json`` 的四个键。前三个是 1.0 的键名（``processor.py:118-121``），
# 第四个是 2.0 加的（探测失败的原因，1.0 那时是整条资产标 failed）。
_VIDEO_METADATA_KEY = "video_metadata"
_PREVIEW_PATH_KEY = "browser_preview_path"
_PREVIEW_ERROR_KEY = "browser_preview_error"
_PROBE_ERROR_KEY = "video_probe_error"

_VIDEO_KEYS = (_VIDEO_METADATA_KEY, _PREVIEW_PATH_KEY, _PREVIEW_ERROR_KEY, _PROBE_ERROR_KEY)

# VideoMetadata 的字段名与字典键一一对应，构造时按它取；缺键用 dataclass 的默认值，
# 所以老库/手改过的 payload 不会让读侧炸。
_METADATA_FIELDS = (
    "captured_at",
    "latitude",
    "longitude",
    "captured_location",
    "duration_sec",
    "fps",
    "width",
    "height",
    "codec",
    "rotation",
    "device",
    "creation_source",
)

_DERIVED_COLUMNS = (
    "id, file_name, media_type, status, captured_at, gps_latitude, gps_longitude,"
    " parent_asset_id, derived_kind, source_timestamp_sec, source_frame_index, source_scene_index"
)


def _parse_metadata(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _metadata_payload(metadata: VideoMetadata) -> dict[str, object]:
    return {field: getattr(metadata, field) for field in _METADATA_FIELDS}


def _metadata_from_payload(payload: object) -> VideoMetadata | None:
    if not isinstance(payload, dict):
        return None
    known = {field: payload[field] for field in _METADATA_FIELDS if field in payload}
    return VideoMetadata(**known)


def _location_text(latitude: float | None, longitude: float | None) -> str | None:
    """派生物的 ``captured_location``。与 ``GpsPoint.as_text`` 同一个表示（六位小数）。"""
    if latitude is None or longitude is None:
        return None
    return f"{float(latitude):.6f},{float(longitude):.6f}"


def _row_to_derived(row: sqlite3.Row) -> DerivedAsset:
    return DerivedAsset(
        id=row["id"],
        file_name=row["file_name"],
        media_type=row["media_type"],
        status=row["status"],
        captured_at=row["captured_at"],
        captured_location=_location_text(row["gps_latitude"], row["gps_longitude"]),
        parent_asset_id=row["parent_asset_id"],
        derived_kind=row["derived_kind"],
        source_timestamp_sec=row["source_timestamp_sec"],
        source_frame_index=row["source_frame_index"],
        source_scene_index=row["source_scene_index"],
    )


class SqliteVideoRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save_probe(self, asset_id: str, probe: VideoProbe) -> None:
        """**合并写**，不整包覆盖 ``metadata_json``：那里可能已经有别的东西
        （1.0 同一列上还压着反地理、事实 id 等），覆盖会把它们一起抹掉。"""
        with self._database.connect() as connection, connection:
            row = connection.execute("SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            payload = _parse_metadata(row["metadata_json"])
            payload[_PREVIEW_PATH_KEY] = probe.browser_preview_path
            payload[_PREVIEW_ERROR_KEY] = probe.browser_preview_error
            payload[_PROBE_ERROR_KEY] = probe.probe_error
            # metadata 为 None（探测失败）时**不写这个键**：留下一份空元数据会让
            # 「探测过但没读出来」与「读出来是空的」分不开。失败原因在上面那个键里。
            if probe.metadata is not None:
                payload[_VIDEO_METADATA_KEY] = _metadata_payload(probe.metadata)
            connection.execute(
                "UPDATE assets SET metadata_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), asset_id),
            )

    def probe(self, asset_id: str) -> VideoProbe | None:
        with self._database.connect() as connection:
            row = connection.execute("SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            return None
        payload = _parse_metadata(row["metadata_json"])
        if not any(key in payload for key in _VIDEO_KEYS):
            return None
        return VideoProbe(
            metadata=_metadata_from_payload(payload.get(_VIDEO_METADATA_KEY)),
            browser_preview_path=str(payload.get(_PREVIEW_PATH_KEY) or ""),
            browser_preview_error=str(payload.get(_PREVIEW_ERROR_KEY) or ""),
            probe_error=str(payload.get(_PROBE_ERROR_KEY) or ""),
        )

    def list_derived(self, parent_asset_id: str, *, scene_index: int | None = None) -> Sequence[DerivedAsset]:
        where = "parent_asset_id = ? AND derived_kind IS NOT NULL"
        params: list[object] = [parent_asset_id]
        if scene_index is not None:
            where += " AND source_scene_index = ?"
            params.append(int(scene_index))
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_DERIVED_COLUMNS} FROM assets WHERE {where}"
                " ORDER BY source_scene_index, source_timestamp_sec, source_frame_index",
                params,
            ).fetchall()
        return [_row_to_derived(row) for row in rows]

    def cleanup_derivatives(self, parent_asset_id: str) -> int:
        """先删随行数据、再删主行。**顺序是硬约束**：``event_observations`` 与
        ``observations`` 都带外键且连接开着 ``PRAGMA foreign_keys = ON``，反过来直接
        ``IntegrityError``（1.0 的 ``cleanup_asset_derivatives`` 先递归清资产也是这个道理）。

        **S8a 只清到「S8a 能产生的那几行」**：场景事件、派生物本身、以及挂在派生物上的
        观察。派生物真正走完图片流水线之后还会带出人脸实例 / 实体提及 / 地点 / 向量——
        那些的行是 S8b 的 T513 造出来的，级联清理**属于 T514**（否则就是照着还没写的
        写侧猜表结构）。今天多删一张表都可能是错的。
        """
        with self._database.connect() as connection, connection:
            derived_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM assets WHERE parent_asset_id = ? AND derived_kind IS NOT NULL",
                    (parent_asset_id,),
                ).fetchall()
            ]
            for asset_id in derived_ids:
                connection.execute(
                    "DELETE FROM event_observations WHERE observation_id IN"
                    " (SELECT id FROM observations WHERE asset_id = ?)",
                    (asset_id,),
                )
                connection.execute("DELETE FROM observations WHERE asset_id = ?", (asset_id,))
            connection.execute(
                "DELETE FROM event_observations WHERE event_id IN"
                " (SELECT id FROM events WHERE source_type = 'video_scene' AND source_asset_id = ?)",
                (parent_asset_id,),
            )
            connection.execute(
                "DELETE FROM events WHERE source_type = 'video_scene' AND source_asset_id = ?",
                (parent_asset_id,),
            )
            connection.execute(
                "DELETE FROM assets WHERE parent_asset_id = ? AND derived_kind IS NOT NULL",
                (parent_asset_id,),
            )
        return len(derived_ids)
