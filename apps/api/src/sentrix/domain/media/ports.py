"""Domain ports and format rules.

格式支持与转码判定是**领域规则**（产品要求浏览器能打开原图），所以规则本身留在
domain；具体怎么解码由 infrastructure 实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType, Observation

SUPPORTED_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff"}
)
# 浏览器无法原生渲染，必须服务端转码后再交付。
BROWSER_UNRENDERABLE_SUFFIXES = frozenset({".heic", ".heif"})
BROWSER_UNRENDERABLE_MIMES = frozenset({"image/heic", "image/heif"})


def needs_transcode(file_name: str, mime_type: str | None) -> bool:
    suffix = Path(file_name).suffix.lower()
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    return suffix in BROWSER_UNRENDERABLE_SUFFIXES or mime in BROWSER_UNRENDERABLE_MIMES


class AssetRepository(Protocol):
    def add(self, asset: Asset) -> None: ...

    def get(self, asset_id: str) -> Asset | None: ...

    def list_by_scope(self, scope_id: str, limit: int) -> Sequence[Asset]: ...

    def find_by_hash(self, scope_id: str, content_sha256: str) -> Asset | None: ...

    def update_status(self, asset_id: str, status: AssetStatus) -> None: ...


class ObservationRepository(Protocol):
    def add(self, observation: Observation) -> None: ...

    def get(self, observation_id: str) -> Observation | None: ...

    def list_by_asset(self, asset_id: str) -> Sequence[Observation]: ...

    def list_scope(self, scope_id: str, *, limit: int) -> Sequence[Observation]: ...

    def enrich(self, observation_id: str, *, semantics: ObservationSemantics) -> Observation: ...


@dataclass(frozen=True, slots=True)
class ObservationSemantics:
    """一次语义富集写进 Observation 的字段集合。

    单独一个 DTO 而不是一长串关键字参数：``enrich`` 的调用方（VLM 观察器与测试假件）
    要能一次把整包语义构造出来，也方便断言「写了哪些、没写哪些」。
    ``revision`` 由仓储自增，不由调用方给——它是版本号，不是内容。
    """

    caption: str = ""
    place: str = ""
    activity: str = ""
    event_type: str = ""
    ocr_text: str = ""
    objects: tuple[str, ...] = ()
    clothing: tuple[str, ...] = ()
    confidence: float = 0.0


class MediaStorage(Protocol):
    def store(self, asset_id: str, file_name: str, content: bytes) -> Path: ...

    def read(self, asset_id: str) -> bytes: ...

    def path_for(self, asset: Asset) -> Path: ...


class MetadataReader(Protocol):
    """从原始文件中抽取拍摄时间、GPS 与设备。"""

    def read(self, path: Path) -> tuple[datetime | None, GpsPoint | None, str | None]: ...


class PreviewEncoder(Protocol):
    """把原图编码成浏览器可直接渲染的 JPEG。"""

    def encode_jpeg(self, path: Path, *, max_dimension: int, quality: int) -> bytes: ...


class MediaTypeResolver(Protocol):
    def resolve(self, content_type: str | None, file_name: str | None) -> MediaType: ...
