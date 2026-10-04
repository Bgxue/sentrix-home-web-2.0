"""``/api/profiles`` 与 ``/api/current-model``（1.0 ``do_POST`` ``:7826`` / ``:7844``）。

两条都是**查询**：问 Manager 有哪些 profile、问端点现在真的在跑哪个模型。起停模型
（1.0 ``_phase_model_deploy``）属 S9b-2 的 run-execution 主链，不在这里。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from photobench.api.deps import get_container, json_body

router = APIRouter(tags=["model-profiles"])


@router.post("/profiles")
async def list_profiles(request: Request) -> dict[str, Any]:
    """1.0 ``:7826``。"""
    payload = await json_body(request)
    return get_container(request).model_profiles.query_profiles(payload)


@router.get("/current-model")
def current_model_get() -> JSONResponse:
    """1.0 ``:7530``：GET 这一条要明说「请用 POST」，而不是让 405 用 FastAPI 的形状回。"""
    return JSONResponse(status_code=405, content={"error": "current-model requires POST"})


@router.post("/current-model")
async def current_model(request: Request) -> dict[str, Any]:
    """1.0 ``:7844``。"""
    payload = await json_body(request)
    return get_container(request).model_profiles.current_model(payload)
