"""Count receipt-bound planning reviews once across an inherited review cycle.

The saved attempts and historical counters are evidence, never rewritten. Only
an exactly evidenced duplicate in the current policy counter can be corrected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def _charges(record):
    count, charges = record.get("astra_calls"), record.get("review_charges", [])
    if (
        type(count) is not int
        or count < 0
        or not isinstance(charges, list)
        or any(not isinstance(charge, str) or not charge for charge in charges)
        or len(set(charges)) != len(charges)
    ):
        return None
    return set(charges)


def _evidence(state: RunState):
    planning, history, stages = state.get("planning"), state.get("planning_history"), state.get("stages")
    if not isinstance(planning, dict) or not isinstance(history, list) or not isinstance(stages, list):
        return None
    inherited = planning.get("inherited_review")
    turn = planning.get("turn_event_id")
    if not isinstance(inherited, dict) or not isinstance(turn, str) or not turn:
        return None
    index = inherited.get("cycle")
    if type(index) is not int or not 0 <= index < len(history) or any(not isinstance(row, dict) for row in history):
        return None
    parent = history[index]
    current = _charges(planning)
    parent_charges = _charges(parent)
    if (
        current is None
        or not parent_charges
        or parent["astra_calls"] != len(parent_charges)
        or parent.get("turn_event_id") != turn
        or type(inherited.get("astra_calls")) is not int
        or inherited["astra_calls"] != parent["astra_calls"]
        or not parent_charges <= current
    ):
        return None
    prior, raw = set(), 0
    for row in history:
        if row.get("turn_event_id") != turn:
            continue
        charges = _charges(row)
        if charges is None or row["astra_calls"] != len(charges):
            return None  # Unreceipted/recovery/legacy usage cannot be inferred away.
        prior.update(charges)
        raw += row["astra_calls"]
    # Charge IDs must name actual, completed, ordinarily charged review attempts.
    for charge in prior | current:
        attempts = [row for row in stages if isinstance(row, dict) and row.get("planning_review_charge") == charge]
        if len(attempts) != 1:
            return None
        attempt = attempts[0]
        if (
            attempt.get("stage") not in ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize")
            or type(attempt.get("exit_code")) is not int
            or attempt["exit_code"] != 0
            or any(
                attempt.get(flag) for flag in ("timed_out", "report_only", "runner_owned", "planning_review_refunded")
            )
        ):
            return None
    before = planning.get("review_calls_before_policy", 0) if planning.get("review_depth_reduced") else 0
    if type(before) is not int or before < 0 or planning["astra_calls"] != before + len(current):
        return None
    return {"raw_archived": raw, "archived": len(prior - current), "charges": sorted(prior & current)}


def archived_calls(state: RunState, fallback):
    """At policy activation, add only reviews not already carried by this cycle."""
    evidence = _evidence(state)
    return evidence["archived"] if evidence else fallback


def correction(state: RunState):
    """A pure correction for an already-saved counter from the old activation formula."""
    planning = state.get("planning") or {}
    if not isinstance(planning, dict) or planning.get("review_depth_reduced") is not True:
        return None
    evidence = _evidence(state)
    if not evidence or planning.get("review_calls_before_policy") != evidence["raw_archived"]:
        return None
    duplicate = evidence["raw_archived"] - evidence["archived"]
    if duplicate <= 0:
        return None
    return {**evidence, "previous": planning["astra_calls"], "used": planning["astra_calls"] - duplicate}


def effective_calls(state: RunState):
    adjusted = correction(state)
    planning = state.get("planning") or {}
    return adjusted["used"] if adjusted else planning.get("astra_calls") if isinstance(planning, dict) else None


def can_resume(state: RunState):
    """A false budget pause has room under its unchanged, independently checked limit."""
    adjusted = correction(state)
    planning = state.get("planning") or {}
    if not isinstance(planning, dict):
        return False
    limit = planning.get("review_call_limit", (state.get("settings") or {}).get("planning_review_call_limit", 2))
    return bool(
        adjusted
        and state.get("status") == "PAUSED_PLANNING_BUDGET"
        and state.get("next_stage") in ("astra_challenge", "astra_finalize")
        and type(limit) is int
        and (limit == 0 or limit >= 2 and adjusted["used"] < limit)
        and not any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
        )
    )


def reconcile(state: RunState, *, at):
    """Apply a proven correction once; preserve limits, receipts, history and reported usage."""
    adjusted = correction(state)
    if not adjusted:
        return False
    planning = state["planning"]
    planning["astra_calls"] = adjusted["used"]
    planning["review_calls_before_policy"] = adjusted["archived"]
    state.setdefault("reconciliation_notes", []).append(
        {
            "at": at,
            "stage": state.get("next_stage"),
            "previous_review_calls": adjusted["previous"],
            "review_calls": adjusted["used"],
            "inherited_charges": adjusted["charges"],
            "reason": "Counted inherited Plan Reviewer charge receipts once; saved attempts, history and limits retained",
        }
    )
    return True
