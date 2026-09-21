"""Media domain model.

纯数据与规则，零 IO。字段语义取自 1.0 ``backend/db.py`` 的 assets / observations
建表语句，但去掉了 1.0 混在里面的大量派生字段（那些属于观察结果，不属于资产）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MediaType(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"

    @classmethod
    def from_upload(cls, content_type: str | None, file_name: str | None) -> MediaType:
        mime = (content_type or "").split(";", 1)[0].strip().lower()
        for prefix, media in (
            ("image/", cls.IMAGE),
            ("audio/", cls.AUDIO),
            ("video/", cls.VIDEO),
            ("text/", cls.TEXT),
        ):
            if mime.startswith(prefix):
                return media
        return cls.from_file_name(file_name or "")

    @classmethod
    def from_file_name(cls, file_name: str) -> MediaType:
        suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        if suffix in {"jpg", "jpeg", "png", "heic", "heif", "webp", "gif", "tif", "tiff"}:
            return cls.IMAGE
        if suffix in {"mov", "mp4", "m4v", "avi", "mkv"}:
            return cls.VIDEO
        if suffix in {"wav", "mp3", "m4a", "aac", "flac", "ogg"}:
            return cls.AUDIO
        return cls.TEXT


class AssetStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GpsPoint:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude 越界: {self.latitude}")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude 越界: {self.longitude}")

    def as_text(self) -> str:
        """1.0 把 GPS 存成 ``captured_location`` 文本列，沿用该表示以兼容检索。"""
        return f"{self.latitude:.6f},{self.longitude:.6f}"


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    scope_id: str
    file_name: str
    media_type: MediaType
    path: str
    mime_type: str | None
    size_bytes: int
    status: AssetStatus
    content_sha256: str
    captured_at: datetime | None
    captured_location: GpsPoint | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.media_type, MediaType):
            raise ValueError(f"未知 media_type: {self.media_type!r}")


@dataclass(frozen=True, slots=True)
class Observation:
    """一次导入对资产抽取出的原始观察结果。"""

    id: str
    scope_id: str
    asset_id: str
    source_type: str
    captured_at: datetime | None
    confidence: float
    created_at: datetime
    updated_at: datetime
