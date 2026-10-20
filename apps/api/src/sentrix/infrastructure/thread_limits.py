"""进程级算子线程上限（1.0 ``backend/thread_limits.py`` 的 2.0 移植）。

背景：一次导入会并发跑多个 CPU 密集任务；若 torch / onnxruntime / opencv 各自按
核数开满，几十个线程抢同一批核会把事件循环饿死。1.0 在 153 上实测 241 个线程抢
32 核，健康探测超过 6 s 被看门狗判死并重启，SIGTERM 打断了正在跑的评测 run。

取值：``SENTRIX_LIB_THREADS``

* 未设置 / 空 → ``max(1, 核数 ÷ SENTRIX_PIPELINE_MAX_WORKERS)``
* 正整数 → 手工钉死
* ``0`` → 不限制

本模块只做环境变量覆盖不到的两件事：``cv2.setNumThreads()`` 与 ONNX Runtime 的
``SessionOptions.intra_op_num_threads``。OMP/MKL/OpenBLAS/NumExpr 由启动脚本在
导入 Python 前设置（这些库只在导入时读环境变量）。
"""

from __future__ import annotations

import os
from typing import Any

ENV_KEY = "SENTRIX_LIB_THREADS"
_DEFAULT_CONCURRENCY = 8


def lib_threads() -> int:
    """每算子并行线程上限；0 表示不限制。"""
    raw = (os.getenv(ENV_KEY) or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = -1
        if value >= 0:
            return value
    return _derive_threads()


def _derive_threads() -> int:
    cores = os.cpu_count() or 1
    try:
        concurrency = int(os.getenv("SENTRIX_PIPELINE_MAX_WORKERS") or _DEFAULT_CONCURRENCY)
    except ValueError:
        concurrency = _DEFAULT_CONCURRENCY
    if concurrency < 1:
        concurrency = 1
    return max(1, cores // concurrency)


def ort_intra_op_threads() -> int:
    """ONNX Runtime 会话的 intra-op 线程数；0 表示交给 ORT 自行决定。"""
    return lib_threads()


def ort_session_options() -> Any | None:
    """带线程上限的 ``SessionOptions``；onnxruntime 不可用时返回 None。"""
    value = lib_threads()
    if value <= 0:
        return None
    try:
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = value
        return options
    except Exception:
        return None


def apply_process_thread_limits() -> int:
    """把上限落到进程级全局开关，返回生效值（0 = 未限制）。幂等。"""
    value = lib_threads()
    if value > 0:
        try:
            import cv2

            cv2.setNumThreads(value)
        except Exception:
            pass
    return value
