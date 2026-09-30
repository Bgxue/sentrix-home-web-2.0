"""Domain ports and format rules.

格式支持与转码判定是**领域规则**（产品要求浏览器能打开原图），所以规则本身留在
domain；具体怎么解码由 infrastructure 实现。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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

    def latest_ocr_text(self, asset_id: str) -> str:
        """这张照片**最近一次**观察里已经存下来的图内文字。没有就是空串。

        1.0 ``agent_runtime/ocr_tool.py:544-548``：

            SELECT ocr_text FROM observations WHERE asset_id = ?
            ORDER BY updated_at DESC LIMIT 1

        **这是 ``read_photo_text`` 的第一条路，而在 1.0 它也是第一条路**
        （在任何 OCR 引擎之前，命中就返回、零推理）——2.0 的导入期
        ``OpenAIVlmObserver`` 本来就往这一列写「图中文字，1000 字内」，
        所以这一路在 2.0 一直有数据。

        与 ``latest_people_snapshot`` 是两条单列读取，看着像重复，**但不是**：
        ``ocr_text`` 经 ``row_to_observation`` 解析后**原样保留**，所以它本来就能从
        ``Observation.ocr_text`` 拿到；``people_json`` 里那位 ``entity_id``
        **恰好被解析丢掉了**，才需要一条旁路。这一条留在这里是为了让「取最新那条」
        这个口径只有一处（1.0 也是 ``updated_at DESC LIMIT 1``）。
        """
        ...

    def latest_people_snapshot(self, asset_id: str) -> Sequence[PeopleSnapshotEntry]:
        """这张照片**最近一次**观察里的人物快照，带绑定状态（T122 用）。

        1.0 ``tools.py:2578-2581``：``SELECT people_json FROM observations
        WHERE asset_id = ? ORDER BY updated_at DESC LIMIT 1``。取最近一次是 1.0 的
        口径——一张照片可以有多条观察（换过模型、补全过），人名快照以最新那条为准。

        没有观察行时返回**空元组**，与「有一条观察但里面没有人」是同一个结果——
        1.0 也是这么收的（``observation`` 为 ``None`` 时 ``people_values = []``）。
        调用方要区分这两件事的话得自己再查，但那条链路上没有任何地方需要区分。
        """
        ...


@dataclass(frozen=True, slots=True)
class PeopleSnapshotEntry:
    """``observations.people_json`` 里的一条，**原样**。

    写入侧（``entities.py`` 的 ``_append_person_snapshot``）存的是
    ``{"entity_id":…, "name":…, "status":…}``，而读取侧一直只取 ``name``
    （``_json_people``）——「**绑没绑**」这一位在 ``Observation.people``
    （``tuple[str, ...]``）里被丢掉了。

    这个 DTO 存在的唯一理由是把它捡回来：1.0 数「未确认同行者」靠的正是
    ``people_json`` 里**没有** ``entity_id`` 的条目（``tools.py:2582-2588``），
    而那个数字是要回给模型的一句话，也是「不要给未绑定的人安名字」这条纪律的落点。

    **它是只读的旁路，不是 ``Observation.people`` 的替代品**（2026-09-30 裁决）：
    ``Observation.people`` 是 S4 定下的形状，词法投影 / 事件锚点 / 参与者投影三处都读它，
    为一个工具去改它等于让 S4 的模型背 S6 的需求。绑定状态只在这一个入口可见。
    """

    name: str
    # ``""`` 表示这一条**没有**绑定实体。1.0 的判据是 ``person.get("entity_id")``
    # 的真假，所以空串与缺失是同一件事——这里用一个值表达，不留 ``None``：
    # 调用方不该去区分「键不在」与「键是空串」，1.0 也没区分过。
    entity_id: str = ""


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


class PhotoInspector(Protocol):
    """拿一个问题去问一张图。实现在 ``infrastructure/media/photo_inspector.py``。

    **为什么另起一个端口，而不是给 ``VlmObserver`` 加一个 ``question`` 形参。**
    ``VlmObserver.observe`` 是**导入期**的观察器：schema 固定七个字段、跑一次就落库、
    每次导入最多占三次推理。它问的是「这张图里有什么」，与用户当场问的那句话无关——
    而 ``ObservationSemantics`` 那七个字段是**产品要落库的形状**，不是模型能回答的形状；
    拿它去装「这张合影里谁戴了帽子」，只会得到一个恒空的字段。

    ``OpenAIPersonVision`` 也不是它：那是画像 / 瞬间 / 外观三个**专用**入口，各自有各自的
    输出形状与各自的词表。这个端口问的是**用户想知道的那件事**——1.0 的 ``inspect_photo``
    （T123）与 ``read_photo_text``（T124）各自只决定往槽位里填什么、以及怎么解析回来的
    那包 JSON（见 ``domain/media/inspection.py``）。

    与 ``PersonVision`` 同一条口径：模型不可用、读不了图、返回的不是 JSON——一律返回
    ``None``，一次都不抛。工具靠 ``None`` 决定降级，而不是靠 catch。

    **``crop`` 的存在是 1.0 的形状，不是预留。** 指名了目标人物时，1.0 发的是**两张**图：
    裁剪件在前、原图在后（``tools.py:2192`` 的 ``inspect_images.insert(0, crop)``），
    提示词里那句「第一张图（如有）是该人物的定位裁剪图」才成立。``crop`` 读不了就
    **退回只发原图**，不让整次检查失败——1.0 的裁剪块自带 ``except (OSError, ValueError,
    TypeError)`` 并落到 ``target_status = "not_located"``（``tools.py:2186-2200``）。

    ``faces`` 是**原样送进提示词**的人脸清单，本端口一个字段都不改写——见
    ``inspection.render_face_manifest`` 的 docstring。
    """

    @property
    def model_id(self) -> str: ...

    def inspect(
        self,
        path: str | Path,
        question: str,
        *,
        faces: Sequence[Mapping[str, object]] = (),
        target_person: str = "",
        crop: str | Path | None = None,
    ) -> dict[str, object] | None: ...


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
