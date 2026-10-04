"""一次 run 的持久状态与相位记账。

1.0 的 ``self.state`` 是一个挂在 ``BenchmarkRun`` 上的裸 dict，配一把 ``threading.RLock``
（``_record_phase`` ``:2278``）。2.0 收成这个类，理由不是整洁：**那份 dict 的形状是
落盘契约**（``run.json`` 会被 ``domain.runs.analysis.run_list_entry`` 读，也会被指到
1.0 ``results/`` 的读者读），散在相位实现里就没人能一眼看出「哪些键必须存在」。

**并发**：QA 池的多个线程会同时 ``append`` item 与写相位进度，GPU 采样线程也会写。
1.0 用一把 RLock 兜住，这里照搬——**换更细的锁换不来任何东西**，因为 ``persist``
（写 run.json）本身就要在一致快照上做。

字典形状与 1.0 ``BenchmarkRun.__init__``（``:1979-2010``）逐键对应，只去掉 2.0
本切片不产出的那几项（judge / 重判 / 遥测聚合 / 云端模型），并加上 2.0 特有的
``sentrix_url`` 与 ``import_directories``。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from photobench.domain.runs.analysis import now_iso


class RunState:
    """带锁的 run 状态字典 + 相位记账。"""

    def __init__(self, initial: dict[str, Any]) -> None:
        self._lock = threading.RLock()
        self.data = initial
        self._phase_started: dict[str, float] = {}

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.data.get(key, default)

    def record_phase(self, phase: str, key: str, value: Any) -> None:
        """1.0 ``:2278``。相位不存在就建一个 ``{"status": "pending"}`` 的空壳，
        这样读侧永远能假定「出现过的相位都有一份形状相同的记录」。"""
        with self._lock:
            bucket = self.data.setdefault("phases", {}).setdefault(phase, {"status": "pending"})
            bucket[key] = value

    def phase_start(self, phase: str) -> None:
        with self._lock:
            self._phase_started[phase] = time.perf_counter()
        self.record_phase(phase, "status", "running")
        self.record_phase(phase, "started_at", now_iso())

    def phase_done(self, phase: str, extra: dict[str, Any] | None = None) -> None:
        """1.0 ``:2289``：**``extra`` 里有 ``total_seconds`` 时不覆盖它**。

        那是给「自己算过更准的耗时」的相位用的（``pipeline_processing`` 从循环开始
        计时、``model_deploy`` 把装载/健康检查分开记），不这么写就会把那份更细的
        计时覆盖成整段墙钟。
        """
        self.record_phase(phase, "status", "done")
        self.record_phase(phase, "finished_at", now_iso())
        with self._lock:
            started = self._phase_started.pop(phase, None)
        if started is not None and not (extra and "total_seconds" in extra):
            self.record_phase(phase, "total_seconds", round(time.perf_counter() - started, 3))
        for key, value in (extra or {}).items():
            self.record_phase(phase, key, value)

    def phase_partial(self, phase: str, extra: dict[str, Any] | None = None) -> None:
        """1.0 ``:2299``：``done`` 之后再压回 ``partial``——这是 ``execute`` 判定
        ``completed_with_errors`` 的唯一依据（``:2389``）。"""
        self.phase_done(phase, extra)
        self.record_phase(phase, "status", "partial")

    def phase_failed(self, phase: str, error: str) -> None:
        self.record_phase(phase, "status", "failed")
        self.record_phase(phase, "error", error)
        self.record_phase(phase, "finished_at", now_iso())
