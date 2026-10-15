"""Apply terminal completion evidence and project its state in the original order.

The controller supplies its gates and persistence callbacks. This module owns
only completion-result application, not the workflow loop or current proof.
"""

from __future__ import annotations


def apply(
    state,
    current,
    record,
    decision,
    workspace,
    *,
    modern,
    native_calls,
    logical_completion_ready,
    regression_required,
    require_visual_completion,
    prepare_progressive_completion,
    blocking_entries,
    ledger_rejection,
    missing_human_reviews,
    completion_ready,
    regression_complete,
    regression_rejection,
    completion_rejection,
    milestones_enabled,
    accept_milestone,
    commit_fields,
    now,
    record_decision,
    save_record,
    paused,
):
    """Return whether archival completion already recorded and saved the result.

    Current completion leaves the controller's common decision recording and
    save path in place. Native publication still follows acceptance and precedes
    that path; simulated completion never acquires current proof or publication.
    """
    try:
        from . import autocode_replay_checks as recorded_checks
    except ImportError:
        import autocode_replay_checks as recorded_checks
    if recorded_checks.simulated(state.get("settings")):
        recorded_checks.finish_decision(
            state,
            current,
            record,
            decision,
            workspace,
            native=native_calls(),
            completion_ready=logical_completion_ready,
            regression_required=lambda value: regression_required(value, all_due=True),
        )
        commit_fields({"final_decision": decision, "completed_at": now()})
        if modern:
            state["phase"] = "COMPLETE"
        record_decision(state, decision)
        save_record(state, record)
        return True
    native = native_calls()
    prepared_completion = native.completion(state, current, record, decision) if native is not None else None
    require_visual_completion(
        state,
        current_snapshot=current,
        **({"scope_authority": prepared_completion.scope_authority} if prepared_completion is not None else {}),
    )
    prepare_progressive_completion(state, current, record, product_findings=blocking_entries(state))
    if modern and blocking_entries(state):
        raise paused("PAUSED_COMPLETION_GATE", ledger_rejection(state))
    if modern and missing_human_reviews(state):
        raise paused(
            "PAUSED_COMPLETION_GATE", "Artifact review requires current passing independent evidence first"
        )
    if not completion_ready(state, decision, current):
        if not regression_complete(state, current["revision"], all_due=True):
            raise paused("PAUSED_COMPLETION_GATE", regression_rejection(state))
        raise paused("PAUSED_COMPLETION_GATE", completion_rejection(state))
    commit_fields({"status": "TASK_COMPLETE", "completed_at": now(), "final_decision": decision, "next_stage": None})
    if milestones_enabled(state):
        accept_milestone(
            state,
            current,
            **({"visual_proof": prepared_completion.proof} if prepared_completion is not None else {}),
        )
    if modern:
        state["phase"] = "COMPLETE"
    if prepared_completion is not None:
        prepared_completion.publication(state)
    return False
