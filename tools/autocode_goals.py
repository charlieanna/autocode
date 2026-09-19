"""Versioned goals and user events for the extracted runner (no provider calls)."""
from __future__ import annotations

import copy
import difflib
import json
from pathlib import Path
import re
import uuid

try:
    from . import autocode_support as s
except ImportError:
    import autocode_support as s


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
QUESTION = obj({"id": STRING, "question": STRING, "why": STRING,
                "options": STRINGS, "proposed_default": STRING})
DECISION = obj({"text": STRING, "basis": {"type": "string", "enum": [
    "original_request", "user_answer", "user_feedback", "agent_proposed", "delegated"]}, "answer_id": STRING})
CRITERION = obj({"id": STRING, "criterion": STRING, "verification_method": STRING,
                 "human_review": {"type": "boolean"}})
BODY_SCHEMA = obj({
    "intended_outcome": STRING, "intended_user": STRING,
    "deliverables": STRINGS, "required_behaviors": STRINGS,
    "important_failure_cases": STRINGS, "scope_exclusions": STRINGS,
    "constraints": STRINGS, "permission_boundaries": STRINGS,
    "accepted_assumptions": {"type": "array", "items": DECISION},
    "delegated_decisions": {"type": "array", "items": DECISION},
    "acceptance_criteria": {"type": "array", "items": CRITERION},
    "open_blocking_questions": {"type": "array", "maxItems": 3, "items": QUESTION},
})
# Keep existing, sealed v3 briefs readable without silently changing their contract.
LEGACY_BODY_SCHEMA = copy.deepcopy(BODY_SCHEMA)
MILESTONE = obj({"id": STRING, "objective": STRING, "acceptance_criteria": STRINGS})
BRIEF_FIELDS = {
    "end_to_end_flow": STRINGS,
    "technical_approach": STRINGS,
    "milestones": {"type": "array", "items": MILESTONE},
}
BODY_SCHEMA["properties"].update(BRIEF_FIELDS)
BODY_SCHEMA["required"] += list(BRIEF_FIELDS)
DISCOVERY_SCHEMA = obj({"contract": BODY_SCHEMA, "summary": STRING})
USER_REQUEST = obj({"kind": {"type": "string", "enum": [
    "none", "clarification", "contradiction", "infeasible", "permission", "goal_change", "blocker"]},
    "discovered": STRING, "impact": STRING, "decision_needed": STRING,
    "options": STRINGS, "proposed_delta": STRING})


def role_schema(legacy, role):
    schema = copy.deepcopy(legacy)
    schema["properties"].update(contract_revision={"type": "integer"}, contract_hash=STRING, task_id=STRING,
                                user_request=USER_REQUEST, deferred_backlog=STRINGS)
    schema["required"] += ["contract_revision", "contract_hash", "task_id", "user_request", "deferred_backlog"]
    if role == "astra":
        schema["properties"]["status"]["enum"] = ["CONTINUE", "REWORK", "BLOCKED", "COMPLETE"]
        schema["properties"]["next_task"] = obj({
            "kind": {"type": "string", "enum": ["implement", "validate", "none"]},
            "milestone_id": STRING, "requirements": STRINGS,
            "acceptance_criteria": STRINGS, "validation_plan": STRINGS,
        })
        schema["properties"]["agreed_limitations"] = STRINGS
        schema["required"] += ["next_task", "agreed_limitations"]
    if role == "terra":
        for key in ("addressed_requirements", "untested_behavior", "recommended_checks"):
            schema["properties"][key] = STRINGS
            schema["required"].append(key)
    if role == "sol":
        findings = schema["properties"]["findings"]["items"]
        findings["properties"]["blocking"] = {"type": "boolean"}
        findings["required"].append("blocking")
        for key, field in {"reproduction_steps": STRINGS, "expected": STRING, "actual": STRING,
                           "why_it_matters": STRING, "suggested_correction": STRING}.items():
            findings["properties"][key] = field
            findings["required"].append(key)
        schema["properties"]["criterion_results"]["items"]["properties"]["status"]["enum"] = [
            "PASS", "FAIL", "NOT_VERIFIED"]
        schema["properties"]["end_to_end_result"] = obj({
            "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
            "summary": STRING, "evidence_refs": STRINGS,
        })
        schema["required"].append("end_to_end_result")
    return schema


def token(contract):
    return f"r{contract['revision']}:{contract['hash']}"


def sealed(contract):
    return contract.get("hash") == s.digest({k: contract[k] for k in ("task_id", "revision", "body")})


def approved(state):
    contract = state.get("goal_contract", {})
    approval = contract.get("approval_event") or {}
    return bool(contract and sealed(contract) and contract.get("approval_status") == "approved"
                and approval.get("token") == token(contract) and approval.get("actor") == "user_cli"
                and approval in state.get("user_events", [])
                and not contract["body"]["open_blocking_questions"])


def validate_body(state, body, *, ready=False, allow_legacy=False):
    legacy = allow_legacy and not any(key in body for key in BRIEF_FIELDS)
    s.validate_schema(body, LEGACY_BODY_SCHEMA if legacy else BODY_SCHEMA)
    questions = body["open_blocking_questions"]
    criteria = body["acceptance_criteria"]
    milestones = body.get("milestones", [])
    for rows in (questions, criteria, milestones):
        ids = [r["id"] for r in rows]
        if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", x) for x in ids):
            raise ValueError("Question, criterion and milestone IDs must be nonempty and unique")
    criterion_ids = {row["id"] for row in criteria}
    for milestone in milestones:
        covered = milestone["acceptance_criteria"]
        if (not milestone["objective"].strip() or not covered or len(set(covered)) != len(covered)
                or not set(covered) <= criterion_ids):
            raise ValueError("Each milestone needs an objective and existing acceptance criterion IDs")
    for key, value in body.items():
        if isinstance(value, list) and any(isinstance(x, str) and not x.strip() for x in value):
            raise ValueError(f"{key} contains an empty entry")
    for q in questions:
        if not q["question"].strip() or not q["why"].strip():
            raise ValueError("A blocking question needs its decision and consequence")
        if q["id"] in state.get("answers", {}):
            raise ValueError(f"Question {q['id']} already answered; use the saved answer")
    for row in body["accepted_assumptions"] + body["delegated_decisions"]:
        if row["basis"] in ("user_answer", "delegated"):
            answer = state.get("answers", {}).get(row["answer_id"])
            if not answer or (row["basis"] == "delegated" and answer["kind"] != "delegated"):
                raise ValueError("A claimed user decision needs an actual saved user event")
        elif row["basis"] == "user_feedback":
            if not any(event.get("id") == row["answer_id"] and event in state.get("user_events", [])
                       for event in state.get("brief_feedback", [])):
                raise ValueError("A feedback-based decision needs an actual saved feedback event")
        elif row["answer_id"]:
            raise ValueError("An inferred decision cannot cite a fabricated answer")
    if any(row["basis"] != "delegated" for row in body["delegated_decisions"]):
        raise ValueError("Delegation must be explicitly recorded by the user")
    if ready or not questions:
        if not criteria or any(not row["criterion"].strip() or not row["verification_method"].strip() for row in criteria):
            raise ValueError("Each required criterion needs a stable ID, behavior and verification method")
        for key in ("intended_outcome", "intended_user", "deliverables", "required_behaviors", "permission_boundaries"):
            if not body[key] or (isinstance(body[key], str) and not body[key].strip()):
                raise ValueError(f"Contract is missing {key}")
        if not legacy:
            for key in BRIEF_FIELDS:
                if not body[key]:
                    raise ValueError(f"Build brief is missing {key}")
            if set(c for m in milestones for c in m["acceptance_criteria"]) != criterion_ids:
                raise ValueError("Implementation milestones must cover every acceptance criterion")


def invalidate(state, reason):
    if state.get("validation"):
        state.setdefault("validation_archive", []).append({"reason": reason, "validation": state.pop("validation")})
    if state.get("human_reviews"):
        state.setdefault("human_review_archive", []).append(state.pop("human_reviews"))
    state["human_reviews"] = {}
    state.pop("displayed_goal", None)
    state.pop("displayed_review", None)


def install_draft(state, body, *, origin, allow_legacy=False):
    validate_body(state, body, allow_legacy=allow_legacy)
    previous = state.get("goal_contract")
    if previous:
        state.setdefault("contract_history", []).append(copy.deepcopy(previous))
    revision = previous["revision"] + 1 if previous else 1
    contract = {"task_id": state["task_id"], "revision": revision, "body": copy.deepcopy(body)}
    contract.update(hash=s.digest(contract), approval_status="draft", approval_event=None,
                    origin=origin, created_at=s.now())
    state["goal_contract"] = contract
    invalidate(state, "Contract revision changed; revalidate the current artifact")
    if state.get("current_task"):
        state.setdefault("task_archive", []).append(state.pop("current_task"))
    state.pop("next_action", None)
    state["acceptance_criteria"] = [{"id": c["id"], "criterion": c["criterion"],
                                    "status": "unverified", "evidence": ""} for c in body["acceptance_criteria"]]
    state["criteria_revision"] = s.digest(s.criteria_definition(state["acceptance_criteria"]))
    state["pending_questions"] = copy.deepcopy(body["open_blocking_questions"])
    state.pop("user_request", None)
    state.update(status="WAITING_FOR_USER" if state["pending_questions"] else "AWAITING_GOAL_APPROVAL",
                 phase="DISCOVERING" if state["pending_questions"] else "AWAITING_GOAL_APPROVAL",
                 next_stage="astra_discovery" if state["pending_questions"] else "astra_plan")


def migrate(state):
    """Call only at a saved, idle boundary. Preserve artifacts and execution history."""
    if state.get("active_stage") or state.get("uncertain_artifacts"):
        raise s.Paused("PAUSED_UNCERTAIN_STAGE", "Reconcile the prior request before goal migration")
    if state.get("version", 2) >= 3:
        return
    state.setdefault("task_id", str(uuid.uuid4()))
    state.setdefault("answers", {})
    state.setdefault("user_events", [])
    state.setdefault("deferred_backlog", [])
    state["pre_goal_checkpoint"] = {k: copy.deepcopy(state.get(k)) for k in (
        "status", "next_stage", "acceptance_criteria", "criteria_revision", "plan", "next_action")}
    body = {k: [] for k in BODY_SCHEMA["properties"]}
    body.update(intended_outcome=state["task"], intended_user="Unconfirmed",
                acceptance_criteria=[{"id": c["id"], "criterion": c["criterion"],
                    "verification_method": "Unconfirmed; reconstruct from saved evidence", "human_review": False}
                    for c in state.get("acceptance_criteria", [])],
                open_blocking_questions=[{"id": "migration-context", "question": "Reconstruct the goal from the request and saved work",
                    "why": "Existing criteria and agent assumptions have no user approval event",
                    "options": [], "proposed_default": ""}])
    install_draft(state, body, origin="migration_draft; no inferred user approval")
    state.update(version=3, phase="DISCOVERING", status="RUNNING", pending_questions=[], next_stage="astra_discovery")


def render(state):
    contract = state.get("goal_contract")
    if not contract:
        return "No contract yet; resume to interview with Astra."
    body = contract["body"]
    lines = [f"Build brief r{contract['revision']} ({contract['approval_status']})",
             f"Approval token: {token(contract)}"]
    if state.get("discovery_summary"):
        lines += ["", "Astra: " + state["discovery_summary"]]
    display_order = ("intended_user", "intended_outcome", "end_to_end_flow", "deliverables", "scope_exclusions",
                     "constraints", "permission_boundaries", "accepted_assumptions", "delegated_decisions",
                     "required_behaviors", "important_failure_cases", "acceptance_criteria", "technical_approach",
                     "milestones", "open_blocking_questions")
    for key in display_order:
        if key not in body:
            continue
        value = body[key]
        lines += ["", key.replace("_", " ").capitalize() + ":"]
        if not isinstance(value, list):
            lines.append(value)
        elif not value:
            lines.append("  (none declared)")
        else:
            for row in value:
                if isinstance(row, str):
                    lines.append("  - " + row)
                elif key == "acceptance_criteria":
                    lines += [f"  [{row['id']}] {row['criterion']}", f"    Verify: {row['verification_method']}",
                              "    Human review: " + ("required" if row["human_review"] else "not required")]
                elif key == "open_blocking_questions":
                    if row["id"] in state.get("answers", {}):
                        lines.append(f"  [{row['id']}] Answer saved; draft will be refreshed on resume.")
                        continue
                    lines += [f"  [{row['id']}] {row['question']}", f"    Why: {row['why']}"]
                    lines += ["    Option: " + option for option in row["options"]]
                    if row["proposed_default"]:
                        lines.append("    Proposed default: " + row["proposed_default"])
                elif key == "milestones":
                    lines += [f"  [{row['id']}] {row['objective']}",
                              "    Acceptance criteria: " + ", ".join(row["acceptance_criteria"])]
                else:
                    lines.append(f"  - {row['text']} (basis: {row['basis']}; answer: {row['answer_id'] or 'none'})")
    history = state.get("contract_history", [])
    if history:
        lines += ["", "Contract delta:"] + list(difflib.unified_diff(
            json.dumps(history[-1]["body"], indent=2, sort_keys=True).splitlines(),
            json.dumps(body, indent=2, sort_keys=True).splitlines(), fromfile="previous", tofile="current", lineterm=""))
    if state.get("answers"):
        lines += ["", "Saved user answers:", json.dumps(state["answers"], indent=2)]
    if state.get("brief_feedback"):
        lines += ["", "Saved brief feedback:"] + ["  - " + row["text"] for row in state["brief_feedback"]]
    if state.get("user_request"):
        lines += ["", "Decision needed:", json.dumps(state["user_request"], indent=2)]
        lines += [f"Answer ID: {q['id']} — {q['question']}" for q in state.get("pending_questions", [])]
    review = review_token(state)
    if review:
        lines += ["", f"Review token (current validated artifact): {review}",
                  "Validation: " + json.dumps(state["validation"], indent=2)]
    lines += ["", f"State: {state.get('phase')} / {state['status']}"]
    return "\n".join(lines)


def present(state):
    if state.get("goal_contract"):
        state["displayed_goal"] = token(state["goal_contract"])
        state["displayed_review"] = review_token(state)
    return render(state)


def approve(state, selected):
    contract = state["goal_contract"]
    if (state["status"] != "AWAITING_GOAL_APPROVAL" or not sealed(contract)
            or selected != token(contract) or state.get("displayed_goal") != selected):
        raise ValueError("Approve only the current displayed draft token; show the goal again")
    validate_body(state, contract["body"], ready=True, allow_legacy=True)
    if contract["body"]["open_blocking_questions"] or state.get("pending_questions"):
        raise ValueError("Blocking questions still need answers")
    event = {"kind": "goal_approval", "actor": "user_cli", "at": s.now(), "token": selected}
    state.setdefault("user_events", []).append(event)
    contract.update(approval_status="approved", approval_event=event)
    state.update(phase="READY_TO_EXECUTE", status="RUNNING", next_stage="astra_plan")


def feedback(state, text):
    """A free-form brief correction is input to Astra, never authorization to build."""
    if state["status"] not in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER") or not text.strip():
        raise ValueError("Brief feedback needs nonempty text at a conversation checkpoint")
    if state.get("user_request", {}).get("kind") == "human_review":
        raise ValueError("Record the artifact review with --approve-review, not brief feedback")
    event = {"kind": "brief_feedback", "id": "feedback-" + uuid.uuid4().hex[:12], "actor": "user_cli", "at": s.now(),
             "text": text.strip(), "contract_token": token(state["goal_contract"])}
    state.setdefault("user_events", []).append(event)
    state.setdefault("brief_feedback", []).append(event)
    state["goal_contract"].update(approval_status="draft", approval_event=None)
    invalidate(state, "Brief feedback requires a refreshed draft and explicit approval")
    state.update(status="RUNNING", phase="DISCOVERING", next_stage="astra_discovery", pending_questions=[])


def answer(state, question_id, text, *, delegated=False):
    matches = [q for q in state.get("pending_questions", []) if q["id"] == question_id]
    if len(matches) != 1 or question_id in state.get("answers", {}) or not text.strip():
        raise ValueError("Answer must address one unresolved question with nonempty text")
    q = matches[0]
    if delegated and not q["proposed_default"].strip():
        raise ValueError("This question has no proposed default to delegate")
    event = {"kind": "delegated" if delegated else "answer", "actor": "user_cli", "at": s.now(),
             "question_id": question_id, "question": q, "text": q["proposed_default"] if delegated else text,
             "contract_token": token(state["goal_contract"])}
    state.setdefault("user_events", []).append(event)
    state.setdefault("answers", {})[question_id] = event
    state["pending_questions"] = [row for row in state["pending_questions"] if row["id"] != question_id]
    if not state["pending_questions"]:
        state.update(status="RUNNING", phase="DISCOVERING", next_stage="astra_discovery")
    # Answers are inputs to a new draft, never goal approvals.
    state["goal_contract"].update(approval_status="draft", approval_event=None)
    invalidate(state, "A new user answer requires a reviewed draft")


def wait_for_user(state, request):
    if not request["decision_needed"].strip() or not request["impact"].strip():
        raise ValueError("A user request needs the smallest decision and its impact")
    state["user_request"] = copy.deepcopy(request)
    question = {"id": "decision-" + uuid.uuid4().hex[:12], "question": request["decision_needed"],
                "why": request["impact"], "options": request["options"], "proposed_default": ""}
    state.update(status="WAITING_FOR_USER", phase="WAITING_FOR_USER", pending_questions=[question])


def execution_guard(state, value=None):
    if not approved(state):
        raise s.Paused("PAUSED_GOAL_UNAPPROVED", "Current goal revision has no valid explicit approval")
    contract = state["goal_contract"]
    if value is not None and (value.get("contract_revision") != contract["revision"]
                              or value.get("contract_hash") != contract["hash"]):
        raise s.Paused("PAUSED_STALE_GOAL", "Role result belongs to another goal revision")
    if value is not None and state.get("current_task") and value.get("task_id") != state["current_task"]["id"]:
        raise s.Paused("PAUSED_STALE_TASK", "Role result belongs to another implementation task")


def assign_task(state, decision, current):
    """Persist one bounded handoff tied to the approved brief, not a second brief."""
    spec = decision.get("next_task")
    if spec is None and "milestones" not in state["goal_contract"]["body"]:
        # A recovered old-format stage still uses its original handoff.
        return "implement"
    if not spec or spec["kind"] not in ("implement", "validate"):
        raise ValueError("CONTINUE or REWORK requires a concrete next task")
    if decision["status"] == "REWORK" and (spec["kind"] != "implement" or not decision["evidence"]):
        raise ValueError("REWORK requires a correction task with defect evidence")
    for field in ("requirements", "acceptance_criteria", "validation_plan"):
        if not spec[field] or any(not entry.strip() for entry in spec[field]):
            raise ValueError(f"The bounded task needs {field}")
    if not decision["next_objective"].strip():
        raise ValueError("The bounded task needs an objective")
    body = state["goal_contract"]["body"]
    ids = spec["acceptance_criteria"]
    if len(set(ids)) != len(ids) or not set(ids) <= {c["id"] for c in body["acceptance_criteria"]}:
        raise ValueError("Task criteria must reference the approved brief")
    milestones = {m["id"]: m for m in body.get("milestones", [])}
    if milestones and (spec["milestone_id"] not in milestones or
            not set(ids) <= set(milestones[spec["milestone_id"]]["acceptance_criteria"])):
        raise ValueError("Task must belong to an approved milestone and its acceptance criteria")
    if state.get("current_task"):
        state.setdefault("task_archive", []).append(copy.deepcopy(state["current_task"]))
    contract = state["goal_contract"]
    state["current_task"] = {**copy.deepcopy(spec), "id": "task-" + uuid.uuid4().hex[:12],
        "objective": decision["next_objective"], "affected_paths": decision["affected_paths"],
        "contract_revision": contract["revision"], "contract_hash": contract["hash"],
        "assigned_at": s.now(), "source_revision": current["revision"], "decision": decision["status"]}
    return spec["kind"]


def record_decision(state, decision):
    saved = {"at": s.now(), "iteration": state["iteration"], "report": copy.deepcopy(decision),
             "next_stage": state["next_stage"], "current_task": copy.deepcopy(state.get("current_task"))}
    state["last_decision"] = saved
    state.setdefault("decisions", []).append(saved)


def milestone_status(state, current):
    contract = state.get("goal_contract", {})
    validation = state.get("validation", {})
    evidence = validation.get("evidence_hashes", {})
    fresh = (validation.get("source_revision") == current["revision"]
             and validation.get("contract_hash") == contract.get("hash") and evidence
             and all(Path(p).is_file() and s.file_hash(p) == h for p, h in evidence.items()))
    results = {row["id"]: row for row in validation.get("criterion_results", [])} if fresh else {}
    progress = []
    for milestone in contract.get("body", {}).get("milestones", []):
        rows = [results.get(cid, {}) for cid in milestone["acceptance_criteria"]]
        status = "NOT_VERIFIED"
        if any(row.get("status") == "FAIL" for row in rows):
            status = "FAIL"
        elif rows and all(row.get("status") == "PASS" and row.get("evidence_refs") for row in rows):
            status = "PASS"
        progress.append({**milestone, "status": status,
                         "validated_source_revision": validation.get("source_revision") if fresh else None})
    return progress


def render_completion(state):
    contract = state["goal_contract"]
    validation = state["validation"]
    results = {row["id"]: row for row in validation["criterion_results"]}
    lines = [f"COMPLETE — build brief r{contract['revision']}",
             "Validated workspace: " + state["workspace"],
             "Source revision: " + validation["source_revision"], "", "Acceptance evidence:"]
    for criterion in contract["body"]["acceptance_criteria"]:
        lines += [f"  PASS [{criterion['id']}] {criterion['criterion']}",
                  "    " + ", ".join(results[criterion["id"]]["evidence_refs"])]
    flow = validation.get("end_to_end_result")
    if flow:
        lines += ["", f"End-to-end flow: {flow['status']} — {flow['summary']}",
                  "  " + ", ".join(flow["evidence_refs"])]
    lines += ["", "Validation report: " + validation["output"]]
    for limitation in state["final_decision"].get("agreed_limitations", []):
        lines.append("Agreed limitation: " + limitation)
    return "\n".join(lines)


def review_token(state):
    val = state.get("validation", {})
    if not approved(state) or not val.get("source_revision"):
        return None
    return token(state["goal_contract"]) + "@" + val["source_revision"] + ":" + s.digest(val)


def missing_human_reviews(state):
    current = review_token(state)
    return [c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"]
            if c["human_review"] and (not current or
                state.get("human_reviews", {}).get(c["id"], {}).get("token") != current or
                state.get("human_reviews", {}).get(c["id"]) not in state.get("user_events", []))]


def approve_review(state, criterion, selected, current):
    execution_guard(state)
    val = state.get("validation", {})
    if (selected != review_token(state) or selected != state.get("displayed_review")
            or val.get("source_revision") != current["revision"] or val.get("verdict") != "PASS"
            or val.get("contract_revision") != state["goal_contract"]["revision"]
            or val.get("contract_hash") != state["goal_contract"]["hash"]
            or not val.get("evidence_hashes") or not val.get("checks")
            or any(c["exit_code"] != 0 for c in val["checks"])
            or any(f.get("blocking", True) for f in val.get("findings", []))
            or not any(c["id"] == criterion and c["status"] == "PASS" and c["evidence_refs"]
                       for c in val.get("criterion_results", []))
            or any(not Path(p).is_file() or s.file_hash(p) != h
                   for p, h in val.get("evidence_hashes", {}).items())):
        raise ValueError("Human approval needs the displayed, current validated artifact")
    required = {c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"] if c["human_review"]}
    if criterion not in required:
        raise ValueError("No such required human-review criterion")
    event = {"kind": "human_review", "actor": "user_cli", "at": s.now(), "criterion": criterion, "token": selected}
    state.setdefault("user_events", []).append(event)
    state.setdefault("human_reviews", {})[criterion] = event
    if not missing_human_reviews(state) and state.get("user_request", {}).get("kind") == "human_review":
        state.pop("user_request", None)
        state.update(status="RUNNING", phase="READY_TO_EXECUTE", next_stage="astra_review")


DISCOVERY_PROMPT = """You are ASTRA, the product lead, technical planner and final reviewer.
Terra implements. Sol independently validates. First help the user define what to build.
Read the rough idea, saved answers, brief feedback, current artifacts and project instructions.
Explain your understanding of the intended outcome in plain English in summary.
Identify decisions that materially affect product, scope, user experience or success.
Ask only material unresolved questions,
usually 2–3 at a time, using stable IDs. Never repeat answered questions. Do not use a
generic mandatory questionnaire. Stop asking once scope and success are clear.
Challenge unnecessary complexity. Prefer the smallest end-to-end version that proves
the central idea. Propose sensible defaults for reversible technical choices instead
of asking the user to decide every implementation detail.
Examine relevant happy paths, failures, permissions, persistence, dependencies and
qualitative expectations. Mark inferred preferences agent_proposed. Reference real answer
IDs for user_answer or delegated decisions; for original_request or agent_proposed rows the
answer_id must be the empty string. Do not invent user approval or delegation.
For user_feedback decisions, use the saved brief_feedback event id as answer_id.
Propose defaults with consequences. If investigation is required, propose a bounded
discovery deliverable and its limits for separate goal approval. Read-only inspection
is allowed; no implementation. Preserve existing work. All criteria are required;
optional enhancements belong in the deferred backlog. Include a verification method
per stable criterion ID and flag criteria needing actual human review.
The versioned brief must include intended_user and intended_outcome; the ordered
end_to_end_flow; deliverables and scope_exclusions; constraints, permissions and
assumptions; observable acceptance criteria with verification methods; a minimal
technical_approach; and small milestones with IDs, objectives and acceptance_criteria
IDs. Every required criterion must belong to at least one milestone.
Return the complete revised contract, including at most three open blocking questions.
The user can send feedback to revise your draft. Use that feedback without inventing
answers; ask a focused follow-up if a consequential decision is still unresolved.
An empty question list asks the runner to present the actual brief for explicit approval,
never to execute. Do not start implementation before that approval.
"""

EXECUTION_PROMPT = """
The EXACT approved goal below controls scope and success. Echo its revision and hash.
Restate acceptance_criteria entries byte-identical from the contract (same ids, criterion
text, verification methods, human_review flags); any rewording is rejected as a criteria
change. Do not weaken criteria, change required behavior or expand scope. Astra may change the
plan inside the goal; a validation task sends preserved implementation straight to Sol.
Terra implements only the authorized batch. Sol validates the actual current artifact
and reports evidence for every criterion and an explicit blocking flag on each finding.
Echo current_task.id as task_id, or the empty string when no task exists yet.
Human approvals come only from runner user events. Tests alone do not prove behaviors
they do not cover. Unknown/untested/skipped is NOT_VERIFIED, never PASS.
Put optional improvements in deferred_backlog; they cannot delay completion.
If a material ambiguity, contradiction, infeasible constraint, permission need or goal
change appears, STOP at a safe checkpoint and set user_request with the discovery,
impact, smallest decision, options/consequences and proposed contract delta. Do not
continue on an assumed answer. Terra and Sol send that request to Astra; Astra decides
whether a user decision is needed and presents it with BLOCKED. With no user decision needed use kind=none and empty
strings/lists. Correct an incorrect test only with a documented goal-consistent reason.
"""
