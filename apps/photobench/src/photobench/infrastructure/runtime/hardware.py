"""CPU 温度读数——**只搬 1.0 ``backend/hardware.py`` 里被运行时提供者用到的那一个函数**。

1.0 的 ``hardware.py``（137 行）一共有五个公开函数，只有 ``read_cpu_temperature_c``
被 ``runtime_providers.py`` 用到（``HostNvidiaTelemetryProvider.gpu_stats`` 在
nvidia-smi 之外补一个 CPU 温度）。其余四个（``collect_gpu`` / ``collect_cpu`` /
``collect_memory`` / ``collect_hardware``）服务的是 ``run_qa_benchmark`` 写 run meta，
归 S9b 的编排侧，本切片不搬、也不在这里预留。

它是 ``/sys/class/hwmon`` 的读操作，与 ``nvidia-smi`` 同性质，所以同样只能落在
infrastructure（``domain-purity`` 与 ``application-purity`` 都禁止 domain/application
碰系统文件系统）。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

# 153 是 AMD k10temp 的 Tctl，Intel 板子是 coretemp，Orin 可能暴露 cpu-thermal。
# NVMe / 网卡 / amdgpu 的传感器一律忽略——把它们当成 CPU 温度比读不到更糟。
_CPU_HWMON_NAMES = ("k10temp", "coretemp", "cpu_thermal", "cpu-thermal")
# 同一颗芯片可能有多个 temp*_input（Tctl / Tccd / 各核心），按标签挑一个。
_CPU_TEMP_LABELS = ("tctl", "tdie", "package id 0", "cpu")


def read_cpu_temperature_c(root: Path | None = None) -> float | None:
    """CPU 封装温度（摄氏度）；传感器不存在时返回 ``None``。

    ``root`` 只为测试可注入（真调用方不传，走 ``/sys/class/hwmon``）。
    """
    base = root or Path("/sys/class/hwmon")
    if not base.is_dir():
        return None
    for hw in sorted(base.iterdir()):
        try:
            name = (hw / "name").read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if name not in _CPU_HWMON_NAMES:
            continue
        chosen = None
        chosen_rank = 99
        for sensor in sorted(hw.glob("temp*_input")):
            label = ""
            label_path = hw / sensor.name.replace("_input", "_label")
            with contextlib.suppress(OSError):
                label = label_path.read_text(encoding="ascii").strip().lower()
            try:
                celsius = round(float(sensor.read_text(encoding="ascii").strip()) / 1000.0, 3)
            except (OSError, ValueError):
                continue
            rank = _CPU_TEMP_LABELS.index(label) if label in _CPU_TEMP_LABELS else 50
            if chosen is None or rank < chosen_rank:
                chosen = celsius
                chosen_rank = rank
        if chosen is not None:
            return chosen
    return None
