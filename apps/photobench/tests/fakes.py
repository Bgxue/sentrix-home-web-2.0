"""PhotoBench 测试用的假件：**不让 CI 依赖这台机器上有没有 GPU、某个进程在不在**。

1.0 的 ``runtime_providers.py`` / ``apple_telemetry.py`` 没有自己的测试（它们被
benchmark 间接覆盖），所以 2.0 补的这套用例必须能在**任何**机器上跑：HTTP 一律走假件，
子进程一律走假件，``/proc`` 与 ``/sys`` 一律走假件。真链路（nvidia-smi / macmon /
ssh 118）的验收不在这里。

**假件的未预设调用直接失败，不回落真实现。** 回落会让一个漏配的假件变成一次真实
网络请求或真实子进程，用例于是变成「这台机器上恰好有没有那个服务」的函数——
那正是这一轮要避免的东西。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any


class FakeHTTPError(Exception):
    """代替 ``httpx.HTTPError``：被测代码里 ``except httpx.HTTPError`` 要接得住它。"""


class FakeResponse:
    def __init__(self, payload: Any = None, *, text: str = "", status_code: int = 200) -> None:
        self.payload = payload
        self.text = text
        self.status_code = status_code
        # ``content`` 为空会让调用方走「没有响应体」那一支（保留 1.0 的语义），
        # 所以它由 payload 是否给出决定，而不是固定非空。
        self.content = b"" if payload is None else b"{}"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise FakeHTTPError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self.payload

    def iter_lines(self) -> Iterator[str]:
        return iter(self.text.splitlines())


class FakeHttpx:
    """httpx 模块的替身：记录调用 + 按预设作答。"""

    HTTPError = FakeHTTPError

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.on_get: Callable[..., FakeResponse] | None = None
        self.on_post: Callable[..., FakeResponse] | None = None
        self.on_request: Callable[..., FakeResponse] | None = None
        self.on_stream: Callable[..., FakeResponse] | None = None

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._answer("get", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return self._answer("post", url, kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self._answer("request", url, kwargs)

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStream:
        self.calls.append((method, url, kwargs))
        return _FakeStream(self._answer("stream", url, kwargs))

    def urls(self) -> list[str]:
        return [url for _, url, _ in self.calls]

    def _answer(self, kind: str, url: str, kwargs: dict[str, Any]) -> FakeResponse:
        handler = getattr(self, f"on_{kind}")
        if handler is None:
            raise AssertionError(f"用例没有预设 {kind.upper()} {url}——假件拒绝真实网络调用")
        result = handler(url, **kwargs)
        assert isinstance(result, FakeResponse)
        return result


class _FakeStream:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> FakeResponse:
        self._response.raise_for_status()
        return self._response

    def __exit__(self, *exc_info: object) -> bool:
        return False


class CompletedProcess:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class FakeSubprocess:
    """subprocess 模块的替身：只提供被测代码用到的那几个名字。

    ``SubprocessError`` 必须与真模块同名，因为被测代码的 ``except`` 子句里写着它。
    """

    SubprocessError = type("SubprocessError", (Exception,), {})
    TimeoutExpired = type("TimeoutExpired", (SubprocessError,), {})
    PIPE = -1
    DEVNULL = -3

    def __init__(self, run: Callable[..., CompletedProcess] | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.on_run = run

    def run(self, command: list[str], **kwargs: Any) -> CompletedProcess:
        self.calls.append((command, kwargs))
        if self.on_run is None:
            raise AssertionError(f"用例没有预设 subprocess.run——收到 {command}")
        return self.on_run(command, **kwargs)


class FakePath:
    """``pathlib.Path`` 的替身：只为让「读某个系统文件」变成可控输入。

    构造一个 ``path_reader(files)``，把它 ``monkeypatch.setattr`` 到被测模块的
    ``Path`` 上即可——那只影响该模块里的 ``Path(...)``，不碰真正的 pathlib。
    """

    def __init__(self, value: Any, files: dict[str, str]) -> None:
        self.value = str(value)
        self._files = files

    def read_text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        if self.value not in self._files:
            raise OSError(f"no such file: {self.value}")
        return self._files[self.value]


def path_reader(files: dict[str, str]) -> Callable[..., FakePath]:
    return lambda value: FakePath(value, files)
