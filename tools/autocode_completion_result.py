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
    if stage == "astra_review":
        _reconcile_preservation_review(state, workspace, run_dir)
    try:
        if delivery.requirement((state.get("goal_contract") or {}).get("body") or {}) is None:
            return
        current = source_scope.snapshot(workspace, state)
        if (
            regression.required(state, all_due=True)
            and regression.complete(state, current["revision"], all_due=True, require_changed_code=False)
            and delivery.prepare(state, workspace, current)
        ):
            regression.before_review(state, stage, workspace, run_dir)
    except (OSError, ValueError) as error:
        raise Paused("PAUSED_COMPLETION_GATE", "Git delivery required: " + str(error)) from error


def _reconcile_preservation_review(state: RunState, workspace, run_dir):
    """Retain an obsolete private proof blocker and let the independent review run.

    This grants no completion: the fresh runner receipt and the independent
    review remain required. Published questions and all other blocker causes stay.
    """
    import copy
    from datetime import datetime
    from pathlib import Path

    try:
        from . import autocode_goals as goals
        from . import autocode_regression as regression
        from . import autocode_resolver_human as human
        from . import autocode_source_scope as source_scope
        from . import autocode_util as util
    except ImportError:
        import autocode_goals as goals
        import autocode_regression as regression
        import autocode_resolver_human as human
        import autocode_source_scope as source_scope
        import autocode_util as util
    proposal = state.get(human.PRIVATE) or {}
    origin = proposal.get("origin") or {}
    proof = state.get("regression_proof") or {}
    task = state.get("current_task") or {}
    contract = state.get("goal_contract") or {}
    if (
        state.get("status") != "RUNNING" or state.get("next_stage") != "astra_review"
        or state.get(human.PUBLIC) or state.get("pending_questions")
        or proposal.get("scope") != "blocker" or origin.get("stage") != "astra_review"
        or not goals.approved(state) or not regression.preserve_only(state)
        or not task.get("id") or task.get("contract_hash") != contract.get("hash")
        or (proof.get("execution_context") or {}).get("contract_identity") != util.digest(contract)
        or proof.get("verdict") != "PASS" or proof.get("source_files") != []
        or any(state.get(k) for k in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts"))
    ):
        return False
    revision = source_scope.snapshot(workspace, state)["revision"]
    review = next((row for row in reversed(state.get("stages", [])) if (
        row.get("stage") == "astra_review" and row.get("output") == origin.get("output")
        and row.get("task_id") == task.get("id") and row.get("contract_hash") == contract.get("hash")
        and row.get("source_revision") == revision and not row.get("rejected")
    )), None)
    try:
        newer = review and datetime.fromisoformat(proof["proved_at"]) > datetime.fromisoformat(review["finished_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if not newer or not regression.complete(state, revision, all_due=False, require_changed_code=False):
        return False
    previous = None
    root = (Path(run_dir) / "regression").resolve()
    for row in reversed(state.get("regression_proofs", [])):
        if row.get("verdict") != "FAIL" or not row.get("path") or not row.get("source_revision"):
            continue
        path = Path(row["path"]).absolute()
        try:
            if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_nlink != 1:
                continue
            if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
                continue
            captured = any(
                s.get("stage") == "regression_proof" and s.get("runner_owned") and s.get("output") == str(path)
                and datetime.fromisoformat(s["finished_at"]) < datetime.fromisoformat(review["finished_at"])
                for s in state.get("stages", [])
            )
            receipt = util.read(path) if captured else {}
            if (isinstance(receipt, dict) and receipt.get("verdict") == "FAIL" and receipt.get("source_revision") == row["source_revision"]
                and receipt.get("failures") == ["Only test files changed; a fix must change product code"]):
                previous = {"history": copy.deepcopy(row), "receipt": receipt}
                break
        except (OSError, KeyError, TypeError, ValueError):
            continue
    if previous is None:
        return False
    state.setdefault("validation_archive", []).append({
        "at": util.now(), "reason": "Fresh authenticated preservation proof supersedes the private product-code-only blocker",
        "resolver_proposal": copy.deepcopy(proposal), "regression_proof": copy.deepcopy(previous),
        "validation": copy.deepcopy(state.get("validation", {})),
    })
    state.pop(human.PRIVATE)
    if state.get("user_request") == proposal.get("request"):
        state.pop("user_request", None)
    return True


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
    required = regression.required(state, all_due=False)
    current_milestone = (state.get("current_task") or {}).get("milestone_id")
    settle_same_revision_ids = [
        row["id"]
        for row in proof_findings.open_rows(state)
        if current_milestone
        and (row.get("scope") or {}).get("milestone_id") == current_milestone
        and revision in (row.get("failed_revisions") or [])
        and regression.MISSING_CASES_REASON in row.get("finding", "")
    ]
    # The runner's own proof only: an equivalent-mutant or unreachable-line reason is the independent
    # reviewer's to give in the review this precedes (#2474), and a missing one has its own Builder route
    # (autocode_changed_code_rework), so it must not open a proof finding the Builder cannot clear.
    proved = required and regression.complete(state, revision, all_due=False, require_changed_code=False)
    reason = "" if proved or not required else regression.rejection(state, all_due=False)
    proof_findings.reconcile(
        state,
        revision,
        required=required,
        proved=proved,
        reason=reason,
        settle_same_revision_ids=settle_same_revision_ids,
    )


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
        or regression_complete(state, current["revision"], all_due=False)
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
