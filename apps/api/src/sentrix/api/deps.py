"""从 app.state 取依赖。路由只从这里拿东西，不自己构造。"""

from __future__ import annotations

from typing import cast

from fastapi import Request

from sentrix.bootstrap.container import Container


def get_container(request: Request) -> Container:
    # app.state 是 Starlette 的动态属性，mypy 只看得见 Any；装进去的就是 Container。
    return cast(Container, request.app.state.container)
