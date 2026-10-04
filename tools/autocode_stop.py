"""Durable-intervention application policy for feedback, pause and stop.

One purpose: decide how accepted intervention requests apply to saved run state,
and keep an applied stop in force. An applied stop receipt is terminal for the
life of the run: no later feedback, pause, resumed pause, user action, restart
or automatic recovery may supersede it or relaunch a stage, the stopped run is
never marked complete, and a stop receipt never receives a resumed_at stamp.
The saved status stays the existing PAUSED_INTERVENTION boundary status with a
stop-specific reason; no new state.json status is invented.

This module sits below the controllers: it imports only autocode_util,
autocode_interventions and autocode_goals, never the runner, autopilot, or any
module in the import cycle. The state writer and clock are passed in.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

try:
    from . import autocode_goals as goals
    from . import autocode_interventions as interventions
    from . import autocode_util as util
except ImportError:
    import autocode_goals as goals
    import autocode_interventions as interventions
    import autocode_util as util


STOP_KIND = "stop"
STOP_STATUS = "PAUSED_INTERVENTION"
STOP_REASON = "Durable stop applied at a saved boundary; this run launches no further stages."


def is_stop_receipt(receipt: Any) -> bool:
    return isinstance(receipt, dict) and receipt.get("kind") == STOP_KIND


def applied_stop(state: dict[str, Any]) -> dict[str, Any] | None:
    """The applied kind-stop receipt, if one stands. Terminal for the run's life."""
    for receipt in state.get("applied_interventions") or []:
        if is_stop_receipt(receipt):
            return receipt
    return None


def pending_stop(run_dir: Path) -> dict[str, Any] | None:
    """The earliest stop request still waiting in the inbox, if any.

    A stop recorded before a crash stays durable there; the owner applies it at
    the next saved boundary. An unreadable inbox holds no authoritative stop,
    so it reads as none rather than blocking reconciliation of the real error.
    """
    try:
        requests = interventions.pending(run_dir)
    except interventions.InterventionError:
        return None
    return next((item for item in sorted(requests, key=lambda row: row.get("order", 0))
                 if isinstance(item, dict) and item.get("kind") == STOP_KIND), None)


def stop_reason(state: dict[str, Any]) -> str:
    return STOP_REASON


def assert_stopped(state: dict[str, Any]) -> bool:
    """Re-assert the stop boundary in saved state; return whether anything changed.

    A stop must never be reported complete, and a drift introduced by an older
    writer is corrected here rather than trusted.
    """
    changed = False
    if state.get("status") != STOP_STATUS:
        state["status"] = STOP_STATUS
        changed = True
    if state.get("phase") != "PAUSED_OR_BLOCKED":
        state["phase"] = "PAUSED_OR_BLOCKED"
        changed = True
    if state.get("stop_reason") != stop_reason(state):
        state["stop_reason"] = stop_reason(state)
        changed = True
    if state.get("status") != "TASK_COMPLETE":
        for field in ("completed_at", "completion_actor", "final_decision"):
            if state.pop(field, None) is not None:
                changed = True
    return changed


def state_writer(ordinary_write: Callable, persist_stopped: Callable) -> Callable:
    """Preserve a terminal Stop while retaining the normal checkpoint journal.

    Ordinary writes keep their existing normalization. A retained human proposal
    cannot normalize an applied Stop into a resumable resolver state. Persistence
    is injected so this policy module does not import the shared state writer.
    """
    def write(path, value):
        if Path(path).name == "state.json" and isinstance(value, dict):
            value.setdefault("intervention_capability", {}).update(supports_stop=True)
            if applied_stop(value):
                assert_stopped(value)
                return persist_stopped(path, value)
        return ordinary_write(path, value)
    return write


def refuse_admission(state: dict[str, Any]) -> None:
    """Refuse one stage admission while an applied stop stands."""
    if applied_stop(state) is not None:
        assert_stopped(state)
        raise util.Paused(STOP_STATUS, stop_reason(state))


def refuse_before_configure(write_json: Callable, state: dict[str, Any], state_path: Path) -> int | None:
    """The CLI's terminal-stop refusal before run setup configures the invocation.

    Some resume flags (the --resume-paused gated family, for example
    --accept-transport-change) are refused by their own earlier configuration
    validation, which would hide the saved stop reason. The CLI applies this
    refusal first, so the whole gated family reports the saved reason with the
    stopped boundary intact. Returns the exit code, or None to continue.
    """
    if applied_stop(state) is None:
        return None
    if assert_stopped(state):
        write_json(state_path, state)
    print(f"{state['status']}: {state['stop_reason']}")
    return 2


def boundary_effects(state: dict[str, Any], consumed: list[dict[str, Any]], now: Callable[[], str]) -> None:
    """Apply pause and stop effects at the saved step boundary.

    Pause keeps its resumable semantics (pause_intent, acknowledged on resume).
    Stop records its terminal marker and keeps the existing PAUSED_INTERVENTION
    boundary status with a stop-specific reason; it wins over any pause in the
    same batch and applies even alongside feedback.
    """
    pauses = [item for item in consumed if item["kind"] == "pause"]
    stops = [item for item in consumed if is_stop_receipt(item)]
    if pauses:
        state["pause_intent"] = {"request_ids": [item["id"] for item in pauses], "applied_at": now(),
                                 "acknowledged_at": None, "next_stage": state.get("next_stage")}
    if stops:
        state["stop_intent"] = {"request_ids": [item["id"] for item in stops], "applied_at": now()}
    if stops:
        state.update(status=STOP_STATUS, phase="PAUSED_OR_BLOCKED", stop_reason=STOP_REASON)
    elif not any(item["kind"] == "feedback" for item in consumed):
        state.update(status=STOP_STATUS, phase="PAUSED_OR_BLOCKED",
                     stop_reason="Queued pause was applied; explicitly resume when ready.")
    # A completion proposal is retained in its report, but cannot commit while
    # an earlier accepted pause is still awaiting explicit continuation.
    if state.get("next_stage") is None:
        state["next_stage"] = "astra_review"
    if state.get("status") != "TASK_COMPLETE":
        state.pop("completed_at", None)
        state.pop("completion_actor", None)
        state.pop("final_decision", None)


def metadata(workspace: Path, run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Return read-only inbox state without creating its inbox or lock file."""
    runner_capability = state.get("intervention_capability", {
        "supported": False, "reason": "The recorded runner predates intervention consumption"})
    try:
        inspection = interventions.inspect(workspace, run_dir)
        pending = inspection["requests"]
        error = None
    except interventions.InterventionError as exc:
        pending = []
        error = {"code": exc.code, "message": str(exc)}
    blocked = []
    if state.get("active_stage"):
        blocked.append("active_stage_requires_reconciliation")
    if state.get("intervention_ack_pending"):
        blocked.append("acknowledgement_pending")
    return {"inspector_capability": {"supported": True, "version": interventions.INBOX_VERSION},
            "runner_capability": runner_capability, "pending_count": len(pending),
            "pending_ids": [item["id"] for item in pending], "pause_intent": state.get("pause_intent"),
            "stop_intent": state.get("stop_intent"),
            "applied_receipts": state.get("applied_interventions", []), "blocked_conditions": blocked,
            "inbox_error": error}


def consume(state: dict[str, Any], run_dir: Path, workspace: Path, *, write_json: Callable[[Path, Any], None],
            now: Callable[[], str], lock_held: bool = False) -> bool:
    """Commit receipt effects and identity together before clearing the inbox.

    Every stage admission funnel passes through here, so an applied stop refuses
    admission before any provider is launched. The initial application of a
    pending stop happens at the saved step boundary exactly like a pause: the
    in-flight step's result is already committed with the same state write.
    """
    if applied_stop(state) is not None:
        assert_stopped(state)
        raise util.Paused(STOP_STATUS, stop_reason(state))

    def write_state():
        write_json(run_dir / "state.json", state)

    def apply_feedback(receipt, applied_receipt):
        pending = state.pop("pending_report_repair", None)
        if pending:
            state.setdefault("report_repair_archive", []).append({
                "reason": "Superseded by applied user feedback", "receipt_id": receipt["id"], "repair": pending})
        goals.apply_intervention_feedback(state, receipt, applied_receipt)

    def apply_boundary_effects(consumed_now):
        boundary_effects(state, consumed_now, now)

    return bool(interventions.consume(run_dir, state, write_state=write_state, apply_feedback=apply_feedback,
                                      before_commit=apply_boundary_effects, lock_held=lock_held))
