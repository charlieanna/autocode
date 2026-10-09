"""Recheck a saved draft's first assignment under the current run settings.

A saved preflight is not execution authority. Rejected drafts reuse the ordinary bounded
report repair path; they never create a user answer or renew planning allowance.
"""
from copy import deepcopy


def reconcile(runner, state, run_dir, validate):
    """Queue repair before displaying or approving an unassignable saved draft; return whether changed."""
    contract = state.get("goal_contract") or {}
    if (state.get("status") not in ("AWAITING_GOAL_APPROVAL", "PAUSED_GOAL_UNAPPROVED")
            or contract.get("approval_status") == "approved"
            or any(state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair"))
            or not state.get("settings", {}).get("joint_planning")
            or not contract.get("body", {}).get("initial_task")):
        return False
    try:
        validate(state, contract["body"])
        return False
    except ValueError as error:
        # A completed authored planning report owns this draft. Preserve the same provider,
        # saved schema and attempt allowance when asking it to correct its first task.
        origin = contract.get("origin")
        if origin == "adaptive_review_approval":
            # This reviewer accepts a draft but cannot rewrite it in its saved schema.
            # Ask its actual Planner to repair, then run the independent review again.
            origin = "astra_discovery"
        report = state.get("planning", {}).get("reports", {}).get(origin, {})
        original = next((row for row in reversed(state.get("stages", []))
                         if (row.get("original_stage") or row.get("stage")) == origin and not row.get("rejected")
                         and row.get("output") == report.get("output")), None)
        if original is None:
            return False  # A user edit or unrecorded legacy draft must use the existing refusal path.
        original = deepcopy(original)
        original.update(stage=origin, draft_assignment_recheck=True)
        state.pop("displayed_goal", None)
        # Invalidation retires the old exact-token handoff; repair must earn a new review/approval.
        runner.goals.invalidate(state, "Saved draft first assignment is no longer assignable")
        state.pop("user_request", None)
        state.pop(runner.resolver_human.PUBLIC, None)
        state.pop(runner.resolver_human.PRIVATE, None)
        state["planning"]["final_token"] = None
        state["next_stage"] = origin
        try:
            runner.reject_completed_stage(state, run_dir, original, error)
        except runner.ReportRepairQueued:
            return True


def retain_review_allowance(state, previous, original):
    """A repair of an accepted draft cannot renew its already-spent review capacity.

    The runner marks draft_assignment_recheck on the saved original attempt when
    rechecking a draft. Repaired-result acceptance consumes it here; model output
    cannot supply this marker. Reports and the refreshed approval token stay new.
    """
    if not original.get("draft_assignment_recheck"):
        return
    for key in ("astra_calls", "review_call_limit", "review_call_limit_origin", "review_charges",
                "refunded_review_charges", "recovery_review_grants", "recovery_review_calls_used"):
        if key in previous:
            state["planning"][key] = deepcopy(previous[key])
