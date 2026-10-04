"""读侧用例的假件：HTTP 客户端与模型运行时端口。

与 ``tests/fakes.py`` 同一条纪律——**未预设的调用直接失败**，不回落真实现。
``fakes.py`` 里那批是运行时提供者的（S9a），这里这三个服务 S9b-1 新建的三条接缝：
``JsonHttpClient`` / ``LifecycleProvider`` / ``InferenceProvider``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class FakeJsonHttpClient:
    """``JsonHttpClient`` 的替身：按 URL 子串作答，未预设就报错。"""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, Mapping[str, Any] | None, str, int]] = []
        self.fail_with: Exception | None = None

    def _answer(self, url: str) -> Any:
        if self.fail_with is not None:
            raise self.fail_with
        for needle, value in self.responses.items():
            if needle in url:
                return value
        raise AssertionError(f"用例没有预设这个 URL —— {url}")

    def request_json(
        self,
        url: str,
        payload: Mapping[str, Any] | None = None,
        method: str = "GET",
        timeout: int = 120,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        self.calls.append((url, payload, method, timeout))
        return self._answer(url)

    def request_bytes(self, url: str, timeout: int = 120) -> bytes:
        self.calls.append((url, None, "GET", timeout))
        return bytes(self._answer(url))


class FakeLifecycleProvider:
    """``LifecycleProvider`` 的替身。``start`` / ``stop`` 故意不实现——

    它们是 S9b-2 的 run-execution 才会调的，这个假件只服务本切片的两个**查询**
    （``profiles`` / ``state``）；多实现一个方法就是在帮「本切片接线了起停」这件事说谎。
    """

    def __init__(self, profiles: Any = None, state: Any = None, error: Exception | None = None) -> None:
        self._profiles = profiles if profiles is not None else {"profiles": []}
        self._state = state if state is not None else {}
        self._error = error
        self.calls: list[str] = []

    def profiles(self) -> dict[str, Any]:
        self.calls.append("profiles")
        if self._error is not None:
            raise self._error
        return self._profiles

    def state(self) -> dict[str, Any]:
        self.calls.append("state")
        if self._error is not None:
            raise self._error
        return self._state


class FakeInferenceProvider:
    """``InferenceProvider`` 的替身，只实现本切片用到的那两个查询。"""

    def __init__(self, models: list[str] | None = None, capabilities: Any = None) -> None:
        self._models = models if models is not None else []
        self._capabilities = capabilities if capabilities is not None else {"modes": ["generic"]}
        self.calls: list[str] = []

    def list_models(self) -> dict[str, Any]:
        self.calls.append("list_models")
        return {"models": list(self._models)}

    def capabilities(self) -> dict[str, Any]:
        self.calls.append("capabilities")
        return dict(self._capabilities)


__all__ = [
    "FakeInferenceProvider",
    "FakeJsonHttpClient",
    "FakeLifecycleProvider",
]
