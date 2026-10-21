"""遥测归因的“当前 run 用了哪些本机服务”配置。

整机/整卡始终全量采样；这份配置只决定 product / auxiliary / main 的归属。
文件不进 git，模板见 ``telemetry_services.example.json``。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

TELEMETRY_SERVICES_ENV = "PHOTOBENCH_TELEMETRY_SERVICES"
RUNTIME_CONNECTION_DEFAULT = "apps/photobench/config/runtime_connection.json"
TELEMETRY_SERVICES_DEFAULT = "apps/photobench/config/telemetry_services.json"


def _local_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "::1"}
    with contextlib.suppress(OSError):
        hosts.update(socket.gethostbyname_ex(socket.gethostname())[2])
    with contextlib.suppress(OSError):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            hosts.add(probe.getsockname()[0])
        finally:
            probe.close()
    return hosts


def _url_port(value: Any) -> int | None:
    try:
        parsed = urlparse(str(value or ""))
        return parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def _from_runtime_connection(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """runtime_connection 是新版的唯一来源；把扁平连接字段映射成监控清单。"""
    model_url = str(raw.get("model_base_url") or "").strip()
    embedding = raw.get("embedding")
    if not model_url and not isinstance(embedding, Mapping):
        return None
    host = (urlparse(model_url).hostname or "").lower() if model_url else ""
    port = _url_port(model_url)
    local = bool(host) and host in _local_hosts()
    sentrix_url = str(raw.get("sentrix_url") or "").strip()
    sentrix_port = _url_port(sentrix_url)
    return {
        "main_model": {
            "base_url": model_url,
            "model": str(raw.get("endpoint_model") or ""),
            "local": local,
            "ports": [port] if local and port else [],
            "keywords": ["llama-server"],
        },
        "embedding": dict(embedding or {}),
        "sentrix": {
            "url": sentrix_url,
            "ports": [sentrix_port] if sentrix_port else [],
        },
        "product": list(raw.get("product") or []),
    }


def load_telemetry_services() -> dict[str, Any] | None:
    # 唯一来源：runtime_connection.json；两个旧文件只作为未迁移设备的兼容回退。
    candidates = [
        Path(os.environ.get(TELEMETRY_SERVICES_ENV) or RUNTIME_CONNECTION_DEFAULT),
        Path(TELEMETRY_SERVICES_DEFAULT),
    ]
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        mapped = _from_runtime_connection(raw) if path.name == "runtime_connection.json" else raw
        if mapped is not None:
            return mapped
    return None


def matches_service(row: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    if spec.get("local") is False:
        return False
    pids = {str(pid) for pid in spec.get("pids") or []}
    if pids and str(row.get("pid")) in pids:
        return True
    ports = {str(port) for port in spec.get("ports") or []}
    row_ports = {str(port) for port in row.get("listening_ports") or []}
    if ports & row_ports:
        return True
    identity = str(row.get("identity") or "").lower()
    return any(keyword and str(keyword).lower() in identity for keyword in spec.get("keywords") or [])


def service_rows(rows: Sequence[Mapping[str, Any]], spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if matches_service(row, spec)]


def dedupe_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        pid = int(row["pid"])
        if pid in seen:
            continue
        seen.add(pid)
        out.append(dict(row))
    return out


__all__ = [
    "RUNTIME_CONNECTION_DEFAULT",
    "TELEMETRY_SERVICES_DEFAULT",
    "TELEMETRY_SERVICES_ENV",
    "dedupe_rows",
    "load_telemetry_services",
    "matches_service",
    "service_rows",
]
