"""``GET /api/assets/{id}/file``：把主 API 的资产文件**代理**出来
（1.0 ``:7724`` + ``_proxy_sentrix_asset`` ``:7992``）。

**为什么是代理而不是重定向。** 1.0 就是这么做的：浏览器只认这一个 origin，主 API
的地址（以及它将来换到哪）不暴露给前端；``Range`` 也由 PhotoBench 中转，视频拖动条
因此不依赖主 API 的 CORS。照搬这条分工。

**为什么手动拼 ``StreamingResponse`` 而不是 ``FileResponse``。** 文件不在本地盘上——
它在上游。所以只能转发上游的状态码、五个头与 body 流；``FileResponse`` 管的是本地
路径。上游的 body 是 ``JsonHttpClient.stream_get`` 给的迭代器，读完由它自己关。

**状态码照搬上游**：上游 4xx/5xx 不是「我们的错」，原样回给调用方，只把 body 换成
1.0 那句 ``asset proxy failed: {code}``。
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from photobench.api.deps import get_container
from photobench.domain.errors import InvalidRequestError

router = APIRouter(tags=["assets"])

#: 1.0 ``_proxy_sentrix_asset``（``:8003``）逐个转发的五个头，一个不多一个不少。
#: ``Content-Type`` 决定浏览器认不认这张图，``Content-Range`` / ``Accept-Ranges``
#: 决定视频能不能拖——少一个都是功能缺失，多一个（如 ``Content-Encoding``）会因为
#: body 已经被 httpx 解压过而自相矛盾。
_FORWARDED_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "cache-control",
)


def _forwarded(headers: Mapping[str, str]) -> dict[str, str]:
    """挑出要转发的头。上游给的是小写名（httpx），这里转回规范写法。"""
    return {name.title(): value for name in _FORWARDED_HEADERS if (value := headers.get(name))}


@router.get("/assets/{asset_id:path}/file")
def get_asset_file(request: Request, asset_id: str) -> Response:
    """1.0 ``:7724-7730``。非法 id → 400 ``invalid asset id``；上游 HTTP 错误 →

    ``{"error": "asset proxy failed: {code}"}`` 且状态码同上游。

    **``asset_id`` 用 ``:path`` 收**：1.0 是在**整条路径**上判
    ``startswith("/api/assets/") and endswith("/file")``，所以 ``/api/assets/a/b/file``
    会走到「id 里有 ``/`` → 400」。用普通的 ``{asset_id}``，那条请求在 2.0 会先撞上
    路由不匹配，回的是 FastAPI 的 404 ``{"detail": ...}``——形状与 1.0 不同。
    """

    if not asset_id or "/" in asset_id:
        raise InvalidRequestError("invalid asset id")
    stream = get_container(request).sentrix.open_asset_file(
        asset_id, range_header=request.headers.get("range")
    )
    if stream.status_code >= 400:
        stream.close()
        return JSONResponse(
            status_code=stream.status_code,
            content={"error": f"asset proxy failed: {stream.status_code}"},
        )
    return StreamingResponse(
        stream.body,
        status_code=stream.status_code,
        headers=_forwarded(stream.headers),
        # 客户端中途断开时也必须关掉上游连接，否则连接池会慢慢漏。
        # ``BackgroundTask`` 在响应体发完之后跑；body 生成器的 ``finally`` 再兜一次。
        background=BackgroundTask(stream.close),
    )
