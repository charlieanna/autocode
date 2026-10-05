"""The completion gate: whether a Completion Owner's COMPLETE decision may end the run.

It reads the goal contract, the findings ledger and the regression proof, so it sits above them;
autocode_support, which those modules use, no longer imports them for it. See AGENTS.md.
"""
from __future__ import annotations

from pathlib import Path

try:
    from .autocode_util import Paused, criteria_definition, file_hash
    from .autocode_progressive_completion import ready as progressive_ready
    from . import autocode_design_coverage as design_coverage, autocode_protected_oracles as protected_oracles
except ImportError:
    from autocode_util import Paused, criteria_definition, file_hash
    from autocode_progressive_completion import ready as progressive_ready
    import autocode_design_coverage as design_coverage, autocode_protected_oracles as protected_oracles

REFUSED = "Completion rejected: missing, stale, failed or unverified independent evidence"


def rejection(state) -> str:
    """Why COMPLETE was refused, naming the criteria the latest validation did not pass.

    A live ladder-20 run (Claude models, 2026-09-30) reached the plain refusal twice: the last
    milestone's validation left out the accepted milestone's criteria, and the Completion Owner
    could not tell what was missing or how to ask for it."""
    if not design_coverage.ready(state):
        record = (state.get("settings") or {}).get("design_manifest") or {}
        inventory_blockers = design_coverage.manifest.blockers(record) if record else []
        if inventory_blockers:
            return (f"{REFUSED}. Figma inventory still has unavailable required fonts/assets: "
                    + "; ".join(inventory_blockers))
        missing = ", ".join(design_coverage.gaps(state)) or "changed or unbound design evidence"
        return f"{REFUSED}. Design coverage needs fresh independent evidence for: {missing}."
    results = {row["id"]: row.get("status") for row in (state.get("validation") or {}).get("criterion_results", [])}
    gaps = [row["id"] for row in state.get("acceptance_criteria", []) if results.get(row["id"]) != "PASS"]
    if not gaps:
        return REFUSED
    return (f"{REFUSED}. The latest validation has no passing result for {', '.join(gaps)}; completion needs one "
            "validation that passes every criterion on the current source. Request CONTINUE with a next_task of "
            "kind=validate that lists every criterion (in a milestone run, on the current milestone: a validate "
            "task may recheck accepted milestones' criteria).")


def stale_validation(state, current):
    """How the saved validation belongs to another source, goal revision or task than the current one, or None.

    Issue #302: after an operator edited a paused run's source, --accept-completion gave only the generic
    refusal, which did not say that the missing step was a fresh validation."""
    validation = state.get("validation") or {}
    checked = validation.get("source_revision")
    if checked and checked != current["revision"]:
        return f"it checked source {checked[:12]}, but the workspace is now at {current['revision'][:12]}"
    contract = state.get("goal_contract") or {}
    if validation.get("contract_hash") and validation["contract_hash"] != contract.get("hash"):
        return (f"it belongs to goal revision {validation.get('contract_revision')}, "
                f"but the approved goal is now revision {contract.get('revision')}")
    task = (state.get("current_task") or {}).get("id")
    if validation.get("task_id") and task and validation["task_id"] != task:
        return f"it belongs to task {validation['task_id']}, but the current task is {task}"
    return None


def completion_ready(state, decision, current, *, require_human_reviews=True, require_independent=True):
    # Design coverage is an independent-validation obligation (sol / checkpoint).
    # The final-audit self-check probe passes require_independent=False and must
    # not demand design_results from the builder's self-assessment.
    if require_independent and not protected_oracles.ready(state, current.get("revision")):
        return False
    if require_independent and not design_coverage.ready(state):
        return False
    if not progressive_ready(state, current):
        return False
    human_only_gap = False
    if (require_independent and state.get('settings', {}).get('milestone_checkpoints', {}).get('enabled')
            and state.get('validation', {}).get('reviewer_role') != 'sol'):
        return False
    if require_independent and state.get('settings',{}).get('workflow',{}).get('mode') == 'glm_final_audit_v2':
        review=state.get('validation',{})
        if review.get('reviewer_role')!='astra' or review.get('final_audit') is not True:
            return False
    if state.get("version", 2) >= 3:
        try:
            from . import autocode_goals as goals
        except ImportError:
            import autocode_goals as goals
        try:
            goals.execution_guard(state, decision)
        except Paused:
            return False
        contract = state["goal_contract"]
        validation = state.get("validation", {})
        human_ids = [c["id"] for c in contract["body"]["acceptance_criteria"] if c["human_review"]]
        human_only_gap = (bool(human_ids)
                          and goals.human_only_pending_validation(state, validation, human_ids[0]))
        if (validation.get("contract_revision") != contract["revision"]
                or validation.get("contract_hash") != contract["hash"]
                or (state.get("current_task") and validation.get("task_id") != state["current_task"]["id"])
                or (require_human_reviews and goals.missing_human_reviews(state))
                or any(f.get("blocking", True) for f in validation.get("findings", []))
                or any(f.get("blocking", True) for f in decision.get("findings", []))):
            return False
        if "end_to_end_flow" in contract["body"]:
            flow = validation.get("end_to_end_result", {})
            # A flow that ends in the person's approval completes with that bound approval (#195).
            awaits_review = human_only_gap and flow.get("status") == "NOT_VERIFIED"
            if ((flow.get("status") != "PASS" and not awaits_review)
                    or not flow.get("evidence_refs") or not flow.get("summary", "").strip()):
                return False
    sol = state.get("validation", {})
    if decision.get("status") not in ("COMPLETE", "TASK_COMPLETE"):
        return False
    if state.get("findings_ledger"):
        try:
            from . import autocode_findings as findings_ledger
        except ImportError:
            import autocode_findings as findings_ledger
        if findings_ledger.blocking_entries(state):
            return False
    criteria = state.get("acceptance_criteria", [])
    if not criteria or criteria_definition(decision.get("acceptance_criteria", [])) != criteria_definition(criteria):
        return False
    if any(c["status"] != "verified" or not c["evidence"].strip() for c in decision["acceptance_criteria"]):
        return False
    if (sol.get("verdict") != "PASS" and not human_only_gap) or sol.get("criteria_revision") != state.get("criteria_revision"):
        return False
    if sol.get("source_revision") != current["revision"] or not sol.get("checks"):
        return False
    outcomes = sol.get("criterion_results", [])
    if sorted(r["id"] for r in outcomes) != sorted(c["id"] for c in criteria):
        return False
    if not human_only_gap and any(r["status"] != "PASS" or not r["evidence_refs"] for r in outcomes):
        return False
    if any(c["exit_code"] != 0 for c in sol["checks"]) or (sol.get("unverified_criteria") and not human_only_gap):
        return False
    if any(f["severity"] in ("critical", "high") for f in sol.get("findings", [])):
        return False
    pins = sol.get("evidence_hashes", {})
    if not pins:
        return False
    try:
        from . import autocode_regression as regression
    except ImportError:
        import autocode_regression as regression
    if not regression.complete(state, current["revision"]):
        return False  # a bug fix needs the runner's passing regression proof for this exact source
    return all(Path(p).is_file() and file_hash(p) == h for p, h in pins.items())


def artifact_review_request(state, decision, current):
    """Offer human acceptance when a conservative owner asks to validate only that gap.

    The probe tests the existing completion gate; it never changes the saved report,
    validation or human events. A human criterion's automated evidence is sufficient
    to *present* review, but only a user's bound receipt can satisfy acceptance.
    """
    try:
        from . import autocode_goals as goals
    except ImportError:
        import autocode_goals as goals
    if decision.get("status") == "CONTINUE":
        if (decision.get("next_task") or {}).get("kind") != "validate":
            return None  # never suppress an implementation or correction task
    elif decision.get("status") not in ("COMPLETE", "TASK_COMPLETE"):
        return None
    missing = goals.missing_human_reviews(state)
    if not missing:
        return None
    validation = state.get("validation") or {}
    replay = validation.get("check_replay") or {}
    if replay.get("verdict") != "PASS" or replay.get("source_revision") != current["revision"]:
        return None
    human = {row["id"] for row in state["goal_contract"]["body"]["acceptance_criteria"] if row["human_review"]}
    # The owner may correctly leave human acceptance unverified. Keep that report
    # unchanged, while checking every technical guard through the normal gate.
    probe = {**decision, "status": "TASK_COMPLETE", "acceptance_criteria": [
        {**row, "status": "verified"} if row["id"] in human else row
        for row in decision.get("acceptance_criteria", [])]}
    if not completion_ready(state, probe, current, require_human_reviews=False):
        # Missing human-row prose must be repaired, not turned into another validation
        # task. Test eligibility with the real Validator references in an ephemeral
        # probe; never accept this substitute as the owner's report or human approval.
        refs = {row["id"]: row.get("evidence_refs", []) for row in validation.get("criterion_results", [])}
        empty = {row["id"] for row in probe["acceptance_criteria"]
                 if row["id"] in human and not row.get("evidence", "").strip()}
        repair_probe = {**probe, "acceptance_criteria": [
            {**row, "evidence": ", ".join(refs.get(row["id"], []))} if row["id"] in empty else row
            for row in probe["acceptance_criteria"]]}
        if empty and completion_ready(state, repair_probe, current, require_human_reviews=False):
            raise ValueError("Human-review decision lacks current validation evidence for " + ", ".join(sorted(empty))
                             + ": cite the existing Validator evidence while leaving human acceptance unverified; "
                               "do not invent a user approval or request another validation solely for human acceptance")
        return None
    return {"kind": "human_review", "criteria": missing,
            "decision_needed": "Review the current artifact and explicitly approve the listed criteria",
            "impact": "Completion requires the declared human acceptance of this validated artifact",
            "options": [], "discovered": "Independent evidence passed; human review remains", "proposed_delta": ""}
