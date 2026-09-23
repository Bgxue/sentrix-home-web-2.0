"""媒体导入与交付。只做 HTTP 映射，业务在 application 层。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from sentrix.api.deps import get_container
from sentrix.api.schemas.media import AssetListResponse, AssetSummary, ImportResponse
from sentrix.application.identity.enroll_face import EnrollFace
from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.application.media.index_place import IndexAssetPlace
from sentrix.application.media.observe_asset import ObserveAsset
from sentrix.application.memories.assign_events import AssignEvents, EventVisualSimilarity
from sentrix.application.memories.maintain_facts import MaintainFacts
from sentrix.application.retrieval.index_visual import IndexAssetVisual
from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import Asset, Observation

router = APIRouter(tags=["media"])

PREVIEW_MAX_DIMENSION = 1600
PREVIEW_QUALITY = 85


def to_summary(asset: Asset) -> AssetSummary:
    return AssetSummary(
        id=asset.id,
        file_name=asset.file_name,
        media_type=asset.media_type.value,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        status=asset.status.value,
        captured_at=asset.captured_at.isoformat() if asset.captured_at else None,
        gps=(
            {"latitude": asset.captured_location.latitude, "longitude": asset.captured_location.longitude}
            if asset.captured_location
            else None
        ),
    )


@router.post("/media/import", status_code=201)
async def import_media(
    file: UploadFile = File(...),
    scope_id: str = Form("home-default"),
    container: Container = Depends(get_container),
) -> ImportResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    use_case = build_import_assets(container, observe=False)
    result = use_case.execute(
        ImportRequest(
            scope_id=scope_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type,
            content=content,
        )
    )
    return ImportResponse(
        asset_id=result.asset_id,
        observation_id=result.observation_id,
        media_type=result.media_type.value,
        status=result.status.value,
        deduplicated=result.deduplicated,
        indexed=result.indexed,
        placed=result.placed,
        event_id=result.event_id,
    )


def build_import_assets(container: Container, *, observe: bool) -> ImportAssets:
    """把导入用例的协作者接起来。**只在这里接一次**。

    单张上传与整册导入共用同一条链：分开接的话，「整册少了词法投影」这类差异会以
    「单张能搜到、批量搜不到」的形式出现，而那种 bug 在两端各看一半时都看不出来。

    ``observe`` 控制要不要为每张图付视觉观察的模型调用（首轮 + 补全 + 中文化，
    最多 3 次）。单张上传走快路径（1.0 的 ``_commit_fast_image`` 同样时机），
    整册导入才接观察器。
    """
    assigner = AssignEvents(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        places=container.asset_place_repository,
        visual_similarity=EventVisualSimilarity(
            vectors=container.vector_repository,
            observations=container.observation_repository,
            assets=container.asset_repository,
        ),
        # 事件打分的 person 分量（权重 0.20）的两个来源。装配点**只有这里**：
        # 单张上传与整册导入共用同一条链，分开接会让「整册少了人物桥」以
        # 「批量导入时事件分得更碎」的形式出现，而那种差异在两端口各看一半时看不出来。
        confirmed_people=container.entity_repository.confirmed_people_for_observation,
        confirmed_event_people=container.entity_repository.confirmed_people_for_event,
    )
    return ImportAssets(
        assets=container.asset_repository,
        observations=container.observation_repository,
        storage=container.media_storage,
        metadata=container.metadata_reader,
        visual_indexer=IndexAssetVisual(
            embedder=container.visual_embedder,
            vectors=container.vector_repository,
            index=container.vector_index,
        ),
        place_indexer=IndexAssetPlace(
            resolver=container.place_resolver, places=container.asset_place_repository
        ),
        observer=(
            ObserveAsset(
                observer=container.vlm_observer,
                observations=container.observation_repository,
            )
            if observe
            else None
        ),
        event_assigner=lambda observation: _event_id(assigner, observation),
        face_enroller=_face_enroller(container),
        fact_maintainer=_fact_maintainer(container),
        lexical_index=container.lexical_index,
    )


def _face_enroller(container: Container) -> Callable[[Asset, Observation], int]:
    """人脸登记：检测 → 在线匹配，逐张脸走一遍 ``EnrollFace``。

    **它必须排在事件归并之前**——1.0 在 ``pipeline.py:316-320`` 用注释钉过这件事：
    与已确认的人相像的脸会在这一步写下 ``entity_mentions``，而事件归并的
    ``person_score`` 读的正是它。顺序写在 ``ImportAssets.execute`` 里，这里只负责接。

    权重没装的机器上 ``detect`` 返回空表，这条链路整个不生效——**不需要在这里判空**：
    让它自然地「没有脸」比在这里分叉出第二条路径更省事，也更难写错。
    """
    enroll = EnrollFace(
        faces=container.face_repository,
        entities=container.entity_repository,
        vectors=container.vector_repository,
        assets=container.asset_repository,
    )

    def run(asset: Asset, observation: Observation) -> int:
        faces = container.face_detector.detect(asset.path)
        for face in faces:
            enroll.enroll(asset.id, observation.id, face)
        return len(faces)

    return run


def _fact_maintainer(container: Container) -> Callable[[Observation], tuple[str, ...]]:
    """事实写入的接线。**这块在 S4 写不出任何一行，两个原因都不在这里**。

    1. **候选事实没有来源**。1.0 的候选来自模型自报的 ``facts``
       （``backend/model_clients.py:1130-1198``），2.0 的观察器明确不搬它
       （``s4-deviations.md`` 第 30 条）。所以下面的 ``candidates`` 恒为空元组。
    2. **门禁关着**。1.0 的 ``is_confirmed_person_name`` 查 ``entities`` 表里
       ``status='confirmed'`` 的人物（``db.py:1494-1503``），而 2.0 的身份层是 S5。
       ``MaintainFacts`` 的 ``confirmed_people`` 默认空集，与 ``AssignEvents``
       同名参数的 S4 取值一致。

    接线的意义在于：``domain/memories/facts.py`` 的三条纯规则与
    ``repositories/facts.py`` 从这一版起**有生产调用方**（1.0 的
    ``_commit_semantic_image`` 整段不调 ``maintain_fact``，两条路径不一致是用户裁决
    第 3 条要修的）。「按设计为空」与「没人调所以恒空」是两件事，前者可断言、后者
    只能靠读代码发现——这条注释与 ``tests/integration/test_maintain_facts.py``
    都按前者写。
    **放宽门禁或补候选来源都是用户裁决点**，不在接线里自己定。
    """
    maintain = MaintainFacts(
        facts=container.fact_repository,
        candidates=lambda _observation: (),
    )
    return maintain.maintain


def _event_id(assigner: AssignEvents, observation: Observation) -> str:
    return assigner.execute(observation).event.id


@router.get("/assets")
def list_assets(
    scope_id: str = "home-default",
    limit: int = 200,
    container: Container = Depends(get_container),
) -> AssetListResponse:
    assets = container.asset_repository.list_by_scope(scope_id, min(limit, 1000))
    return AssetListResponse(items=[to_summary(asset) for asset in assets])


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, container: Container = Depends(get_container)) -> AssetSummary:
    asset = container.asset_repository.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return to_summary(asset)


def deliver_asset(asset_id: str, *, original: bool, container: Container) -> Response:
    """按 ``original`` 交付一张资产：``True`` 原文件，``False`` 服务端转码的 JPEG 预览。

    抽出来是因为两条路由共用同一份「怎么把一张已授权的图交出去」：``/assets/{id}/file``
    与助手侧的 ``/assistant/result-sets/{id}/photo``——1.0 的 ``asset_file``
    （``app.py:2450``）也是被这两处共用的。转码参数只留一份。
    """
    asset = container.asset_repository.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    path = Path(asset.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="media file missing on disk")
    if original:
        return FileResponse(path, media_type=asset.mime_type or "application/octet-stream")
    payload = container.preview_encoder.encode_jpeg(
        path, max_dimension=PREVIEW_MAX_DIMENSION, quality=PREVIEW_QUALITY
    )
    return Response(content=payload, media_type="image/jpeg")


@router.get("/assets/{asset_id}/file")
def get_asset_file(asset_id: str, container: Container = Depends(get_container)) -> Response:
    return deliver_asset(asset_id, original=True, container=container)


@router.get("/assets/{asset_id}/preview")
def get_asset_preview(asset_id: str, container: Container = Depends(get_container)) -> Response:
    return deliver_asset(asset_id, original=False, container=container)
