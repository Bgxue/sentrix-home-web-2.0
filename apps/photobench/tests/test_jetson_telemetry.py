"""Jetson 统一内存口径：nvmap 账本优先、残差兜底、零残差报不可用。

1.0 的结论（``backend/jetson_telemetry.py:149-195``）：nvmap 是分配量、可读时权威；
残差是驻留量，只在 L4T >= R36.4.7 有效。46 是 R36.4.3，nvmap.ko 不把 GPU 页记进
进程 RSS，残差恒为 0；把这个 0 当真实值会漏掉约 79% 的占用。
"""

from __future__ import annotations

import pytest

from photobench.infrastructure.runtime import jetson_telemetry as jetson


def test_nvmap_ledger_wins_when_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: {42: 2617.9})
    gpu, source = jetson._process_gpu_memory_mib({42}, rss_mib=714.6, host_rss_mib=714.6)
    assert gpu == 2617.9
    assert source == "nvmap_iovmm"


def test_residual_is_used_only_when_the_ledger_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: None)
    gpu, source = jetson._process_gpu_memory_mib({42}, rss_mib=5370.0, host_rss_mib=1030.0)
    assert gpu == 4340.0
    assert source == "vmrss_residual"


def test_a_zero_residual_is_unavailable_not_a_real_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """46 的关键分支：差额 0 代表「没记账」，不是「没占用」。"""
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: None)
    gpu, source = jetson._process_gpu_memory_mib({42}, rss_mib=714.6, host_rss_mib=714.6)
    assert gpu is None
    assert source == "unavailable"


def test_missing_rss_or_host_rss_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: None)
    assert jetson._process_gpu_memory_mib({42}, rss_mib=None, host_rss_mib=1.0) == (
        None,
        "unavailable",
    )
    assert jetson._process_gpu_memory_mib({42}, rss_mib=1.0, host_rss_mib=None) == (
        None,
        "unavailable",
    )


def test_unified_total_is_host_plus_gpu_without_double_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: {42: 2617.9})
    data = jetson.unified_model_memory({42}, rss_mib=714.6, host_rss_mib=714.6)
    assert data["model_process_system_memory_used_mib"] == 3332.5
    assert data["model_process_host_memory_mib"] == 714.6
    assert data["model_process_gpu_memory_mib"] == 2617.9
    assert data["model_process_gpu_memory_source"] == "nvmap_iovmm"
    assert data["model_process_system_memory_scope"] == "host_smaps_rss_plus_gpu_uma"


def test_without_smaps_split_the_whole_vmrss_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: {42: 2617.9})
    data = jetson.unified_model_memory({42}, rss_mib=714.6, host_rss_mib=None)
    assert data["model_process_system_memory_used_mib"] == 714.6
    assert data["model_process_system_memory_scope"] == "host_process_vmrss"
    assert data["model_process_gpu_memory_source"] == "unavailable"
    assert "model_process_host_memory_mib" not in data


def test_no_inputs_writes_nothing() -> None:
    assert jetson.unified_model_memory({42}, rss_mib=None, host_rss_mib=None) == {}


def test_unified_metadata_replaces_the_legacy_pss_unit() -> None:
    data = {
        "model_process_host_memory_mib": 1670.84,
        "model_process_gpu_memory_mib": 5094.35,
    }
    jetson.apply_jetson_memory_metadata(data)
    assert data["memory_unit"] == "host_smaps_rss_plus_gpu_mib"
    assert data["memory_scope"] == "host_smaps_rss_plus_gpu_uma"
    assert data["memory_profile"]["method"] == "orin_smaps_rss_plus_gpu_uma_v1"


def test_no_split_fields_keeps_the_legacy_unit() -> None:
    data = {"memory_unit": "process_pss_uma_mib"}
    jetson.apply_jetson_memory_metadata(data)
    assert data["memory_unit"] == "process_pss_uma_mib"
    assert "memory_profile" not in data
