"""The pause a client may request while a run is working (#1598).

This is the boundary pause of docs/interventions.md: the current step finishes and
is saved, then the run pauses with its next stage selected and continues through
the ordinary `--resume-paused` action. Pure over the saved state and the fresh
status view; the status command supplies the pending inbox requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

LABEL = "Pause after this step"
EFFECT = "The current step finishes and is saved, then the run pauses. Resume continues where it paused."


def working(view: dict[str, Any]) -> bool:
    """A live stage, or a fresh birth-bound controller (or lock holder) advancing the run."""
    liveness = view.get("liveness") if isinstance(view.get("liveness"), dict) else {}
    controller = view.get("controller") if isinstance(view.get("controller"), dict) else {}
    if liveness.get("kind") in ("supervised", "unsupervised"):
        return True
    if controller.get("kind") == "running":
        return controller.get("checked") is True and controller.get("alive") is True
    return controller.get("kind") == "busy"


def project(
    state: RunState, view: dict[str, Any], *, pending=(), run_dir=None, workspace=None
) -> dict[str, Any] | None:
    """The offer, or None when nothing is running, the runner cannot consume a pause, or the run is stopped.

    `requested` is true while a pause request waits in the inbox; the offer then
    names no command, so a client cannot queue a second one.
    """
    if view.get("done") or not working(view):
        return None
    capability = state.get("intervention_capability")
    if not isinstance(capability, dict) or capability.get("supported") is not True:
        return None
    applied = state.get("applied_interventions")
    if state.get("stop_intent") or any(
        isinstance(row, dict) and row.get("kind") == "stop" for row in (applied if isinstance(applied, list) else [])
    ):
        return None
    requested = any(isinstance(row, dict) and row.get("kind") == "pause" for row in pending)
    command = [
        "intervention",
        "submit",
        "--workspace",
        str(workspace) if workspace else "WORKSPACE",
        "--run-dir",
        str(run_dir) if run_dir else "RUN_DIR",
        "--request-id",
        "REQUEST_ID",
        "--kind",
        "pause",
        "--json",
    ]
    return {"label": LABEL, "effect": EFFECT, "requested": requested, "next_command": None if requested else command}
