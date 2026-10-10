"""Explicit verification-command repair at a reconciled stopped run boundary."""

from __future__ import annotations

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def configure_resume(state, settings, args):
    """Update existing regression settings; retain proofs for their context check.

    Only this function writes command-change user_events. Status/history readers
    retain the receipt; regression.prove compares settings to cached evidence.
    The caller owns the run lock and persists settings and events together.
    """
    selected = {key: getattr(args, key, None) for key in ("test_command", "regression_command")}
    selected = {key: value for key, value in selected.items() if value is not None}
    previous = dict(settings.get("regression") or {})
    changed = {key: value for key, value in selected.items() if value != previous.get(key)}
    if not changed:
        return settings
    if any(not isinstance(value, str) or not value.strip() for value in changed.values()):
        raise ValueError("Verification commands must not be empty")
    if not getattr(args, "resume_paused", False) or not str(state.get("status", "")).startswith("PAUSED_"):
        raise ValueError("Changing saved verification commands requires a paused run and --resume-paused")
    if state.get("next_stage") not in ("sol", "astra_checkpoint"):
        raise ValueError(
            "Changing verification commands requires a stopped checkpoint before the Validator "
            "or combined checkpoint; the new proof must run before any acceptance"
        )
    if any(
        state.get(key)
        for key in (
            "active_stage",
            "pending_report_repair",
            "uncertain_artifacts",
            "active_runner_check",
            "runner_check",
        )
    ):
        raise ValueError("Reconcile the active or uncertain attempt before changing verification commands")
    current = {**previous, **changed}
    settings["regression"] = current
    state.setdefault("user_events", []).append(
        {
            "kind": "verification_commands_changed",
            "actor": "user_cli",
            "at": util.now(),
            "previous": {key: previous.get(key) for key in changed},
            "current": changed,
        }
    )
    return settings
