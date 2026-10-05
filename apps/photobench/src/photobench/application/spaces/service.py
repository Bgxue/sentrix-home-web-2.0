"""``GET /api/memory-spaces``（1.0 ``do_GET`` ``:7641-7656``）。

1.0 是**转发**：把 ``sentrix_url``（查询参数，缺省取配置里的）后面的
``/api/memory-spaces`` 原样取回来，按 ``created_at`` 逆序，再补一层
「可复用基座」。2.0 的地址是 ``/api/v1/memory-spaces``，信封键仍是 ``spaces``
（``apps/api/src/sentrix/api/routes/spaces.py:19``）。

**``sentrix_url`` 参数必须保留**：调用方用它指向另一个 Sentrix（多环境 / 换机验收），
把它换成「只读配置」会让那条能力消失，而且不报错——只是答案永远是配置里那台的。
"""

from __future__ import annotations

from typing import Any

from photobench.application.config.service import ConfigService
from photobench.application.runs.index import RunIndex
from photobench.domain.http.ports import JsonHttpClient
from photobench.domain.runs.reuse import build_reuse_bases
from photobench.domain.sentrix.ports import API_PREFIX


class MemorySpaces:
    """转发 + 组装。依赖 ``JsonHttpClient`` / ``ConfigService`` / ``RunIndex``。"""

    def __init__(self, http: JsonHttpClient, config: ConfigService, index: RunIndex) -> None:
        self._http = http
        self._config = config
        self._index = index

    def list_spaces(self, sentrix_url: str = "") -> dict[str, Any]:
        """1.0 ``:7642-7655``。

        ``sentrix_url`` 只做 ``rstrip("/")``——**不补协议头**：1.0 就是这样，
        给一个 ``host:port`` 会让 ``request_json`` 直接失败并回一句可读的 400。
        补上协议头会把一个用法错误悄悄变成「能用」。

        ``reuse_bases`` 吃的是 ``RunIndex`` 的**全部**条目（1.0 的 ``repo.list_runs()``
        返回全部）。**不能用 ``ReadRuns.list_runs``**——那一条是分页的（默认 20 条），
        用它会把「第 21 条之后建过的基座」整批漏掉，而列表看起来完全正常。
        """
        base = (sentrix_url or self._config.sentrix_url).rstrip("/")
        spaces = self._http.request_json(f"{base}{API_PREFIX}/memory-spaces", timeout=30)
        if isinstance(spaces, dict):
            # 两个键都认：1.0 处理过的上游两种形状都出现过。
            spaces = spaces.get("spaces") or spaces.get("items") or []
        spaces = sorted(
            spaces or [],
            key=lambda space: str(space.get("created_at") or ""),
            reverse=True,
        )
        return {"spaces": spaces, "reuse_bases": build_reuse_bases(spaces, self._index.entries())}
