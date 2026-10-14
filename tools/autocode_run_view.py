"""The read side of the task-run interface: a stable summary of one run.

Programs that drive AutoCode (the scenario harness, multi-task coordination)
read this instead of the ~140 keys of state.json, which stay private to the
runner. `autocode --run-dir RUN --status` prints it under "view".

This is a contract (see docs/task-run.md). Add fields; never rename or remove
one, and bump SCHEMA if a meaning changes. It is a read-only projection of saved
state and explicitly named usage logs, never sibling/private state discovery.
It imports nothing from the runner.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_interaction_timing as interaction_timing
except ImportError:
    import autocode_interaction_timing as interaction_timing

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_accepted_source as accepted_source
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_code_checkpoints as code_checkpoints
    from . import autocode_component_plan as component_plan
    from . import autocode_containment_policy as containment_policy
    from . import autocode_contract_identity as contract_identity
    from . import autocode_conversation_projection as conversation_projection
    from . import autocode_cost as cost
    from . import autocode_design_coverage as design_coverage
    from . import autocode_efficiency, autocode_usage
    from . import autocode_finding_rescope as finding_rescope
    from . import autocode_liveness as liveness_policy
    from . import autocode_member_stop as member_stop
    from . import autocode_operational_information as operational_information
    from . import autocode_output_policy as output_policy
    from . import autocode_pause_category as pause_category
    from . import autocode_pause_command as pause_command
    from . import autocode_progressive_plan as progressive_rules
    from . import autocode_quota_route as quota_route
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_recovery_view as recovery_view
    from . import autocode_report_retry as report_retry
    from . import autocode_request_usage as request_usage
    from . import autocode_resolver_recommendation as recommendation
    from . import autocode_risk_disclosure as risk_disclosure
    from . import autocode_route_ladder as route_ladder
    from . import autocode_stop_explanations as stop_explanations
    from . import autocode_task_input as task_input
    from . import autocode_verification_view as verification_view
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_accepted_source as accepted_source
    import autocode_classification_recovery as classification_recovery
    import autocode_code_checkpoints as code_checkpoints
    import autocode_component_plan as component_plan
    import autocode_containment_policy as containment_policy
    import autocode_contract_identity as contract_identity
    import autocode_conversation_projection as conversation_projection
    import autocode_cost as cost
    import autocode_design_coverage as design_coverage
    import autocode_efficiency
    import autocode_finding_rescope as finding_rescope
    import autocode_liveness as liveness_policy
    import autocode_member_stop as member_stop
    import autocode_operational_information as operational_information
    import autocode_output_policy as output_policy
    import autocode_pause_category as pause_category
    import autocode_pause_command as pause_command
    import autocode_progressive_plan as progressive_rules
    import autocode_quota_route as quota_route
    import autocode_recovery_limits as recovery_limits
    import autocode_recovery_view as recovery_view
    import autocode_report_retry as report_retry
    import autocode_request_usage as request_usage
    import autocode_resolver_recommendation as recommendation
    import autocode_risk_disclosure as risk_disclosure
    import autocode_route_ladder as route_ladder
    import autocode_stop_explanations as stop_explanations
    import autocode_task_input as task_input
    import autocode_usage
    import autocode_verification_view as verification_view

SCHEMA = 2
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Statuses where relaunching the run, with no user input, continues the work.
CONTINUE = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL")
QUESTION_FIELDS = ("id", "question", "why", "options", "proposed_default")


def view(
    state: dict,
    *,
    completion_current=None,
    visual_acceptance=None,
    stale_report_repair=False,
    liveness=None,
    runner_check_liveness=None,
    verification_obligation=None,
    conversation_journal=None,
    controller=None,
    run_dir=None,
    workspace=None,
) -> dict:
    """Caller supplies fresh completion, evidence, repair and supervision inspections."""
    try:
        from . import autocode_replay_view as replay_view
    except ImportError:
        import autocode_replay_view as replay_view
    settings = state.get("settings")
    authority = settings.get("replay_recording") if isinstance(settings, dict) else None
    replay_progress = replay_view.progress(state)
    invalid_replay = isinstance(settings, dict) and "replay_recording" in settings and replay_progress is None
    simulated = isinstance(authority, dict) and authority.get("checks") == "recorded"
    if simulated or invalid_replay:
        completion_current = False
    status = state.get("status", "")
    next_action = action(state, run_dir=run_dir, workspace=workspace, stale_report_repair=stale_report_repair)
    need = next_action["needs"]
    task = state.get("current_task") or {}
    active = state.get("active_stage")
    supervision = active.get("supervision") if isinstance(active, dict) else None
    check = state.get("active_runner_check")
    check_supervision = check.get("supervision") if isinstance(check, dict) else None
    result = {
        "replay_source": None if invalid_replay else replay_view.project(state, run_dir=run_dir),
        "replay_progress": replay_progress,
        "replay_result": deepcopy(state.get("replay_result")),
        "runner_check": {
            **{
                key: deepcopy(check.get(key))
                for key in ("stage", "summary", "started_at", "updated_at", "command", "output", "supervision")
            },
            "liveness": liveness_policy.classify(check_supervision, runner_check_liveness),
        }
        if check
        else None,
        "dependency": state.get("dependency_wait"),
        "verification_obligation": deepcopy(verification_obligation),
        "schema": SCHEMA,
        "controller": deepcopy(controller),
        # Saved launch identity, never inferred from current installed resources.
        "prompts_hash": state.get("prompts_hash"),
        "task": state.get("task"),
        "task_sha256": task_input.status_digest(state.get("task")),
        # Controller that last saved the checkpoint; legacy and unversioned code stay unknown.
        "runner": deepcopy(state.get("runner")) if isinstance(state.get("runner"), dict) else None,
        "interaction_timing": interaction_timing.project(state),
        "pause_category": next_action["pause_category"],
        "pause_category_label": next_action["pause_category_label"],
        "status": status,
        "next_command": next_action["next_command"],
        "liveness": liveness_policy.classify(supervision, liveness),
        "done": status in COMPLETE and not simulated and not invalid_replay,
        "needs": need,
        "recovery": recovery_view.project(state, need),
        "verification": verification_view.project(state),
        "code_checkpoints": code_checkpoints.project(state),
        "phase": state.get("phase"),
        "next_stage": state.get("next_stage"),
        "iteration": state.get("iteration"),
        "stop_reason": state.get("stop_reason"),
        # Plain-English explanation of this stop (#715), added never renamed.
        "explanation": stop_explanations.explain(
            status,
            stop_reason=state.get("stop_reason") or "",
            needs=need,
        ),
        "current_task": {key: task.get(key) for key in ("id", "objective", "milestone_id")} if task else None,
        # The kind of job recognized from the request (autocode_workflows.WORKFLOWS);
        # None until the first stage has run, and for runs that predate recognition.
        "workflow": (state.get("workflow") or {}).get("kind"),
        # "model" when the recognizer decided it, "user" when --workflow named it; the reason it gave.
        "workflow_source": (state.get("workflow") or {}).get("source"),
        "workflow_reason": (state.get("workflow") or {}).get("reason"),
        # Which request of the conversation the run is on: 1, then one more per --follow-up
        # (autocode_follow_up).
        "turn": len(state.get("turns") or []) + 1,
        "evidence": evidence(state),
        # Canonical publication metadata is historical until the status interface
        # authenticates its files and current completion; no state mutation here.
        "evidence_report": evidence_report(state),
        "acceptance_tests": acceptance_tests.view(state),  # spec-first Tester files and results (#1200)
        # Tokens and cost so far, by role (autocode_usage.summary): reported, estimated and unknown kept apart.
        "usage": autocode_usage.summary(state),
        # Spend against the person's money cap at the dated price table (autocode_cost, #721).
        "cost": cost.view(state),
        # How the run's route escalations resolved (autocode_route_ladder): a
        # validation PASS passed the retried work, a retry-limit pause ended it,
        # and an open one is still unresolved.
        "escalation_outcomes": route_ladder.outcome_summary(state),
        "request_context": request_usage.view(state),
        "output_transport": output_policy.view(state),
        # Runner-owned assignment provenance, never a model diagnosis or completion proof.
        "direct_rework_assignments": deepcopy(state.get("direct_rework_assignments", [])),
        # The model and engine each role's next launch uses, and every model a person named for a
        # role after its quota ran out (autocode_quota_route): role, from, to, stage, at, via.
        "routes": quota_route.routes(state),
        "route_assignments": quota_route.assignments(state),
        # AutoResolver's one re-evaluation of corrective information sent to an operational request
        # (autocode_operational_information, #486): status pending, held, admitted, stale or superseded
        # (the run left the pause unevaluated); the decision, its reason and, when held, the exact next
        # action. None when no response is current.
        "information_review": operational_information.projection(state),
        # Built-in OpenCode non-planning stages: "contained" (kernel tool boundary) or
        # "uncontained_user_accepted" (--allow-uncontained-tools); None when no stage uses it (#413).
        "tool_containment": containment_policy.mode(state.get("settings")),
        # Status inspection supplies a fresh offer; saved state alone grants no adoption.
        "job_report_recovery": None,
    }
    if simulated or invalid_replay:
        result["completion_current"] = False
    result["efficiency"] = autocode_efficiency.summary(
        state,
        accounting=result["usage"]["accounting"],
        completion_current=completion_current,
        visual_acceptance=visual_acceptance,
    )
    contract = state.get("goal_contract") or {}
    if isinstance(contract, dict) and isinstance(contract.get("body"), dict):
        try:
            if (
                type(contract.get("revision")) is int
                and contract["revision"] > 0
                and state.get("displayed_goal") == contract_identity.token(contract)
                and contract_identity.sealed(contract)
            ):
                # Approval consumers need actual fields, not model-authored display text.
                result["displayed_plan"] = {
                    "revision": contract["revision"],
                    "hash": contract["hash"],
                    "token": state["displayed_goal"],
                    **{
                        key: deepcopy(contract["body"].get(key))
                        for key in ("acceptance_criteria", "constraints", "permission_boundaries")
                    },
                }
        except (KeyError, TypeError, ValueError):
            pass  # Missing, stale or unsealed plans cannot supply approval authority.
        approved = approved_contract(state)
        if approved is not None:
            result["approved_contract"] = approved
    design = design_coverage.projection(state)
    if design is not None:
        result["design"] = design
    if state.get("task_preflight"):
        result["task_preflight"] = {
            key: deepcopy(state["task_preflight"].get(key))
            for key in (
                "kind",
                "execution_context",
                "execution_identity",
                "phase",
                "status",
                "checked_at",
                "manifest_hash",
                "binding",
                "errors",
                "receipt",
                "receipt_sha256",
                "checks",
                "design",
            )
        }
    result["conversation"] = conversation_projection.project(
        state,
        conversation_journal,
        displayed_plan=result.get("displayed_plan"),
        needs=need,
        public_status={
            key: result.get(key)
            for key in ("status", "done", "phase", "stop_reason", "next_command", "recovery", "liveness")
        },
        review_findings=result["evidence"].get("findings"),
        review_scope_moves=result["evidence"].get("finding_scope_moves"),
    )
    if isinstance(result["conversation"], dict) and result["conversation"].get("availability") == "available":
        result["conversation"]["run_dir"] = str(run_dir) if run_dir is not None else None
        result["conversation"]["workspace"] = str(workspace) if workspace is not None else None
    projection = progressive(state)
    if projection is not None:
        result["progressive"] = projection
    return result


def approved_contract(state: dict) -> dict | None:
    """The plan in force: the approved contract, as long as that approval still holds.

    Approved by the user, or, for a bug fix's small correction, under the workflow policy
    the user agreed to (autocode_workflows.POLICY_ORIGINS); the view does not say which.
    None while there is no approval, after a new draft revision replaces the approved one,
    and while the contract itself records an open blocking question (approval refuses one);
    a question the run asks after approval does not remove it (the runner's own approval
    check, contract_identity.approved). Until a --follow-up drafts its own plan, and for a
    follow-up answered by a review, design or discussion, it is the earlier request's plan.
    A program coordinating several runs reads the child's approved criteria here, never
    state.json.
    """
    contract = state.get("goal_contract") or {}
    try:
        if not contract_identity.approved(state):
            return None
        return {
            "revision": contract["revision"],
            "hash": contract["hash"],
            "token": contract_identity.token(contract),
            "task_id": contract.get("task_id"),
            "approved_at": (contract.get("approval_event") or {}).get("at"),
            "body": deepcopy(contract["body"]),
        }
    except (KeyError, TypeError, AttributeError, ValueError):
        return None


def progressive(state: dict) -> dict | None:
    """Read-only, additive projection of the version-one progressive ledger.

    Plan/candidate envelopes contain {proposal, plan_hash}; active contains
    {definition, plan_hash, artifact, review}. The history list contains
    demonstrated checkpoints, never current product proof. budget, when
    present, is the budget policy ledger and is copied without invented limits.
    Status describes saved activation, not permission to dispatch: this reader
    does not authenticate artifact files, source snapshots or replay receipts.
    Until a current-proof reader is defined by the evidence owner, no saved
    boolean, prior PASS, empty future map or COMPLETE status establishes proof.
    """
    record = state.get("progressive")
    record = record if isinstance(record, dict) else {}
    contract = state.get("goal_contract") or {}
    body = contract.get("body") or {}
    disclosure = [
        line
        for field in ("constraints", "technical_approach")
        for line in body.get(field) or []
        if isinstance(line, str)
        and line.startswith(
            (
                progressive_rules.DISCLOSURE_DELEGATION,
                progressive_rules.DISCLOSURE_SLICE,
                progressive_rules.DISCLOSURE_OUTSTANDING,
            )
        )
    ]
    if not record and not disclosure:
        return None

    candidate = record.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    plan = record.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    proposal = candidate.get("proposal") or plan.get("proposal") or {}
    proposal = proposal if isinstance(proposal, dict) else {}
    active = record.get("active")
    active = active if isinstance(active, dict) else {}
    definition = active.get("definition")
    definition = definition if isinstance(definition, dict) else None
    approved = False
    reason = None
    try:
        progressive_rules.require_delegation(
            record,
            contract_token=contract_identity.token(contract),
            contract_body=body,
            contract_approved=contract_identity.approved(state),
            contract_sealed=contract_identity.sealed(contract),
        )
        approved = True
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        reason = str(error)
    if record.get("suspended") or (record.get("delegation") and not approved):
        status = "suspended"
    elif not approved:
        status = "proposed" if candidate else "unapproved"
    elif (
        definition
        and definition.get("tentative") is False
        and active.get("plan_hash")
        and active.get("plan_hash") == plan.get("plan_hash")
        and definition == ((plan.get("proposal") or {}).get("slices") or [None])[0]
        and active.get("artifact")
        and active.get("review")
    ):
        status = "active"
    else:
        status = "needs_activation"

    slices = [row for row in proposal.get("slices") or [] if isinstance(row, dict)]
    criteria = [
        {"id": row.get("id"), "criterion": row.get("criterion") or row.get("text")}
        for row in body.get("acceptance_criteria") or []
        if isinstance(row, dict)
    ]
    result = {
        "status": status,
        "delegation_approved": approved,
        "authority_issue": reason,
        "disclosure": disclosure,
        "plan_hash": plan.get("plan_hash") or candidate.get("plan_hash"),
        "demonstrated_slices": record.get("history") or [],
        "checkpoints": record.get("history") or [],
        "retirements": record.get("retirements") or [],
        "active_slice": definition,
        "tentative_next_work": [row for row in slices if row.get("tentative") is True],
        "required_checks": record.get("required_checks") or [],
        # Every product criterion remains unproven here, including those mapped
        # to a green slice. The narrower map deferrals remain separately visible.
        "outstanding_product_criteria": criteria,
        "outstanding_criteria": record.get("outstanding_criteria", proposal.get("outstanding_criteria", [])),
        "current_whole_product_proof": {
            "verified": False,
            "status": "not_established",
            "reason": "Historical slice/checkpoint evidence is not authenticated current whole-product proof.",
        },
    }
    if "budget" in record:
        result["allowance_usage"] = record["budget"]
    return deepcopy(result)


def evidence_report(state):
    try:
        from .autocode_evidence_export import metadata
    except ImportError:
        from autocode_evidence_export import metadata
    return metadata(state)


def evidence(state: dict) -> dict:
    """What the run agreed to deliver and what supports it, for reports outside the runner.

    outcome           the approved contract's intended outcome, or None
    base_commit       the revision the run started from
    acceptance        one row per criterion: its latest recorded outcome and evidence;
                      validator_status is the latest saved validation's result for that
                      criterion (FAIL, PASS or NOT_VERIFIED), None when that validation
                      has no row — a failed criterion must be distinguishable from an
                      unchecked one
    validator_source_revision  the source revision that validation checked, or None.
                      The view does not read the workspace: after rework, validator_status
                      still reports that validation until a newer one replaces it. Compare
                      this revision to the workspace before treating the status as current.
    findings          the findings ledger: id, status, severity, finding, source, scope (sol: Tester,
                      astra: Completion Reviewer, runner: a failed runner lifecycle observation, #451)
    finding_scope_moves  present once an approved revision moved criteria that open findings cite:
                      one row per finding it re-attributed (autocode_finding_rescope): finding, from,
                      to (every resulting row's id, milestone_id, criteria), contract_token, at
    regression_proof  for bug fixes, the runner's own fail-before/pass-after proof, else None;
                      case_tests maps each English test case to the tests that prove it; unstable
                      lists the proving tests whose repeated runs disagreed, else None
    test_cases        a reproduced bug's regression tests in plain English (id, given, when, then), else []
    check_replay      the current validation's checks as the runner itself re-ran them in a clean copy
                      (autocode_check_replay): verdict, source_revision and one row per command, else None
    proof_stability   the proving tests' repeats (autocode_proof_stability): repeats, unstable (test, side,
                      kind, runs), unrepeated, next_step when one is unstable, note; else None
    diff_coverage     the proving tests' line coverage of changed product hunks (autocode_diff_coverage):
                      collected, tool, note, files, uncovered, unmeasured; else None. Report only.
    mutation_check    small mutations of the changed product lines run against the proving tests once the
                      proof's verdict was final and a pass (autocode_mutation_check): collected, note,
                      limit_per_file, budget_seconds, files (path, notes, mutants: line, operator, before,
                      after, outcome killed, survived, timeout or not_run, survived_tests) and, when
                      collected, survivors: keyed by the same test-case ids as regression_proof.case_tests
                      (not acceptance-criterion ids), each case's proving tests that passed with a product
                      line changed (test, path, line, operator, before, after, text), {} when none did. A
                      proof that did not pass, even one refused before any test ran, has the note "not run:
                      the proof did not pass"; None when a passing proof had no due test case, or there is
                      no proof. Report only: a survivor never changes the verdict or completion
    unverified_risk_claims  durability or concurrency promises in the person's own words that no runner
                      protocol proves (autocode_risk_disclosure): kind, source_id, quote, reason; [] when none.
                      Disclosure only: a PASS never verifies them
    """
    contract = (state.get("goal_contract") or {}).get("body") or {}
    criteria = contract.get("acceptance_criteria") or state.get("acceptance_criteria") or []
    decision = state.get("last_decision")
    if not isinstance(decision, dict):
        decision = {}
    report = decision.get("report")
    if not isinstance(report, dict):
        report = decision
    outcomes = {row.get("id"): row for row in report.get("acceptance_criteria") or [] if isinstance(row, dict)}
    reviewed = state.get("human_reviews")
    if not isinstance(reviewed, dict):
        reviewed = {}
    validation = state.get("validation")
    if not isinstance(validation, dict):
        validation = {}
    validated = {
        row.get("id"): row.get("status") for row in validation.get("criterion_results") or [] if isinstance(row, dict)
    }
    acceptance = []
    for item in criteria:
        item = item if isinstance(item, dict) else {"criterion": str(item)}
        outcome = outcomes.get(item.get("id")) or {}
        acceptance.append(
            {
                "id": item.get("id"),
                "criterion": item.get("criterion") or item.get("text"),
                "status": outcome.get("status"),
                "evidence": outcome.get("evidence"),
                "validator_status": validated.get(item.get("id")),
                "human_reviewed": item.get("id") in reviewed,
            }
        )
    proof = state.get("regression_proof")
    replay = validation.get("check_replay")
    investigation = state.get("investigation")
    if not isinstance(investigation, dict):
        investigation = {}
    moves = finding_rescope.history(state.get("findings_ledger"))
    return {
        "created_at": state.get("created_at"),
        **({"interaction_timing": interaction_timing.project(state)} if state.get("interaction_timing") else {}),
        "outcome": contract.get("intended_outcome"),
        "workflow_result": workflow_result(state),
        "base_commit": state.get("base_commit"),
        "runner": deepcopy(state.get("runner")) if isinstance(state.get("runner"), dict) else None,
        **(
            {"protected_tests": deepcopy(state["settings"]["protected_tests"])}
            if state.get("settings", {}).get("protected_tests")
            else {}
        ),
        "acceptance": acceptance,
        "validator_source_revision": validation.get("source_revision"),
        "findings": [
            {
                **{key: row.get(key) for key in ("id", "status", "severity", "finding", "source")},
                **({"scope": deepcopy(row["scope"])} if "scope" in row else {}),
            }
            for row in state.get("findings_ledger") or []
            if isinstance(row, dict)
        ],
        **({"finding_scope_moves": moves} if moves else {}),
        "regression_proof": {
            key: proof.get(key)
            for key in (
                "verdict",
                "fail_to_pass",
                "failures",
                "unverified",
                "commands",
                "source_revision",
                "case_tests",
                "unstable",
            )
        }
        if isinstance(proof, dict)
        else None,
        "test_cases": [
            {key: case.get(key) for key in ("id", "given", "when", "then")}
            for case in investigation.get("test_cases") or []
            if isinstance(case, dict)
        ]
        if investigation.get("outcome") == "reproduced"
        else [],
        "check_replay": {
            "protected_tests": deepcopy(replay.get("protected_tests")),
            "brief_acceptance": deepcopy(replay.get("brief_acceptance")),
            "risk_acceptance": deepcopy(replay.get("risk_acceptance")),
            "verdict": replay.get("verdict"),
            "source_revision": replay.get("source_revision"),
            "scheduling": deepcopy(replay.get("scheduling")),
            "checks": [
                {
                    key: deepcopy(row.get(key))
                    for key in (
                        "command",
                        "evidence_ref",
                        "exit_code",
                        "timed_out",
                        "output",
                        "output_sha256",
                        "duration_seconds",
                        "error",
                        "tail",
                        "purpose",
                        "scheduling",
                        "results",
                    )
                }
                for row in replay.get("checks") or []
                if isinstance(row, dict)
            ],
        }
        if isinstance(replay, dict)
        else None,
        "proof_stability": deepcopy(proof.get("proof_stability")) if isinstance(proof, dict) else None,
        "diff_coverage": deepcopy(proof.get("diff_coverage")) if isinstance(proof, dict) else None,
        "mutation_check": deepcopy(proof.get("mutation_check")) if isinstance(proof, dict) else None,
        "unverified_risk_claims": risk_disclosure.view(state),
    }


def workflow_result(state):
    """Bounded job outcome facts; job completion is separate from code checks."""
    kind = (state.get("workflow") or {}).get("kind")
    if kind == "review" and state.get("review"):
        row = state["review"]
        return {
            "outcome": row.get("verdict"),
            "artifact": row.get("report_path"),
            "artifact_sha256": row.get("report_sha256"),
            "summary": f"{row.get('blocking', 0)} blocking, {row.get('advisory', 0)} advisory findings",
        }
    if kind == "design" and (state.get("design_review") or {}).get("mode") == "review":
        row = state["design_review"]
        return {
            "outcome": row.get("verdict"),
            "artifact": row.get("report_path"),
            "artifact_sha256": row.get("report_sha256"),
            "summary": f"Design revision {row.get('revision')}; {row.get('blocking', 0)} blocking concerns",
        }
    if kind == "discuss" and state.get("answer"):
        row = state["answer"]
        return {
            "outcome": "answered",
            "artifact": row.get("note_path") or row.get("output"),
            "artifact_sha256": None,
            "summary": "Analyst answer recorded; code correctness is not established by an answer",
        }
    return None


def _route(question: dict) -> dict:
    """``needs.route``: a role's model question after a quota stop or a content-filter refusal (#184, #463)."""
    route = {
        "question_id": question["id"],
        "role": question["route_role"],
        "job": question.get("job"),
        "current_model": question.get("current_model"),
        "engine": question.get("engine"),
        "cause": question.get("cause", "quota"),
        "stopped_model": question.get("stopped_model"),
    }
    if "candidates" in question:
        route["candidates"] = list(question["candidates"])
    return route


def action(state: dict, *, run_dir=None, workspace=None, stale_report_repair=False) -> dict:
    """What this run asks of a person: the need, its category, and the command that answers it.

    ``view`` composes those three from here, so a caller that only needs the next
    action never re-derives it; ``autocode runs`` pays for this, not for the view's
    evidence and usage accounting. Leaving ``stale_report_repair`` false keeps an
    uncertain attempt's abandon step in the need instead of promising a resume that
    would discard it.
    """
    status = state.get("status", "")
    need = needs(state, stale_report_repair=stale_report_repair)
    category = pause_category.category(status, need)
    terminal = any(row.get("kind") == "stop" for row in state.get("applied_interventions", []) if isinstance(row, dict))
    return {
        "needs": need,
        "pause_category": category,
        "pause_category_label": pause_category.LABELS.get(category) if category else None,
        "next_command": None
        if not category
        else pause_command.command(
            need,
            run_dir=run_dir or state.get("run_dir"),
            workspace=workspace or state.get("workspace"),
            # This status also names novelty/source holds; only its bound hold needs a larger limit.
            resume_flags=None
            if status == "PAUSED_NO_PROGRESS" and not recovery_limits.no_progress_bound_holds(state)
            else operational_information.resume_flags(state, (need or {}).get("pause_origin") or status),
            terminal=terminal,
        ),
    }


def needs(state: dict, *, stale_report_repair=False) -> dict | None:
    """What must happen next for the run to progress, or None when it is complete.

    kind          what it asks for                  answered with
    review        human acceptance of criteria      --approve-review CRITERION --review-token - (reads AUTOCODE_REVIEW_TOKEN)
    answer        answers to pending questions      --answer QUESTION_ID=TEXT (plus --resolver-token
                                                     when the view carries one); with `route` set, a
                                                     role's quota ran out: --answer route-ROLE=MODEL;
                                                     with `resolver_scope` blocker or
                                                     operational_exhaustion and no `route`, AutoResolver
                                                     is asking a person and refuses --answer: `action`
                                                     is the response (--resolver-request ID
                                                     --resolver-token - (reads AUTOCODE_RESOLVER_TOKEN) --resolver-response
                                                     provide_information --resolver-message TEXT); a
                                                     decision that changes the approved contract then
                                                     takes --edit-goal body.json and --approve-goal; a
                                                     held repeated incident (pause_origin
                                                     PAUSED_NO_PROGRESS) also names `retry_action`,
                                                     --resume-paused --retry-failed-stage
    approve_plan  approval of the displayed plan    --approve-goal - (reads AUTOCODE_APPROVE_GOAL_TOKEN)
    planning_budget  more planning review calls     --feedback TEXT or --planning-review-call-limit N;
                                                     after AutoResolver held corrective information,
                                                     `action` is the control it requires
    resume        a person to inspect a pause       --resume-paused, after resolving stop_reason;
                                                     `new_run_required` instead preserves this run
                                                     and requires a fresh run with valid caller inputs;
                                                     `edit_required` instead prescribes --edit-goal FILE
                                                     and `feedback_action` when offered, then fresh approval;
                                                     when `abandon_stage` is set, --abandon-stage
                                                     ATTEMPT first (the attempt is uncertain);
                                                     `action`, when set, is the one command that
                                                     continues (a source-only PAUSED_STALE_HANDOFF:
                                                     --resume-paused --accept-source-edit; a PAUSED_NO_PROGRESS its unchanged-
                                                     batch limit caused: --resume-paused --no-progress-
                                                     limit N, N above `no_progress_batches`, the
                                                     retained count, or 0; after AutoResolver held
                                                     corrective information, the control it requires,
                                                     view.information_review; at a parallel Builder
                                                     member's quota or content-filter stop answered
                                                     without a model, --resume-paused --retry-builder M)
    retry_job     a person to inspect a stopped job  --resume-paused --retry-failed-stage --job-retry-token
                                                     TOKEN; with `route` set (quota or a content-filter
                                                     refusal), --answer route-ROLE=MODEL --job-retry-token
                                                     TOKEN first names another model and issues a new token
                                                     (after a refusal, `action` is that answer until a
                                                     model is named)
    recover_source missing original identity       inspect archive and source before a new run
    continue      nothing; relaunch to proceed      the same command with --run-dir
    """
    status = state.get("status", "")
    if status in COMPLETE:
        return None
    if status == "PAUSED_COMPONENT_PLAN":
        return {
            "kind": "resume",
            "reason": state.get("stop_reason"),
            **component_plan.recovery(
                state.get("task", ""), (state.get("settings") or {}).get("regression", {}).get("test_root")
            ),
        }
    classification_need = classification_recovery.offer(state)
    if classification_need:
        return classification_need
    if (
        status == "PAUSED_BUILDER_CLASSIFICATION"
        and not (state.get("builder_failure_hold") or {}).get("classification_retry")
        and "(The Investigator could not finish:" in (state.get("stop_reason") or "")
    ):
        return {
            "kind": "resume",
            "reason": state.get("stop_reason"),
            "edit_required": True,
            "action": "--edit-goal FILE",
            "feedback_action": None,
            "recovery_hint": "This older checkpoint did not retain its failed classification request. Review and change the approved plan, or start a new run; its discarded Resolver proposal cannot be reconstructed by resume.",
        }
    failure = state.get("job_failure") or {}
    if quota_route.job_pause_current(state) and failure:
        known_source = bool(failure.get("source_identity"))
        need = {
            "kind": "retry_job" if known_source else "recover_source",
            "reason": failure["reason"],
            "stage": failure["stage"],
            "attempt_id": failure["attempt_id"],
            "job_retry_token": failure["job_retry_token"],
            "archive": failure["archive"],
            "source_identity": failure["source_identity"],
            "write_diagnosis": deepcopy(failure["write_diagnosis"]),
            "unrestored": list(failure["unrestored"]),
            "action": "--resume-paused --retry-failed-stage --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
            if known_source
            else None,
            "recovery_hint": (
                "Exact retry rechecks the saved original source identity, including file modes and Git HEAD. "
                "Restore that exact source before retrying; the archived restoration diagnosis is retained."
                if known_source
                else "Exact retry is unavailable because this attempt has no saved original source identity. "
                "Inspect the archived attempt and current changes before starting a new run. "
                "The current checkout cannot establish the missing original identity."
            ),
        }
        # A job stopped on quota or by its provider's content filter (#463) keeps its model question on
        # the failure: --answer route-ROLE=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN) names another model and issues a
        # new token for the exact retry. Same shape as the answer need's ``route``.
        route = quota_route.job_question(state)
        if known_source and isinstance(route, dict):
            need["route"] = _route(route)
            # Until a person names another model (job_failure.route_assignment), the exact retry of a
            # refused job would replay the refused model: the next step is the answer. job_retry_token
            # stays, since the CLI still accepts it. A quota stop keeps the retry (the quota resets).
            if need["route"]["cause"] in ("content_filter", "output_limit") and not isinstance(
                failure.get("route_assignment"), dict
            ):
                need["action"] = (
                    f"--answer {need['route']['question_id']}=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
                )
        return need
    if status == "WAITING_FOR_DEPENDENCY":
        return {
            "kind": "dependency",
            "reason": state.get("stop_reason"),
            "producer_run": (state.get("dependency_wait") or {}).get("producer_run"),
        }
    questions = state.get("pending_questions") or []
    reviews = [question for question in questions if question.get("review_criteria")]
    if reviews:
        return {
            "kind": "review",
            "criteria": list(reviews[0]["review_criteria"]),
            "token": reviews[0].get("review_token"),
            "question": reviews[0].get("question"),
        }
    request = state.get("user_request") or {}
    if request.get("kind") == "human_review":
        # A completion-time human review (autopilot.apply_review_result, after validation
        # passes with unreviewed criteria) is signaled directly on user_request with no
        # pending_questions entry at all; the token is the one already displayed for this
        # validated artifact.
        return {
            "kind": "review",
            "criteria": list(request.get("criteria") or []),
            "token": state.get("displayed_review"),
            "question": request.get("decision_needed"),
        }
    if questions:
        answer = {
            "kind": "answer",
            "request_kind": request.get("kind"),
            "questions": [{field: question.get(field) for field in QUESTION_FIELDS} for question in questions],
        }
        if request.get("kind") == "permission" and len(questions) == 1 and "operational_options" in questions[0]:
            answer["operational_options"] = deepcopy(questions[0]["operational_options"])
        # Answers to a published AutoResolver request must carry its token
        # (autocode.py: require --resolver-token). Surface the current one so
        # drivers can serve this gate from the view alone; a stale or consumed
        # request carries no token and the CLI re-verifies freshness anyway.
        # ``resolver_scope`` says what kind of request it is: "operational_exhaustion"
        # and "blocker" mean AutoResolver could not continue safely and is asking a
        # person, not asking a requirements question a default could answer.
        published = state.get("resolver_human_request")
        entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get((published or {}).get("request_id"))
        if isinstance(published, dict) and isinstance(entry, dict) and entry.get("status") == "pending":
            answer["resolver_request_id"] = published.get("request_id")
            answer["resolver_token"] = published.get("request_token")
            answer["resolver_scope"] = published.get("scope")
            if answer["resolver_scope"] in ("blocker", "operational_exhaustion"):
                origin = ((entry.get("identity") or {}).get("proposal") or {}).get("origin") or {}
                answer["pause_origin"] = origin.get("pause_status")
                revision = published.get("request", {}).get("recommended_revision")
                if revision is not None:
                    answer["recommended_revision"] = deepcopy(revision)
                    answer["recommended_revision_response"] = recommendation.ACCEPTANCE
        # A quota stop or a content-filter refusal asks for a model (#184): answer --answer
        # route-ROLE=MODEL. It has no default and is a person's decision, never a delegable
        # requirements answer. ``cause`` is "quota" or "content_filter"; ``stopped_model`` is the
        # model that stopped; ``candidates`` (refusal only) the configured models that would pass.
        route = next(
            (
                q
                for q in questions
                if q.get("category") == quota_route.CATEGORY and quota_route.asked_route(questions, q.get("id"))
            ),
            None,
        )
        if route:
            answer["route"] = _route(route)
        elif answer.get("resolver_scope") in ("blocker", "operational_exhaustion"):
            # Not a requirements question: the CLI refuses --answer here and takes the
            # operational response (#675). The route question above is the one exception.
            answer["action"] = recovery_limits.RESPONSE_COMMAND
            # A recovery novelty hold also admits one explicit scoped retry after inspection (#1012).
            if answer.get("pause_origin") == "PAUSED_NO_PROGRESS" and (retry := recovery_limits.scoped_retry(state)):
                answer["retry_action"] = retry
        return answer
    if status == "AWAITING_GOAL_APPROVAL":
        # The approval token is saved when the CLI displays the plan; until then, relaunch to display it.
        return (
            {"kind": "approve_plan", "token": state["displayed_goal"]}
            if state.get("displayed_goal")
            else {"kind": "continue"}
        )
    if status == "PAUSED_PLANNING_BUDGET":
        need = {"kind": "planning_budget", "reason": state.get("stop_reason")}
        # AutoResolver evaluated corrective information and held: name the control it requires (#486).
        review = operational_information.projection(state) or {}
        if review.get("status") == "held" and review.get("action"):
            need["action"] = review["action"]
        return need
    if status.startswith(("PAUSED_", "BLOCKED_")) or status not in CONTINUE:
        need = {"kind": "resume", "reason": state.get("stop_reason") or status}
        # An uncertain attempt must be set aside before a resume can continue (#340).
        active = state.get("active_stage") or {}
        if active.get("output") and isinstance(active.get("iteration"), int):
            attempt = f"{active['iteration']:03d}/{Path(active['output']).stem}"
            need["abandon_stage"] = attempt
            need["action"] = f"--abandon-stage {attempt} then --resume-paused"
        if stale_report_repair:
            need["action"] = "--resume-paused"
            return need
        if "action" not in need:
            # A source-only stale repair names the command that accepts the edit.
            # Other stale pauses do not.
            action = accepted_source.resume_action(state)
            if action:
                need["action"] = action
        if status == "PAUSED_NO_PROGRESS" and "action" not in need and recovery_limits.no_progress_bound_holds(state):
            # A plain resume holds here, also after a consumed response; only a bound
            # that admits the retained count acknowledges it (#448). Information pending
            # review cannot release this bound either, so this command comes first (#486).
            need["action"] = "--resume-paused --no-progress-limit N"
            need["no_progress_batches"] = state.get("no_progress_batches", 0)
        # A parallel member's model stop whose request a person answered continues only by its own
        # control (#541): --resume-paused --retry-builder M.
        member = member_stop.action(state) if "action" not in need else None
        if member:
            need["action"] = member
        # AutoResolver evaluated corrective information and held: name the control it requires (#486).
        review = operational_information.projection(state) or {}
        if review.get("action") and (review["status"] == "held" or "action" not in need):
            need["action"] = review["action"]
        pending = state.get("pending_report_repair") or {}
        rejected = report_retry.rejected_attempt(state) or {}
        if (
            status == "PAUSED_REPEATED_FAILURE"
            and (pending.get("original") or {}).get("stage") == "sol"
            and not any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
            and pending.get("error") in report_retry.RETRYABLE_ERRORS
            and report_retry.bounded_failure(
                state, (state.get("settings") or {}).get("report_repair", {}).get("max_attempts", 0)
            )
            and isinstance(rejected.get("iteration"), int)
            and rejected.get("output")
        ):
            need["retry_report_attempt"] = f"{rejected['iteration']:03d}/{Path(rejected['output']).stem}"
            need["action"] = f"--resume-paused --retry-report {need['retry_report_attempt']}"
        return need
    return {"kind": "continue"}
