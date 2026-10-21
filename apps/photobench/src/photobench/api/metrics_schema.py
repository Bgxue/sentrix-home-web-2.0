"""Additive v2 metrics projections; unknown historical fields remain supported."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class MemoryObservation(BaseModel):
    model_config = ConfigDict(extra="allow")
    memory_accounting: str | None = None
    memory_complete: bool | None = None
    component_validity: dict[str, str] | None = None
    observed_at_epoch: float | None = None
    device_observation_age_seconds: float | None = None
    device_observed_at_monotonic: float | None = None
    device_observation_ttl_seconds: float | None = None
    device_freshness_version: int | None = None
    model_process_system_memory_used_mib: float | None = None
    model_process_host_memory_mib: float | None = None
    model_process_gpu_memory_mib: float | None = None
    model_process_footprint_mib: float | None = None
    model_process_mapped_weights_mib: float | None = None
    product_stack_memory_mib: float | None = None
    power_rail: str | None = None
    power_scope: str | None = None
    power_rails_w: dict[str, float] | None = None


class TelemetryProjection(BaseModel):
    model_config = ConfigDict(extra="allow")
    latest: MemoryObservation | None = None
    field_stats: dict[str, Any] | None = None
    rejected_samples: dict[str, int] | None = None
    sampling: dict[str, Any] | None = None


class RunMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    metrics_contract_version: int | None = None
    build_revision: str | None = None
    runtime_snapshot: dict[str, Any] | None = None
    telemetry_live: TelemetryProjection | None = None


class JudgeObservation(BaseModel):
    model_config = ConfigDict(extra="allow")
    # Preserve legacy raw values; validation belongs to the versioned domain rule.
    score: Any = None
    score_normalization_version: int | None = None
    score_normalization: dict[str, Any] | None = None
    score_derivation: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] | None = None
    consistency_version: int | None = None
    consistency_status: str | None = None


class RunItemObservation(BaseModel):
    model_config = ConfigDict(extra="allow")
    judge: JudgeObservation | None = None


class RunItemResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    item: RunItemObservation | None = None


class RunItemsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[RunItemObservation] | None = None
