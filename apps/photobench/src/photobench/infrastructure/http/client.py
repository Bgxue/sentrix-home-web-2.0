"""``JsonHttpClient`` 的 httpx 实现：1.0 ``request_json`` / ``request_bytes`` 的替代。

**行为对照表（逐条核对 1.0，``:731`` / ``:799``）：**

===============================  =======================================================
1.0 ``request_json``             2.0
===============================  =======================================================
``json.dumps(payload, ensure_ascii=False)`` 同（字节体、UTF-8 原样）
``Content-Type: application/json``          同（``extra_headers`` 可覆盖）
``Content-Length`` 显式写            httpx 自动写，值相同
``timeout`` 按调用传                 同
非 2xx → ``RuntimeError("HTTP {code} {reason}: {detail}")``  同（同一段 detail 提取逻辑）
连接失败 / 超时 → 原样抛              原样抛（类型变成 httpx 的，见下）
重试                                 无（1.0 没有）
===============================  =======================================================

**三处必须说清楚的差异：**

1. **TLS 校验关掉。** 1.0 建的是 ``ssl.create_default_context()`` 然后把
   ``check_hostname`` 与 ``verify_mode`` 都改掉（``:742-743``）——即**不校验证书**。
   内网自签证书的部署依赖这一点，所以 2.0 用 ``verify=False`` 照搬。
2. **异常类型换了名字。** ``urllib.error.HTTPError`` → ``httpx.HTTPStatusError``；
   连接失败从 ``URLError`` → ``httpx.ConnectError``。**「非 2xx 转成同一条
   RuntimeError 文案」这条照搬**（那是 1.0 唯一显式约定的失败形状）；
   其余失败两边都是「抛异常」，只是类型不同。本切片唯一的调用方
   （``_hydrate_legacy_gt_images``）用的是 ``except Exception``，两边等价。
3. **跟随重定向。** ``urllib.request`` 默认跟随 301/302，而 ``httpx.Client`` 默认
   **不**跟随。这里显式开成 ``follow_redirects=True``——否则一个被重定向的端点
   在 1.0 能通、在 2.0 会变成 302 错误体。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx


def _error_detail(response: httpx.Response) -> str:
    """1.0 ``:745-760``：从错误体里掏出给人看的 detail。"""
    raw = response.text[:2000]
    detail: Any = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            detail = parsed.get("detail") or parsed.get("error") or parsed.get("message") or parsed
        else:
            detail = parsed
    except (json.JSONDecodeError, TypeError):
        pass
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    return str(detail or response.reason_phrase or "request failed")[:1000]


class HttpxJsonClient:
    """``JsonHttpClient`` 的实现。

    ``client`` 可注入是为了**用例不碰网络**：测试传一个
    ``httpx.Client(transport=httpx.MockTransport(handler))`` 就能把状态码、超时、
    错误体逐条摆出来。默认的那个是进程级的——httpx 复用连接，而 1.0 的
    ``urlopen`` 每次新建连接；这不改变任何返回值，只是少几次握手。
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = (
            client if client is not None else httpx.Client(verify=False, follow_redirects=True, timeout=120)
        )

    def request_json(
        self,
        url: str,
        payload: Mapping[str, Any] | None = None,
        method: str = "GET",
        timeout: int = 120,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        content = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"}
        headers.update(extra_headers or {})
        try:
            response = self._client.request(method, url, content=content, headers=headers, timeout=timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            response = exc.response
            raise RuntimeError(
                f"HTTP {response.status_code} {response.reason_phrase}: {_error_detail(response)}"
            ) from exc
        return response.json()

    def request_bytes(self, url: str, timeout: int = 120) -> bytes:
        """1.0 ``:799``。**非 2xx 不做文案转换**——1.0 这一段没有 except，``HTTPError``
        原样冒出去；这里保持同一分工，让 ``raise_for_status`` 的异常原样冒。"""
        response = self._client.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content
