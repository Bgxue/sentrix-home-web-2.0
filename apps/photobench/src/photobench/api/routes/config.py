"""``/api/config`` 的读写（1.0 ``do_GET`` ``:7536`` / ``do_POST`` ``:7822``）。

**响应体比 1.0 少三项**：``judge_prompt`` / ``custom_judge_prompt`` /
``evidence_judge_enabled``。它们是打分口径的一部分（默认 judge 提示词常量与自定义提示词
读写），归 S9b-3 的 judge 域。这条差异登记在本切片的偏离记录里。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from photobench.api.deps import get_container, json_body

router = APIRouter(tags=["config"])


@router.get("/config")
def get_config(request: Request) -> dict[str, Any]:
    """1.0 ``:7536``。"""
    return get_container(request).config.public_config()


@router.post("/config")
async def save_config(request: Request) -> dict[str, Any]:
    """1.0 ``:7822``：只落非密钥的连接参数。

    成功时的响应体是 1.0 的 ``{"saved": True, "runtime_config": ...}``。
    """
    payload = await json_body(request)
    saved = get_container(request).config.persist_runtime_connection(payload)
    return {"saved": True, "runtime_config": saved}
