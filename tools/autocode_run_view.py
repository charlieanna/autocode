"""The read side of the task-run interface: a stable summary of one run.

Programs that drive AutoCode (the scenario harness, multi-task coordination)
read this instead of the ~140 keys of state.json, which stay private to the
runner. `autocode --run-dir RUN --status` prints it under "view".

This is a contract (see docs/task-run.md). Add fields; never rename or remove
one, and bump SCHEMA if a meaning changes. It is a pure function of the saved
state and imports nothing from the runner.
"""
from __future__ import annotations

from copy import deepcopy

try:
    from . import autocode_usage, autocode_design_coverage as design_coverage
    from . import autocode_contract_identity as contract_identity
    from . import autocode_progressive_plan as progressive_rules
except ImportError:
    import autocode_usage, autocode_design_coverage as design_coverage
    import autocode_contract_identity as contract_identity
    import autocode_progressive_plan as progressive_rules

SCHEMA = 1
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Statuses where relaunching the run, with no user input, continues the work.
CONTINUE = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL")
QUESTION_FIELDS = ("id", "question", "why", "options", "proposed_default")


def view(state: dict) -> dict:
    status = state.get("status", "")
    task = state.get("current_task") or {}
    result = {
        "runner_check": {key: state["active_runner_check"].get(key) for key in
                         ("stage", "summary", "started_at", "updated_at", "command", "output")}
                        if state.get("active_runner_check") else None,
        "dependency": state.get("dependency_wait"),
        "schema": SCHEMA,
        "status": status,
        "done": status in COMPLETE,
        "needs": needs(state),
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
    }
    design = design_coverage.projection(state)
    if design is not None:
        result["design"] = design
    projection = progressive(state)
    if projection is not None:
        result["progressive"] = projection
    return result


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
    acceptance        one row per criterion: its latest recorded outcome and evidence
    findings          the findings ledger: id, status, severity, finding
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
    acceptance = []
    for item in criteria:
        item = item if isinstance(item, dict) else {"criterion": str(item)}
        outcome = outcomes.get(item.get("id")) or {}
        acceptance.append({"id": item.get("id"), "criterion": item.get("criterion") or item.get("text"),
                           "status": outcome.get("status"), "evidence": outcome.get("evidence"),
                           "human_reviewed": item.get("id") in reviewed})
    proof = state.get("regression_proof")
    replay = (state.get("validation") or {}).get("check_replay") if isinstance(state.get("validation"), dict) else None
    investigation = state.get("investigation") if isinstance(state.get("investigation"), dict) else {}
    return {
        "outcome": contract.get("intended_outcome"),
        "base_commit": state.get("base_commit"),
        "acceptance": acceptance,
        "findings": [{key: row.get(key) for key in ("id", "status", "severity", "finding")}
                     for row in state.get("findings_ledger") or [] if isinstance(row, dict)],
        "regression_proof": {key: proof.get(key) for key in
                             ("verdict", "fail_to_pass", "failures", "unverified", "commands", "source_revision",
                              "case_tests")}
                            if isinstance(proof, dict) else None,
        "test_cases": [{key: case.get(key) for key in ("id", "given", "when", "then")}
                       for case in investigation.get("test_cases") or [] if isinstance(case, dict)]
                      if investigation.get("outcome") == "reproduced" else [],
        "check_replay": {"verdict": replay.get("verdict"), "source_revision": replay.get("source_revision"),
                         "checks": [{key: row.get(key) for key in ("command", "exit_code", "timed_out", "output")}
                                    for row in replay.get("checks") or [] if isinstance(row, dict)]}
                        if isinstance(replay, dict) else None,
    }


def needs(state: dict) -> dict | None:
    """What must happen next for the run to progress, or None when it is complete.

    kind          what it asks for                  answered with
    review        human acceptance of criteria      --approve-review CRITERION --review-token TOKEN
    answer        answers to pending questions      --answer QUESTION_ID=TEXT (plus --resolver-token
                                                     when the view carries one)
    approve_plan  approval of the displayed plan    --approve-goal TOKEN
    planning_budget  more planning review calls     --feedback TEXT or --planning-review-call-limit N
    resume        a person to inspect a pause       --resume-paused, after resolving stop_reason
    continue      nothing; relaunch to proceed      the same command with --run-dir
    """
    status = state.get("status", "")
    if status in COMPLETE:
        return None
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
        return answer
    if status == "AWAITING_GOAL_APPROVAL":
        # The approval token is saved when the CLI displays the plan; until then, relaunch to display it.
        return {"kind": "approve_plan", "token": state["displayed_goal"]} if state.get("displayed_goal") else {"kind": "continue"}
    if status == "PAUSED_PLANNING_BUDGET":
        return {"kind": "planning_budget", "reason": state.get("stop_reason")}
    if status.startswith(("PAUSED_", "BLOCKED_")) or status not in CONTINUE:
        return {"kind": "resume", "reason": state.get("stop_reason") or status}
    return {"kind": "continue"}
