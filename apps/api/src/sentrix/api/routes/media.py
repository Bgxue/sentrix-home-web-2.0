"""媒体导入与交付。只做 HTTP 映射，业务在 application 层。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from sentrix.api.deps import get_container
from sentrix.api.schemas.media import AssetListResponse, AssetSummary, ImportResponse
from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.application.retrieval.index_visual import IndexAssetVisual
from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import Asset

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

    use_case = ImportAssets(
        assets=container.asset_repository,
        observations=container.observation_repository,
        storage=container.media_storage,
        metadata=container.metadata_reader,
        visual_indexer=IndexAssetVisual(
            embedder=container.visual_embedder,
            vectors=container.vector_repository,
            index=container.vector_index,
        ),
    )
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
    )


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
