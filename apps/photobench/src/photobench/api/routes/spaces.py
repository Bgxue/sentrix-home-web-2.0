"""``GET /api/memory-spaces``（1.0 ``do_GET`` ``:7641``）。

转发 + 可复用基座都在 ``application.spaces.MemorySpaces`` 里；这一层只把查询参数
``sentrix_url`` 透下去。**不传时用配置里的默认**——那条默认（以及「可以指向别处」）
是 1.0 的行为，参数声明成 ``str = ""`` 才保得住它。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from photobench.api.deps import get_container

router = APIRouter(tags=["spaces"])


@router.get("/memory-spaces")
def list_memory_spaces(request: Request, sentrix_url: str = "") -> dict[str, Any]:
    return get_container(request).spaces.list_spaces(sentrix_url)
