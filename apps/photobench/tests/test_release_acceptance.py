"""Release and acceptance guardrails exercised without touching real listeners."""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/deploy/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relative_paths_bind_to_the_old_deployment(tmp_path):
    release = script("photobench_release")
    assert release.bound_path("data", tmp_path) == tmp_path / "data"
    assert release.bound_path(tmp_path / "absolute", tmp_path / "other") == tmp_path / "absolute"


def test_unknown_total_cannot_pass_as_zero():
    acceptance = script("photobench_acceptance")
    assert acceptance.expected_total([10, None], None)
    assert not acceptance.expected_total([10, None], 10)
    assert not acceptance.expected_total([None], 0)
    assert acceptance.expected_total([0, 10], 10)


def test_preserved_lan_endpoint_must_belong_to_this_host(monkeypatch):
    release = script("photobench_release")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda name: (name, [], ["192.168.0.118"]))
    endpoint = "http://192.168.0.118:8103/v1"
    assert release.local_url(endpoint, preserve_local=True) == endpoint
    assert release.local_url(endpoint) == "http://127.0.0.1:8103/v1"
    with pytest.raises(RuntimeError, match="not on this host"):
        release.local_url("http://203.0.113.88:8103/v1", preserve_local=True)


def test_raw_statistics_detect_downsampled_or_null_coerced_summary():
    acceptance = script("photobench_acceptance")
    points = [{"system_memory_used_mib": value} for value in (None, 0, 20)]
    good = {"peak": 20, "mean": 10, "p95": 20, "valid_count": 2}
    result = {
        "phases": {"gpu_metrics": {"system_memory_used_mib": good}},
        "telemetry_live": {"field_stats": {"system_memory_used_mib": good}},
    }
    assert acceptance.check_statistics(points, result, result) == []
    bad = {"phases": {"gpu_metrics": {"system_memory_used_mib": {**good, "mean": 6.7}}}}
    assert "saved:system_memory_used_mib:full_sample_statistics" in acceptance.check_statistics(
        points, bad, result
    )


def test_release_verification_rejects_modified_source(tmp_path):
    release = script("photobench_release")
    (tmp_path / "source.py").write_text("version=1")
    release.write_json(tmp_path / "build-info.json", {"revision": "fixed"})
    release.write_json(
        tmp_path / "release-manifest.json",
        {
            "revision": "fixed",
            "sha256": {"source.py": release.digest(tmp_path / "source.py")},
        },
    )
    assert release.verify(tmp_path)["revision"] == "fixed"
    (tmp_path / "source.py").write_text("version=2")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        release.verify(tmp_path)


def test_attribution_cannot_validate_an_unrelated_ram_and_gpu_pair():
    acceptance = script("photobench_acceptance")
    sample = {
        "model_system_processes": [{"pid": 10, "ppid": 1}, {"pid": 11, "ppid": 10}],
        "model_processes": [{"pid": 11}],
    }
    assert acceptance.check_nvidia_attribution(sample, 10)
    sample["model_system_processes"].append({"pid": 99, "ppid": 1})
    sample["model_processes"].append({"pid": 99})
    assert not acceptance.check_nvidia_attribution(sample, 10)


def test_typed_media_arithmetic_keeps_types_and_exclusions():
    acceptance = script("photobench_acceptance")
    items = [
        {"media_retrieval_counts": {"gt": 2, "predicted": 4, "matched": 1}},
        {
            "answerability": "unanswerable",
            "media_retrieval_counts": {"gt": 100, "predicted": 100, "matched": 100},
        },
    ]
    good = {
        "media_retrieval_precision_micro": 0.25,
        "media_retrieval_recall_micro": 0.5,
        "media_retrieval_f1_micro": 0.333,
    }
    assert acceptance.check_retrieval_summary(items, good) == []
    assert "media_retrieval_recall_arithmetic" in acceptance.check_retrieval_summary(
        items, {**good, "media_retrieval_recall_micro": 1}
    )
