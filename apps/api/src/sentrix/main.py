"""FastAPI assembly. Route modules contain transport mapping only."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from sentrix import __version__
from sentrix.api.routes import health
from sentrix.bootstrap.container import Container
from sentrix.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_directories()

    container = Container.build(settings)
    container.database.migrate()

    app = FastAPI(title="Sentrix Home API", version=__version__, docs_url=None, redoc_url=None)
    app.state.container = container

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(health.router)
    app.include_router(v1)
    return app


app = create_app()
