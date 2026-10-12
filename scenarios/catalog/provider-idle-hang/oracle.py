"""A silent provider hang must stop on the idle bound with a CLI-accepted recovery.

In check mode (``run is None``) the oracle scores the greenfield-greeting-cli product,
so OracleControlTests can reject the seed and accept the reference without a run.
With a run, it judges the idle stop — never the undelivered greeting product.
"""

import json
from pathlib import Path

from harness import catalog
from harness.needs_command import Rejected, accept
from harness.oracle import Check


def _product_checks(project, scenario):
    sibling = catalog.load("greenfield-greeting-cli")
    return sibling.oracle()(project, sibling)


def _state(run) -> dict:
    if not run or not run.get("run_dir"):
        return {}
    path = Path(run["run_dir"]) / "state.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def _idle_records(state: dict) -> list[dict]:
    rows = []
    for key in ("automatic_timeout_recoveries", "stages"):
        for row in state.get(key) or []:
            if isinstance(row, dict) and row.get("timeout_kind") == "idle":
                rows.append(row)
    recovery = state.get("recovery_context") or {}
    if recovery.get("timeout_kind") == "idle":
        rows.append(recovery)
    active = state.get("active_stage") or {}
    if active.get("timeout_kind") == "idle":
        rows.append(active)
    return rows


def check(project, scenario, run=None):
    """With a run: judge the idle stop. Without one: score the greeting product."""
    if run is None:
        return _product_checks(project, scenario)
    status = (run or {}).get("status") or ""
    state = _state(run)
    if not status:
        status = state.get("status") or ""
    idle = _idle_records(state)
    view = (run or {}).get("view") or {}
    next_command = view.get("next_command")
    accepted = False
    accept_detail = "no next_command"
    if next_command:
        try:
            accept(next_command)
            accepted = True
            accept_detail = next_command
        except Rejected as error:
            accept_detail = str(error)
    reason = " ".join(
        filter(
            None,
            (
                state.get("stop_reason"),
                (idle[0] if idle else {}).get("timeout_reason"),
                (idle[0] if idle else {}).get("timeout_kind"),
            ),
        )
    )
    stopped = status.startswith(("PAUSED_", "WAITING_FOR_USER", "BLOCKED_")) and bool(idle)
    # Idle is 2 s; a few automatic recoveries still finish far below the tool bound (1800 s).
    idle_bound = any(
        "inactivity limit (2 second" in (row.get("timeout_reason") or "") for row in idle
    ) or "2 second" in (state.get("stop_reason") or "")
    return [
        Check("stopped_for_provider_idle", stopped, f"status={status!r}; idle={len(idle)}"),
        Check("timeout_kind_idle", bool(idle), f"idle records: {len(idle)}; reason={reason[:200]}"),
        Check("idle_bound_not_tool", idle_bound, reason[:200]),
        Check("recovery_command_accepted", accepted, accept_detail[:300]),
    ]
