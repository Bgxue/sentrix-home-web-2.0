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

from sentrix.domain.media.models import (
    Asset,
    AssetStatus,
    GpsPoint,
    MediaType,
    Observation,
    ObservationFact,
)

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

    def list_by_scope(self, scope_id: str | None, limit: int) -> Sequence[Asset]:
        """``scope_id`` 为 ``None`` 表示**跨全部相册**（1.0 ``db.py:1514`` 的 ``if scope_id:``）。

        这不是「默认相册」——空与 ``"home-default"`` 是两件事：前者是用户在界面上选了
        「全部相册」，后者是一个真叫 home-default 的空间。
        """
        ...

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
    # 模型自报的候选事实。1.0 的 ``analyze_image`` 让模型回一个 ``facts`` 数组
    # （``model_clients.py:1130-1198`` 的提示词原文），2.0 的 S4 明确不搬它，
    # 于是 ``MaintainFacts`` 的候选在 S4 的生产装配下恒为空元组——门禁全开也写不出一条
    # 事实。用户裁决④选了「按 1.0 原样：模型自报」，这个字段就是那条来源。
    facts: tuple[ObservationFact, ...] = ()
    confidence: float = 0.0


class VlmObserver(Protocol):
    """一张图 → 一次视觉观察。**最多 3 次模型调用**（首轮 / 补全 / 中文化），串行。

    1.0 的三段结构在 ``backend/model_clients.py:1130-1198`` 的 ``analyze_image``。
    2.0 保留同一结构与同一上限，但只落 ``ObservationSemantics`` 的七个字段——
    1.0 的 semantic 受控词表与 ``detail`` 属于 S6 的精读。

    模型不可用、读不了图、返回的不是 JSON——一律返回 ``None``。观察是**增强**，
    不是导入的前置条件。
    """

    @property
    def model_id(self) -> str: ...

    def observe(self, path: Path) -> ObservationSemantics | None: ...


class AssetObserver(Protocol):
    """把一张已导入的 Asset 变成一次语义观察，并把结果写回 Observation。

    与 ``AssetVisualIndexer`` / ``AssetPlaceIndex`` 同构：导入用例依赖这个抽象，
    不依赖具体的观察器与仓储。
    """

    def observe(self, asset: Asset, observation: Observation) -> Observation | None: ...


class MediaStorage(Protocol):
    def store(self, asset_id: str, file_name: str, content: bytes) -> Path: ...

    def read(self, asset_id: str) -> bytes: ...

    def path_for(self, asset: Asset) -> Path: ...


class MetadataReader(Protocol):
    """从原始文件中抽取拍摄时间、GPS 与设备。"""

    def read(self, path: Path) -> tuple[datetime | None, GpsPoint | None, str | None]: ...


class PreviewEncoder(Protocol):
    """把原图编码成浏览器可直接渲染的 JPEG。

    ``crop_jpeg`` 也在这里而不是另立一个端口：人脸裁剪与预览是同一件事的两个取景
    ——打开一张图、按 EXIF 转置、编码成 JPEG。分开会得到两个各自压一次参数的适配器，
    而它们必须用同一套转置口径，否则簇里的样本脸会与预览里的位置对不上。
    """

    def encode_jpeg(self, path: Path, *, max_dimension: int, quality: int) -> bytes: ...

    def crop_jpeg(
        self, path: Path | str, bbox: Sequence[float], *, max_dimension: int, quality: int
    ) -> bytes: ...


class MediaTypeResolver(Protocol):
    def resolve(self, content_type: str | None, file_name: str | None) -> MediaType: ...
