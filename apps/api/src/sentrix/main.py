"""FastAPI assembly. Route modules contain transport mapping only."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from sentrix import __version__
from sentrix.api.errors import DomainError, domain_error_handler
from sentrix.api.routes import assistant, health, media, search, timeline
from sentrix.bootstrap.container import Container
from sentrix.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_directories()

    container = Container.build(settings)
    container.database.migrate()

    app = FastAPI(title="Sentrix Home API", version=__version__, docs_url=None, redoc_url=None)
    app.state.container = container
    app.add_exception_handler(DomainError, domain_error_handler)

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(health.router)
    v1.include_router(media.router)
    v1.include_router(search.router)
    v1.include_router(assistant.router)
    v1.include_router(timeline.router)
    app.include_router(v1)
    return app


# 这里刻意没有模块级 ``app = create_app()``：它会让任何 ``import sentrix.main``
# （pytest 收集、脚本、类型检查）都带上副作用——在进程 CWD 下建出 data/、跑一次迁移。
# 进程入口走工厂：``uvicorn --factory sentrix.main:create_app``（见 Makefile 的 dev）。
# 理由与 Task 5 的定稿一致；本块曾残留该行（1076a7f 只改了 Task 5，漏改这里）。
