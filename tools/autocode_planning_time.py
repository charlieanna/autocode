"""Saved execution limits made visible to planning models; no budget authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def context(state: RunState):
    limits = (state.get("settings") or {}).get("limits") or {}
    used = state.get("active_seconds", 0)
    maximum = limits.get("max_seconds")
    return {
        "run_active_seconds_used": used,
        "run_active_seconds_remaining": max(0, maximum - used) if maximum else None,
        "stage_seconds_limit": limits.get("stage_timeout_seconds") or None,
        "run_cost_limit_usd": limits.get("max_cost_usd") or None,
    }
