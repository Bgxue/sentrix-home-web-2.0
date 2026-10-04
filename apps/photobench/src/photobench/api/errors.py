"""领域错误 → HTTP 状态。**映射只有这一处。**

1.0 的 ``OrchestratorHandler`` 用两个 ``except`` 兜住一切（``:7758-7762`` /
``:7854-7856``）：``KeyError`` → 404，其余 → 400，响应体固定 ``{"error": str(e)}``。
本轮**前端一行不动**，所以状态码与响应体的形状都要原样保留：
``{"error": ...}``（不是 FastAPI 默认的 ``{"detail": ...}``）。

**为什么还要兜底 ``Exception``。** 那是 1.0 的行为：一个没预料到的异常在前端看到的
是一句可读的错误，而不是一个空响应。代价是「真正的 bug 看起来像 400」，所以这里
**先 logger.exception 再回**——行为不变，但服务端日志里留着堆栈。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from photobench.domain.errors import DomainError

log = logging.getLogger("photobench.api")


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    error = exc if isinstance(exc, DomainError) else DomainError(str(exc))
    return _error_response(error.status_code, error.message)


async def key_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """1.0 的 ``except KeyError as e: {"error": str(e)}, 404``。

    ``str(KeyError("abc"))`` 是 ``"'abc'"``（带引号）——听着怪，但那是 1.0 的原样输出，
    前端按它显示「找不到这个 run」。形状不变。
    """
    return _error_response(404, str(exc))


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("未处理的异常（1.0 会把它当 400 回给调用方）")
    return _error_response(400, str(exc))


def install(app: Any) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(KeyError, key_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
