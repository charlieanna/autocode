"""Receipt-bound evidence for one review round after an approved-plan correction.

Pure over core-owned records. Historical failures retain their accounting; only
the current accepted draft (including its accepted report repair) funds a grant.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def _time(value):
    try:
        parsed = dt.datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _token(contract):
    if not isinstance(contract, dict) or not all(key in contract for key in ("task_id", "revision", "body", "hash")):
        return None
    if contract["hash"] != util.digest({key: contract[key] for key in ("task_id", "revision", "body")}):
        return None
    return f"r{contract['revision']}:{contract['hash']}"


def _default(state: RunState, planning):
    settings = state.get("settings") or {}
    if any(key in settings for key in ("planning_review_call_limit", "planning_review_call_limit_origin")):
        return False  # Configured and unmarked saved settings are both protected.
    for item in [planning, *(state.get("planning_history") or [])]:
        if not isinstance(item, dict):
            return False
        if (item.get("astra_calls", 0) or "review_call_limit" in item or "review_call_limit_origin" in item) and (
            type(item.get("review_call_limit")) is not int
            or item["review_call_limit"] != 2
            or item.get("review_call_limit_origin") != "runner_default"
        ):
            return False
    return not any(row.get("kind") == "planning_budget_change" for row in state.get("user_events") or [])


def _feedback(state: RunState):
    event_id = (state.get("clarification_episode") or {}).get("started_by")
    rows = [row for row in state.get("brief_feedback") or [] if row.get("id") == event_id]
    if len(rows) != 1:
        return None
    event = rows[0]
    events = state.get("user_events") or []
    if (
        not event.get("starts_episode")
        or not str(event.get("text") or "").strip()
        or event.get("actor") not in ("user_cli", "user_intervention")
        or events.count(event) != 1
        or _time(event.get("at")) is None
    ):
        return None
    if event.get("actor") == "user_intervention" and not any(
        row.get("id") == event.get("receipt_id")
        and row.get("kind") == "feedback"
        and row.get("text") == event["text"]
        and row.get("observed_goal_token") == event.get("contract_token")
        for row in state.get("applied_interventions") or []
    ):
        return None
    before = events[: events.index(event)]
    if not any(
        row.get("kind") == "goal_approval"
        and row.get("actor") == "user_cli"
        and row.get("token") == event.get("contract_token")
        for row in before
    ):
        return None
    return event


def _accepted_draft(state: RunState, stages, output):
    attempts = [row for row in stages if not row.get("runner_owned")]
    if not attempts:
        return None
    current = attempts[-1]
    if (
        current.get("output") != output
        or type(current.get("exit_code")) is not int
        or current["exit_code"] != 0
        or any(
            current.get(key)
            for key in (
                "rejected",
                "abandoned",
                "timed_out",
                "cleanup_error",
                "dry_run",
                "interrupted",
                "changed_files",
            )
        )
        or not current.get("source_revision")
    ):
        return None
    if current.get("stage") == "draft_plan" and not current.get("report_only"):
        return current, {}
    if not (
        current.get("stage") == "draft_plan_report_repair"
        and current.get("original_stage") == "draft_plan"
        and current.get("report_only") is True
        and current.get("applied_original_events")
    ):
        return None
    accepted = [
        row
        for row in state.get("report_repair_history") or []
        if row.get("result") == "accepted" and row.get("repair") == current
    ]
    if len(accepted) != 1:
        return None
    receipt = accepted[0]
    digest = receipt.get("output_hash")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    originals = [
        row
        for row in attempts[:-1]
        if row.get("stage") == "draft_plan"
        and row.get("events") == current["applied_original_events"]
        and row.get("output") == receipt.get("original_output")
    ]
    if len(originals) != 1:
        return None
    original = originals[0]
    if (
        type(original.get("exit_code")) is not int
        or original["exit_code"] != 0
        or original.get("source_revision") != current["source_revision"]
        or any(
            original.get(key)
            for key in ("timed_out", "abandoned", "report_only", "cleanup_error", "interrupted", "changed_files")
        )
        or any(
            original.get(key) != current.get(key) for key in ("role", "iteration", "contract_hash", "criteria_revision")
        )
    ):
        return None
    # accepted is written only after the existing repair transaction validates
    # the original source, contract and input pins. Retired pending pins are not
    # reconstructed from model text here. The canonical runtime witness checks
    # schema equivalence (including bounded edit offers), hash and original session.
    return current, {
        "output_hash": digest,
        "original_events": original["events"],
        "original_finished_at": original.get("finished_at"),
    }


def _evidence(state: RunState, planning, stages, clock, *, fingerprint):
    """A first review of a materially changed, externally corrected approved plan."""
    if (
        state.get("next_stage") != "review_plan"
        or (state.get("settings") or {}).get("joint_planning") is not True
        or (state.get("settings") or {}).get("adaptive_planning") is not True
        or state.get("status") not in ("RUNNING", "PAUSED_PLANNING_BUDGET")
        or planning.get("review_depth_reduced") is not True
        or planning.get("astra_calls") != 2
        or planning.get("final_token")
        or planning.get("recovery_review_grants")
        or planning.get("recovery_review_calls_used", 0)
        or not _default(state, planning)
        or any(
            state.get(key) for key in ("active_runner_check", "job_failure", "pending_builder_failure", "stop_intent")
        )
        or (state.get("stuck_investigation") or {}).get("stage") == "review_plan"
        or any(row.get("kind") == "stop" for row in state.get("applied_interventions") or [])
    ):
        return None
    reports = planning.get("reports") or {}
    if any(name in reports for name in ("review_plan", "revise_plan", "finalize_plan")):
        return None  # Not another debate round or an internal revision.
    entry = reports.get("draft_plan") or {}
    output = entry.get("output")
    event = _feedback(state)
    current = state.get("goal_contract") or {}
    current_token = _token(current)
    if (
        not event
        or not output
        or not current_token
        or current.get("approval_status") != "draft"
        or current.get("approval_event")
    ):
        return None
    history = state.get("planning_history") or []
    if not history:
        return None
    previous = history[-1]
    inputs, old_inputs = planning.get("inputs"), previous.get("inputs")
    if (
        not isinstance(inputs, list)
        or not isinstance(old_inputs, list)
        or "feedback:" + event["id"] not in set(inputs) - set(old_inputs)
        or planning.get("turn_event_id") != previous.get("turn_event_id")
    ):
        return None
    turn = (state.get("turns") or [{}])[-1]
    if turn.get("event_id") and planning.get("turn_event_id") != turn["event_id"]:
        return None
    old = [row for row in state.get("contract_history") or [] if _token(row) == event.get("contract_token")]
    if len(old) != 1 or old[0].get("task_id") != state.get("task_id") or current.get("task_id") != state.get("task_id"):
        return None
    body = (entry.get("report") or {}).get("contract")
    # source_trace.prepare carries the prior independent seal when a Planner
    # omits it. Admit only that exact inheritance, never a changed/new seal.
    if isinstance(body, dict) and body.get("source_trace") is None and old[0]["body"].get("source_trace") is not None:
        body = {**body, "source_trace": old[0]["body"]["source_trace"]}
    if not isinstance(body, dict) or body != current.get("body") or event["contract_token"] == current_token:
        return None
    fields = (
        "required_behaviors",
        "important_failure_cases",
        "constraints",
        "scope_exclusions",
        "permission_boundaries",
        "acceptance_criteria",
        "technical_approach",
        "end_to_end_flow",
        "milestones",
    )
    before = {key: old[0]["body"].get(key) for key in fields}
    after = {key: body.get(key) for key in fields}
    if fingerprint(before) == fingerprint(after):
        return None
    accepted = _accepted_draft(state, stages, output)
    if not accepted:
        return None
    record, repair = accepted
    finished, feedback_at = _time(record.get("finished_at")), _time(event.get("at"))
    if finished is None or not feedback_at <= finished <= clock or clock - finished > 1800:
        return None
    if repair:
        original_finished = _time(repair["original_finished_at"])
        if original_finished is None or not feedback_at <= original_finished <= finished:
            return None
    if any(
        (row.get("reports") or {}).get("draft_plan", {}).get("output") == output
        for row in state.get("planning_history") or []
    ):
        return None
    return {
        "source": "accepted_corrective_plan",
        "output": output,
        "feedback_id": event["id"],
        "feedback_at": event["at"],
        "stage_index": stages.index(record),
        "approved_token": event["contract_token"],
        "contract_token": current_token,
        "task_id": state["task_id"],
        "source_revision": record["source_revision"],
        "cycle": len(state.get("planning_history") or []),
        **repair,
    }


def evidence(state: RunState, planning, stages, clock, *, fingerprint):
    """Malformed retained metadata never supplies additional authority."""
    try:
        return _evidence(state, planning, stages, clock, fingerprint=fingerprint)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return None


def historical_usage_allowed(state: RunState, record, index, correction):
    """Preserve settled pre-feedback lower bounds only under an explicit disabled cost cap.

    This is not a monetary estimate or permission to complete with unknown proof.
    Current draft/repair usage and unscoped usage or custody holds still refuse.
    """
    if not correction or correction.get("source") != "accepted_corrective_plan":
        return False
    cap = ((state.get("settings") or {}).get("limits") or {}).get("max_cost_usd")
    finished, feedback = _time(record.get("finished_at")), _time(correction.get("feedback_at"))
    return bool(
        type(cap) in (int, float)
        and cap == 0
        and index < correction["stage_index"]
        and finished is not None
        and feedback is not None
        and finished < feedback
    )
