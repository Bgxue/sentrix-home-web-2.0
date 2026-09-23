"""人脸簇 / 实例 / 原型仓储。SQL 只出现在这一层。

对应 1.0 ``backend/db.py`` 的 ``list_face_clusters`` :5133、``_refresh_face_prototypes``
:4922、``get_face_instance`` :4467、``list_face_prototypes`` :4547。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sentrix.domain.identity.models import (
    ENTITY_STATUS_PENDING,
    PENDING_CLUSTER_NAME,
    FaceClusterRecord,
    FaceInstanceRecord,
    FacePrototypeRecord,
)
from sentrix.infrastructure.persistence.database import Database

_CLUSTER_COLUMNS = (
    "fc.id, fc.scope_id, fc.status, fc.entity_id, fc.representative_embedding_json,"
    " fc.member_count, fc.confidence, fc.revision, fc.created_at, fc.updated_at,"
    " e.canonical_name, e.family_role, e.status AS entity_status"
)
_INSTANCE_COLUMNS = (
    "fi.id, fi.asset_id, fi.observation_id, fi.cluster_id, fi.bbox_json, fi.embedding_json,"
    " fi.detection_confidence, fi.quality, fi.pose_json, fi.pose_bucket, fi.embedding_model,"
    " fi.embedding_version, fi.embedding_quality_signal, fi.validity, fi.area_ratio,"
    " fi.sharpness, fi.created_at, a.path AS asset_path, a.file_name, a.mime_type,"
    " a.media_type, a.scope_id AS asset_scope_id"
)
_PROTOTYPE_COLUMNS = (
    "id, cluster_id, face_instance_id, pose_bucket, embedding_json, quality, model_name, model_version"
)


def _new_id(prefix: str) -> str:
    """1.0 ``make_id``：``f"{prefix}_{uuid4().hex[:12]}"``。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _vector(value: str | None) -> tuple[float, ...]:
    try:
        items = json.loads(value or "[]")
    except json.JSONDecodeError:
        return ()
    return tuple(float(item) for item in items) if isinstance(items, list) else ()


def _row_to_cluster(row: sqlite3.Row) -> FaceClusterRecord:
    # 未确认的簇不许把内部名字与家庭角色漏出去（1.0 ``app.py:1976`` 在路由层做这件事，
    # 2.0 落在出口处）。判据是**实体**状态而不是簇状态：簇可以已经 confirmed 而实体还在
    # pending，那种情况下名字同样还没被用户认可。
    pending = row["entity_status"] == ENTITY_STATUS_PENDING
    return FaceClusterRecord(
        id=row["id"],
        scope_id=row["scope_id"],
        status=row["status"],
        entity_id=row["entity_id"],
        representative_embedding=_vector(row["representative_embedding_json"]),
        member_count=row["member_count"],
        confidence=row["confidence"],
        revision=row["revision"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
        canonical_name=PENDING_CLUSTER_NAME if pending else row["canonical_name"],
        family_role=None if pending else row["family_role"],
        entity_status=row["entity_status"],
    )


def _row_to_instance(row: sqlite3.Row) -> FaceInstanceRecord:
    return FaceInstanceRecord(
        id=row["id"],
        asset_id=row["asset_id"],
        observation_id=row["observation_id"],
        cluster_id=row["cluster_id"],
        bbox=_vector(row["bbox_json"]),
        embedding=_vector(row["embedding_json"]),
        detection_confidence=row["detection_confidence"],
        quality=row["quality"],
        pose=_vector(row["pose_json"]),
        pose_bucket=row["pose_bucket"],
        embedding_model=row["embedding_model"],
        embedding_version=row["embedding_version"],
        embedding_quality_signal=row["embedding_quality_signal"],
        validity=row["validity"],
        area_ratio=row["area_ratio"],
        sharpness=row["sharpness"],
        created_at=_parse_datetime(row["created_at"]),
        # 1.0 ``get_face_instance`` 从 asset 取 scope，缺省 ``home-default``。
        scope_id=row["asset_scope_id"] or "home-default",
        asset_path=row["asset_path"],
        file_name=row["file_name"],
        mime_type=row["mime_type"] or "",
        media_type=row["media_type"],
    )


def _row_to_prototype(row: sqlite3.Row) -> FacePrototypeRecord:
    return FacePrototypeRecord(
        id=row["id"],
        cluster_id=row["cluster_id"],
        face_instance_id=row["face_instance_id"],
        pose_bucket=row["pose_bucket"],
        embedding=_vector(row["embedding_json"]),
        quality=row["quality"],
        model_name=row["model_name"],
        model_version=row["model_version"],
    )


class SqliteFaceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def list_clusters(self, *, status: str | None = None) -> Sequence[FaceClusterRecord]:
        clause = "fc.status = ?" if status else "fc.status != 'rejected'"
        params: list[object] = [status] if status else []
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_CLUSTER_COLUMNS} FROM face_clusters fc"
                " LEFT JOIN entities e ON e.id = fc.entity_id"
                # INNER JOIN 有意为之：没有记忆空间行的 scope 查不到任何簇。1.0 就是这样，
                # 而「默认空间没建过行」在 1.0 是真实发生过的——那时列表是空的，不是全量。
                " JOIN memory_spaces ms ON ms.id = fc.scope_id"
                f" WHERE {clause} AND ms.include_in_people = 1"
                " ORDER BY fc.updated_at DESC",
                params,
            ).fetchall()
        return [_row_to_cluster(row) for row in rows]

    def get_instance(self, instance_id: str) -> FaceInstanceRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                f"SELECT {_INSTANCE_COLUMNS} FROM face_instances fi"
                " JOIN assets a ON a.id = fi.asset_id WHERE fi.id = ?",
                (instance_id,),
            ).fetchone()
        return _row_to_instance(row) if row else None

    def list_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                f"SELECT {_PROTOTYPE_COLUMNS} FROM face_prototypes WHERE cluster_id = ?"
                " ORDER BY quality DESC, pose_bucket ASC",
                (cluster_id,),
            ).fetchall()
        return [_row_to_prototype(row) for row in rows]

    def refresh_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]:
        now = datetime.now(UTC).isoformat()
        with self._database.connect() as connection, connection:
            # ``quality DESC, created_at ASC`` 两个键都在用：第一个选最清晰的，第二个在
            # 同质量时选先到的那张——少第二个键，桶内容会随插入顺序漂移。
            rows = connection.execute(
                "SELECT id, pose_bucket, embedding_json, quality, embedding_model, embedding_version"
                " FROM face_instances WHERE cluster_id = ? ORDER BY quality DESC, created_at ASC",
                (cluster_id,),
            ).fetchall()
            best: dict[str, sqlite3.Row] = {}
            for row in rows:
                best.setdefault(row["pose_bucket"] or "unknown", row)
            connection.execute("DELETE FROM face_prototypes WHERE cluster_id = ?", (cluster_id,))
            for bucket, row in best.items():
                connection.execute(
                    "INSERT INTO face_prototypes"
                    " (id, cluster_id, face_instance_id, pose_bucket, embedding_json, quality,"
                    "  model_name, model_version, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        _new_id("prototype"),
                        cluster_id,
                        row["id"],
                        bucket,
                        row["embedding_json"],
                        float(row["quality"] or 0),
                        row["embedding_model"] or "unknown",
                        row["embedding_version"] or "unknown",
                        now,
                        now,
                    ),
                )
            representative = max(best.values(), key=lambda item: float(item["quality"] or 0), default=None)
            connection.execute(
                "UPDATE face_clusters SET representative_embedding_json = ?, member_count = ?,"
                " confidence = MAX(confidence, ?), revision = revision + 1, updated_at = ?"
                " WHERE id = ?",
                (
                    representative["embedding_json"] if representative else "[]",
                    len(rows),
                    float(representative["quality"] or 0) if representative else 0.0,
                    now,
                    cluster_id,
                ),
            )
        return self.list_prototypes(cluster_id)
