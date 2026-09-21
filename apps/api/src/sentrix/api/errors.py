"""领域错误 → HTTP 状态。所有 v1 端点共用同一结构。"""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    code = "domain_error"
    status_code = 400

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class NotFoundError(DomainError):
    code = "not_found"
    status_code = 404


class InvalidRequestError(DomainError):
    code = "invalid_request"
    status_code = 400


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Starlette 的 add_exception_handler 要求第二参数是 Exception（函数参数逆变），
    # 而这里注册的就是 DomainError；cast 零开销，运行时要拿到的仍然是 DomainError。
    error = cast(DomainError, exc)
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message, "detail": {}},
    )
