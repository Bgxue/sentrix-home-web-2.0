"""Regression cases from the four-device T006 correctness audit."""

from __future__ import annotations

import json

import pytest

from photobench.domain.runs import analysis
from photobench.infrastructure.runtime import jetson_telemetry as jetson


def test_unknown_nvmap_does_not_become_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: None)
    result = jetson.unified_model_memory({12}, rss_mib=100, host_rss_mib=100)
    assert result["model_process_host_memory_mib"] == 100
    assert result["model_process_gpu_memory_mib"] is None
    assert result["model_process_system_memory_used_mib"] is None
    assert result["memory_complete"] is False


def test_nx_power_is_board_power_and_keeps_rails() -> None:
    sample = jetson._parse_tegrastats_line(
        "RAM 4000/16000MB GR3D_FREQ 75% gpu@55C "
        "VDD_IN 12000mW/11000mW VDD_CPU_GPU_CV 8000mW/7000mW VDD_SOC 2000mW/1900mW"
    )
    assert sample["power_draw_w"] == 12
    assert sample["power_scope"] == "board"
    assert sample["power_rail"] == "VDD_IN"
    assert sample["power_rails_w"]["VDD_CPU_GPU_CV"] == 8


def test_nearest_rank_p95_has_no_off_by_one() -> None:
    assert analysis.series_stats([{"x": n} for n in range(1, 21)], "x")["p95"] == 19


def test_downsampling_preserves_all_phase_boundaries() -> None:
    history = [{"phase": str(n // 2), "t": n} for n in range(40)]
    selected = analysis.downsample_history(history, limit=8)
    assert {point["phase"] for point in selected} == {str(n) for n in range(20)}


def test_history_rejects_apple_outliers_without_editing_raw() -> None:
    line = json.dumps({"_t": 1, "source": "apple_unified", "power_draw_w": 325, "temperature_c": 0.8})
    live: dict[str, object] = {"source": "apple_unified"}
    analysis.attach_gpu_sample_history(live, [line])
    assert not (live.get("peak") or {})
    assert json.loads(line)["power_draw_w"] == 325


def test_native_completion_does_not_require_legacy_turn_outcome() -> None:
    item = {
        "metrics_contract_version": 2,
        "agent_status": "complete",
        "termination_reason": "answered_with_gap",
        "answer": "answer",
        "agent2_trace": {"budget_outcome": {"model_steps": 3, "max_steps": 4}},
        "execution_trace": [{"stage": "model", "call_type": "agent", "action": "final"}],
    }
    result = analysis.agent_stability(item)
    assert result["completed_within_steps"] is True
    assert result["json_parse_total"] is None


def test_endpoint_attribution_tracks_descendants_and_port_reuse() -> None:
    from photobench.infrastructure.runtime.telemetry_scope import service_rows

    rows = [
        {"pid": 1, "ppid": 0, "listening_ports": [8100], "identity": "llama-server"},
        {"pid": 2, "ppid": 1, "listening_ports": [], "identity": "worker"},
        {"pid": 3, "ppid": 0, "listening_ports": [8106], "identity": "llama-server"},
    ]
    spec = {"ports": [8100], "keywords": ["llama-server"]}
    assert {row["pid"] for row in service_rows(rows, spec)} == {1, 2}
    rows[0]["listening_ports"] = []
    rows[2]["listening_ports"] = [8100]
    assert {row["pid"] for row in service_rows(rows, spec)} == {3}
    assert service_rows([], spec) == []


def test_nvidia_ram_never_uses_nvmap_or_other_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    from photobench.infrastructure.runtime import providers

    monkeypatch.setattr(
        providers,
        "_host_process_rows",
        lambda: [
            {"pid": 1, "ppid": 0, "listening_ports": [8100]},
            {"pid": 2, "ppid": 1, "listening_ports": []},
            {"pid": 3, "ppid": 0, "listening_ports": [8106]},
        ],
    )
    monkeypatch.setattr(providers, "_process_pss_mib", lambda pid: pid * 10.0)
    result = providers._augment_host_process_memory(
        {},
        telemetry_services={
            "main_model": {"ports": [8100]},
            "embedding": {},
            "product": [],
        },
        compute_apps=[{"pid": 1, "used_memory_mib": 100}, {"pid": 3, "used_memory_mib": 900}],
    )
    assert result["model_process_system_memory_used_mib"] == 30
    assert result["benchmark_process_gpu_memory_mib"] == 100
    assert result["all_processes_memory_mib"] == 1000
    assert result["memory_accounting"] == "nvidia_pss_and_vram"
    aggregate = analysis.aggregate_gpu_samples([{"source": "host_nvidia_smi", **result}])
    assert aggregate["source"] == "host_nvidia_smi"


@pytest.mark.parametrize("residual,expected", [(0, None), (20, 20)])
def test_nvmap_residual_is_only_a_positive_estimate(
    monkeypatch: pytest.MonkeyPatch, residual, expected
) -> None:
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: None)
    result = jetson.unified_model_memory({12}, rss_mib=100 + residual, host_rss_mib=100)
    assert result["model_process_gpu_memory_mib"] == expected
    assert result["model_process_gpu_memory_source"] == ("vmrss_residual" if residual else "unavailable")


def test_running_execution_freezes_real_http_and_telemetry(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from photobench.application.run_execution.service import RunRequest
    from photobench.bootstrap.container import Container
    from photobench.config import Settings

    config = tmp_path / "config"
    config.mkdir()
    (config / "runtime_connection.json").write_text(
        json.dumps(
            {
                "sentrix_url": "http://127.0.0.1:8291",
                "model_base_url": "http://127.0.0.1:8100/v1",
                "embedding": {"mode": "no_local_embedding", "local": False},
            }
        )
    )
    (config / "judge_providers.json").write_text(
        json.dumps(
            {
                "default_provider_id": "cloud",
                "providers": {"cloud": {"url": "https://judge.example/v1", "model": "judge"}},
            }
        )
    )
    (config / "vllm_targets.json").write_text(
        json.dumps(
            {
                "default_target_id": "local",
                "targets": {"local": {"model_base_url": "http://127.0.0.1:8100/v1"}},
            }
        )
    )
    container = Container.build(Settings(config, tmp_path / "results", tmp_path / "data"))
    request = RunRequest(
        album_id="a",
        qa_set="q",
        model_profile="__current__",
        sentrix_url="http://127.0.0.1:8291",
        model_base_url="http://127.0.0.1:8100/v1",
        runtime_framework="llama_cpp",
    )
    execution = container.run_execution(request)
    urls = []
    monkeypatch.setattr(container.http, "request_json", lambda url, **kw: urls.append(url) or {"items": []})
    container.config.persist_runtime_connection(
        {
            "sentrix_url": "http://127.0.0.1:9999",
            "model_base_url": "http://127.0.0.1:8106/v1",
            "judge_url": "https://judge.example/v1",
            "judge_model": "judge",
        }
    )
    execution.client.list_assets(scope_id="x", limit=10)
    assert urls and all(url.startswith("http://127.0.0.1:8291/") for url in urls)
    assert execution.runtime_snapshot.connection()["model_base_url"].endswith(":8100/v1")
    assert execution.telemetry.telemetry_services["main_model"]["ports"] == [8100]


def test_missing_auxiliary_listener_is_unknown_but_explicit_absence_is_zero(monkeypatch):
    from photobench.infrastructure.runtime import providers

    monkeypatch.setattr(providers, "_host_process_rows", lambda: [{"pid": 1, "listening_ports": [8100]}])
    monkeypatch.setattr(providers, "_process_pss_mib", lambda pid: 10)
    scope = {"main_model": {"ports": [8100]}, "embedding": {}, "product": []}
    absent = providers._augment_host_process_memory({}, telemetry_services=scope, compute_apps=[])
    assert absent["sentrix_auxiliary_model_pss_mib"] == 0
    assert absent["product_stack_memory_mib"] == 10
    scope["embedding"] = {"mode": "split", "profiles": {"split": {"ports": [8101, 8103]}}}
    failed = providers._augment_host_process_memory({}, telemetry_services=scope, compute_apps=[])
    assert failed["sentrix_auxiliary_model_pss_mib"] is None
    assert failed["product_stack_memory_mib"] is None


def test_orphan_backup_does_not_block_recovery_after_interrupted_replace(tmp_path):
    from photobench.infrastructure.results.recovery import FileRunStartupRecovery

    run = tmp_path / "old"
    run.mkdir()
    (run / "run.json").write_text('{"status":"cancelling"}')
    backup = run / "run.before-orphan-recovery.json"
    backup.write_text("first evidence")
    recovery = FileRunStartupRecovery(tmp_path)
    try:
        assert recovery.acquire_and_recover() == 1
        assert backup.read_text() == "first evidence"
        assert json.loads((run / "run.json").read_text())["status"] == "interrupted"
    finally:
        recovery.close()


def test_native_parse_facts_exclude_text_and_count_bad_json() -> None:
    item = {
        "metrics_contract_version": 2,
        "termination_reason": "model_error",
        "agent_status": "error",
        "execution_trace": [
            {"stage": "model", "action": "final", "call_type": "agent"},
            {
                "stage": "model",
                "call_type": "agent",
                "tool_argument_parse_facts": [{"json_object_valid": True}, {"json_object_valid": False}],
            },
        ],
    }
    result = analysis.agent_stability(item)
    assert result["tool_argument_json_total"] == 2
    assert result["tool_argument_json_rate"] == 0.5
    assert result["json_parse_total"] is None
    assert result["completed_within_steps"] is False


def test_agent_wall_is_not_reduced_by_overlapping_judge() -> None:
    item = {"metrics_contract_version": 2, "wall_clock_ms": 100, "judge_ms": 300}
    result = analysis.capability_summary(
        [item], {"qa_eval": {"agent_phase_wall_ms": 200, "agent_completed": 1}}
    )
    assert result["agent_task_latency_mean_ms"] == 100
    assert result["agent_throughput_qa_per_s"] == 5


def test_stream_exit_and_age_invalidate_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from photobench.infrastructure.runtime import apple_telemetry as apple

    class Process:
        dead = False

        def poll(self):
            return 1 if self.dead else None

    process = Process()
    stream = apple._MacmonStream()
    stream._proc = process
    stream._observed_at = 10
    stream._latest = {"power_draw_w": 10}
    monkeypatch.setattr(apple.time, "monotonic", lambda: 12)
    assert stream.latest()["power_draw_w"] == 10
    monkeypatch.setattr(apple.time, "monotonic", lambda: 14)
    assert stream.latest() == {}
    process.dead = True
    monkeypatch.setattr(stream, "start", lambda: None)
    assert stream.latest() == {}


def test_single_instance_orphan_recovery_preserves_original(tmp_path) -> None:
    from photobench.infrastructure.results.recovery import FileRunStartupRecovery

    run = tmp_path / "r"
    run.mkdir()
    original = json.dumps({"run_id": "r", "status": "cancelling", "items": [{"answer": "retained"}]})
    (run / "run.json").write_text(original)
    recovery = FileRunStartupRecovery(tmp_path)
    assert recovery.acquire_and_recover() == 1
    assert (run / "run.before-orphan-recovery.json").read_text() == original
    saved = json.loads((run / "run.json").read_text())
    assert saved["status"] == "interrupted" and saved["items"][0]["answer"] == "retained"
    second = FileRunStartupRecovery(tmp_path)
    with pytest.raises(RuntimeError, match="live owner"):
        second.acquire_and_recover()
    recovery.close()
    assert second.acquire_and_recover() == 0
    second.close()


def test_jetson_device_cache_cannot_extend_stream_lifetime(monkeypatch):
    class Stream:
        _observed_at = 10
        line = "VDD_IN 12000mW/11000mW GR3D_FREQ 80%"

        def latest_line(self):
            return self.line

    stream = Stream()
    monkeypatch.setattr(jetson, "_TEGRASTATS", stream)
    monkeypatch.setattr(jetson, "_meminfo_mib", lambda: {"system_memory_used_mib": 100})
    monkeypatch.setattr(jetson.time, "monotonic", lambda: 10.1)
    provider = jetson.LocalJetsonLlamaCppTelemetryProvider("http://127.0.0.1:8100")
    assert provider._device()["power_draw_w"] == 12
    stream.line = ""
    result = provider._device()
    assert "power_draw_w" not in result
    assert result["system_memory_used_mib"] == 100


def test_apple_scoped_model_workers_deduplicate_shared_roles(monkeypatch):
    from photobench.infrastructure.runtime import apple_telemetry as apple

    rows = [{"pid": 1, "ppid": 0}, {"pid": 2, "ppid": 1}, {"pid": 3, "ppid": 0}]
    for row in rows:
        row["process_name"] = "worker"
    provider = apple.LocalAppleUnifiedTelemetryProvider(
        "http://127.0.0.1:8100",
        telemetry_services={
            "main_model": {"ports": [8100]},
            "embedding": {"mode": "split", "profiles": {"split": {"ports": [8101]}}},
            "sentrix": {},
            "product": [{"ports": [8101]}],
        },
    )
    monkeypatch.setattr(provider, "_device", lambda: {})
    monkeypatch.setattr(provider, "_ports", lambda: {1: {"8100"}, 2: {"8101"}, 3: {"8106"}})
    monkeypatch.setattr(apple, "_process_rows", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(apple, "_phys_footprint_bytes", lambda pid: 100 * 1048576)
    monkeypatch.setattr(apple, "_weight_map_bytes", lambda pid: 10 * 1048576)
    data = provider._augment()
    assert data["model_process_footprint_mib"] == 200
    assert data["product_stack_memory_mib"] == 220
    assert data["sentrix_auxiliary_model_pss_mib"] == 0
    assert data["memory_complete"] is True
    provider.telemetry_services["embedding"]["profiles"]["split"]["ports"] = [8103]
    assert provider._augment()["memory_complete"] is False


def test_orphan_rejudge_on_terminal_run_is_also_recovered(tmp_path):
    from photobench.infrastructure.results.recovery import FileRunStartupRecovery

    run = tmp_path / "r"
    run.mkdir()
    original = {"status": "completed", "rejudge": {"status": "running"}, "items": [{"answer": "keep"}]}
    (run / "run.json").write_text(json.dumps(original))
    lease = FileRunStartupRecovery(tmp_path)
    try:
        assert lease.acquire_and_recover() == 1
        result = json.loads((run / "run.json").read_text())
        assert result["status"] == "completed"
        assert result["rejudge"]["status"] == "interrupted"
        assert result["items"] == original["items"]
        assert json.loads((run / "run.before-orphan-recovery.json").read_text()) == original
    finally:
        lease.close()


def test_jetson_no_auxiliary_is_zero_and_disappeared_model_cannot_reuse_total(monkeypatch):
    rows = [{"pid": 1, "ppid": 0, "rss_mib": 10, "listening_ports": [8100], "process_name": "llama-server"}]
    monkeypatch.setattr(jetson, "_host_process_rows", lambda: rows)
    monkeypatch.setattr(jetson, "_process_pss_mib", lambda pid: 8)
    monkeypatch.setattr(jetson, "_process_smaps_field_mib", lambda pid, field: 10)
    monkeypatch.setattr(jetson, "_nvmap_clients_mib", lambda: {1: 20})
    scope = {"main_model": {"ports": [8100]}, "embedding": {}, "sentrix": {}, "product": []}
    good = jetson._augment_local_process_memory(
        {}, model_pid=None, endpoint_port="8100", telemetry_services=scope
    )
    assert good["product_stack_memory_mib"] == 28
    assert good["model_process_system_memory_used_mib"] == 30
    assert good["sentrix_auxiliary_model_uma_mib"] == 0
    rows.clear()
    lost = jetson._augment_local_process_memory(
        good, model_pid=None, endpoint_port="8100", telemetry_services=scope
    )
    assert lost["product_stack_memory_mib"] is None
    assert lost["model_process_system_memory_used_mib"] is None
    assert lost["root_pid"] is None


def test_jetson_and_nvidia_memory_remains_available_without_device_source(monkeypatch):
    from photobench.infrastructure.runtime import providers

    monkeypatch.setattr(
        jetson, "_augment_local_process_memory", lambda *a, **k: {"model_process_system_memory_used_mib": 10}
    )
    monkeypatch.setattr(jetson, "_meminfo_mib", lambda: {"system_memory_used_mib": 100})
    provider = jetson.LocalJetsonLlamaCppTelemetryProvider("http://127.0.0.1:8100")
    assert provider.process_memory()["data"]["model_process_system_memory_used_mib"] == 10
    assert provider.system_memory()["data"]["system_memory_used_mib"] == 100
    nvidia = providers.HostNvidiaTelemetryProvider(endpoint_url="http://127.0.0.1:8100")

    def fail(*a, **k):
        raise OSError("GPU driver unavailable")

    monkeypatch.setattr(nvidia, "_query", fail)
    monkeypatch.setattr(
        providers,
        "_augment_host_process_memory",
        lambda *a, **k: {"model_process_system_memory_used_mib": 20},
    )
    result = nvidia.process_memory()
    assert result["status"] == "available"
    assert result["data"]["model_process_system_memory_used_mib"] == 20
    assert result["data"]["process_memory_used_mib"] is None


def test_linux_memory_never_substitutes_memfree_for_memavailable(monkeypatch):
    from photobench.domain.runtime.metrics import system_used

    assert system_used(100, 30) == 70
    assert system_used(100, None) is None
    assert system_used(100, 200) is None
    monkeypatch.setattr(jetson.Path, "read_text", lambda *a, **k: "MemTotal: 102400 kB\nMemFree: 1024 kB\n")
    assert jetson._meminfo_mib() == {}
