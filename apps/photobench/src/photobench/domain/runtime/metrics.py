"""Platform accounting rules and validity; no operating-system access."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

APPLE_POWER_LIMIT_W = 150.0
APPLE_TEMPERATURE_MIN_C = 10.0
APPLE_TEMPERATURE_MAX_C = 130.0
MEMORY_OBSERVATION_TTL_SECONDS = 3.0
MEMORY_OBSERVATION_FIELDS = (
    "model_process_memory_used_mib",
    "model_process_host_memory_mib",
    "model_process_gpu_memory_mib",
    "model_process_system_memory_used_mib",
    "sentrix_stack_pss_mib",
    "sentrix_product_pss_mib",
    "sentrix_auxiliary_model_pss_mib",
    "sentrix_auxiliary_model_uma_mib",
    "product_stack_memory_mib",
    "benchmark_process_memory_used_mib",
    "system_memory_used_mib",
    "system_memory_total_mib",
    "system_memory_available_mib",
)

DEVICE_FIELDS = (
    "gpu_utilization_pct",
    "memory_used_mib",
    "memory_total_mib",
    "temperature_c",
    "cpu_temperature_c",
    "power_draw_w",
    "cpu_power_w",
    "gpu_power_w",
    "sm_clock_mhz",
    "gpu_allocated_unified_memory_mib",
    "gpu_in_use_unified_memory_mib",
    "power_rails_w",
    "power_rails_average_w",
)


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def apple_power(value: object) -> float | None:
    number = finite_number(value)
    return number if number is not None and 0 <= number <= APPLE_POWER_LIMIT_W else None


def apple_temperature(value: object) -> float | None:
    number = finite_number(value)
    return (
        number if number is not None and APPLE_TEMPERATURE_MIN_C < number <= APPLE_TEMPERATURE_MAX_C else None
    )


def component_total(values: Sequence[float | None]) -> float | None:
    """Unknown necessary components invalidate a total; an explicitly empty set is zero."""
    if any(value is None or finite_number(value) is None or value < 0 for value in values):
        return None
    return round(sum(value for value in values if value is not None), 3)


def system_used(total: object, available: object) -> float | None:
    """Linux physical memory use; MemFree is not a substitute for MemAvailable."""
    capacity, free = finite_number(total), finite_number(available)
    if capacity is None or free is None or not 0 <= free <= capacity:
        return None
    return round(capacity - free, 2)


def device_freshness(sample: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Rebase source age at persistence/read time; retained timestamps are the facts."""
    result = dict(sample)
    captured = finite_number(sample.get("_t", sample.get("t")))
    observed = finite_number(sample.get("device_observed_at_monotonic"))
    if captured is None or observed is None:
        return result, []
    age = captured - observed
    # Old observations did not record source periods. This explicit compatibility
    # policy is versioned; it does not reconstruct an unknown refresh cadence.
    ttl = finite_number(sample.get("device_observation_ttl_seconds", 3.0))
    result.update(device_observation_age_seconds=age, device_freshness_version=1)
    if ttl is not None and ttl > 0 and 0 <= age <= ttl:
        return result, []
    rejected = [key for key in DEVICE_FIELDS if key in result]
    for key in rejected:
        result[key] = None
    status = dict(result.get("source_status") or {})
    status["device"] = "unavailable"
    result["source_status"] = status
    diagnostics = dict(result.get("source_diagnostics") or {})
    diagnostics["device"] = {
        **(diagnostics.get("device") or {}),
        "reason": "device_observation_expired_at_sample",
    }
    result["source_diagnostics"] = diagnostics
    return result, rejected


def memory_freshness(sample: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate recorded dependencies, without inventing observations for legacy data."""
    if type(sample.get("memory_observation_version")) is not int or sample["memory_observation_version"] != 1:
        return sample, []
    result = dict(sample)
    captured = finite_number(sample.get("_t", sample.get("t")))
    observations = sample.get("memory_observations") or {}
    dependencies = sample.get("memory_dependencies") or {}
    if not isinstance(observations, dict):
        observations = {}
    if not isinstance(dependencies, dict):
        dependencies = {}
    derived: dict[str, Any] = {}
    for key, raw in observations.items():
        row = dict(raw) if isinstance(raw, dict) else {}
        observed = finite_number(row.get("observed_at_monotonic"))
        finished = finite_number(row.get("finished_at_monotonic"))
        ttl = finite_number(row.get("ttl_seconds"))
        age = captured - observed if captured is not None and observed is not None else None
        fresh = (
            row.get("status") == "available"
            and age is not None
            and ttl is not None
            and 0 < ttl <= MEMORY_OBSERVATION_TTL_SECONDS
            and 0 <= age <= ttl
            and finished is not None
            and observed is not None
            and captured is not None
            and observed <= finished <= captured
        )
        row.update(age_seconds=age, fresh=fresh)
        derived[key] = row
    result["memory_observations"] = derived
    raw_validity = sample.get("component_validity")
    validity = dict(raw_validity) if isinstance(raw_validity, dict) else {}
    rejected: list[str] = []
    process_times: list[float] = []
    for key in MEMORY_OBSERVATION_FIELDS:
        if key not in sample and key not in dependencies:
            continue
        refs = dependencies.get(key)
        value = finite_number(sample.get(key))
        valid = (
            isinstance(refs, list)
            and value is not None
            and value >= 0
            and bool(refs)
            and all(isinstance(ref, str) and derived.get(ref, {}).get("fresh") is True for ref in refs)
        )
        if not valid:
            result[key] = None
            validity[key] = "unavailable"
            rejected.append(key)
        else:
            validity[key] = "valid"
            if not key.startswith("system_") and isinstance(refs, list):
                process_times.extend(float(derived[ref]["observed_at_monotonic"]) for ref in refs)
    result["component_validity"] = validity
    result["memory_complete"] = all(
        finite_number(result.get(key)) is not None
        for key in ("model_process_system_memory_used_mib", "product_stack_memory_mib")
    )
    oldest = min(process_times) if process_times else None
    result["process_observed_at_monotonic"] = oldest if result["memory_complete"] else None
    result["pss_sampled_at_monotonic"] = result["process_observed_at_monotonic"]
    if rejected:
        raw_diagnostics = result.get("source_diagnostics")
        diagnostics = dict(raw_diagnostics) if isinstance(raw_diagnostics, dict) else {}
        diagnostics["memory"] = {"reason": "memory_dependency_unavailable_or_expired", "fields": rejected}
        result["source_diagnostics"] = diagnostics
    return result, rejected
