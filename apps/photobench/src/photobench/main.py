"""PhotoBench 的进程入口：FastAPI 装配 + 后台扫描 ``results/``。

**端口 8772。** 设计文档 §5.1 的端口表给 2.0 的 PhotoBench 定的是 8772；1.0 跑在 8771
（裸进程 + ``scripts/deploy/restart_photobench.sh``，不受 systemd 管）。本切片只把
**服务本身**起得来，进程守护方式归 S11。

**为什么 ``create_app`` 里没有模块级 ``app = create_app()``。** 与 ``apps/api``
``sentrix/main.py`` 同一条理由：模块级实例会让任何 ``import photobench.main``
（pytest 收集、脚本、类型检查）都带上副作用——建目录、读配置、起一个扫描 11 GB 的
线程。进程入口走工厂：``uvicorn --factory photobench.main:create_app``。

**启动扫描为什么在后台线程。** 见 ``application/runs/index.py`` 的实测与理由。一句话：
1.0 因为同步扫完 267 个 ``run.json`` 才绑端口，实测 75 秒不可服务；2.0 先把端口绑上，
扫描进度由 ``GET /api/runs`` 的 ``scan`` 字段暴露出来。
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, FastAPI

from photobench import __version__
from photobench.api import errors
from photobench.api.routes import config as config_routes
from photobench.api.routes import model_profiles as model_profiles_routes
from photobench.api.routes import runs as runs_routes
from photobench.api.routes import suites as suites_routes
from photobench.bootstrap.container import Container
from photobench.config import Settings

#: 1.0 的 API 前缀就是 ``/api``。改成 ``/api/v1`` 是 S9c · T924——那是「对 2.0 的端点
#: 适配」那一段（同时要改前端），本切片不动。
API_PREFIX = "/api"


def create_app(
    settings: Settings | None = None,
    *,
    container: Container | None = None,
    scan_in_background: bool = True,
) -> FastAPI:
    """建应用。

    ``container`` 与 ``scan_in_background`` 两个参数是给用例留的缝：用例注入假件、
    并且**同步**跑一次扫描，这样断言不必与线程赛跑。
    """
    settings = settings or Settings.load()
    container = container or Container.build(settings)

    app = FastAPI(title="PhotoBench", version=__version__, docs_url=None, redoc_url=None)
    app.state.container = container
    errors.install(app)

    api: APIRouter = APIRouter(prefix=API_PREFIX)
    # 写侧（S9b-3）先于只读侧挂上：``/runs/{id}/judge-prompt`` 这类更具体的路径要与
    # ``/runs/{id}`` 区分开，先挂的一方在同形路径上优先匹配。
    api.include_router(suites_routes.router)
    api.include_router(runs_routes.router)
    api.include_router(config_routes.router)
    api.include_router(model_profiles_routes.router)
    app.include_router(api)

    if scan_in_background:
        threading.Thread(
            target=container.run_index.refresh,
            args=(container.run_store,),
            name="photobench-result-scan",
            daemon=True,
        ).start()
    return app
