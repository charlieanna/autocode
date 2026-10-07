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

from pathlib import Path
from copy import deepcopy

try:
    from . import autocode_output_policy as output_policy, autocode_request_usage as request_usage
    from . import autocode_usage, autocode_efficiency, autocode_design_coverage as design_coverage
    from . import autocode_contract_identity as contract_identity, autocode_report_retry as report_retry
    from . import autocode_progressive_plan as progressive_rules
    from . import autocode_verification_view as verification_view
    from . import autocode_recovery_view as recovery_view, autocode_code_checkpoints as code_checkpoints
    from . import autocode_quota_route as quota_route, autocode_finding_rescope as finding_rescope
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_liveness as liveness_policy
    from . import autocode_operational_information as operational_information
    from . import autocode_containment_policy as containment_policy
    from . import autocode_accepted_source as accepted_source
except ImportError:
    import autocode_output_policy as output_policy, autocode_request_usage as request_usage
    import autocode_usage, autocode_efficiency, autocode_design_coverage as design_coverage
    import autocode_contract_identity as contract_identity, autocode_report_retry as report_retry
    import autocode_progressive_plan as progressive_rules
    import autocode_verification_view as verification_view
    import autocode_recovery_view as recovery_view, autocode_code_checkpoints as code_checkpoints
    import autocode_quota_route as quota_route, autocode_finding_rescope as finding_rescope
    import autocode_recovery_limits as recovery_limits
    import autocode_liveness as liveness_policy
    import autocode_operational_information as operational_information
    import autocode_containment_policy as containment_policy
    import autocode_accepted_source as accepted_source

SCHEMA = 2
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Statuses where relaunching the run, with no user input, continues the work.
CONTINUE = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL")
QUESTION_FIELDS = ("id", "question", "why", "options", "proposed_default")


def view(state: dict, *, completion_current=None, visual_acceptance=None, stale_report_repair=False, liveness=None,
         runner_check_liveness=None) -> dict:
    """Caller supplies fresh completion, evidence, repair and supervision inspections."""
    status = state.get("status", "")
    task = state.get("current_task") or {}
    active = state.get("active_stage")
    supervision = active.get("supervision") if isinstance(active, dict) else None
    check = state.get("active_runner_check")
    check_supervision = check.get("supervision") if isinstance(check, dict) else None
    result = {
        "runner_check": {**{key: deepcopy(check.get(key)) for key in
                            ("stage", "summary", "started_at", "updated_at", "command", "output", "supervision")},
                         "liveness": liveness_policy.classify(check_supervision, runner_check_liveness)}
                        if check else None,
        "dependency": state.get("dependency_wait"),
        "schema": SCHEMA,
        "status": status,
        "liveness": liveness_policy.classify(supervision, liveness),
        "done": status in COMPLETE,
        "needs": needs(state, stale_report_repair=stale_report_repair),
        "recovery": recovery_view.project(state, needs(state, stale_report_repair=stale_report_repair)),
        "verification": verification_view.project(state),
        "code_checkpoints": code_checkpoints.project(state),
        "phase": state.get("phase"),
        "next_stage": state.get("next_stage"),
        "iteration": state.get("iteration"),
        "stop_reason": state.get("stop_reason"),
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
        # Tokens and cost so far, by role (autocode_usage.summary): reported, estimated and unknown kept apart.
        "usage": autocode_usage.summary(state),
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
    result["efficiency"] = autocode_efficiency.summary(
        state, accounting=result["usage"]["accounting"], completion_current=completion_current,
        visual_acceptance=visual_acceptance)
    contract = state.get("goal_contract") or {}
    if isinstance(contract, dict) and isinstance(contract.get("body"), dict):
        try:
            if (type(contract.get("revision")) is int and contract["revision"] > 0
                    and state.get("displayed_goal") == contract_identity.token(contract)
                    and contract_identity.sealed(contract)):
                # Approval consumers need actual fields, not model-authored display text.
                result["displayed_plan"] = {
                    "revision": contract["revision"], "hash": contract["hash"],
                    "token": state["displayed_goal"],
                    **{key: deepcopy(contract["body"].get(key)) for key in
                       ("acceptance_criteria", "constraints", "permission_boundaries")},
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
        result["task_preflight"] = {key: deepcopy(state["task_preflight"].get(key)) for key in
            ("kind", "execution_context", "execution_identity", "phase", "status", "checked_at", "manifest_hash", "binding",
             "errors", "receipt", "receipt_sha256", "checks", "design")}
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
        return {"revision": contract["revision"], "hash": contract["hash"], "token": contract_identity.token(contract),
                "task_id": contract.get("task_id"), "approved_at": (contract.get("approval_event") or {}).get("at"),
                "body": deepcopy(contract["body"])}
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
    disclosure = [line for field in ("constraints", "technical_approach")
                  for line in body.get(field) or [] if isinstance(line, str) and line.startswith(
                      (progressive_rules.DISCLOSURE_DELEGATION, progressive_rules.DISCLOSURE_SLICE,
                       progressive_rules.DISCLOSURE_OUTSTANDING))]
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
            record, contract_token=contract_identity.token(contract), contract_body=body,
            contract_approved=contract_identity.approved(state),
            contract_sealed=contract_identity.sealed(contract))
        approved = True
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        reason = str(error)
    if record.get("suspended") or (record.get("delegation") and not approved):
        status = "suspended"
    elif not approved:
        status = "proposed" if candidate else "unapproved"
    elif (definition and definition.get("tentative") is False and active.get("plan_hash")
          and active.get("plan_hash") == plan.get("plan_hash")
          and definition == ((plan.get("proposal") or {}).get("slices") or [None])[0]
          and active.get("artifact") and active.get("review")):
        status = "active"
    else:
        status = "needs_activation"

    slices = [row for row in proposal.get("slices") or [] if isinstance(row, dict)]
    criteria = [{"id": row.get("id"), "criterion": row.get("criterion") or row.get("text")}
                for row in body.get("acceptance_criteria") or [] if isinstance(row, dict)]
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
            "verified": False, "status": "not_established",
            "reason": "Historical slice/checkpoint evidence is not authenticated current whole-product proof.",
        },
    }
    if "budget" in record:
        result["allowance_usage"] = record["budget"]
    return deepcopy(result)


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
    findings          the findings ledger: id, status, severity, finding
    finding_scope_moves  present once an approved revision moved criteria that open findings cite:
                      one row per finding it re-attributed (autocode_finding_rescope): finding, from,
                      to (every resulting row's id, milestone_id, criteria), contract_token, at
    regression_proof  for bug fixes, the runner's own fail-before/pass-after proof, else None;
                      case_tests maps each English test case to the tests that prove it
    test_cases        a reproduced bug's regression tests in plain English (id, given, when, then), else []
    check_replay      the current validation's checks as the runner itself re-ran them in a clean copy
                      (autocode_check_replay): verdict, source_revision and one row per command, else None
    """
    contract = (state.get("goal_contract") or {}).get("body") or {}
    criteria = contract.get("acceptance_criteria") or state.get("acceptance_criteria") or []
    decision = state.get("last_decision") if isinstance(state.get("last_decision"), dict) else {}
    report = decision.get("report") if isinstance(decision.get("report"), dict) else decision
    outcomes = {row.get("id"): row for row in report.get("acceptance_criteria") or [] if isinstance(row, dict)}
    reviewed = state.get("human_reviews") if isinstance(state.get("human_reviews"), dict) else {}
    validation = state.get("validation") if isinstance(state.get("validation"), dict) else {}
    validated = {row.get("id"): row.get("status") for row in validation.get("criterion_results") or []
                 if isinstance(row, dict)}
    acceptance = []
    for item in criteria:
        item = item if isinstance(item, dict) else {"criterion": str(item)}
        outcome = outcomes.get(item.get("id")) or {}
        acceptance.append({"id": item.get("id"), "criterion": item.get("criterion") or item.get("text"),
                           "status": outcome.get("status"), "evidence": outcome.get("evidence"),
                           "validator_status": validated.get(item.get("id")),
                           "human_reviewed": item.get("id") in reviewed})
    proof = state.get("regression_proof")
    replay = validation.get("check_replay")
    investigation = state.get("investigation") if isinstance(state.get("investigation"), dict) else {}
    moves = finding_rescope.history(state.get("findings_ledger"))
    return {
        "outcome": contract.get("intended_outcome"),
        "base_commit": state.get("base_commit"),
        **({"protected_tests": deepcopy(state["settings"]["protected_tests"])}
           if state.get("settings", {}).get("protected_tests") else {}),
        "acceptance": acceptance,
        "validator_source_revision": validation.get("source_revision"),
        "findings": [{key: row.get(key) for key in ("id", "status", "severity", "finding")}
                     for row in state.get("findings_ledger") or [] if isinstance(row, dict)],
        **({"finding_scope_moves": moves} if moves else {}),
        "regression_proof": {key: proof.get(key) for key in
                             ("verdict", "fail_to_pass", "failures", "unverified", "commands", "source_revision",
                              "case_tests")}
                            if isinstance(proof, dict) else None,
        "test_cases": [{key: case.get(key) for key in ("id", "given", "when", "then")}
                       for case in investigation.get("test_cases") or [] if isinstance(case, dict)]
                      if investigation.get("outcome") == "reproduced" else [],
        "check_replay": {"protected_tests": deepcopy(replay.get("protected_tests")), "brief_acceptance": deepcopy(replay.get("brief_acceptance")), "risk_acceptance": deepcopy(replay.get("risk_acceptance")), "verdict": replay.get("verdict"), "source_revision": replay.get("source_revision"),
                         "scheduling": deepcopy(replay.get("scheduling")),
                         "checks": [{key: deepcopy(row.get(key)) for key in
                                     ("command", "exit_code", "timed_out", "output", "output_sha256", "duration_seconds",
                                      "error", "tail", "purpose", "scheduling", "results")}
                                     for row in replay.get("checks") or [] if isinstance(row, dict)]}
                        if isinstance(replay, dict) else None,
    }


def _route(question: dict) -> dict:
    """``needs.route``: a role's model question after a quota stop or a content-filter refusal (#184, #463)."""
    route = {"question_id": question["id"], "role": question["route_role"], "job": question.get("job"),
             "current_model": question.get("current_model"), "engine": question.get("engine"),
             "cause": question.get("cause", "quota"), "stopped_model": question.get("stopped_model")}
    if "candidates" in question:
        route["candidates"] = list(question["candidates"])
    return route


def needs(state: dict, *, stale_report_repair=False) -> dict | None:
    """What must happen next for the run to progress, or None when it is complete.

    kind          what it asks for                  answered with
    review        human acceptance of criteria      --approve-review CRITERION --review-token TOKEN
    answer        answers to pending questions      --answer QUESTION_ID=TEXT (plus --resolver-token
                                                     when the view carries one); with `route` set, a
                                                     role's quota ran out: --answer route-ROLE=MODEL
    approve_plan  approval of the displayed plan    --approve-goal TOKEN
    planning_budget  more planning review calls     --feedback TEXT or --planning-review-call-limit N;
                                                     after AutoResolver held corrective information,
                                                     `action` is the control it requires
    resume        a person to inspect a pause       --resume-paused, after resolving stop_reason;
                                                     when `abandon_stage` is set, --abandon-stage
                                                     ATTEMPT first (the attempt is uncertain);
                                                     `action`, when set, is the one command that
                                                     continues (a source-only PAUSED_STALE_HANDOFF:
                                                     --resume-paused --accept-source-edit; a PAUSED_NO_PROGRESS its unchanged-
                                                     batch limit caused: --resume-paused --no-progress-
                                                     limit N, N above `no_progress_batches`, the
                                                     retained count, or 0; after AutoResolver held
                                                     corrective information, the control it requires,
                                                     view.information_review)
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
    failure = state.get('job_failure') or {}
    if quota_route.job_pause_current(state) and failure:
        known_source = bool(failure.get('source_identity'))
        need = {'kind': 'retry_job' if known_source else 'recover_source',
                'reason': failure['reason'], 'stage': failure['stage'],
                'attempt_id': failure['attempt_id'], 'job_retry_token': failure['job_retry_token'],
                'archive': failure['archive'], 'source_identity': failure['source_identity'],
                'write_diagnosis': deepcopy(failure['write_diagnosis']),
                'unrestored': list(failure['unrestored']),
                'action': '--resume-paused --retry-failed-stage --job-retry-token TOKEN' if known_source else None,
                'recovery_hint': ('Exact retry rechecks the saved original source identity, including file modes and Git HEAD. '
                                  'Restore that exact source before retrying; the archived restoration diagnosis is retained.'
                                  if known_source else
                                  'Exact retry is unavailable because this attempt has no saved original source identity. '
                                  'Inspect the archived attempt and current changes before starting a new run. '
                                  'The current checkout cannot establish the missing original identity.')}
        # A job stopped on quota or by its provider's content filter (#463) keeps its model question on
        # the failure: --answer route-ROLE=MODEL --job-retry-token TOKEN names another model and issues a
        # new token for the exact retry. Same shape as the answer need's ``route``.
        if known_source and isinstance(failure.get('route'), dict):
            need['route'] = _route(failure['route'])
            # Until a person names another model (job_failure.route_assignment), the exact retry of a
            # refused job would replay the refused model: the next step is the answer. job_retry_token
            # stays, since the CLI still accepts it. A quota stop keeps the retry (the quota resets).
            if (need['route']['cause'] == 'content_filter'
                    and not isinstance(failure.get('route_assignment'), dict)):
                need['action'] = f"--answer {need['route']['question_id']}=MODEL --job-retry-token TOKEN"
        return need
    if status == "WAITING_FOR_DEPENDENCY":
        return {"kind": "dependency", "reason": state.get("stop_reason"),
                "producer_run": (state.get("dependency_wait") or {}).get("producer_run")}
    questions = state.get("pending_questions") or []
    reviews = [question for question in questions if question.get("review_criteria")]
    if reviews:
        return {"kind": "review", "criteria": list(reviews[0]["review_criteria"]),
                "token": reviews[0].get("review_token"), "question": reviews[0].get("question")}
    request = state.get("user_request") or {}
    if request.get("kind") == "human_review":
        # A completion-time human review (autopilot.apply_review_result, after validation
        # passes with unreviewed criteria) is signaled directly on user_request with no
        # pending_questions entry at all; the token is the one already displayed for this
        # validated artifact.
        return {"kind": "review", "criteria": list(request.get("criteria") or []),
                "token": state.get("displayed_review"), "question": request.get("decision_needed")}
    if questions:
        answer = {"kind": "answer", "request_kind": request.get("kind"),
                  "questions": [{field: question.get(field) for field in QUESTION_FIELDS} for question in questions]}
        # Answers to a published AutoResolver request must carry its token
        # (autocode.py: require --resolver-token). Surface the current one so
        # drivers can serve this gate from the view alone; a stale or consumed
        # request carries no token and the CLI re-verifies freshness anyway.
        # ``resolver_scope`` says what kind of request it is: "operational_exhaustion"
        # and "blocker" mean AutoResolver could not continue safely and is asking a
        # person, not asking a requirements question a default could answer.
        published = state.get("resolver_human_request")
        entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get(
            (published or {}).get("request_id"))
        if isinstance(published, dict) and isinstance(entry, dict) and entry.get("status") == "pending":
            answer["resolver_request_id"] = published.get("request_id")
            answer["resolver_token"] = published.get("request_token")
            answer["resolver_scope"] = published.get("scope")
        # A quota stop or a content-filter refusal asks for a model (#184): answer --answer
        # route-ROLE=MODEL. It has no default and is a person's decision, never a delegable
        # requirements answer. ``cause`` is "quota" or "content_filter"; ``stopped_model`` is the
        # model that stopped; ``candidates`` (refusal only) the configured models that would pass.
        route = next((q for q in questions if q.get("category") == quota_route.CATEGORY
                      and quota_route.asked_route(questions, q.get("id"))), None)
        if route:
            answer["route"] = _route(route)
        return answer
    if status == "AWAITING_GOAL_APPROVAL":
        # The approval token is saved when the CLI displays the plan; until then, relaunch to display it.
        return {"kind": "approve_plan", "token": state["displayed_goal"]} if state.get("displayed_goal") else {"kind": "continue"}
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
        if (status == "PAUSED_NO_PROGRESS" and "action" not in need
                and recovery_limits.no_progress_bound_holds(state)):
            # A plain resume holds here, also after a consumed response; only a bound
            # that admits the retained count acknowledges it (#448). Information pending
            # review cannot release this bound either, so this command comes first (#486).
            need["action"] = "--resume-paused --no-progress-limit N"
            need["no_progress_batches"] = state.get("no_progress_batches", 0)
        # AutoResolver evaluated corrective information and held: name the control it requires (#486).
        review = operational_information.projection(state) or {}
        if review.get("action") and (review["status"] == "held" or "action" not in need):
            need["action"] = review["action"]
        pending = state.get("pending_report_repair") or {}
        rejected = report_retry.rejected_attempt(state) or {}
        if (status == "PAUSED_REPEATED_FAILURE"
                and (pending.get("original") or {}).get("stage") == "sol"
                and not any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
                and pending.get("error") in report_retry.RETRYABLE_ERRORS
                and report_retry.bounded_failure(state, (state.get("settings") or {}).get("report_repair", {}).get("max_attempts", 0))
                and isinstance(rejected.get("iteration"), int) and rejected.get("output")):
            need["retry_report_attempt"] = f"{rejected['iteration']:03d}/{Path(rejected['output']).stem}"
            need["action"] = f"--resume-paused --retry-report {need['retry_report_attempt']}"
        return need
    return {"kind": "continue"}
