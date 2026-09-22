"""时间线端点。只做 HTTP 映射：读取在 ``application/memories/read_timeline.py``，
批量导入在 ``application/media/import_album.py``。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from sentrix.api.deps import get_container
from sentrix.api.routes.media import build_import_assets
from sentrix.api.schemas.timeline import (
    ImportBatchRequest,
    ImportBatchResponse,
    ImportFailure,
    TimelineDay,
    TimelineEvent,
    TimelinePhoto,
    TimelineResponse,
)
from sentrix.application.media.import_album import AlbumImportRequest, ImportAlbum
from sentrix.application.memories.read_timeline import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ReadTimeline,
)
from sentrix.application.memories.read_timeline import (
    TimelineEvent as TimelineEventResult,
)
from sentrix.bootstrap.container import Container

log = logging.getLogger("sentrix.api.timeline")

router = APIRouter(tags=["timeline"])


def _event_payload(event: TimelineEventResult) -> TimelineEvent:
    return TimelineEvent(
        id=event.id,
        title=event.title,
        event_type=event.event_type,
        time_start=event.time_start.isoformat() if event.time_start else None,
        time_end=event.time_end.isoformat() if event.time_end else None,
        admin_district=event.admin_district,
        place=event.place,
        activity=event.activity,
        summary=event.summary,
        confidence=event.confidence,
        photos=[
            TimelinePhoto(
                asset_id=photo.asset_id,
                file_name=photo.file_name,
                captured_at=photo.captured_at.isoformat() if photo.captured_at else None,
                place=photo.place,
            )
            for photo in event.photos
        ],
    )


def _read_timeline(container: Container) -> ReadTimeline:
    return ReadTimeline(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        places=container.asset_place_repository,
    )


@router.get("/timeline")
def read_timeline(
    scope_id: str = "home-default",
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    place: str = Query("", max_length=60, description="地点筛选，服务端按地点/行政区匹配"),
    container: Container = Depends(get_container),
) -> TimelineResponse:
    result = _read_timeline(container).execute(
        scope_id=scope_id, limit=limit, offset=offset, place=place.strip()
    )
    return TimelineResponse(
        scope_id=result.scope_id,
        total=result.total,
        offset=result.offset,
        limit=result.limit,
        days=[
            TimelineDay(date=day.date, events=[_event_payload(item) for item in day.events])
            for day in result.days
        ],
    )


@router.get("/events/{event_id}")
def get_event(event_id: str, container: Container = Depends(get_container)) -> TimelineEvent:
    event = _read_timeline(container).get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return _event_payload(event)


@router.post("/imports", status_code=201)
def import_album(
    request: ImportBatchRequest,
    container: Container = Depends(get_container),
) -> ImportBatchResponse:
    """整册导入。**同步执行**：2.0 没有任务队列，装作异步只会把「跑到哪了」藏起来。

    整册导入要串行走模型调用，所以这个请求会跑很久——客户端的超时必须自己调大，
    这一点写在 ``docs/runbooks/geonames-data.md`` 的相邻章节（导入一节）。
    """
    album = ImportAlbum(importer=build_import_assets(container, observe=True))
    try:
        result = album.execute(
            AlbumImportRequest(scope_id=request.scope_id, directory=request.directory, limit=request.limit)
        )
    except NotADirectoryError:
        raise HTTPException(status_code=400, detail="directory not found") from None
    return ImportBatchResponse(
        scope_id=result.scope_id,
        directory=result.directory,
        imported=result.imported,
        deduplicated=result.deduplicated,
        events=result.events,
        lexical_terms=result.lexical_terms,
        failures=[ImportFailure(file_name=item.file_name, reason=item.reason) for item in result.failures],
    )
