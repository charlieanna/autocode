"""Apply terminal completion evidence and project its state in the original order.

The controller supplies its gates and persistence callbacks. This module owns
only completion-result application, not the workflow loop or current proof.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def before_review(state: RunState, stage, workspace, run_dir):
    """A passing runner regression proof can deliver before the judge inspects Git.

    This avoids requiring a judge to claim that an undelivered commit exists.
    The changed HEAD receives fresh runner proof and ordinary independent review.
    Plans without executable regression cases retain the final completion gate.
    """
    try:
        from . import autocode_git_delivery as delivery
        from . import autocode_regression as regression
        from . import autocode_source_scope as source_scope
        from .autocode_util import Paused
    except ImportError:
        import autocode_git_delivery as delivery
        import autocode_regression as regression
        import autocode_source_scope as source_scope
        from autocode_util import Paused
    regression.before_review(state, stage, workspace, run_dir)
    if stage not in ("sol", "astra_checkpoint", "astra_review"):
        return
    _reconcile_proof_finding(state, workspace)
    try:
        if delivery.requirement((state.get("goal_contract") or {}).get("body") or {}) is None:
            return
        current = source_scope.snapshot(workspace, state)
        if (
            regression.required(state, all_due=True, include_changed_code=False)
            and regression.complete(state, current["revision"], all_due=True, require_changed_code=False)
            and delivery.prepare(state, workspace, current)
        ):
            regression.before_review(state, stage, workspace, run_dir)
    except (OSError, ValueError) as error:
        raise Paused("PAUSED_COMPLETION_GATE", "Git delivery required: " + str(error)) from error


def _proof_routing(state: RunState) -> bool:
    """Runs whose unproven regression is carried by a runner finding (autocode_proof_findings)."""
    try:
        from . import autocode_workflow as workflow
    except ImportError:
        import autocode_workflow as workflow
    return state.get("version", 2) >= 3 and not workflow.enabled(state)


def _reconcile_proof_finding(state: RunState, workspace):
    try:
        from . import autocode_proof_findings as proof_findings
        from . import autocode_regression as regression
        from . import autocode_source_scope as source_scope
    except ImportError:
        import autocode_proof_findings as proof_findings
        import autocode_regression as regression
        import autocode_source_scope as source_scope
    if not _proof_routing(state):
        return
    revision = source_scope.snapshot(workspace, state)["revision"]
    required = regression.required(state, all_due=True)
    proved = required and regression.complete(state, revision, all_due=True)
    reason = "" if proved or not required else regression.rejection(state)
    proof_findings.reconcile(state, revision, required=required, proved=proved, reason=reason)


def _send_back_unproven(state: RunState, current, record, regression_complete):
    """Fields that return a completion request to review while the runner's proof finding is open, or None."""
    try:
        from . import autocode_proof_findings as proof_findings
    except ImportError:
        import autocode_proof_findings as proof_findings
    if (
        record.get("stage") != "astra_review"
        or not _proof_routing(state)
        or not proof_findings.open_rows(state)
        or regression_complete(state, current["revision"], all_due=True)
    ):
        return None
    return {
        "next_stage": "astra_review",
        "iteration": state.get("iteration", 0) + 1,
        **proof_findings.send_back(state, current["revision"]),
    }


def check_source_verdict(decision):
    """Refuse a completion the Completion Reviewer's own source verdict contradicts (#1909).

    On a traced contract the reviewer records ``source_verdict`` beside its decision (#595), and
    its prompt says a FAIL without a real gap only costs a repair. A COMPLETE decision whose
    verdict is FAIL, or names an uncovered sentence, is therefore an invalid report: the bounded
    report repair asks for one consistent answer. The runner's mechanical trace check stays the gate.
    """
    verdict = decision.get("source_verdict")
    if not isinstance(verdict, dict):
        return
    uncovered = [str(sentence) for sentence in verdict.get("uncovered") or []]
    if verdict.get("verdict") != "FAIL" and not uncovered:
        return
    named = "; ".join(f'"{sentence}"' for sentence in uncovered) or "none named"
    raise ValueError(
        f"source_verdict is {verdict.get('verdict')} with uncovered source sentences ({named}) but status is "
        f"{decision.get('status')}: record PASS with no uncovered sentence if the delivery honors every source "
        "sentence, or return REWORK with a next_task that covers them"
    )


def apply(
    state: RunState,
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
    check_source_verdict(decision)
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
    if modern and (sent_back := _send_back_unproven(state, current, record, regression_complete)):
        commit_fields(sent_back)
        record_decision(state, decision)
        save_record(state, record)
        return True
    if modern and blocking_entries(state):
        raise paused("PAUSED_COMPLETION_GATE", ledger_rejection(state))
    if modern and missing_human_reviews(state):
        raise paused("PAUSED_COMPLETION_GATE", "Artifact review requires current passing independent evidence first")
    if not completion_ready(state, decision, current):
        if not regression_complete(state, current["revision"], all_due=True):
            raise paused("PAUSED_COMPLETION_GATE", regression_rejection(state))
        raise paused("PAUSED_COMPLETION_GATE", completion_rejection(state))
    try:
        from . import autocode_git_delivery as delivery
        from . import autocode_workflow as workflow
    except ImportError:
        import autocode_git_delivery as delivery
        import autocode_workflow as workflow
    try:
        delivered = delivery.prepare(state, workspace, current)
    except (OSError, ValueError) as error:
        raise paused("PAUSED_COMPLETION_GATE", "Git delivery required: " + str(error)) from error
    if delivered:
        commit_fields(
            {
                "status": "RUNNING",
                "next_stage": workflow.review_stage(state),
                "phase": "EXECUTING",
                "iteration": state.get("iteration", 0) + 1,
            }
        )
        record_decision(state, decision)
        save_record(state, record)
        return True
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
