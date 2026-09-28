"""The read side of the task-run interface: a stable summary of one run.

Programs that drive AutoCode (the scenario harness, multi-task coordination)
read this instead of the ~140 keys of state.json, which stay private to the
runner. `autocode --run-dir RUN --status` prints it under "view".

This is a contract (see docs/task-run.md). Add fields; never rename or remove
one, and bump SCHEMA if a meaning changes. It is a pure function of the saved
state and imports nothing from the runner.
"""
from __future__ import annotations

SCHEMA = 1
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Statuses where relaunching the run, with no user input, continues the work.
CONTINUE = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL")
QUESTION_FIELDS = ("id", "question", "why", "options", "proposed_default")


def view(state: dict) -> dict:
    status = state.get("status", "")
    task = state.get("current_task") or {}
    return {
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
        "evidence": evidence(state),
    }


def evidence(state: dict) -> dict:
    """What the run agreed to deliver and what supports it, for reports outside the runner.

    outcome           the approved contract's intended outcome, or None
    base_commit       the revision the run started from
    acceptance        one row per criterion: its latest recorded outcome and evidence
    findings          the findings ledger: id, status, severity, finding
    regression_proof  for bug fixes, the runner's own fail-before/pass-after proof, else None;
                      case_tests maps each English test case to the tests that prove it
    test_cases        a reproduced bug's regression tests in plain English (id, given, when, then), else []
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
        published = state.get("resolver_human_request")
        entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get(
            (published or {}).get("request_id"))
        if isinstance(published, dict) and isinstance(entry, dict) and entry.get("status") == "pending":
            answer["resolver_request_id"] = published.get("request_id")
            answer["resolver_token"] = published.get("request_token")
        return answer
    if status == "AWAITING_GOAL_APPROVAL":
        # The approval token is saved when the CLI displays the plan; until then, relaunch to display it.
        return {"kind": "approve_plan", "token": state["displayed_goal"]} if state.get("displayed_goal") else {"kind": "continue"}
    if status == "PAUSED_PLANNING_BUDGET":
        return {"kind": "planning_budget", "reason": state.get("stop_reason")}
    if status.startswith(("PAUSED_", "BLOCKED_")) or status not in CONTINUE:
        return {"kind": "resume", "reason": state.get("stop_reason") or status}
    return {"kind": "continue"}
