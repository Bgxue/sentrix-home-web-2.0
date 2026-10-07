"""时间线端点。只做 HTTP 映射：读取在 ``application/memories/read_timeline.py``，
批量导入在 ``application/media/import_album.py``。"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, Query

from sentrix.api.deps import get_container
from sentrix.api.routes.family import TRIGGER_INGEST, run_family_graph_for_scope
from sentrix.api.routes.insights import run_insights_for_scope
from sentrix.api.routes.media import build_import_assets
from sentrix.api.schemas.family import FamilyRunResponse
from sentrix.api.schemas.timeline import (
    ImportBatchRequest,
    ImportBatchResponse,
    ImportFailure,
    InsightCounts,
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
from sentrix.application.memories.summarize_event import SummarizeEvent
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


def _summarize_event(container: Container) -> SummarizeEvent:
    """整册导入的批末收尾用的总结器。

    ``model`` 传的是 ``container.chat_model``——它是 **8100 上的文本模型**，
    不是 8101 的嵌入器。``SummarizeEvent`` 拿不到模型或模型报错时都会退回
    ``fallback_projection``（确定性摘要），所以端点不可用不会让批末卡住或让事件
    重新变回占位值。``model is None`` 才是「接了也白接」的那条早退，这里不会发生。
    """
    return SummarizeEvent(
        events=container.event_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        model=container.chat_model,
    )


@router.get("/timeline")
def read_timeline(
    # 空串与不传都表示**全部相册**（1.0 的 `if scope_id:`）。默认值从 "home-default"
    # 改成 "" 是行为修正：原来不传会去查一个叫 home-default 的空间，而它不存在。
    scope_id: str = "",
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    place: str = Query("", max_length=60, description="地点筛选，服务端按地点/行政区匹配"),
    container: Container = Depends(get_container),
) -> TimelineResponse:
    result = _read_timeline(container).execute(
        scope_id=scope_id or None, limit=limit, offset=offset, place=place.strip()
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
    """整册导入 + **批末自动跑一趟人物画像**（裁决④）。**同步执行**：2.0 没有任务队列，
    装作异步只会把「跑到哪了」藏起来。

    整册导入要串行走模型调用，本切片又在这个请求的末尾加了一整趟画像流水线（每个核心
    人物至少一次模型调用，加上 moments 阶段每人每资产一次），所以这个请求会跑很久——
    客户端的超时必须自己调大，这一点写在 ``docs/runbooks/geonames-data.md`` 的相邻章节
    （导入一节）。**这是裁决④ 明确接受的代价**，记账在
    ``docs/refactor/2026-09-24-s5c-implementation-plan.md`` 第 0 节。

    **画像那一趟抛出来会让整个请求 500，而照片其实已经入库了。** 不吞是有意的：吞掉
    之后「画像从来没生成过」会变成一个没人看得见的状态，而重跑一次导入的代价由去重
    兜住（``deduplicated`` 那一列）。判据与 ``RunInsights`` 自己的 docstring 一致。
    """
    album = ImportAlbum(
        importer=build_import_assets(container, observe=True),
        summarize_event=_summarize_event(container).execute,
        workers=container.settings.album_workers,
    )
    try:
        result = album.execute(
            AlbumImportRequest(scope_id=request.scope_id, directory=request.directory, limit=request.limit)
        )
    except NotADirectoryError:
        raise HTTPException(status_code=400, detail="directory not found") from None
    # **顺序照 1.0**：``pipeline.py:733-734`` 是先 family 后 person-insights。
    family_graph = _maybe_run_family_graph(container, request.scope_id)
    insights = _maybe_run_insights(container, request.scope_id, result.imported)
    return ImportBatchResponse(
        scope_id=result.scope_id,
        directory=result.directory,
        imported=result.imported,
        deduplicated=result.deduplicated,
        events=result.events,
        lexical_terms=result.lexical_terms,
        summarized=result.summarized,
        failures=[ImportFailure(file_name=item.file_name, reason=item.reason) for item in result.failures],
        insights=insights,
        family_graph=family_graph,
    )


def _watermark(stats: Mapping[str, object]) -> int:
    """``stats['event_watermark']``。1.0 是裸 ``int(...)``——缺键当 0；
    值不是数字（老库、手工改过）时也当 0，那样下一趟会重跑而不是崩。"""
    value = stats.get("event_watermark", 0)
    return int(value) if isinstance(value, (int, float, str)) else 0


def _maybe_run_family_graph(container: Container, scope_id: str) -> FamilyRunResponse | None:
    """批末跑一趟家庭图谱推断。**没有新事件就不跑。**

    判据是 **event watermark**（1.0 ``pipeline.py:746-749``）：拿本 scope 当前的事件数，
    与最近一次 run 写进 ``stats['event_watermark']`` 的那个数比——**没涨就不跑**。

    与 ``_maybe_run_insights`` 的判据（``imported > 0``）**不同，而且这次是刻意的**：
    insights 没有 run 表，只能用「这次真导入进了照片」表达同一意图；家庭图谱有
    ``family_analysis_runs``，所以能用 1.0 那个判据。**两者不可互换**——按 ``imported``
    判的话，「导入的全是重复照片、但事件早就变了」这种情况会漏跑。

    **失败时 ``stats`` 保持空 → watermark 缺失 → 下次一定重跑**（T312 钉过）。

    **单张导入不触发**：``POST /media/import`` 走 ``ImportAssets.execute``，那里没有
    「一批结束」这个概念——裁决④取的是「贴 1.0」（1.0 也是按批次收尾）。

    **为什么这里必须接住异常。** 1.0 的触发跑在 ``threading.Thread(daemon=True)`` 里
    （``pipeline.py:762``），**它的失败根本传不到导入响应上**；2.0 裁决成同步之后，
    异常会顺着 ``POST /imports`` 冒出去变成 500。要让导入的行为与 1.0 一致，
    就得在这里接住——**家庭图谱是导入的副作用，不是它的前置**。

    这条不是纸上推演：实测撞到过。``family_analysis_runs.scope_id`` 有指向
    ``memory_spaces(id)`` 的外键，而契约测试里那个 scope 不是真的 memory_space
    （``POST /imports`` 本身不要求它是），于是 ``create_run`` 抛 ``IntegrityError``，
    把整个导入请求带崩了。
    """
    try:
        repository = container.family_graph_repository
        latest = repository.latest_run(scope_id)
        event_count = container.event_repository.count_by_scope(scope_id)
        watermark = _watermark(latest.stats if latest is not None else {})
        if latest is not None and event_count <= watermark:
            return None
        return run_family_graph_for_scope(container, scope_id, trigger_type=TRIGGER_INGEST)
    except Exception as error:  # noqa: BLE001 —— 见下面那段「为什么这里必须接住」
        log.warning("批末触发家庭图谱推断失败（scope=%s）: %s", scope_id, error)
        return None


def _maybe_run_insights(container: Container, scope_id: str, imported: int) -> InsightCounts | None:
    """批末跑一趟画像。**没有新照片就不跑。**

    1.0 有这道闸，只是它挂在两道更前面的闸后面、从来没机会执行
    （``pipeline.py:758-771``）：::

        allowlist = {SENTRIX_PERSON_INSIGHT_SCOPES}   # 默认空
        if scope_id not in allowlist: return None     # <- 永远在这里返回
        ...
        if latest is not None and event_count <= watermark: return None   # 没有新事件就不跑

    第一道（allowlist）被裁决④ 判掉——它正是「画像在 1.0 里从没自动触发过」的原因。
    **第二道是 1.0 真实的设计意图**：没有新内容就别重跑，因为一趟画像最多几十次模型
    调用。2.0 没有 run 表、也就没有那个 ``event_watermark``，所以这里用**这次真的新导
    进了照片**（``imported > 0``）表达同一个意图。

    判据取 ``imported`` 而不是 ``events``：新照片可能落在既有事件里（那时 ``events``
    照样计到），也可能还没形成事件（那时 ``events`` 是 0，但人可能是新的）。去重掉的
    那一批（``imported == 0``）才是唯一能确定「什么新东西都没有」的情形。
    """
    if imported <= 0:
        return None
    counts = run_insights_for_scope(container, scope_id)
    return InsightCounts(**counts)
