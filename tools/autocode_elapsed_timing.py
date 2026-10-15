"""Pure wall-versus-monotonic elapsed-time evidence for supervision receipts."""

from __future__ import annotations

import math

# A clock gap must be too large to be ordinary sampling or scheduler jitter.
SUSPENSION_TOLERANCE_SECONDS = 30.0


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def admission(wall_origin, monotonic_origin, admitted_stage_limit):
    """Return a validated keeper timing declaration, or None for invalid input."""
    if (
        not _finite_number(wall_origin)
        or wall_origin <= 0
        or not _finite_number(monotonic_origin)
        or monotonic_origin < 0
        or (
            admitted_stage_limit is not None and (not _finite_number(admitted_stage_limit) or admitted_stage_limit <= 0)
        )
    ):
        return None
    return {
        "wall_origin_seconds": float(wall_origin),
        "monotonic_origin_seconds": float(monotonic_origin),
        "admitted_stage_limit_seconds": (None if admitted_stage_limit is None else float(admitted_stage_limit)),
    }


def observation(declaration, *, wall_now, monotonic_now):
    """Build passive elapsed-time evidence from both clocks; never a stop decision."""
    if (
        not isinstance(declaration, dict)
        or admission(
            declaration.get("wall_origin_seconds"),
            declaration.get("monotonic_origin_seconds"),
            declaration.get("admitted_stage_limit_seconds"),
        )
        != declaration
    ):
        return None
    if not _finite_number(wall_now) or not _finite_number(monotonic_now):
        return None
    monotonic_elapsed = round(max(0.0, float(monotonic_now) - declaration["monotonic_origin_seconds"]), 6)
    wall_elapsed = round(max(0.0, float(wall_now) - declaration["wall_origin_seconds"]), 6)
    excess = round(max(0.0, wall_elapsed - monotonic_elapsed), 6)
    return {
        "monotonic_elapsed_seconds": monotonic_elapsed,
        "wall_elapsed_seconds": wall_elapsed,
        "admitted_stage_limit_seconds": declaration["admitted_stage_limit_seconds"],
        "excess_wall_seconds": excess,
        "suspension_tolerance_seconds": SUSPENSION_TOLERANCE_SECONDS,
        "likely_host_or_vm_suspension": excess > SUSPENSION_TOLERANCE_SECONDS,
    }


def normalize_observation(value):
    """Return canonical receipt evidence, or None for legacy/malformed timing."""
    required = (
        "monotonic_elapsed_seconds",
        "wall_elapsed_seconds",
        "admitted_stage_limit_seconds",
        "excess_wall_seconds",
        "suspension_tolerance_seconds",
        "likely_host_or_vm_suspension",
    )
    if not isinstance(value, dict) or any(key not in value for key in required):
        return None
    elapsed = value.get("monotonic_elapsed_seconds")
    wall = value.get("wall_elapsed_seconds")
    limit = value.get("admitted_stage_limit_seconds")
    if (
        not _finite_number(elapsed)
        or elapsed < 0
        or not _finite_number(wall)
        or wall < 0
        or (limit is not None and (not _finite_number(limit) or limit <= 0))
    ):
        return None
    excess = round(max(0.0, float(wall) - float(elapsed)), 6)
    canonical = {
        "monotonic_elapsed_seconds": round(float(elapsed), 6),
        "wall_elapsed_seconds": round(float(wall), 6),
        "admitted_stage_limit_seconds": None if limit is None else float(limit),
        "excess_wall_seconds": excess,
        "suspension_tolerance_seconds": SUSPENSION_TOLERANCE_SECONDS,
        "likely_host_or_vm_suspension": excess > SUSPENSION_TOLERANCE_SECONDS,
    }
    for key, expected in canonical.items():
        supplied = value.get(key)
        if type(expected) is bool:
            if type(supplied) is not bool or supplied is not expected:
                return None
        elif expected is None:
            if supplied is not None:
                return None
        elif not _finite_number(supplied) or not math.isclose(float(supplied), expected, rel_tol=0, abs_tol=1e-5):
            return None
    return canonical
