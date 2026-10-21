#!/usr/bin/env python3
"""Strict T006 preflight and independent acceptance, separate from production run_valid."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TypeGuard
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.deploy.photobench_release import (  # noqa: E402
    digest,
    listener,
    local_url,
    process_environment,
    read_json,
    verify,
    write_json,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Local dependencies must not redirect")


def http_value(url: str, body: dict[str, Any] | None = None, timeout: int = 180) -> Any:
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        return json.load(response)


def http(url: str, body: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
    value = http_value(url, body, timeout)
    require(isinstance(value, dict), "Expected a JSON object")
    result: dict[str, Any] = value
    return result


def probe_text_embedding(url: str, unified: bool) -> tuple[dict[str, Any], str]:
    try:
        return http(url + ("/embed_text" if unified else "/embed"), {"text": "测试图像"}), "sidecar"
    except HTTPError as error:
        if unified or error.code != 404:
            raise
    try:
        result = http(url + "/v1/embeddings", {"input": "测试图像"})
    except HTTPError as error:
        if error.code != 400:
            raise
        detail = json.load(error)
        require(
            (detail.get("error") or {}).get("message")
            == "Pooling type 'none' is not OAI compatible. Please use a different pooling type",
            "Unrecognized text embedding protocol error",
        )
        rows = http_value(url + "/embedding", {"content": "测试图像"})
        require(
            isinstance(rows, list) and len(rows) == 1 and rows[0].get("index") == 0,
            "Native embedding must return one input",
        )
        tokens = rows[0].get("embedding") or []
        require(
            tokens and isinstance(tokens[0], list) and len(tokens[0]) == 1024,
            "Native BGE-M3 CLS must have 1024 dimensions",
        )
        cls = tokens[0]
        require(all(finite(v) for v in cls), "Non-finite native BGE-M3 CLS")
        norm = math.sqrt(sum(v * v for v in cls))
        require(finite(norm) and norm > 0, "Invalid native BGE-M3 CLS norm")
        return {"vector": [v / norm for v in cls]}, "llama_cpp_bge_m3_cls_l2"
    rows = result.get("data") or []
    require(len(rows) == 1, "Text embedding must return one vector")
    return {"vector": rows[0].get("embedding")}, "openai_compatible"


def finite(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(value: Any, reason: str) -> None:
    if not value:
        raise RuntimeError(reason)


def expected_total(parts: list[Any], actual: Any, tolerance: float = 0.1) -> bool:
    expected = sum(parts) if parts and all(finite(v) and v >= 0 for v in parts) else None
    return actual is None if expected is None else finite(actual) and abs(actual - expected) <= tolerance


def check_jetson_product_total(sample: dict[str, Any]) -> bool:
    rows = sample.get("benchmark_processes") or []
    # An absent process observation is unknown, including while the API restarts.
    # GPU allocations alone cannot stand in for the missing related-process PSS.
    parts = [row.get("pss_mib") for row in rows] if rows else [None]
    parts += [sample.get("model_process_gpu_memory_mib"), sample.get("sentrix_auxiliary_model_uma_mib")]
    return expected_total(parts, sample.get("product_stack_memory_mib"))


def check_statistics(samples: list[dict[str, Any]], saved: dict[str, Any], api: dict[str, Any]) -> list[str]:
    """Independent arithmetic over raw values, without importing production formulas."""
    errors = []
    keys = {
        "system_memory_used_mib",
        "model_process_system_memory_used_mib",
        "model_process_host_memory_mib",
        "model_process_gpu_memory_mib",
        "model_process_memory_used_mib",
        "product_stack_memory_mib",
        "power_draw_w",
        "temperature_c",
        "gpu_utilization_pct",
        "model_process_footprint_mib",
        "model_process_mapped_weights_mib",
    }
    for key in sorted(keys):
        values = sorted(float(s[key]) for s in samples if finite(s.get(key)) and s[key] >= 0)
        for label, projection in (("saved", saved), ("api", api)):
            stats = ((projection.get("phases") or {}).get("gpu_metrics") or {}).get(key) or {}
            if not values:
                if stats.get("mean") is not None:
                    errors.append(f"{label}:{key}:missing_became_value")
                continue
            expected = {
                "peak": max(values),
                "mean": round(sum(values) / len(values), 1),
                "p95": values[max(0, math.ceil(len(values) * 0.95) - 1)],
                "valid_count": len(values),
            }
            if any(not finite(stats.get(k)) or abs(stats[k] - v) > 0.01 for k, v in expected.items()):
                errors.append(f"{label}:{key}:full_sample_statistics")
        field_stats = (api.get("telemetry_live") or {}).get("field_stats") or {}
        if values and field_stats.get(key) != ((api.get("phases") or {}).get("gpu_metrics") or {}).get(key):
            errors.append(f"{key}:chart_statistics_mismatch")
    if saved.get("summary") != api.get("summary"):
        errors.append("saved_api_summary_mismatch")
    return errors


def epoch(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return None


def local_listener(url: str, *, allow_local_address: bool = False) -> dict[str, Any]:
    parsed = urlparse(url)
    if allow_local_address:
        local_url(url, preserve_local=True)
    else:
        require(parsed.hostname in {"127.0.0.1", "localhost", "::1"}, f"Non-loopback dependency: {url}")
    require(not parsed.username and not parsed.password, "Credentials in local dependency URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pid = listener(port)
    require(pid, f"Missing local listener: {port}")
    command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip()
    # Loopback nginx/SSH/HAProxy can hide a remote model. A reviewed inference or
    # embedding process must own the listener; llama.cpp RPC/offload is forbidden.
    require(
        not any(
            x in command.lower()
            for x in (
                "ssh ",
                "socat",
                "haproxy",
                "nginx",
                "--rpc",
                "rpc-server",
                "--proxy",
                "--remote",
            )
        ),
        f"Proxy or remote offload found on {port}",
    )
    return {"port": port, "pid": pid, "command": command}


def dataset_hash(root: Path) -> dict[str, str]:
    files = {str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}
    return {
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "file_count": str(len(files)),
    }


def check_retrieval_summary(items: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    eligible = [
        item for item in items if str(item.get("answerability") or "").lower().strip() != "unanswerable"
    ]
    for prefix in ("media", "image", "video", "candidate"):
        field = f"{prefix}_retrieval_counts"
        rows = [
            item[field]
            for item in eligible
            if isinstance(item.get(field), dict) and item[field].get("gt", 0) > 0
        ]
        gt = sum(row["gt"] for row in rows)
        predicted = sum(row["predicted"] for row in rows)
        matched = sum(row["matched"] for row in rows)
        precision = matched / predicted if predicted else (0.0 if gt else None)
        recall = matched / gt if gt else None
        f1 = 2 * matched / (predicted + gt) if gt else None
        for key, value in (("precision", precision), ("recall", recall), ("f1", f1)):
            expected = round(value, 3) if value is not None else None
            if summary.get(f"{prefix}_retrieval_{key}_micro") != expected:
                errors.append(f"{prefix}_retrieval_{key}_arithmetic")
    return errors


def check_nvidia_attribution(sample: dict[str, Any], listener_pid: int) -> bool:
    rows = sample.get("model_system_processes") or []
    allowed = {listener_pid}
    while True:
        expanded = allowed | {row["pid"] for row in rows if row.get("ppid") in allowed}
        if expanded == allowed:
            break
        allowed = expanded
    ram_pids = {row["pid"] for row in rows}
    gpu_pids = {row["pid"] for row in sample.get("model_processes") or []}
    return bool(ram_pids) and ram_pids <= allowed and gpu_pids <= allowed


def face_weights(effective: dict[str, str]) -> list[dict[str, Any]]:
    for key in ("FACE_PROVIDERS", "RETINAFACE_PROVIDERS"):
        require(effective.get(key) == "CPUExecutionProvider", f"CPU face policy not effective: {key}")
    home = Path(effective.get("HOME", str(Path.home())))
    model_dir = (
        Path(effective.get("FACE_MODEL_ROOT", str(home / ".insightface")))
        / "models"
        / effective.get("FACE_MODEL_NAME", "buffalo_l")
    )
    paths = [
        Path(effective.get("RETINAFACE_MODEL_PATH", str(home / "benchmarks/retinaface/retinaface_r50.onnx"))),
        model_dir / "det_10g.onnx",
        model_dir / "w600k_r50.onnx",
    ]
    # Load local ONNX files on CPU in an isolated process; no download path is used.
    program = """import json,sys,onnxruntime as ort
options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
for path in sys.argv[1:]:
 session=ort.InferenceSession(path,sess_options=options,providers=['CPUExecutionProvider'])
 assert session.get_providers()==['CPUExecutionProvider'], path
print(json.dumps({'provider':'CPUExecutionProvider','loaded':len(sys.argv)-1}))
"""
    for path in paths:
        require(path.is_file() and path.stat().st_size > 0, f"Missing local face weight: {path}")
    env = {**os.environ, **effective}
    subprocess.run(
        [sys.executable, "-c", program, *(str(path) for path in paths)],
        env=env,
        check=True,
        capture_output=True,
        timeout=90,
    )
    return [{"path": str(path), "sha256": digest(path), "provider": "CPUExecutionProvider"} for path in paths]


def preflight(root: Path, runtime: Path) -> dict[str, Any]:
    revision = verify(root)["revision"]
    bindings = read_json(runtime / "bindings.json")
    connection = read_json(Path(bindings["config"]) / "runtime_connection.json")
    config = http("http://127.0.0.1:8872/api/v1/config")
    diagnostics = http("http://127.0.0.1:8291/api/v1/diagnostics")
    require(diagnostics.get("status") == "ok", f"Degraded product dependency: {diagnostics}")
    require(diagnostics.get("build_revision") == revision, "Product revision mismatch")
    require(config.get("build_revision") == revision, "PhotoBench revision mismatch")
    for key in ("sentrix_url", "model_base_url", "embedding", "product"):
        require(
            (config.get("runtime_config") or {}).get(key) == connection.get(key),
            f"Effective PhotoBench configuration differs: {key}",
        )
    require(
        config.get("default_vllm_model_base_url") == connection["model_base_url"],
        "PhotoBench environment overrides the model topology",
    )
    runs = http("http://127.0.0.1:8872/api/v1/runs")
    require(runs.get("active_count", 0) == 0, "Active orphan/run remains")
    model_url = connection["model_base_url"].rstrip("/")
    adjustments = read_json(runtime / "topology-adjustments.json")
    override = adjustments.get("local_model_endpoint_override")
    require(not override or override.rstrip("/") == model_url, "Undeclared model address override")
    main = local_listener(model_url, allow_local_address=bool(override))
    api_pid = listener(8291)
    require(api_pid, "No product listener")
    effective = process_environment(int(api_pid or 0))
    require(effective.get("SENTRIX_VLM_BASE_URL") == model_url, "Product/PB model topology mismatch")
    local_hosts = ["127.0.0.1", "localhost", "::1"]
    model_host = urlparse(model_url).hostname
    if model_host and model_host not in local_hosts:
        local_hosts.append(model_host)
    require(effective.get("NO_PROXY") == ",".join(local_hosts), "Local HTTP may use a proxy")
    weights = face_weights(effective)
    model_list = http(model_url + "/models")
    model_id = model_list["data"][0]["id"]
    model_probe = http(
        model_url + "/chat/completions",
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply OK."}],
            "temperature": 0,
            "max_tokens": 32,
        },
    )
    require(model_probe.get("choices"), "Empty main-model probe")
    embedding = connection["embedding"]
    unified = embedding["mode"] == "unified_multimodal"
    text_url = embedding["wemm_url"] if unified else embedding["text_url"]
    image_url = embedding["wemm_url"] if unified else embedding["image_url"]
    for name, url in (("text", text_url), ("image", image_url)):
        key = (
            "SENTRIX_WEMM_URL"
            if unified
            else ("SENTRIX_TEXT_EMBEDDER_URL" if name == "text" else "SENTRIX_LEGACY_CLIP_URL")
        )
        require(effective.get(key) == url, f"Product/PB {name} topology mismatch")
    services = [main, local_listener(text_url), local_listener(image_url)]
    # An actual image on this host; no download, remote storage or embedded QA.
    image = runtime / "embedding-probe.png"
    from PIL import Image

    Image.new("RGB", (96, 96), (220, 50, 50)).save(image)
    text_result, text_protocol = probe_text_embedding(text_url.rstrip("/"), unified)
    image_result = http(image_url.rstrip("/") + "/embed_image", {"path": str(image)})
    dimensions: dict[str, int] = {}
    for name, response, expected in (
        ("text", text_result, 2048 if unified else 1024),
        ("image", image_result, 2048 if unified else 768),
    ):
        vector = response.get("vector") or []
        require(len(vector) == expected and all(finite(v) for v in vector), f"Bad {name} embedding")
        require(sum(v * v for v in vector) > 0, f"Zero {name} embedding")
        dimensions[name] = len(vector)
    # Exercise the vision path on the SAME main endpoint, using local inline pixels.
    vision = http(
        model_url + "/chat/completions",
        {
            "model": model_id,
            "temperature": 0,
            "max_tokens": 32,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe the image briefly."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image.read_bytes()).decode()
                            },
                        },
                    ],
                }
            ],
        },
    )
    require(vision.get("choices"), "Main-model visual probe failed")
    album = Path(bindings["datasets"]) / "album3-14"
    require(album.is_dir(), "Missing album3-14 data")
    report = {
        "revision": revision,
        "diagnostics": diagnostics,
        "connection": {
            key: connection.get(key) for key in ("sentrix_url", "model_base_url", "embedding", "product")
        },
        "services": services,
        "models": model_list,
        "embedding_dimensions": dimensions,
        "text_embedding_protocol": text_protocol,
        "face_weights": weights,
        "dataset": dataset_hash(album),
        "api_pid": api_pid,
        "pb_pid": listener(8872),
        "checked_at_epoch": time.time(),
    }
    write_json(runtime / "preflight.json", report)
    return report


def run(runtime: Path) -> dict[str, Any]:
    preflight_report = read_json(runtime / "preflight.json")
    require(time.time() - preflight_report["checked_at_epoch"] < 900, "Preflight is older than 15 minutes")
    if (runtime / "run-request.json").exists():
        raise RuntimeError(
            "This release already has a recorded request; retain it and use a new attempt folder"
        )
    connection = preflight_report["connection"]
    request = {
        "album_id": "album3-14",
        "qa_set": "compact-10q",
        "mode": "full",
        "models": ["__current__"],
        "model_base_url": connection["model_base_url"],
        "sentrix_url": "http://127.0.0.1:8291",
        "runtime_framework": "llama_cpp",
        "qa_concurrency": 1,
        "turn_timeout": 900,
        "import_timeout": 14400,
    }
    write_json(runtime / "run-request.json", request)
    response = http("http://127.0.0.1:8872/api/v1/runs", request)
    write_json(runtime / "run-response.json", response)
    return response


def audit(runtime: Path) -> dict[str, Any]:
    bindings = read_json(runtime / "bindings.json")
    pre = read_json(runtime / "preflight.json")
    run_id = read_json(runtime / "run-response.json")["run_ids"][0]
    directory = Path(bindings["results"]) / run_id
    saved = read_json(directory / "run.json")
    api = http(f"http://127.0.0.1:8872/api/v1/runs/{run_id}")
    write_json(runtime / "api-result.json", api)
    items = saved.get("items") or []
    if not items and (directory / "results.jsonl").is_file():
        items = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines() if line]
    lines = (directory / "gpu_samples.jsonl").read_text().splitlines()
    samples = [json.loads(line) for line in lines if line]
    chain_errors: list[str] = []
    metric_errors: list[str] = check_statistics(samples, saved, api)
    for phase in (
        "model_deploy",
        "scope_setup",
        "identity_seed",
        "photo_import",
        "pipeline_processing",
        "qa_eval",
        "gpu_metrics",
        "aggregate",
    ):
        if ((saved.get("phases") or {}).get(phase) or {}).get("status") != "done":
            chain_errors.append(f"required_phase_not_done:{phase}")
    if saved.get("status") != "completed" or len(items) != 10:
        chain_errors.append("run_not_completed_with_10_items")
    if saved.get("build_revision") != pre["revision"] or api.get("build_revision") != pre["revision"]:
        metric_errors.append("revision_mismatch")
    scores: list[float] = []
    for index, item in enumerate(items):
        reasons = []
        if item.get("execution_status") != "completed":
            reasons.append("execution_not_completed")
        score = (item.get("judge") or {}).get("score")
        if (
            item.get("judge_status") != "completed"
            or not finite(score)
            or score not in {0, 1, 2}
            or (item.get("judge") or {}).get("consistency_status") == "inconsistent"
        ):
            reasons.append("judge_missing")
        else:
            scores.append(float(score))
        if item.get("termination_reason") in {
            "model_error",
            "planner_call_error",
            "timeout",
            "step_budget",
            "step_budget_exhausted",
            "tool_budget_exhausted",
            "parse_failure",
            "cancelled",
        }:
            reasons.append(str(item.get("termination_reason")))
        calls = item.get("model_call_metrics") or []
        trace = item.get("execution_trace") or []
        if any(call.get("status") in {"error", "failed"} for call in calls + trace):
            reasons.append("recorded_call_error")
        if any(
            tool.get("error") or tool.get("status") in {"error", "failed"}
            for tool in item.get("tool_trace") or []
        ):
            reasons.append("tool_error")
        if reasons:
            chain_errors.append(f"{index}:{','.join(reasons)}")
    phase_report: dict[str, Any] = {}
    for phase in ("photo_import", "pipeline_processing", "qa_eval"):
        points = [s for s in samples if s.get("phase") == phase]
        core = [
            s
            for s in points
            if all(
                finite(s.get(k))
                for k in (
                    "system_memory_used_mib",
                    "model_process_system_memory_used_mib",
                    "product_stack_memory_mib",
                )
            )
            and s.get("memory_complete") is True
            and finite(s.get("process_observed_at_monotonic"))
            and 0 <= s["_t"] - s["process_observed_at_monotonic"] <= 3
        ]
        intervals = [b["_t"] - a["_t"] for a, b in zip(core, core[1:], strict=False)]
        phase_report[phase] = {
            "samples": len(points),
            "valid_core_memory": len(core),
            "max_gap_seconds": max(intervals, default=None),
            "first_epoch": core[0].get("observed_at_epoch") if core else None,
            "last_epoch": core[-1].get("observed_at_epoch") if core else None,
            "valid_fraction": len(core) / len(points) if points else 0,
        }
        bucket = (saved.get("phases") or {}).get(phase) or {}
        start, end = epoch(bucket.get("started_at")), epoch(bucket.get("finished_at"))
        if start is not None and end is not None:
            first = core[0].get("observed_at_epoch") if core else None
            last = core[-1].get("observed_at_epoch") if core else None
            phase_report[phase].update(
                duration_seconds=end - start,
                start_gap_seconds=max(0, first - start) if finite(first) else None,
                end_gap_seconds=max(0, end - last) if finite(last) else None,
                observed_span_fraction=min(1, (last - first) / (end - start))
                if finite(first) and finite(last) and end > start
                else None,
            )
        if not core:
            metric_errors.append(f"no_core_memory_in_{phase}")
        if len(core) != len(points):
            metric_errors.append(f"incomplete_core_memory_in_{phase}")
        if any(
            finite(phase_report[phase].get(k)) and phase_report[phase][k] > 3
            for k in ("max_gap_seconds", "start_gap_seconds", "end_gap_seconds")
        ):
            metric_errors.append(f"core_memory_gap_over_3_seconds_in_{phase}")
    formulas_checked = 0
    rails: set[str] = set()
    for index, sample in enumerate(samples):
        accounting = sample.get("memory_accounting")
        if accounting == "jetson_pss_plus_nvmap_estimate":
            components = [
                sample.get("model_process_host_memory_mib"),
                sample.get("model_process_gpu_memory_mib"),
            ]
            expected = sum(components) if all(finite(v) for v in components) else None
            actual = sample.get("model_process_system_memory_used_mib")
            if (
                expected is None
                and actual is not None
                or expected is not None
                and (not finite(actual) or abs(actual - expected) > 0.05)
            ):
                metric_errors.append(f"{index}:jetson_main_total")
            if not check_jetson_product_total(sample):
                metric_errors.append(f"{index}:jetson_product_total")
            formulas_checked += 1
        elif accounting == "apple_capacity_estimate":
            for total, left, right in (
                (
                    "model_process_system_memory_used_mib",
                    "model_process_footprint_mib",
                    "model_process_mapped_weights_mib",
                ),
                (
                    "product_stack_memory_mib",
                    "product_stack_footprint_mib",
                    "product_stack_mapped_weights_mib",
                ),
            ):
                parts = [sample.get(left), sample.get(right)]
                if not expected_total(parts, sample.get(total)):
                    metric_errors.append(f"{index}:{total}")
            formulas_checked += 1
        elif accounting == "nvidia_pss_and_vram":
            if not check_nvidia_attribution(sample, pre["services"][0]["pid"]):
                metric_errors.append(f"{index}:other_gpu_instance_attributed_to_main")
            if not finite(sample.get("model_process_memory_used_mib")):
                metric_errors.append(f"{index}:missing_main_vram")
            if sample.get("source") in {"jetson_local_pss", "orin_ssh_pss"}:
                metric_errors.append(f"{index}:nvidia_labeled_jetson")
            formulas_checked += 1
        if sample.get("power_rail") and finite(sample.get("power_draw_w")):
            rails.add(str(sample["power_rail"]))
        if sample.get("device_observation_age_seconds", 0) > 3 and finite(sample.get("power_draw_w")):
            metric_errors.append(f"{index}:stale_device_power")
    if not formulas_checked:
        metric_errors.append("no_accounted_samples")
    if any(s.get("memory_accounting") == "jetson_pss_plus_nvmap_estimate" for s in samples) and not rails:
        metric_errors.append("no_labeled_jetson_power")
    if saved.get("runtime_snapshot") != api.get("runtime_snapshot"):
        metric_errors.append("snapshot_projection_mismatch")
    for projection in (saved, api):
        summary = projection.get("summary") or {}
        metric_errors.extend(check_retrieval_summary(items, summary))
        aqm = round(sum(scores) / len(scores), 3) if scores else None
        if summary.get("answer_quality_mean") != aqm or summary.get("judge_valid_count") != len(scores):
            metric_errors.append("judge_summary_arithmetic")
        agent_times = [item["wall_clock_ms"] for item in items if finite(item.get("wall_clock_ms"))]
        expected_latency = round(sum(agent_times) / len(agent_times), 1) if agent_times else None
        if summary.get("agent_task_latency_mean_ms") != expected_latency:
            metric_errors.append("agent_latency_arithmetic")
    for role in ("api", "photobench"):
        log = runtime / f"{role}.log"
        if log.exists():
            tail = log.read_text(errors="replace")[-2_000_000:].lower()
            if any(x in tail for x in ("out of memory", "outofmemoryerror", "oom-kill", "killed process")):
                chain_errors.append(f"{role}:oom_in_service_log")
    if listener(8291) != pre["api_pid"] or listener(8872) != pre["pb_pid"]:
        chain_errors.append("unexpected_service_restart")
    report = {
        "run_id": run_id,
        "revision": pre["revision"],
        "chain_pass": not chain_errors,
        "metrics_pass": not metric_errors,
        "chain_errors": chain_errors,
        "metric_errors": metric_errors,
        "items": len(items),
        "valid_judge_scores": len(scores),
        "aqm": sum(float(score) for score in scores) / len(scores) if scores else None,
        "samples": len(samples),
        "formulas_checked": formulas_checked,
        "phases": phase_report,
        "power_rails": sorted(rails),
        "raw_directory": str(directory),
        "dataset": pre["dataset"],
    }
    write_json(runtime / "acceptance.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "run", "audit"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "preflight":
        report = preflight(args.root.resolve(), args.runtime.resolve())
    elif args.action == "run":
        report = run(args.runtime.resolve())
    else:
        report = audit(args.runtime.resolve())
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
