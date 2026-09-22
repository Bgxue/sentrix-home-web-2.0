"""时间线端点的传输模型。字段与 ``application/memories/read_timeline.py`` 一一对应。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TimelinePhoto(BaseModel):
    asset_id: str
    file_name: str
    captured_at: str | None
    # ``Place.display()`` 的原样输出。**后端算好的最终字符串**——前端不做任何拼接，
    # 否则「附近」加给谁、什么时候加就成了两套规则。
    place: str


class TimelineEvent(BaseModel):
    id: str
    title: str
    event_type: str
    time_start: str | None
    time_end: str | None
    # 事件的行政区归属（可做硬条件过滤的那一级，可能为空）。``place`` 是给展示的整串。
    admin_district: str
    place: str
    activity: str
    summary: str
    confidence: float
    photos: list[TimelinePhoto]


class TimelineDay(BaseModel):
    date: str
    events: list[TimelineEvent]


class TimelineResponse(BaseModel):
    scope_id: str
    # scope 内的事件总数（不是本页条数），分页据此判断还有没有下一段。
    total: int
    offset: int
    limit: int
    days: list[TimelineDay]


class ImportBatchRequest(BaseModel):
    """整册导入。``directory`` 是**服务端**路径——相册在服务器上，不在浏览器里。"""

    directory: str = Field(..., min_length=1, max_length=4096)
    scope_id: str = "home-default"
    limit: int = Field(200, ge=1, le=1000)


class ImportFailure(BaseModel):
    file_name: str
    reason: str


class ImportBatchResponse(BaseModel):
    scope_id: str
    directory: str
    imported: int
    deduplicated: int
    events: int
    lexical_terms: int
    failures: list[ImportFailure]
