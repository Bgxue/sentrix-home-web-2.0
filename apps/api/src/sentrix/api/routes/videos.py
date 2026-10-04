"""视频端点（S8a T504）：源片详情 / 场景列表 / 场景详情 / 重跑。

**只做 HTTP 映射**：读取在仓储（``EventRepository.list_video_scenes`` 与
``VideoRepository.list_derived``），业务在 ``application/media/import_assets.py``。

响应形状照 1.0 ``backend/app.py:1448-1481`` 四条路由，**前缀不同**：2.0 的 v1 路由挂在
``/api/v1`` 下（``main.py`` 的 ``APIRouter(prefix="/api/v1")``），1.0 是 ``/api``。

**S8a 里 ``scenes`` 恒为空数组**：建场景是 S8b（T512）的事。这四条路由现在就把形状
定下来，S8b 只管往里填数据；读侧不随着写侧一起长，是因为「场景成型后能不能读回来」
正是 S8a 的血缘列要保证的事（``s8-deviations.md`` §0）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from sentrix.api.deps import get_container
from sentrix.api.schemas.videos import (
    KeyframeAssetPayload,
    SourceVideoPayload,
    VideoDetailResponse,
    VideoMetadataPayload,
    VideoPayload,
    VideoReprocessResponse,
    VideoSceneDetailResponse,
    VideoScenePayload,
    VideoScenesResponse,
)
from sentrix.bootstrap.container import Container
from sentrix.domain.media.models import Asset, AssetStatus, DerivedAsset, MediaType, VideoMetadata
from sentrix.domain.media.ports import VideoProbe
from sentrix.domain.memories.events import EventRecord

router = APIRouter(tags=["videos"])

VIDEO_SCENE_SOURCE_TYPE = "video_scene"

# 重跑之后资产停在这个状态。**它不是 AssetStatus 的成员**——1.0 的 ``video-queued``
# 是它的视频阶段名，2.0 没有那一列（见 ``reprocess_video`` 的注释）。
REPROCESS_STATUS = "video-queued"


def _metadata_payload(metadata: VideoMetadata | None) -> VideoMetadataPayload | None:
    if metadata is None:
        return None
    return VideoMetadataPayload(
        captured_at=metadata.captured_at,
        latitude=metadata.latitude,
        longitude=metadata.longitude,
        captured_location=metadata.captured_location,
        duration_sec=metadata.duration_sec,
        fps=metadata.fps,
        width=metadata.width,
        height=metadata.height,
        codec=metadata.codec,
        rotation=metadata.rotation,
        device=metadata.device,
        creation_source=metadata.creation_source,
    )


def _video_payload(asset: Asset, probe: VideoProbe | None) -> VideoPayload:
    location = asset.captured_location
    return VideoPayload(
        id=asset.id,
        scope_id=asset.scope_id,
        file_name=asset.file_name,
        media_type=asset.media_type.value,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        status=asset.status.value,
        captured_at=asset.captured_at.isoformat() if asset.captured_at else None,
        captured_location=location.as_text() if location else None,
        gps=({"latitude": location.latitude, "longitude": location.longitude} if location else None),
        metadata=_metadata_payload(probe.metadata if probe else None),
        browser_preview_path=probe.browser_preview_path if probe else "",
        browser_preview_error=probe.browser_preview_error if probe else "",
        probe_error=probe.probe_error if probe else "",
    )


def _scene_payload(event: EventRecord) -> VideoScenePayload:
    return VideoScenePayload(
        id=event.id,
        scope_id=event.scope_id,
        title=event.title,
        event_type=event.event_type,
        time_start=event.time_start.isoformat() if event.time_start else None,
        time_end=event.time_end.isoformat() if event.time_end else None,
        place=event.place,
        activity=event.activity,
        summary=event.summary,
        confidence=event.confidence,
        status=event.status,
        source_type=event.source_type,
        source_asset_id=event.source_asset_id,
        source_scene_index=event.source_scene_index,
        source_start_sec=event.source_start_sec,
        source_end_sec=event.source_end_sec,
    )


def _keyframe_payload(derived: DerivedAsset) -> KeyframeAssetPayload:
    return KeyframeAssetPayload(
        id=derived.id,
        file_name=derived.file_name,
        media_type=derived.media_type,
        status=derived.status,
        captured_at=derived.captured_at,
        captured_location=derived.captured_location,
        parent_asset_id=derived.parent_asset_id,
        derived_kind=derived.derived_kind,
        source_timestamp_sec=derived.source_timestamp_sec,
        source_frame_index=derived.source_frame_index,
        source_scene_index=derived.source_scene_index,
    )


def _require_video(asset_id: str, container: Container) -> Asset:
    """取一个**视频**资产，否则 404。1.0 三条读路由的同一个判据（``app.py:1450`` 等）。"""
    asset = container.asset_repository.get(asset_id)
    if asset is None or asset.media_type is not MediaType.VIDEO:
        raise HTTPException(status_code=404, detail="video asset not found")
    return asset


@router.get("/videos/{asset_id}")
def get_video(asset_id: str, container: Container = Depends(get_container)) -> VideoDetailResponse:
    asset = _require_video(asset_id, container)
    return VideoDetailResponse(
        video=_video_payload(asset, container.video_repository.probe(asset_id)),
        scenes=[_scene_payload(item) for item in container.event_repository.list_video_scenes(asset_id)],
    )


@router.get("/videos/{asset_id}/scenes")
def get_video_scenes(asset_id: str, container: Container = Depends(get_container)) -> VideoScenesResponse:
    _require_video(asset_id, container)
    return VideoScenesResponse(
        video_asset_id=asset_id,
        scenes=[_scene_payload(item) for item in container.event_repository.list_video_scenes(asset_id)],
    )


@router.get("/video-scenes/{scene_id}")
def get_video_scene(scene_id: str, container: Container = Depends(get_container)) -> VideoSceneDetailResponse:
    event = container.event_repository.get(scene_id)
    if event is None or event.source_type != VIDEO_SCENE_SOURCE_TYPE:
        raise HTTPException(status_code=404, detail="video scene not found")
    # 派生物按 ``(source_asset_id, source_scene_index)`` 找——迁移 0010 为这对键
    # 各建了一个索引，正是「跳回原片」这条链的落点。
    keyframes = container.video_repository.list_derived(
        event.source_asset_id, scene_index=event.source_scene_index
    )
    source_video = container.asset_repository.get(event.source_asset_id)
    source_probe = container.video_repository.probe(event.source_asset_id) if source_video else None
    source_meta = source_probe.metadata if source_probe else None
    return VideoSceneDetailResponse(
        event=_scene_payload(event),
        keyframe_assets=[_keyframe_payload(item) for item in keyframes],
        source_video=(
            SourceVideoPayload(
                id=source_video.id,
                file_name=source_video.file_name,
                media_type=source_video.media_type.value,
                mime_type=source_video.mime_type,
                status=source_video.status.value,
                captured_at=(source_video.captured_at.isoformat() if source_video.captured_at else None),
                captured_location=(
                    source_video.captured_location.as_text() if source_video.captured_location else None
                ),
                size_bytes=source_video.size_bytes,
                duration_sec=source_meta.duration_sec if source_meta else 0.0,
            )
            if source_video
            else None
        ),
    )


@router.post("/videos/{asset_id}/reprocess", status_code=202)
def reprocess_video(asset_id: str, container: Container = Depends(get_container)) -> VideoReprocessResponse:
    """清派生数据 + 重置状态，**S8a 阶段没有抽取链可跑**（那是 S8b 的 T511–T514）。

    1.0 这里还有一句 ``background_tasks.add_task(process_asset, asset_id)``
    （``app.py:1477``）——那正是 S8a 做不了的一半。**不装作异步**：2.0 没有任务队列，
    起一个查无此人的后台任务只会把「跑到哪了」藏起来（``routes/timeline.py`` 的
    ``import_album`` 也是为这个选择同步的）。所以这里如实做两件现在成立的事：

    1. ``cleanup_derivatives`` 清掉这个源片的派生物与场景事件；
    2. 状态回到 ``queued``——**S8b 的抽取链会从这个状态上接走它**。

    ``status`` 回给客户端的是 1.0 的那个阶段名 ``video-queued``；资产行上的状态是
    2.0 的 ``AssetStatus.QUEUED``。两者不是一个词表（2.0 没有 ``video_stage`` 列），
    但指同一件事：**排队等着被处理**。S8a 里没有消费者把它接走，这一点写在
    ``s8-deviations.md`` §4，不藏的。
    """
    _require_video(asset_id, container)
    container.video_repository.cleanup_derivatives(asset_id)
    container.asset_repository.update_status(asset_id, AssetStatus.QUEUED)
    return VideoReprocessResponse(accepted=True, asset_id=asset_id, status=REPROCESS_STATUS)
