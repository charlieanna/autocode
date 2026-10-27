"""Bound metadata-only recovery of an unfinished, positively stopped supervisor.

No configuration, artifact adoption, retry or provider dispatch happens here.
Fresh process inspection is supplied by the interface/action under its run lock.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_liveness as liveness
    from . import autocode_util as util
    from . import autocode_visual_profile as visual_profile
except ImportError:
    import autocode_liveness as liveness
    import autocode_util as util
    import autocode_visual_profile as visual_profile

BOUND = (
    "task",
    "ui_run",
    "workspace",
    "run_dir",
    "status",
    "active_stage",
    "active_runner_check",
    "settings",
    "goal_contract",
    "current_task",
    "source_revision",
    "stages",
    "active_seconds",
    "automatic_recoveries_since_resume",
    "automatic_timeout_recoveries",
    "recovery_grants",
    "pending_report_repair",
    "job_failure",
    "recovery_context",
    "pause_intent",
)


def token(state: RunState):
    return "checkpoint-v1:" + util.digest({key: state.get(key) for key in BOUND})


def require_binding(state: RunState, expected):
    if not isinstance(expected, str) or expected != token(state):
        raise ValueError("The retained checkpoint changed; refresh its status before reconciling supervision")


def offer(state: RunState, inspection, *, controller=None):
    active = state.get("active_stage")
    if (
        any(row.get("kind") == "stop" for row in state.get("applied_interventions") or [])
        or state.get("ui_run")
        or visual_profile.initial_intent(state.get("task") or "") is not None
        or state.get("status") != "RUNNING"
        or not isinstance(active, dict)
        or active.get("finished_at")
        or state.get("active_runner_check")
        or type(active.get("iteration")) is not int
        or not isinstance(active.get("output"), str)
        or not active["output"]
        or (controller is not None and controller.get("kind") != "ended")
    ):
        return None
    fresh = liveness.classify(active.get("supervision"), inspection)
    if not all(fresh[role]["checked"] and fresh[role]["alive"] is False for role in liveness.ROLES):
        return None
    attempt = f"{active['iteration']:03d}/{Path(active['output']).stem}"
    return {
        "kind": "continue",
        "action": "--reconcile-only " + token(state),
        "reason": f"Reconcile stopped supervision for {attempt} without starting a provider. "
        "The retained attempt and uncertain usage stay recorded; any retry remains a separate decision.",
    }


def decision(state: RunState, expected, inspection):
    require_binding(state, expected)
    if offer(state, inspection) is None:
        raise ValueError("Reconciliation requires an unfinished checkpoint with freshly confirmed dead supervision")
    active = state["active_stage"]
    assert isinstance(active, dict)  # offer() established the retained attempt shape above.
    attempt = f"{active['iteration']:03d}/{Path(active['output']).stem}"
    return {
        "status": "PAUSED_UNCERTAIN_STAGE",
        "stop_reason": f"Supervised attempt {attempt} ended without a confirmed result. "
        "Its work, logs and usage uncertainty are retained. Inspect the artifacts and cleanup receipt "
        "before deciding whether to set this attempt aside. No provider launched or retry authorized.",
    }
