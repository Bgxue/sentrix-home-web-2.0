"""``thread_limits``：取值优先级与进程级落地。

1.0 ``backend/thread_limits.py`` 的行为对照。重点不是「函数能跑」，而是三条边界：
手工值优先于推导、空值按并发数推导、``0`` 表示不限制（而不是回落到推导）。
"""

from __future__ import annotations

import pytest

from sentrix.infrastructure import thread_limits


def test_env_value_wins_and_zero_means_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "4")
    assert thread_limits.lib_threads() == 4
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "0")
    assert thread_limits.lib_threads() == 0


def test_empty_or_invalid_value_falls_back_to_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(thread_limits.os, "cpu_count", lambda: 32)
    monkeypatch.setenv("SENTRIX_PIPELINE_MAX_WORKERS", "8")
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "")
    assert thread_limits.lib_threads() == 4
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "abc")
    assert thread_limits.lib_threads() == 4
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "-1")
    assert thread_limits.lib_threads() == 4


def test_derivation_never_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(thread_limits.os, "cpu_count", lambda: 1)
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "")
    monkeypatch.setenv("SENTRIX_PIPELINE_MAX_WORKERS", "8")
    assert thread_limits.lib_threads() == 1


def test_derivation_is_capped_at_four(monkeypatch: pytest.MonkeyPatch) -> None:
    """32/4 仍然封顶 4；8 核设备至少拿到 2，不能掉到 1。"""
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "")
    monkeypatch.setenv("SENTRIX_PIPELINE_MAX_WORKERS", "4")
    monkeypatch.setattr(thread_limits.os, "cpu_count", lambda: 32)
    assert thread_limits.lib_threads() == 4
    monkeypatch.setattr(thread_limits.os, "cpu_count", lambda: 8)
    assert thread_limits.lib_threads() == 2


def test_ort_session_options_use_the_same_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("onnxruntime")
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "4")
    options = thread_limits.ort_session_options()
    assert options is not None
    assert options.intra_op_num_threads == 4


def test_zero_skips_ort_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "0")
    assert thread_limits.ort_session_options() is None


def test_apply_returns_the_effective_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRIX_LIB_THREADS", "4")
    assert thread_limits.apply_process_thread_limits() == 4
