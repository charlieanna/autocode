"""Admission and public offer for the existing explicit connection-change action."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def scope_matches(state: RunState, engine):
    return engine == "opencode" and state.get("status") == "PAUSED_TRANSPORT_CHANGED"


def unresolved_attempt(state: RunState):
    """The configuration action's existing retained-attempt refusal."""
    return any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts"))


def offer(state: RunState, *, immutable_role_transports):
    settings = state.get("settings")
    if not isinstance(settings, dict) or not scope_matches(state, settings.get("engine")):
        return None
    if (
        immutable_role_transports
        or unresolved_attempt(state)
        or any(state.get(key) is not None for key in ("active_stage", "active_runner_check", "pending_report_repair"))
    ):
        return None
    return {
        "action": "--resume-paused --accept-transport-change",
        "confirmation_required": True,
        "recovery_hint": (
            "This action explicitly accepts the changed connection settings after checking their models and "
            "billing routes, then continues. Review the connection change before confirming; it does not "
            "approve a plan, replace selected role models or grant extra retries."
        ),
    }
