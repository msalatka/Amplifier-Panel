"""Side-effect-free validation for operator-provided numeric settings."""

from __future__ import annotations

import copy
import math
from typing import Any


def finite_float(value: Any, label: str) -> float:
    """Convert a value to a finite float or raise a labeled validation error."""

    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    return number


def validate_gain_set(value: Any, minimum: float, maximum: float) -> float:
    """Return a finite gain setpoint constrained to configured bounds."""

    gain_set = finite_float(value, "Gain setpoint")
    if not minimum <= gain_set <= maximum:
        raise ValueError(f"Gain setpoint must be between {minimum:g} and {maximum:g}.")
    return gain_set


def validated_dashboard_settings(
    current: dict[str, Any],
    gain_tolerance: Any | None,
    warn_limits: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return a validated copy of dashboard settings with requested updates."""

    candidate = copy.deepcopy(current)
    if gain_tolerance is not None:
        tolerance = finite_float(gain_tolerance, "Gain tolerance")
        if tolerance < 0:
            raise ValueError("Gain tolerance cannot be negative.")
        candidate["gain_tolerance"] = tolerance

    if warn_limits is not None:
        known_fields = set(candidate.get("warn_limits", {}))
        unknown_fields = set(warn_limits) - known_fields
        if unknown_fields:
            raise ValueError("Unknown warning fields: " + ", ".join(sorted(unknown_fields)) + ".")

        for field, limits in warn_limits.items():
            if not isinstance(limits, dict):
                raise ValueError(f"{field}: warning limits must be an object.")
            unknown_sides = set(limits) - {"min", "max"}
            if unknown_sides:
                raise ValueError(
                    f"{field}: unknown limit keys: " + ", ".join(sorted(unknown_sides)) + "."
                )
            for side in ("min", "max"):
                if side not in limits:
                    continue
                value = limits[side]
                candidate["warn_limits"][field][side] = (
                    None
                    if value is None
                    else finite_float(value, f"{field} {side.upper()} threshold")
                )

    for field, limits in candidate.get("warn_limits", {}).items():
        minimum = limits.get("min")
        maximum = limits.get("max")
        if minimum is not None:
            minimum = finite_float(minimum, f"{field} MIN threshold")
            limits["min"] = minimum
        if maximum is not None:
            maximum = finite_float(maximum, f"{field} MAX threshold")
            limits["max"] = maximum
        if minimum is not None and maximum is not None and minimum >= maximum:
            raise ValueError(f"{field}: MIN threshold must be lower than MAX threshold.")

    return candidate
