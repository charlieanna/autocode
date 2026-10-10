"""Correct pre-build planning recoveries miscounted as implementation batches."""

PLANNING = frozenset({"requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"})
PRE_BUILD = PLANNING | {"recognize_workflow"}


def planning_only_count(state):
    """Prove an old counter contains no implementation attempts, or decline."""
    count = state.get("no_progress_batches", 0)
    if type(count) is not int or count <= 0:
        return None
    if any(
        state.get(key)
        for key in (
            "active_stage",
            "pending_report_repair",
            "uncertain_artifacts",
            "implementation",
            "no_progress_reports",
            "orchestration_batch",
            "orchestration_history",
        )
    ):
        return None
    stages = state.get("stages") or []
    if not stages or any(
        (row.get("original_stage") or row.get("stage")) not in PRE_BUILD
        for row in stages
        if not row.get("runner_owned")
    ):
        return None
    recovered = [*state.get("automatic_timeout_recoveries", []), *state.get("automatic_permission_recoveries", [])]
    attempts = {
        row.get("attempt_id")
        for row in recovered
        if row.get("stage") in PLANNING and row.get("attempt_id") and not row.get("changed_files")
    }
    return sorted(attempts) if len(attempts) >= count else None


def reconcile(state, *, issued, approved, supersede, now):
    """One explicit resume may withdraw only a verified, false pre-build hold.

    The caller verifies the current request under the run lock. Unknown history,
    any implementation attempt and stale requests retain their existing hold.
    Timeout allowances and all original attempt evidence remain unchanged.
    """
    if not issued or issued.get("scope") != "operational_exhaustion" or not approved:
        return False
    entry = state.get("resolver", {}).get("human_escalations", {}).get(issued["request_id"], {})
    origin = entry.get("identity", {}).get("proposal", {}).get("origin", {})
    if origin.get("pause_status") != "PAUSED_NO_PROGRESS":
        return False
    attempts = planning_only_count(state)
    if attempts is None:
        return False
    reason = "Planning recoveries are not unchanged implementation batches; no Builder has run"
    if not supersede(state, reason):
        return False
    state.setdefault("user_events", []).append(
        {
            "kind": "recovery_accounting_correction",
            "actor": "runner",
            "at": now(),
            "request_id": issued["request_id"],
            "previous_no_progress_batches": state["no_progress_batches"],
            "planning_attempts": attempts,
            "reason": reason,
        }
    )
    state.update(no_progress_batches=0, status="RUNNING", phase="READY_TO_EXECUTE")
    state.pop("stop_reason", None)
    return True
