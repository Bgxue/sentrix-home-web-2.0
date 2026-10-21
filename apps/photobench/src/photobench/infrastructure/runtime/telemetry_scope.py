"""遥测归因的“当前 run 用了哪些本机服务”配置。

整机/整卡始终全量采样；这份配置只决定 product / auxiliary / main 的归属。
文件不进 git，模板见 ``telemetry_services.example.json``。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TELEMETRY_SERVICES_ENV = "PHOTOBENCH_TELEMETRY_SERVICES"
TELEMETRY_SERVICES_DEFAULT = "apps/photobench/config/telemetry_services.json"


def load_telemetry_services() -> dict[str, Any] | None:
    path = Path(os.environ.get(TELEMETRY_SERVICES_ENV) or TELEMETRY_SERVICES_DEFAULT)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


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
    "TELEMETRY_SERVICES_DEFAULT",
    "TELEMETRY_SERVICES_ENV",
    "dedupe_rows",
    "load_telemetry_services",
    "matches_service",
    "service_rows",
]
