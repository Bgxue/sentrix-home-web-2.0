"""部署路径（T923）的假件。

两份：``FakeSentrixProcess`` 是 ``SentrixProcess`` 端口的替身（application 用例用它
钉「要不要重启、请求的端点是多少」）；真进程原语的替身不在这里——它们是以可注入
函数的形式传给 ``SubprocessSentrixProcess`` 的，见 ``test_sentrix_process.py``
里的 ``_Harness``。
"""

from __future__ import annotations

from photobench.domain.deploy.ports import RestartOutcome


class FakeSentrixProcess:
    """``SentrixProcess`` 的替身：记下每一次重启请求与请求的端点。"""

    def __init__(
        self,
        *,
        current: str = "",
        restart_ok: bool = True,
        restart_error: str = "",
        pid: int = 4242,
    ) -> None:
        self.current = current
        self.restart_ok = restart_ok
        self.restart_error = restart_error
        self.pid = pid
        self.restarts: list[str] = []
        self.endpoint_reads = 0

    def running_endpoint(self) -> str:
        self.endpoint_reads += 1
        return self.current

    def restart(self, *, endpoint: str, timeout: float = 60) -> RestartOutcome:
        self.restarts.append(endpoint)
        previous, self.current = self.current, endpoint
        return RestartOutcome(
            ok=self.restart_ok,
            restarted=True,
            endpoint=endpoint,
            previous_endpoint=previous,
            pid=self.pid,
            reason="restarted",
            error=self.restart_error,
        )
