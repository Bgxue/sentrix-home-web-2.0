"""路由取装配点的唯一入口。

与 ``apps/api`` 的 ``api/deps.py`` 同一体例：**api 层不许 import infrastructure**
（``photobench-api-transport-only``），依赖一律经 ``app.state.container`` 取，而那个
字段的类型来自 ``photobench.bootstrap.container``——bootstrap 不在分层链上，所以
api -> bootstrap 这条边不被 ``photobench-layers`` 判成反向依赖（与 sentrix 相同）。
"""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import Request

from photobench.bootstrap.container import Container


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # 装配点没挂上——那是启动代码写错了，不是调用方的错
        raise RuntimeError("app.state.container 未装配")
    return cast(Container, container)


async def json_body(request: Request) -> dict[str, Any]:
    """1.0 ``OrchestratorHandler._payload``（``:7523``）的等价物。

    **空体、坏 JSON、非对象一律当 ``{}``**，不报错——那是 1.0 的口径。用 FastAPI 的
    ``payload: dict`` 声明会把它变成 422，形状与 1.0 不同（1.0 会走到「这个字段是空的」
    那条校验上，报一条更具体的 400）。
    """
    raw = await request.body()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
