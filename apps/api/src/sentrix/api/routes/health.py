from fastapi import APIRouter, Request

from sentrix import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    database_ok = request.app.state.container.database_healthy()
    return {
        "status": "ok" if database_ok else "degraded",
        "version": __version__,
        "database": "ok" if database_ok else "error",
    }
