"""Pure admission and retained-history transition for one stopped native planning attempt.

These facts do not authenticate a launch. The original native journal owner
must separately verify its released root, supervisor and unchanged source.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_output_cap as output_cap
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_output_cap as output_cap
    import autocode_run_status as run_status
    import autocode_util as util


def require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native planning abandonment: " + reason)


def only_abandon(argv):
    """Accept only the existing non-advancing public control and identity flags."""
    require(isinstance(argv, (list, tuple)) and argv, "missing original CLI arguments")
    valued = {"--abandon-stage", "--workspace", "--run-dir"}
    switches = {"--json", "--no-chat"}
    counts, index = {}, 0
    while index < len(argv):
        token = argv[index]
        name, separator, value = token.partition("=")
        require(name in valued or token in switches, "cannot combine abandonment with another control or setting")
        counts[name] = counts.get(name, 0) + 1
        require(counts[name] == 1, "duplicate abandonment/identity flag")
        if name in valued:
            if not separator:
                index += 1
                require(index < len(argv) and not argv[index].startswith("--"), "missing identity/control value")
            else:
                require(bool(value), "empty identity/control value")
        index += 1
    require(counts.get("--abandon-stage") == counts.get("--run-dir") == 1, "needs one exact saved-run attempt")


def repair_launch(state: RunState, record) -> bool:
    """True when ``record`` is the counted launch of this run's pending report-only repair.

    That launch stops like the planning stage it repairs (timeout, collected output limit,
    cleanup) and is closed the same way; its counted attempt stays spent.
    """
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict) or not isinstance(record, dict):
        return False
    original = pending.get("original")
    if not isinstance(original, dict):
        return False
    stage, attempts = original.get("stage"), pending.get("attempts")
    return (
        isinstance(stage, str)
        and record.get("report_only") is True
        and record.get("original_stage") == stage
        and record.get("stage") == stage + "_report_repair"
        and (record.get("route_role") or record.get("role")) == (original.get("route_role") or original.get("role"))
        and type(attempts) is int
        and attempts >= 1
    )


def request(state: RunState, selected, stages):
    """Select an accounted stop; the original owner must authenticate collected length bytes."""
    record = state.get("active_stage")
    goal = state.get("goal_contract") or {}
    repair = repair_launch(state, record)
    require(
        state.get("status") in ("PAUSED_PROVIDER_TIMEOUT", output_cap.STATUS, "PAUSED_PROCESS_CLEANUP")
        and state.get("settings", {}).get("planning_flow") == "v2"
        and state.get("current_task") is None
        and goal.get("approval_status") != "approved"
        and not goal.get("approval_event")
        and (repair or not state.get("pending_report_repair"))
        and not any(
            state.get(k)
            for k in ("parent_run", "parent_batch", "orchestration_batch", "orchestration_history", "progressive")
        ),
        "only an original preapproval planning timeout or collected output limit, or a stopped launch of its "
        "pending report repair, is supported",
    )
    require(isinstance(record, dict) and record, "missing active planning stop")
    assert isinstance(record, dict)  # require above refused anything else
    stage = record.get("original_stage") if repair else record.get("stage")
    require(
        type(record.get("iteration")) is int
        and record["iteration"] == state.get("iteration")
        and selected == f"{record['iteration']:03d}/{Path(record.get('output') or '').stem}"
        and stage in stages
        and stage == state.get("next_stage"),
        "selected attempt does not match the current planning frontier",
    )
    require(
        record.get("accounted") is True
        and type(record.get("pid")) is int
        and record["pid"] > 0
        and type(record.get("exit_code")) is int
        and isinstance(record.get("finished_at"), str)
        and bool(record["finished_at"])
        and bool(record.get("supervision"))
        and (repair or not record.get("report_only"))
        and not any(record.get(k) for k in ("interrupted", "dry_run", "rejected", "abandoned", "cleanup_error")),
        "uncertain, unaccounted or already rejected attempt",
    )
    if state.get("status") == output_cap.STATUS:
        require(
            record.get("engine") == "opencode"
            and record.get("timed_out") is False
            and record.get("exit_code") == 0
            and "expected_session" in record
            and not record.get("supervision_errors"),
            "output limit must be an original collected clean exit, not an uncertain stop",
        )
    else:
        require(record.get("timed_out") is True, "planning timeout was not recorded as timed out")
        if state.get("status") == "PAUSED_PROCESS_CLEANUP":
            require(
                record.get("exit_code") != 0
                and record.get("timeout_kind") in ("idle", "tool", "stage")
                and isinstance(record.get("timeout_reason"), str)
                and bool(record["timeout_reason"])
                and not record.get("supervision_errors"),
                "cleanup stop lacks its original collected deadline decision",
            )
    return record


@dataclass(frozen=True)
class PlanningAbandonIntent:
    """One exact non-advancing action; never permission to dispatch a target."""

    selected: str
    stages: frozenset
    record_sha256: str


def constructor_intent(args, state: RunState, stages):
    """Validate the public action before the original native constructor."""
    selected = getattr(args, "abandon_stage", None)
    if selected is None:
        return None
    only_abandon(getattr(args, "_native_action_argv", None))
    allowed = frozenset(stages)
    record = request(state, selected, allowed)
    return PlanningAbandonIntent(selected, allowed, util.digest(record))


def constructor_record(intent, state: RunState):
    """Recheck the same immutable intent; the owner authenticates the real tail."""
    require(
        type(intent) is PlanningAbandonIntent
        and type(intent.stages) is frozenset
        and type(intent.selected) is str
        and type(intent.record_sha256) is str
        and intent.stages
        and all(type(stage) is str for stage in intent.stages),
        "missing exact planning abandonment intent",
    )
    record = request(state, intent.selected, intent.stages)
    require(util.digest(record) == intent.record_sha256, "stopped record changed before construction")
    return record


def abandoned(state: RunState, record, selected, *, at):
    """Keep original paths/accounting/routes; leave explicit resume to the CLI.

    A stopped report repair returns the run to the stage it repairs; its counted attempt stays spent.
    """
    candidate = deepcopy(state)
    rejected = deepcopy(record)
    collected_length = state.get("status") == output_cap.STATUS
    cancelled = state.get("status") == "PAUSED_PROCESS_CLEANUP"
    repair = record.get("report_only") is True
    # An original stage keeps its texts; a repair names itself instead.
    original, kind = ("", "native report repair") if repair else ("original ", "native planning")
    title = "Native report repair" if repair else "Original native"
    held = "Native report repair held cancellation" if repair else "Original native held planning cancellation"
    stop_title = "Native report repair" if repair else "Original native planning"
    rejected.update(
        abandoned=True,
        rejected=True,
        rejection_reason=(
            f"Operator abandoned {original}collected {kind} output limit; incomplete response retained"
            if collected_length
            else f"Operator abandoned {original}cancelled {kind} launch; no response adopted"
            if cancelled
            else f"Operator abandoned {original}stopped {kind} timeout; partial response retained"
        ),
    )
    candidate.setdefault("stages", []).append(rejected)
    candidate.setdefault("history", []).append(deepcopy(rejected))
    candidate.pop("active_stage", None)
    candidate.setdefault("sessions", {}).pop(record.get("route_role", record["role"]), None)
    candidate.setdefault("user_events", []).append(
        {"kind": "stage_abandoned", "actor": "user_cli", "at": at, "attempt_id": selected, "changed_files": []}
    )
    candidate["recovery_context"] = {
        "attempt_id": selected,
        "role": record["role"],
        "source_revision": record["source_revision"],
        "changed_files": [],
        "events": record["events"],
        "source_snapshot": record["after_ref"],
        "instruction": (
            f"{title} output limit abandoned after authenticated collected stop; incomplete report is not successful evidence."
            if collected_length
            else f"{title} held launch cancelled after verified stop; no report is successful evidence."
            if cancelled
            else f"{title} timeout abandoned after verified stop; partial report is not successful evidence."
        ),
    }
    run_status.pause(
        candidate,
        "PAUSED_STAGE_ABANDONED",
        (
            f"{stop_title} output limit retained. Resume explicitly; no agent launched."
            if collected_length
            else f"{held} retained. Resume explicitly; no agent launched."
            if cancelled
            else f"{stop_title} timeout retained. Resume explicitly; no agent launched."
        ),
        next_stage=record["original_stage"] if repair else record["stage"],
    )
    return candidate
