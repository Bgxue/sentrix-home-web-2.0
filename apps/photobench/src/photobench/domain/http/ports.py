"""PhotoBench 打向主 API 的 HTTP 客户端端口。

对应 1.0 ``benchmark_orchestrator.py:731`` 的 ``request_json`` 与 ``:799`` 的
``request_bytes``。1.0 直接用 stdlib ``urllib.request``；2.0 换 httpx（沿用
``apps/photobench/pyproject.toml`` 已有的依赖，不新增）。

**接口在这里、实现在 infrastructure，理由与 runtime 那几个端口一样**：httpx 在
``domain-purity`` 的 forbidden 名单上（与 ``sentrix.domain`` 逐条对齐），而
``application-purity`` 也不许 application 直接 import httpx。

**三处形状差异，全部由分层与「不泄漏实现类型」逼出来，不是口味：**

1. **返回值是解析后的对象，不是 HTTP 响应。** 1.0 的 ``request_json`` 直接
   ``json.loads(resp.read())`` 返回 Python 对象，形状不变；``request_bytes`` 返回
   ``bytes``，形状也不变。**所以这两个方法的行为与 1.0 完全一致**，只是把
   ``urllib`` 换成了 httpx。
2. **失败仍然抛异常，不返回错误值。** 1.0 只捕获 ``HTTPError`` 并转成
   ``RuntimeError(f"HTTP {code} {reason}: {detail}")``；连接失败 / 超时由 urllib
   原样抛出。2.0 照搬这条分工：非 2xx 由适配器转成同一条 ``RuntimeError`` 文案。
3. **没有重试。** 1.0 没有。加一层「看起来更健壮」的重试会改变调用方观察到的失败
   次数与时序（评测里的 judge 调用自己有一套重试，见 1.0 ``_judge_retry_delay``），
   那不属于本切片。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class JsonHttpClient(Protocol):
    """主 API 的只读/写 JSON 客户端。方法名沿用 1.0，便于逐行对照。"""

    def request_json(
        self,
        url: str,
        payload: Mapping[str, Any] | None = None,
        method: str = "GET",
        timeout: int = 120,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any: ...

    def request_bytes(self, url: str, timeout: int = 120) -> bytes: ...
