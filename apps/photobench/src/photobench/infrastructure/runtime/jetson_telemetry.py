"""1.0 ``backend/jetson_telemetry.py``（660 行 → 实测 521 行）在 2.0 的重建。

**它的调用者只有一个**：1.0 ``select_runtime_providers``（``benchmark_orchestrator.py:176``）
里「本机是 Jetson 且端点是本机 llama.cpp」那一路会构造
``LocalJetsonLlamaCppTelemetryProvider``。S9a 落地时没搬它（1.0 的
``backend/runtime_providers.py`` **import 了它两个名字却从未使用**，ruff F401），
S9a 施工记录把它裁决给 S9b；S9b 切三段时漏分，S9b-2 施工记录的偏离 1 再次上报，
**S9b-3 收下它**——它与 ``select_runtime_providers`` 是一块（provider 选择 + 遥测）。

**为什么不能与 ``infrastructure/runtime/providers.py`` 合并。** 两份都要读 ``/proc``、
都要按端口/关键词归属进程，但**它们的主机形态不同**：这一份只对本机 Jetson（Orin）
成立，常量里多 ``8100``/``8101``/``6333``，进程归属走 PSS + tegrastats；providers.py
那一份服务「任意 NVIDIA 宿主机」，归属走 nvidia-smi 的 compute-apps。S9a 已按
「Apple 那份不与 providers 合并」的同一理由保留独立性，这里照同一条口径——合并会
悄悄改变两台主机上的归属结果。

**它是基础设施**：起 ``tegrastats`` 子进程、读 ``/proc``、打 llama.cpp 的 ``/metrics``。
按 ``photobench-infrastructure-purity`` 只能落在 infrastructure；端口在 domain。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from photobench.domain.runtime.metrics import component_total, system_used
from photobench.infrastructure.runtime.telemetry_scope import (
    dedupe_rows,
    load_telemetry_services,
    service_resolved,
    service_rows,
)


def _configured_set(env_name: str, defaults: tuple[str, ...]) -> set[str]:
    """1.0 同名函数照搬：环境变量给了就整份替换（不是并入），空项剔除。"""
    raw = os.environ.get(env_name)
    if not raw:
        return set(defaults)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


#: 这份是与 providers.py **独立的一份**（S9a 对 Apple 那份的裁决，同一条理由）：
#: Jetson 上模型进程绑的是 8100/8101，而 providers.py 的默认集里那两条用的是 2.0 的
#: 8772/8191。合并会让一台 Jetson 上的归属结果跟着另一台主机的端口表变。
SYSTEM_PROCESS_PORTS = _configured_set(
    "PHOTOBENCH_SYSTEM_PROCESS_PORTS",
    ("8771", "8091", "8500", "8501", "8100", "8101", "6333"),
)
SYSTEM_PROCESS_KEYWORDS = _configured_set(
    "PHOTOBENCH_SYSTEM_PROCESS_KEYWORDS",
    (
        "photobench",
        "benchmark_orchestrator.py",
        "sentrix",
        "vllm",
        "llama-server",
        "ollama",
        "qdrant",
    ),
)


def _process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        text = raw.replace(bytes([0]), bytes([32])).decode("utf-8", "replace").strip()
        if text:
            return text
    except (OSError, ValueError):
        pass
    # cmdline 读不到（内核线程 / 权限）时退回 comm；两者都读不到才是空串。
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _process_rss_mib(pid: int) -> float | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return round(float(line.split()[1]) / 1024.0, 2)
    except (OSError, ValueError, IndexError):
        return None
    return None


NVMAP_CLIENTS_PATH = os.environ.get("PHOTOBENCH_NVMAP_CLIENTS_PATH", "/sys/kernel/debug/nvmap/iovmm/clients")
NVMAP_CACHE_SECONDS = max(0.0, float(os.environ.get("PHOTOBENCH_NVMAP_CACHE_SECONDS", "2")))

_nvmap_cache: dict[str, Any] = {"at": float("-inf"), "table": None}


def _read_nvmap_clients() -> str | None:
    """Read the Tegra nvmap client table, or None when it is not readable.

    The table lives in debugfs and is root-only. A passwordless sudo rule for
    exactly this one file is the deployment hook (see the README); without it
    the caller must treat GPU memory as *unknown*, never as zero.
    """
    try:
        return Path(NVMAP_CLIENTS_PATH).read_text(encoding="ascii", errors="replace")
    except OSError:
        pass
    sudo = shutil.which("sudo")
    if not sudo:
        return None
    try:
        completed = subprocess.run(
            [sudo, "-n", "cat", NVMAP_CLIENTS_PATH],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or None


def _nvmap_clients_mib() -> dict[int, float] | None:
    """Per-PID nvmap (GPU/UMA) allocation in MiB, or None when unreadable."""
    now = time.monotonic()
    if now - float(_nvmap_cache["at"]) < NVMAP_CACHE_SECONDS:
        cached = _nvmap_cache["table"]
        return cached if isinstance(cached, dict) else None
    text = _read_nvmap_clients()
    table: dict[int, float] | None = None
    if text:
        parsed: dict[int, float] = {}
        for line in text.splitlines():
            parts = line.split()
            # CLIENT PROCESS PID SIZE — the trailing "total" row has 2 fields.
            if len(parts) < 4 or not parts[2].isdigit() or not parts[3].endswith("K"):
                continue
            try:
                size_mib = float(parts[3][:-1]) / 1024.0
            except ValueError:
                continue
            pid = int(parts[2])
            parsed[pid] = round(parsed.get(pid, 0.0) + size_mib, 3)
        table = parsed
    _nvmap_cache["at"] = now
    _nvmap_cache["table"] = table
    return table


def _process_gpu_memory_mib(
    model_pids: set[int], *, rss_mib: float | None, host_rss_mib: float | None
) -> tuple[float | None, str]:
    """GPU/UMA memory charged to the model process, plus which source supplied it.

    The nvmap ledger is the allocation basis and is authoritative when readable.
    The residual (VmRSS - smaps Rss) is a residency basis and is real only on
    L4T >= R36.4.7; on R36.4.3 it is structurally zero because nvmap.ko does not
    charge GPU pages to the process. A zero residual therefore means "not
    accounted", never "not used", and must not be reported as a real value.
    """
    table = _nvmap_clients_mib()
    if table is not None and model_pids:
        # The table is complete, so an absent PID really does mean zero and a
        # listed value outranks the residual.
        return round(sum(table.get(int(pid), 0.0) for pid in model_pids), 3), "nvmap_iovmm"
    if rss_mib is None or host_rss_mib is None:
        return None, "unavailable"
    residual = round(max(0.0, float(rss_mib) - float(host_rss_mib)), 3)
    if residual <= 0:
        return None, "unavailable"
    return residual, "vmrss_residual"


JETSON_UNIFIED_MEMORY_UNIT = "host_smaps_rss_plus_gpu_mib"
JETSON_UNIFIED_MEMORY_METHOD = "orin_smaps_rss_plus_gpu_uma_v1"
JETSON_UNIFIED_MEMORY_SCOPE = "host_smaps_rss_plus_gpu_uma"


def apply_jetson_memory_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """统一口径的样本必须带独立的 unit/method/scope。

    只写 ``model_process_host_memory_mib`` 而不改 ``memory_unit``，读侧会继续按
    旧的 ``process_pss_uma_mib`` / ``orin_process_pss_uma_v1`` 解释，前端就会把
    「smaps Rss + GPU」显示成 VmRSS。这里把三处元数据一次改齐；没有拆分字段时
    保持旧口径（远端 PSS provider / 历史样本）。
    """
    if data.get("model_process_host_memory_mib") is None:
        return data
    data["memory_unit"] = JETSON_UNIFIED_MEMORY_UNIT
    data["memory_scope"] = JETSON_UNIFIED_MEMORY_SCOPE
    data["memory_profile"] = {
        "method": JETSON_UNIFIED_MEMORY_METHOD,
        "memory_unit": JETSON_UNIFIED_MEMORY_UNIT,
        "scope": JETSON_UNIFIED_MEMORY_SCOPE,
    }
    return data


def unified_model_memory(
    model_pids: set[int], *, rss_mib: float | None, host_rss_mib: float | None
) -> dict[str, Any]:
    """Jetson CPU residency + GPU allocation estimate, with explicit unknowns.

    PSS and nvmap are different accounting views; no universal physical-page
    deduplication is claimed across driver versions or shared GPU buffers.
    """
    result: dict[str, Any] = {}
    if rss_mib is None and host_rss_mib is None:
        return result
    if host_rss_mib is None:
        # Unknown host residency cannot be replaced by a different accounting basis.
        result["model_process_system_memory_used_mib"] = None
        result["model_process_host_memory_mib"] = None
        result["model_process_gpu_memory_mib"] = None
        result["memory_complete"] = False
        result["memory_accounting"] = "jetson_pss_plus_nvmap_estimate"
        result["model_process_system_memory_scope"] = "host_smaps_rss_plus_gpu_uma"
        result["model_process_gpu_memory_source"] = "unavailable"
        return result
    gpu_mib, gpu_source = _process_gpu_memory_mib(model_pids, rss_mib=rss_mib, host_rss_mib=host_rss_mib)
    total = component_total([host_rss_mib, gpu_mib])
    result["memory_accounting"] = "jetson_pss_plus_nvmap_estimate"
    result["memory_complete"] = total is not None
    result["model_process_system_memory_used_mib"] = total
    result["model_process_system_memory_scope"] = "host_smaps_rss_plus_gpu_uma"
    result["model_process_host_memory_mib"] = round(float(host_rss_mib), 2)
    result["model_process_gpu_memory_mib"] = round(gpu_mib, 2) if gpu_mib is not None else None
    result["model_process_gpu_memory_source"] = gpu_source
    return result


def _listening_ports_by_pid(target_ports: set[str] | None = None) -> dict[int, set[str]]:
    """``/proc/net/tcp`` 的监听 socket → 持有它的 PID。只收 ``target_ports`` 里的端口。

    1.0 照搬。socket inode 是两张表之间的桥：``/proc/net/tcp`` 给 inode→端口，
    ``/proc/*/fd`` 的 readlink 给 fd→inode。不给 ``target_ports`` 过滤的话，一台
    跑满连接的机器上这一步会扫遍所有 fd——这是采样线程每 0.5 秒跑一次的热路径。
    """
    target_ports = {str(port) for port in (target_ports or set())}
    socket_ports: dict[str, str] = {}
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":  # 0A = LISTEN
                continue
            try:
                port = str(int(parts[1].rsplit(":", 1)[1], 16))
            except (ValueError, IndexError):
                continue
            if target_ports and port not in target_ports:
                continue
            socket_ports[parts[9]] = port
    if not socket_ports:
        return {}
    result: dict[int, set[str]] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        fd_dir = proc / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if match and match.group(1) in socket_ports:
                result.setdefault(int(proc.name), set()).add(socket_ports[match.group(1)])
    return result


def _process_parent_pid(pid: int) -> int | None:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _host_process_rows() -> list[dict[str, Any]]:
    ports_by_pid = _listening_ports_by_pid(set())
    rows = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        pid = int(proc.name)
        rss_mib = _process_rss_mib(pid)
        cmdline = _process_cmdline(pid)
        if rss_mib is None and not cmdline:
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": _process_parent_pid(pid),
                "process_name": Path(cmdline.split(" ", 1)[0]).name if cmdline else "",
                "rss_mib": rss_mib,
                "listening_ports": sorted(ports_by_pid.get(pid, set()), key=lambda value: int(value)),
                "identity": cmdline.lower()[:1000],
            }
        )
    return rows


def _is_system_related_process(row: dict[str, Any]) -> bool:
    identity = str(row.get("identity") or "").lower()
    ports = {str(port) for port in row.get("listening_ports") or []}
    if ports & SYSTEM_PROCESS_PORTS:
        return True
    return any(keyword and keyword in identity for keyword in SYSTEM_PROCESS_KEYWORDS)


def _sum_mib(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows or any(not isinstance(row.get(key), (int, float)) for row in rows):
        return None
    return round(sum(float(row[key]) for row in rows), 2)


def _process_smaps_memory_mib(pid: int) -> tuple[float | None, float | None]:
    """Read PSS and RSS in one kernel walk, so both describe the same observation."""
    values: dict[str, float] = {}
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as file:
            for line in file:
                key = line.split(":", 1)[0]
                if key in {"Pss", "Rss"}:
                    values[key] = round(int(line.split()[1]) / 1024, 2)
    except (OSError, ValueError, IndexError):
        return None, None
    return values.get("Pss"), values.get("Rss")


def _is_model_process(row: dict[str, Any], *, model_pid: int | None, endpoint_port: str) -> bool:
    identity = str(row.get("identity") or "")
    return (
        (model_pid is not None and row.get("pid") == model_pid)
        or "llama-server" in identity
        or f"--port {endpoint_port}" in identity
        or f"--port {endpoint_port} " in identity
    )


def _augment_local_process_memory(
    data: dict[str, Any] | None,
    *,
    model_pid: int | None,
    endpoint_port: str,
    telemetry_services: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fresh related-PID PSS plus main/auxiliary nvmap allocation estimates."""
    # Never let a disappeared PID or failed smaps read inherit a previous total.
    data = {
        key: value
        for key, value in (data or {}).items()
        if not key.startswith(
            ("model_process_", "model_system_", "sentrix_", "product_stack_", "benchmark_process_")
        )
    }
    data.update(
        model_process_system_memory_used_mib=None,
        model_process_host_memory_mib=None,
        model_process_gpu_memory_mib=None,
        model_process_gpu_memory_source="unavailable",
        memory_accounting="jetson_pss_plus_nvmap_estimate",
    )
    try:
        rows = _host_process_rows()
    except Exception:
        rows = []
    scope = telemetry_services if telemetry_services is not None else load_telemetry_services()
    if scope:
        main_spec = dict(scope.get("main_model") or {})
        model_rows = service_rows(rows, main_spec) if main_spec.get("local") is not False else []
        model_pids = {row["pid"] for row in model_rows}
        embedding = dict(scope.get("embedding") or {})
        mode = str(embedding.get("mode") or "")
        profile = dict((embedding.get("profiles") or {}).get(mode) or {})
        if embedding.get("local") is False or profile.get("local") is False:
            profile = {}
        auxiliary_rows = [row for row in service_rows(rows, profile) if row["pid"] not in model_pids]
        auxiliary_pids = {row["pid"] for row in auxiliary_rows}
        product_rows: list[dict[str, Any]] = []
        for product_spec in scope.get("product") or []:
            if isinstance(product_spec, dict):
                product_rows.extend(service_rows(rows, product_spec))
        product_rows.extend(service_rows(rows, dict(scope.get("sentrix") or {})))
        product_rows = [
            row
            for row in dedupe_rows(product_rows)
            if row["pid"] not in model_pids and row["pid"] not in auxiliary_pids
        ]
        sentrix_rows = dedupe_rows(product_rows + auxiliary_rows)
    else:
        model_rows = [
            row
            for row in rows
            if _is_model_process(row, model_pid=model_pid, endpoint_port=str(endpoint_port))
        ]
        product_rows = [row for row in rows if _is_system_related_process(row)]
        sentrix_rows = [
            row for row in product_rows if row.get("pid") not in {item.get("pid") for item in model_rows}
        ]
        auxiliary_rows = []
    observations = {
        int(row["pid"]): _process_smaps_memory_mib(int(row["pid"]))
        for row in dedupe_rows(sentrix_rows + model_rows)
    }
    for row in sentrix_rows + model_rows + product_rows + auxiliary_rows:
        row["pss_mib"], row["smaps_rss_mib"] = observations[int(row["pid"])]
    model_pids = {row["pid"] for row in model_rows}
    model_rss = _sum_mib(model_rows, "rss_mib")
    model_pss = _sum_mib(model_rows, "pss_mib")
    model_smaps_rss = _sum_mib(model_rows, "smaps_rss_mib")
    sentrix_pss = _sum_mib(sentrix_rows, "pss_mib")
    auxiliary_pids = {row["pid"] for row in auxiliary_rows}
    auxiliary_memory = unified_model_memory(
        auxiliary_pids,
        rss_mib=_sum_mib(auxiliary_rows, "rss_mib"),
        host_rss_mib=_sum_mib(auxiliary_rows, "smaps_rss_mib"),
    )
    auxiliary_expected = (
        bool(
            embedding.get("local") is not False
            and (profile.get("ports") or profile.get("pids") or profile.get("keywords"))
        )
        if scope
        else bool(auxiliary_rows)
    )
    auxiliary_uma = (
        auxiliary_memory.get("model_process_gpu_memory_mib") if auxiliary_rows or auxiliary_expected else 0.0
    )
    model_memory = unified_model_memory(model_pids, rss_mib=model_rss, host_rss_mib=model_smaps_rss)
    main_complete = bool(model_rows) and (not scope or service_resolved(rows, main_spec))
    data["root_pid"] = next(
        (
            row["pid"]
            for row in model_rows
            if str(endpoint_port) in {str(p) for p in row.get("listening_ports") or []}
        ),
        None,
    )
    data["process_memory_used_mib"] = model_pss if main_complete else None
    data["model_system_processes"] = []
    if model_memory and main_complete:
        data.update(model_memory)
        data["model_system_processes"] = [
            {key: row.get(key) for key in ("pid", "process_name", "rss_mib", "pss_mib", "listening_ports")}
            for row in model_rows[:20]
        ]
    roles_complete = not scope or all(
        service_resolved(rows, spec)
        for spec in [dict(scope.get("sentrix") or {}), profile, *(scope.get("product") or [])]
        if isinstance(spec, dict)
    )
    sentrix_pss_complete = (
        roles_complete
        and (bool(sentrix_rows) or bool(scope))
        and all(row.get("pss_mib") is not None for row in sentrix_rows)
    )
    if not sentrix_rows and sentrix_pss_complete:
        sentrix_pss = 0.0
    model_pss_complete = main_complete and all(row.get("pss_mib") is not None for row in model_rows)
    if sentrix_pss is not None and sentrix_pss_complete:
        data["sentrix_stack_pss_mib"] = sentrix_pss
        product_pss = _sum_mib(product_rows, "pss_mib")
        auxiliary_pss = _sum_mib(auxiliary_rows, "pss_mib")
        data["sentrix_product_pss_mib"] = (
            product_pss if product_pss is not None else (0.0 if not product_rows else None)
        )
        data["sentrix_auxiliary_model_pss_mib"] = (
            auxiliary_pss if auxiliary_pss is not None else (0.0 if not auxiliary_rows else None)
        )
        data["sentrix_auxiliary_model_uma_mib"] = (
            round(float(auxiliary_uma), 2) if auxiliary_uma is not None else None
        )
        data["sentrix_stack_pss_scope"] = "sentrix_related_pss_excluding_llama"
        data["sentrix_processes"] = [
            {key: row.get(key) for key in ("pid", "process_name", "rss_mib", "pss_mib", "listening_ports")}
            for row in sentrix_rows[:50]
        ]
    data["sentrix_auxiliary_model_uma_mib"] = auxiliary_uma
    if auxiliary_expected and not auxiliary_rows:
        data["sentrix_auxiliary_model_pss_mib"] = None
    data["product_stack_memory_mib"] = None
    # 每个相关进程的 PSS 都在、且模型的两个补偿输入（VmRSS / smaps-Rss）也在时才写总量。
    if (
        sentrix_pss is not None
        and sentrix_pss_complete
        and model_pss is not None
        and model_pss_complete
        and model_rss is not None
        and model_smaps_rss is not None
        and model_memory.get("model_process_gpu_memory_mib") is not None
        and auxiliary_uma is not None
        and (not auxiliary_expected or bool(auxiliary_rows))
    ):
        uma_extra = float(model_memory["model_process_gpu_memory_mib"])
        product = component_total([sentrix_pss, model_pss, uma_extra, auxiliary_uma])
        data["product_stack_memory_mib"] = product
        data["benchmark_process_memory_used_mib"] = product
        data["benchmark_process_memory_scope"] = "all_related_pss_plus_model_uma_extra"
        data["model_uma_extra_mib"] = round(uma_extra, 2)
        data["benchmark_processes"] = [
            {key: row.get(key) for key in ("pid", "process_name", "rss_mib", "pss_mib", "listening_ports")}
            for row in (sentrix_rows + model_rows)[:50]
        ]
    data["memory_complete"] = (
        data.get("model_process_system_memory_used_mib") is not None
        and data.get("product_stack_memory_mib") is not None
    )
    data["pss_sampled_at_monotonic"] = time.monotonic()
    return data


def is_jetson_host() -> bool:
    """本机是不是 Jetson：读 ``/proc/device-tree/model`` 里有没有 ``jetson``。

    这是**能力信号**，不是配置项——1.0 早期用主机名/固定地址判，换台机器就错。
    """
    try:
        return (
            "jetson"
            in Path("/proc/device-tree/model")
            .read_bytes()
            .replace(b"\0", b"")
            .decode("utf-8", "replace")
            .lower()
        )
    except OSError:
        return False


JETSON_SAMPLE_INTERVAL_SECONDS = max(
    0.1, float(os.environ.get("PHOTOBENCH_JETSON_SAMPLE_INTERVAL_SECONDS", "0.5"))
)


def _meminfo_mib() -> dict[str, Any]:
    values: dict[str, float] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            name, _, rest = line.partition(":")
            if name in {"MemTotal", "MemAvailable", "MemFree"}:
                values[name] = float(rest.split()[0]) / 1024.0
    except (OSError, ValueError, IndexError):
        return {}
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    used = system_used(total, available)
    if used is None or total is None or available is None:
        return {}
    return {
        "system_memory_used_mib": used,
        "system_memory_total_mib": round(total, 3),
        "system_memory_available_mib": round(max(0.0, available), 3),
        "system_memory_scope": "host_all_processes",
    }


def _parse_tegrastats_line(line: str) -> dict[str, Any]:
    sample: dict[str, Any] = {}
    for key, pattern, scale in (
        ("system_memory_used_mib", r"RAM\s+(\d+)/(\d+)MB", 1),
        ("gpu_utilization_pct", r"GR3D_FREQ\s+(\d+)%", 1),
        ("temperature_c", r"(?:gpu|tj)@([\d.]+)C", 1),
    ):
        match = re.search(pattern, line)
        if not match:
            continue
        sample[key] = round(float(match.group(1)) * scale, 3)
        if key == "system_memory_used_mib":
            sample["system_memory_total_mib"] = round(float(match.group(2)) * scale, 3)
            sample["system_memory_available_mib"] = round(
                max(0.0, sample["system_memory_total_mib"] - sample[key]), 3
            )
            sample["system_memory_scope"] = "host_all_processes"
    rails = {
        name: round(float(current) / 1000, 3)
        for name, current in re.findall(r"(VDD_[A-Z0-9_]+)\s+(\d+)mW", line)
    }
    if rails:
        sample["power_rails_average_w"] = {
            name: round(float(average) / 1000, 3)
            for name, average in re.findall(r"(VDD_[A-Z0-9_]+)\s+\d+mW/(\d+)mW", line)
        }
        rail = next(
            (name for name in ("VDD_IN", "VDD_GPU_SOC", "VDD_CPU_GPU_CV", "VDD_SOC") if name in rails),
            next(iter(rails)),
        )
        sample.update(
            power_draw_w=rails[rail],
            power_rails_w=rails,
            power_rail=rail,
            power_scope="board" if rail == "VDD_IN" else "rail",
        )
    cpu = re.search(r"cpu@([\d.]+)C", line)
    if cpu:
        sample["cpu_temperature_c"] = round(float(cpu.group(1)), 3)
    return sample


class _TegrastatsStream:
    """一个长驻的 ``tegrastats`` 进程，0.5 秒的采样不再每次都 fork 一个子进程。"""

    def __init__(self, interval_ms: int):
        self.interval_ms = max(100, int(interval_ms))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._latest = ""
        self._observed_at = float("-inf")
        self._restart_at = 0.0
        self._failures = 0

    def latest_line(self) -> str:
        self._ensure()
        with self._lock:
            if (
                self._proc is None
                or self._proc.poll() is not None
                or time.monotonic() - self._observed_at > 3 * self.interval_ms / 1000
            ):
                return ""
            return self._latest

    def _ensure(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if time.monotonic() < self._restart_at:
                return
            self._latest = ""
            self._stop.clear()
            self._restart_at = time.monotonic() + min(30, 2 ** min(self._failures, 5))
            self._failures += 1
            try:
                self._proc = subprocess.Popen(
                    ["tegrastats", "--interval", str(self.interval_ms)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except OSError:
                # 没有 tegrastats（比如非 Jetson 或没装）——不抛，留给调用方按「没有
                # 设备读数」处理。起不来一个可选的采样器不该让评测起不来。
                self._proc = None
                return
            self._thread = threading.Thread(target=self._read, daemon=True, name="tegrastats-reader")
            self._thread.start()

    def _read(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if self._stop.is_set():
                break
            text = line.strip()
            if text:
                with self._lock:
                    if self._proc is proc:
                        self._latest = text
                        self._observed_at = time.monotonic()
                        self._failures = 0


_TEGRASTATS = _TegrastatsStream(int(JETSON_SAMPLE_INTERVAL_SECONDS * 1000))


class LocalJetsonLlamaCppTelemetryProvider:
    """采样一个**被核验过**的本机 llama-server 进程，绝不对一个任意远端 PID 采样。

    设备读数与 PSS 走与 153 NVML 相同的 0.5s 节奏；tegrastats 保持单个长驻进程。
    **缺 PSS / KV 就留空**，绝不把整机 RAM 标成 GPU VRAM（Orin 是 UMA，本来就没有
    独立 VRAM——这是这一路与 ``HostNvidiaTelemetryProvider`` 最根本的区别）。
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        pid_file: str | None = None,
        pss_interval: float | None = None,
        telemetry_services: Mapping[str, Any] | None = None,
    ):
        self.telemetry_services = telemetry_services
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in {"http", "https"} or not parsed.port:
            raise ValueError("a local llama.cpp endpoint with an explicit port is required")
        self.endpoint = f"{parsed.scheme}://{parsed.netloc}"
        self.port = parsed.port
        self.pid_file = Path(
            pid_file
            or os.environ.get(
                "PHOTOBENCH_LLAMA_PID_FILE",
                "/home/orin/VLM/gemma4/results/orin/server_photobench_8100.pid",
            )
        )
        self.pss_interval = (
            JETSON_SAMPLE_INTERVAL_SECONDS if pss_interval is None else max(0.0, float(pss_interval))
        )
        self.device_interval = self.pss_interval
        self._last_probe = 0.0
        self._last_device_probe = 0.0
        self._device_sample: dict[str, Any] = {}
        self._last_pid: int | None = None
        self._system_baseline_mib: float | None = None
        self._last_memory: dict[str, Any] = {"status": "unavailable", "reason": "not_sampled"}

    def _pid_from_proc(self, pid: int) -> int:
        """只认「comm 是 llama-server 且 ``--port`` 就是选中端点那个端口」的 PID。

        1.0 的四道闸门之一：PID 文件可能过期、可能被别的进程复用了那个号。
        """
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if not args or Path(os.fsdecode(args[0])).name != "llama-server":
            raise ValueError("PID does not belong to llama-server")
        command = [os.fsdecode(arg) for arg in args if arg]
        if "--port" not in command or command[command.index("--port") + 1] != str(self.port):
            raise ValueError("PID is not serving the selected endpoint port")
        return pid

    def _pid_from_port(self) -> int:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                return self._pid_from_proc(int(entry.name))
            except (ValueError, FileNotFoundError, PermissionError, OSError):
                continue
        raise ValueError(f"no llama-server is listening on port {self.port}")

    def _pid(self) -> int:
        try:
            raw = self.pid_file.read_text(encoding="ascii").strip()
            if raw.isdecimal():
                return self._pid_from_proc(int(raw))
        except (OSError, ValueError):
            pass
        pid = self._pid_from_port()
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(f"{pid}\n", encoding="ascii")
        except OSError:
            pass
        return pid

    @staticmethod
    def _pss_mib(pid: int) -> float:
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as file:
            for line in file:
                if line.startswith("Pss:"):
                    return round(int(line.split()[1]) / 1024, 2)
        raise ValueError("Pss not found in smaps_rollup")

    @staticmethod
    def _rss_mib(pid: int) -> float:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 2)
        raise ValueError("VmRSS not found")

    def _llama_args(self, pid: int) -> dict[str, Any]:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        args = [os.fsdecode(item) for item in raw if item]

        def flag(*names: str) -> str | None:
            for name in names:
                if name in args:
                    index = args.index(name)
                    if index + 1 < len(args):
                        return args[index + 1]
            return None

        return {
            "ctx_size": flag("--ctx-size", "-c"),
            "parallel": flag("--parallel", "-np"),
            "n_gpu_layers": flag("--n-gpu-layers", "-ngl"),
            "cache_ram": flag("--cache-ram"),
            "model": flag("--model", "-m"),
            "cmdline": " ".join(args)[:2000],
        }

    def _metrics(self) -> dict[str, Any]:
        """llama.cpp 的 ``/metrics`` 里那几个 ``llamacpp:kv_cache_*``。探不到就什么都不加。"""
        data: dict[str, Any] = {}
        try:
            response = httpx.get(f"{self.endpoint}/metrics", timeout=2)
            response.raise_for_status()
            values: dict[str, float] = {}
            for line in response.text.splitlines():
                if line.startswith("llamacpp:kv_cache_") and "{" not in line:
                    name, _, value = line.partition(" ")
                    try:
                        values[name] = float(value.strip().split()[0])
                    except (ValueError, IndexError):
                        continue
            ratio = values.get("llamacpp:kv_cache_usage_ratio")
            if ratio is not None and 0 <= ratio <= 1:
                data["vllm_metrics"] = {"kv_cache_usage_pct": round(ratio * 100, 2)}
            tokens = values.get("llamacpp:kv_cache_tokens")
            if tokens is not None and tokens >= 0:
                data["kv_cache_used_tokens"] = int(tokens)
        except (httpx.HTTPError, ValueError):
            pass
        return data

    def _device(self) -> dict[str, Any]:
        # Revalidate stream liveness on every read; a device cache must not extend
        # the source TTL or keep a terminated tegrastats process alive on paper.
        self._last_device_probe = time.monotonic()
        sample = _meminfo_mib()
        line = _TEGRASTATS.latest_line()
        if line:
            parsed = _parse_tegrastats_line(line)
            parsed.pop("system_memory_used_mib", None)
            parsed.pop("system_memory_total_mib", None)
            parsed.pop("system_memory_available_mib", None)
            parsed.pop("system_memory_scope", None)
            sample.update(parsed)
            sample["device_observed_at_monotonic"] = _TEGRASTATS._observed_at
            sample["device_observation_age_seconds"] = time.monotonic() - _TEGRASTATS._observed_at
            sample["device_observation_ttl_seconds"] = 3 * _TEGRASTATS.interval_ms / 1000
        self._device_sample = sample
        return self._device_sample

    def gpu_stats(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self._last_memory.get("data") or {})
        data.update(self._metrics())
        data["memory_unit"] = "process_pss_uma_mib"
        data["memory_scope"] = "model_process_pss"
        data["memory_profile"] = {"method": "orin_process_pss_uma_v1"}
        try:
            pid = self._pid()
            if pid != self._last_pid:
                # PID 变了意味着换过一次模型进程：上一次的进程内存读数不再属于它。
                self._last_pid = pid
                self._last_probe = 0.0
                data.pop("process_memory_used_mib", None)
            data["root_pid"] = pid
            if pid != getattr(self, "_baseline_pid", None):
                self._baseline_pid = pid
                self._system_baseline_mib = None
            if time.monotonic() - self._last_probe >= self.pss_interval:
                self._last_probe = time.monotonic()
                data["process_memory_used_mib"] = self._pss_mib(pid)
                data["process_memory_scope"] = "llama_server_pss"
                try:
                    data["process_rss_mib"] = self._rss_mib(pid)
                    data["llama_server_args"] = self._llama_args(pid)
                except (OSError, ValueError, IndexError):
                    pass
                data["pss_sampled_at_monotonic"] = time.monotonic()
                data.pop("pss_error", None)
            device = self._device()
            used = device.get("system_memory_used_mib")
            if self._system_baseline_mib is None and isinstance(used, (int, float)):
                self._system_baseline_mib = float(used)
            if self._system_baseline_mib is not None and isinstance(used, (int, float)):
                data["system_memory_baseline_mib"] = round(self._system_baseline_mib, 2)
                data["system_memory_delta_mib"] = round(float(used) - self._system_baseline_mib, 2)
        except (OSError, ValueError, IndexError) as exc:
            # 探不到就撤掉进程内存指标并写明原因——留旧值会被读成当前值。
            data.pop("process_memory_used_mib", None)
            data["pss_error"] = str(exc)
        self._last_memory = {"status": "available", "source": "jetson_local_pss", "data": data}
        return {
            "status": "available",
            "source": "jetson_local_pss",
            "data": {
                "gpus": [
                    {
                        "index": 0,
                        "memory_unit": "process_pss_uma_mib",
                        "memory_scope": "model_process_pss",
                        **self._device(),
                    }
                ],
            },
        }

    def process_memory(self) -> dict[str, Any]:
        try:
            data: dict[str, Any] = self._last_memory.get("data") or {}
            augmented = _augment_local_process_memory(
                data, model_pid=None, endpoint_port=str(self.port), telemetry_services=self.telemetry_services
            )
            return {
                "status": "available",
                "source": "jetson_local_pss",
                "data": apply_jetson_memory_metadata(augmented),
            }
        except Exception as error:
            return {"status": "unavailable", "source": "jetson_local_pss", "reason": str(error)}

    def system_memory(self) -> dict[str, Any]:
        data: dict[str, Any] = _meminfo_mib()
        keys = {
            "system_memory_used_mib",
            "system_memory_total_mib",
            "system_memory_available_mib",
            "system_memory_scope",
        }
        data = {key: value for key, value in data.items() if key in keys}
        if "system_memory_used_mib" not in data:
            return {"status": "unavailable", "source": "tegrastats", "reason": "ram_sample_unavailable"}
        return {"status": "available", "source": "tegrastats", "data": data}

    def kv_cache(self) -> dict[str, Any]:
        data: dict[str, Any] = self._last_memory.get("data") or {}
        if "kv_cache_used_tokens" not in data and "vllm_metrics" not in data:
            return {"status": "unavailable", "source": "llamacpp_metrics", "reason": "kv_metrics_not_exposed"}
        return {
            "status": "available",
            "source": "llamacpp_metrics",
            "data": {
                "used_tokens": data.get("kv_cache_used_tokens"),
                "usage_pct": (data.get("vllm_metrics") or {}).get("kv_cache_usage_pct"),
                "used_bytes": None,
            },
        }


__all__ = [
    "JETSON_SAMPLE_INTERVAL_SECONDS",
    "LocalJetsonLlamaCppTelemetryProvider",
    "SYSTEM_PROCESS_KEYWORDS",
    "SYSTEM_PROCESS_PORTS",
    "is_jetson_host",
]
