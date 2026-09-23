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

    def distance_km(self, other: GpsPoint) -> float:
        """大圆距离（公里）。1.0 ``backend/db.py:297 gps_distance_km``，地球半径一值不差。

        事件候选的 GPS 衰减、地点解析的最近邻都读它，所以放在点上而不是某一边的
        工具模块里——两点之间的距离是点自己的性质。
        """
        latitude_one, longitude_one = math.radians(self.latitude), math.radians(self.longitude)
        latitude_two, longitude_two = math.radians(other.latitude), math.radians(other.longitude)
        delta_latitude = latitude_two - latitude_one
        delta_longitude = longitude_two - longitude_one
        a = (
            math.sin(delta_latitude / 2) ** 2
            + math.cos(latitude_one) * math.cos(latitude_two) * math.sin(delta_longitude / 2) ** 2
        )
        return 6371.0088 * 2 * math.asin(math.sqrt(a))


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


# 1.0 ``model_clients.py:1190``：``normalize_fact_confidences(parsed.get("facts"), 0.65)``。
# 模型没给 confidence 时用它。它在**观察器**这一层，与 ``maintain_facts`` 那一层
# 处理「观察整体置信度」的兜底不是同一个数。
MODEL_FACT_CONFIDENCE = 0.65

# 1.0 ``model_clients.py`` 的提示词原文：``facts 最多8项``。
MODEL_FACT_LIMIT = 8


@dataclass(frozen=True, slots=True)
class ObservationFact:
    """模型自报的一条候选事实。1.0 ``model_clients.py`` 的 ``facts`` 数组元素。

    **候选由模型自报，写法由规则决定**（用户裁决④）。这两句不矛盾，是两件事：
    模型只负责说「画面上这个人好像去过赵县」，至于这条候选落成 ``active`` 还是
    ``pending``、要不要合并证据、要不要记 ``supersedes``，全在
    ``domain/memories/facts.py`` 的 ``decide_fact_write`` 里。

    形状定义在 media 而不是 memories，是因为它**是观察的一部分**（跟着
    ``ObservationSemantics`` 一起从观察器出来、跟着 Observation 一起落库）；
    ``domain/memories/events.py`` 反过来要 import ``GpsPoint``，两边互相 import 就成环了。
    """

    subject: str
    predicate: str
    object: str
    confidence: float = MODEL_FACT_CONFIDENCE


@dataclass(frozen=True, slots=True)
class Observation:
    """一次导入对资产抽取出的观察结果。

    S4 把 1.0 散在 ``observations.*_json`` 里的语义字段提升成正式成员：词法索引与
    事件打分都直接读它们，读之前先去解析一个 JSON blob 是 1.0 反复出错的地方
    （``MetadataRetriever`` 的 place 预筛就因为拿到的是 JSON 字符串而整条失效）。

    ``revision`` 是**词法投影的过期依据**：同一条 Observation 被重新富集时要能区分
    「同一版本重跑」与「新版本覆盖」，1.0 没有这个字段，投影只能删了重插。
    """

    id: str
    scope_id: str
    asset_id: str
    source_type: str
    captured_at: datetime | None
    confidence: float
    created_at: datetime
    updated_at: datetime
    caption: str = ""
    place: str = ""
    activity: str = ""
    event_type: str = ""
    ocr_text: str = ""
    objects: tuple[str, ...] = ()
    clothing: tuple[str, ...] = ()
    # 模型自报的候选事实（1.0 ``observations`` 上没有这一列，它们只活在
    # ``assets.metadata_json["fact_ids"]`` 与那次调用的内存里）。2.0 单开一列而不是塞进
    # ``detail_json``：事实是**有结构**的东西，``subject/predicate/object`` 三个字段直接
    # 被 ``is_writable_fact`` 与 ``decide_fact_write`` 读，摊平进一个无结构 blob 之后
    # 那两个纯规则就只能靠字符串约定去取字段了。
    facts: tuple[ObservationFact, ...] = ()
    revision: int = 1

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError(f"revision 从 1 开始，收到 {self.revision}")

    @property
    def has_semantics(self) -> bool:
        """VLM 语义是否已经落上。1.0 用「caption 非空」当这个判据。"""
        return bool(self.caption or self.place or self.activity or self.event_type)
