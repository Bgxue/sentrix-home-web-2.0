"""Platform accounting rules and validity; no operating-system access."""

from __future__ import annotations

import math
from collections.abc import Sequence

APPLE_POWER_LIMIT_W = 150.0
APPLE_TEMPERATURE_MIN_C = 10.0
APPLE_TEMPERATURE_MAX_C = 130.0


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
