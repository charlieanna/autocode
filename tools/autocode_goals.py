"""Versioned goals and user events for the extracted runner (no provider calls)."""
from __future__ import annotations

import copy
import difflib
import json
from pathlib import Path
import re
import uuid

try:
    from . import autocode_support as s, autocode_workflows as workflows
    from . import autocode_milestones as checkpoints
    from . import autocode_findings as findings
    from . import autocode_resolver_human as human
except ImportError:
    import autocode_support as s, autocode_workflows as workflows
    import autocode_milestones as checkpoints
    import autocode_findings as findings
    import autocode_resolver_human as human


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
QUESTION = obj({"id": STRING, "question": STRING, "why": STRING,
                "options": STRINGS, "proposed_default": STRING})
# Optional classification of an unknown; absent reports (and any question the
# runner itself generates, e.g. permission/blocker checkpoints) are read as
# kind="decision", category="requested_outcome", delegable=False. These three
# fields are never in QUESTION["required"], so older saved reports validate
# unchanged. A "discoverable" question must never reach the user (see
# AutoPlanner's requirements-clarification investigation pass); it is either
# resolved from the workspace, reclassified to "decision", or turned into an
# access blocker.
ASSUMPTION_CATEGORIES = ("cost", "quota", "permission", "external_side_effect",
                         "requested_outcome", "behavior", "technical", "other")
# A choice in one of these categories changes cost, quota, permissions, external
# side effects, or the user's literal requested outcome. It may never be silently
# inferred from convention, and it may never be bulk-delegated.
NON_INFERABLE_CATEGORIES = frozenset({"cost", "quota", "permission", "external_side_effect", "requested_outcome"})
QUESTION["properties"]["kind"] = {"type": "string", "enum": ["discoverable", "inferable", "decision"]}
QUESTION["properties"]["category"] = {"type": "string", "enum": list(ASSUMPTION_CATEGORIES)}
QUESTION["properties"]["delegable"] = {"type": "boolean"}
DECISION = obj({"text": STRING, "basis": {"type": "string", "enum": [
    "original_request", "user_answer", "user_feedback", "agent_proposed", "delegated"]}, "answer_id": STRING})
CRITERION = obj({"id": STRING, "criterion": STRING, "verification_method": STRING,
                 "human_review": {"type": "boolean"}})
DELTA_CONCERN = obj({"id": STRING, "concern": STRING, "evidence_refs": STRINGS,
                     "requested_change": STRING, "acceptance_test": STRING,
                     "blocking": {"type": "boolean"}})
DELTA_RESPONSE = obj({"concern_id": STRING, "response": STRING, "evidence_refs": STRINGS,
                      "change": STRING, "acceptance_test": STRING})
DELTA_DECISION = obj({"concern_id": STRING, "decision": STRING, "rationale": STRING,
                      "acceptance_test": STRING, "resolved": {"type": "boolean"}})
GRAPH_NODE_DIFF = obj({"id": STRING, "change": STRING})
GRAPH_EDGE_DIFF = obj({"from": STRING, "to": STRING, "change": STRING})
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
REQUIREMENTS_BODY_SCHEMA = copy.deepcopy(BODY_SCHEMA)
MILESTONE = obj({"id": STRING, "objective": STRING, "acceptance_criteria": STRINGS})
MILESTONE["properties"]["depends_on"] = STRINGS
MILESTONE["properties"]["affected_paths"] = STRINGS
BRIEF_FIELDS = {
    "end_to_end_flow": STRINGS,
    "technical_approach": STRINGS,
    "milestones": {"type": "array", "items": MILESTONE},
}
BODY_SCHEMA["properties"].update(BRIEF_FIELDS)
BODY_SCHEMA["required"] += list(BRIEF_FIELDS)
INITIAL_TASK = obj({"objective": STRING, "affected_paths": STRINGS,
                    "kind": {"type": "string", "enum": ["implement", "validate", "none"]},
                    "milestone_id": STRING, "requirements": STRINGS,
                    "acceptance_criteria": STRINGS, "validation_plan": STRINGS})
PLANNING_BODY_SCHEMA = copy.deepcopy(BODY_SCHEMA)
PLANNING_BODY_SCHEMA["properties"]["initial_task"] = INITIAL_TASK
PLANNING_BODY_SCHEMA["required"].append("initial_task")
DELTA_SCHEMA = obj({"schema_version": {"type": "integer", "enum": [1]}, "stage": STRING,
                    "input_path": STRING, "input_sha256": STRING, "changed_paths": STRINGS,
                    "answers_consumed": STRINGS, "feedback_consumed": STRINGS,
                    "concerns": {"type": "array", "items": DELTA_CONCERN},
                    "responses": {"type": "array", "items": DELTA_RESPONSE},
                    "decisions": {"type": "array", "items": DELTA_DECISION},
                    "graph_node_diffs": {"type": "array", "items": GRAPH_NODE_DIFF},
                    "graph_edge_diffs": {"type": "array", "items": GRAPH_EDGE_DIFF}})
# Optional job type. "bugfix" makes the runner prove the fix with a regression test
# (it must fail on the base revision and pass on the fix) before completion. It is
# part of the hashed body, so changing it requires approving the brief again. Old
# bodies without it are "build".
TASK_KINDS = ("build", "bugfix")
TASK_KIND = {"type": "string", "enum": list(TASK_KINDS)}
for _schema in (LEGACY_BODY_SCHEMA, REQUIREMENTS_BODY_SCHEMA, BODY_SCHEMA, PLANNING_BODY_SCHEMA):
    _schema["properties"]["task_kind"] = TASK_KIND
DISCOVERY_SCHEMA = obj({"contract": BODY_SCHEMA, "summary": STRING})
JOB_TYPE_POLICY = """
JOB TYPE. task_kind is "bugfix" when the request reports existing behavior that is wrong
(a crash, a wrong result, a missed validation, a regression) and asks for it to be corrected;
otherwise "build". The Requirements Gatherer proposes it and says why in its summary; the
Planner sets contract.task_kind; the Plan Reviewer confirms or challenges it. The user sees it
at approval. For a bugfix, keep the plan to the defect: reproduction, root cause, the smallest
correct fix, and a regression test in the project's own test suite that fails on the current
code and passes after the fix. One milestone is usually enough; do not add features or
unrelated refactors, and keep review concerns to whether the plan fixes the root cause and
proves it. Before completion the runner itself runs the new or changed tests against the
original code (they must fail) and the fixed code (they must pass), then the project suite.
"""
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
        # Optional: the ledger IDs this task addresses (default: every open finding).
        schema["properties"]["next_task"]["properties"]["findings"] = STRINGS
        # Optional structured reviewer findings; prose in requirements is not tracked.
        schema["properties"]["findings"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["severity", "finding", "evidence"],
            "properties": {"id": STRING,
                           "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                           "finding": STRING, "evidence": STRING, "blocking": {"type": "boolean"}}}}
        schema["properties"]["finding_dispositions"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "disposition", "evidence"],
            "properties": {"id": STRING, "disposition": {"type": "string", "enum": ["resolved", "retracted"]},
                           "evidence": STRING}}}
        schema["properties"]["agreed_limitations"] = STRINGS
        schema["required"] += ["next_task", "agreed_limitations"]
    if role == "terra":
        for key in ("addressed_requirements", "untested_behavior", "recommended_checks"):
            schema["properties"][key] = STRINGS
            schema["required"].append(key)
    if role == "sol":
        findings = schema["properties"]["findings"]["items"]
        findings["properties"]["id"] = STRING
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
        schema["properties"]["finding_dispositions"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "disposition", "evidence"],
            "properties": {"id": STRING, "disposition": {"type": "string", "enum": ["resolved", "retracted"]},
                           "evidence": STRING}}}
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
                and approval.get("token") == token(contract) and workflows.approval_actor_ok(contract.get("origin"), approval)
                and approval in state.get("user_events", [])
                and not contract["body"]["open_blocking_questions"])


def validate_body(state, body, *, ready=False, allow_legacy=False):
    legacy = allow_legacy and not any(key in body for key in BRIEF_FIELDS)
    s.validate_schema(body, PLANNING_BODY_SCHEMA if "initial_task" in body else LEGACY_BODY_SCHEMA if legacy else BODY_SCHEMA)
    if "initial_task" in body and not (body["initial_task"]["kind"] == "none" and body["open_blocking_questions"]):
        probe = {"goal_contract": {"body": body, "revision": 0, "hash": "draft"}}
        assign_task(probe, initial_decision(body), {"revision": "draft"})
    questions = body["open_blocking_questions"]
    check_delegable(questions)
    criteria = body["acceptance_criteria"]
    milestones = body.get("milestones", [])
    if not questions and not criteria:
        raise ValueError("Each required criterion needs a stable ID, behavior and verification method")
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
    if any("depends_on" in milestone for milestone in milestones):
        if any("depends_on" not in milestone for milestone in milestones):
            raise ValueError("Every milestone must declare depends_on when dependencies are planned")
        graph = {milestone["id"]: milestone["depends_on"] for milestone in milestones}
        visiting, visited = set(), set()
        def visit(milestone_id):
            if milestone_id in visiting:
                raise ValueError("Milestone dependencies contain a cycle")
            if milestone_id in visited:
                return
            visiting.add(milestone_id)
            for dependency in graph[milestone_id]:
                if dependency not in graph:
                    raise ValueError(f"Milestone {milestone_id} depends on an unknown milestone")
                visit(dependency)
            visiting.remove(milestone_id)
            visited.add(milestone_id)
        for milestone_id in graph:
            visit(milestone_id)
        first = body.get("initial_task", {})
        if first.get("kind") in ("implement", "validate") and graph.get(first["milestone_id"]):
            raise ValueError(f"initial_task milestone {first['milestone_id']} has unmet prerequisites; "
                             "start with a milestone whose depends_on is []")
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


def validate_requirements_body(state, body):
    """Validate the nontechnical v2 handoff without creating a goal contract."""
    s.validate_schema(body, REQUIREMENTS_BODY_SCHEMA)
    questions = body["open_blocking_questions"]
    criteria = body["acceptance_criteria"]
    for rows in (questions, criteria):
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)) or any(
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item) for item in ids):
            raise ValueError("Question and criterion IDs must be nonempty and unique")
    if not criteria or any(not row["criterion"].strip() or not row["verification_method"].strip()
                           for row in criteria):
        raise ValueError("Each required criterion needs a stable ID, behavior and verification method")
    for question in questions:
        if not question["question"].strip() or not question["why"].strip():
            raise ValueError("A blocking question needs its decision and consequence")
        if question["id"] in state.get("answers", {}):
            raise ValueError(f"Question {question['id']} already answered; use the saved answer")
    for row in body["accepted_assumptions"] + body["delegated_decisions"]:
        if row["basis"] in ("user_answer", "delegated"):
            answer = state.get("answers", {}).get(row["answer_id"])
            if not answer or (row["basis"] == "delegated" and answer.get("kind") != "delegated"):
                raise ValueError("A claimed user decision needs an actual saved user event")
        elif row["basis"] == "user_feedback":
            if not any(event.get("id") == row["answer_id"] and event in state.get("user_events", [])
                       for event in state.get("brief_feedback", [])):
                raise ValueError("A feedback-based decision needs an actual saved feedback event")
        elif row["answer_id"]:
            raise ValueError("An inferred decision cannot cite a fabricated answer")
    if any(row["basis"] != "delegated" for row in body["delegated_decisions"]):
        raise ValueError("Delegation must be explicitly recorded by the user")
    for key in ("intended_outcome", "intended_user", "deliverables", "required_behaviors", "permission_boundaries"):
        if not body[key] or (isinstance(body[key], str) and not body[key].strip()):
            raise ValueError(f"Requirements are missing {key}")


def apply_requirements(state, body, *, artifact_sha256, record=None):
    """Install a v2 requirements handoff and privately stage any clarification."""
    validate_requirements_body(state, body)
    state["requirements_artifact_token"] = "requirements:" + artifact_sha256
    state["requirements_body"] = copy.deepcopy(body)
    state["pending_questions"] = []
    state.pop("user_request", None)
    questions = copy.deepcopy(body["open_blocking_questions"])
    if questions:
        record = record or {}
        origin = {"stage": record.get("stage") or "requirements",
                  **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record}}
        output = record.get("output")
        evidence = {"hashes": {output: s.file_hash(Path(output))}
                    if output and Path(output).is_file() else {}}
        human.queue(state, "clarification", origin, questions=questions, evidence=evidence,
                    phase="DISCOVERING", next_stage="requirements")
    else:
        state.update(status="RUNNING", phase="PLANNING", next_stage="plan")


PLANNER_ORIGINS = {"glm_draft", "glm_revise", "astra_finalize", "astra_discovery"}
_PROTECTED_LISTS = ("required_behaviors", "scope_exclusions", "constraints", "important_failure_cases")
_CUE = re.compile(r"\b(must not|must|never|do not|don't|required|exactly|only)\b", re.I)


def protected_contract_snapshot(state):
    body = (state.get("goal_contract") or {}).get("body") or {}
    return {key: copy.deepcopy(body.get(key))
            for key in (*_PROTECTED_LISTS, "acceptance_criteria", "permission_boundaries")}


def _saved_user_basis(state, basis, answer_id):
    if basis == "user_answer":
        return bool(answer_id) and answer_id in state.get("answers", {})
    if basis == "user_feedback":
        return bool(answer_id) and any(event.get("id") == answer_id and event in state.get("user_events", [])
                                       for event in state.get("brief_feedback", []))
    return False


def _cites_saved_user_event(state, evidence):
    """Accept a saved event ID alone or as a whole token in an explanation."""
    ids = {key for key in state.get("answers", {}) if _saved_user_basis(state, "user_answer", key)}
    ids.update(event.get("id") for event in state.get("brief_feedback", [])
               if _saved_user_basis(state, "user_feedback", event.get("id")))
    known = any(re.search(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])", evidence) for key in ids)
    # A valid citation must not mask a fabricated feedback ID alongside it.
    event_tokens = re.findall(r"(?<![\w-])(?:feedback|intervention)-[\w-]+", evidence)
    return known and all(key in ids for key in event_tokens)


def revision_guard(state, body, changes, origin):
    """A planner revision may not drop protected text or widen permissions on its own."""
    previous_contract = state.get("goal_contract") or {}
    previous = previous_contract.get("body")
    if origin not in PLANNER_ORIGINS or not previous:
        return
    # A new draft may replace an unapproved one. Revising the current draft, or
    # replacing an approved contract, cannot drop protected text on its own.
    if origin in ("glm_draft", "astra_discovery") and previous_contract.get("approval_status") != "approved":
        return
    if not isinstance(changes, list):
        raise ValueError("Planner revision needs contract_changes")
    for raw in changes:
        if not isinstance(raw, dict) or raw.get("change") not in ("removed", "reworded", "permission_changed"):
            raise ValueError("contract_changes entries need item, change, basis and answer_id")
        basis = raw.get("basis")
        if not _saved_user_basis(state, basis, raw.get("answer_id")):
            raise ValueError("Changing a protected contract item needs a saved user answer or feedback event")
    declared = {}
    for raw in changes:
        declared.setdefault(raw["item"], []).append(raw)

    def consume(item, kind):
        rows = declared.get(item, [])
        match = next((row for row in rows if row["change"] == kind), None)
        if match is None:
            raise ValueError(f"Planner revision drops or changes {item!r} without a user-backed contract change")
        rows.remove(match)
        if kind == "reworded":
            replacement = str(match.get("replacement", "")).strip()
            if not replacement:
                raise ValueError(f"Rewording {item!r} needs the replacement text")
            return "user", replacement
        return "user", None

    for key in _PROTECTED_LISTS:
        for item in previous.get(key, []):
            if item in body.get(key, []):
                continue
            _, replacement = consume(item, "reworded" if any(row["change"] == "reworded" for row in declared.get(item, [])) else "removed")
            if replacement and replacement not in body.get(key, []):
                raise ValueError(f"Rewording {item!r} must appear in {key}")
    old_criteria = {row["id"]: (row["criterion"], row["verification_method"]) for row in previous.get("acceptance_criteria", [])}
    new_criteria = {row["id"]: (row["criterion"], row["verification_method"]) for row in body.get("acceptance_criteria", [])}
    for cid, text in old_criteria.items():
        if new_criteria.get(cid) == text:
            continue
        consume(cid, "removed" if cid not in new_criteria else "reworded")
    previous_permissions = previous.get("permission_boundaries", [])
    if previous_permissions and set(previous_permissions) != set(body.get("permission_boundaries", [])):
        changed = set(previous.get("permission_boundaries", [])) ^ set(body.get("permission_boundaries", []))
        for item in changed:
            consume(item, "permission_changed")
    if any(rows for rows in declared.values()):
        raise ValueError("contract_changes contains an item that was not changed in the protected contract")


def cue_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return [part.strip() for part in parts if part.strip() and _CUE.search(part)]


def source_texts(state):
    texts = [state.get("task") or ""]
    texts += [event.get("text", "") for event in state.get("brief_feedback", [])]
    texts += [event.get("text", "") for event in state.get("answers", {}).values() if isinstance(event, dict)]
    return [text for text in texts if text]


def requirement_coverage_text(text):
    """Ignore Markdown list markers when comparing already verified quotes."""
    return re.sub(r"(?m)^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", "", str(text)).strip()


_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def check_delegable(questions):
    """The protected-category rule is enforced on model output, not just in the
    prompt: a cost, quota, permission, side-effect or requested-outcome choice,
    or an unclassified one, is never offered as delegable."""
    for question in questions:
        category = question.get("category", "requested_outcome")
        if question.get("delegable") and category in NON_INFERABLE_CATEGORIES:
            raise ValueError(f"Question {question['id']} ({category}) cannot be delegable; "
                             "it is the user's own decision")


def normalize_assumption(row):
    """A legacy proposed_assumptions entry (plain string) becomes an unstructured,
    id-less record: displayed and counted, but never evidenced and never a
    target for --reject-assumption."""
    if isinstance(row, str):
        return {"id": None, "text": row, "kind": "inferable", "category": "requested_outcome",
                "convention_ref": "", "rationale": "", "supports": [], "legacy": True}
    if not isinstance(row, dict):
        raise ValueError("proposed_assumptions entries must be a string or an object")
    return {"id": row.get("id"), "text": str(row.get("text", "")), "kind": row.get("kind", "inferable"),
            "category": row.get("category", "requested_outcome"), "convention_ref": str(row.get("convention_ref", "")),
            "rationale": str(row.get("rationale", "")), "supports": list(row.get("supports") or []), "legacy": False}


def validate_assumptions(rows, known_requirement_ids):
    if not isinstance(rows, list):
        raise ValueError("proposed_assumptions must be an array")
    normalized = [normalize_assumption(row) for row in rows]
    seen_ids = set()
    for row in normalized:
        if row["legacy"]:
            continue
        if not row["text"].strip():
            raise ValueError("Each structured assumption needs text")
        if not row["id"] or not _ID_RE.fullmatch(row["id"]):
            raise ValueError("Each structured assumption needs a stable id")
        if row["id"] in seen_ids:
            raise ValueError(f"Duplicate assumption id {row['id']}")
        seen_ids.add(row["id"])
        if row["category"] not in ASSUMPTION_CATEGORIES:
            raise ValueError(f"Assumption {row['id']} has an invalid category")
        if not set(row["supports"]) <= known_requirement_ids:
            raise ValueError(f"Assumption {row['id']} supports an unknown requirement id")
        if row["kind"] == "inferable":
            if row["category"] in NON_INFERABLE_CATEGORIES:
                raise ValueError(f"Assumption {row['id']} in category {row['category']} cannot be inferable; "
                                 "it must be an open decision question")
            if not row["convention_ref"].strip():
                raise ValueError(f"Assumption {row['id']} needs a convention_ref as evidence for the inference")
            if not row["rationale"].strip():
                raise ValueError(f"Assumption {row['id']} needs a rationale for the inference")
    return normalized


def check_requirement_handoff(state, report):
    sources = source_texts(state)
    requirements = report.get("requirements", [])
    if not isinstance(requirements, list):
        raise ValueError("requirements must be an array")
    seen = set()
    quotes = []
    quote_by_id = {}
    for row in requirements:
        if not isinstance(row, dict) or not str(row.get("id", "")).strip() or not str(row.get("text", "")).strip():
            raise ValueError("Each requirement needs an id, text and source_quote")
        if row["id"] in seen:
            raise ValueError(f"Duplicate requirement id {row['id']}")
        seen.add(row["id"])
        quote = str(row.get("source_quote", "")).strip()
        if not quote or not any(quote in text for text in sources):
            raise ValueError(f"Requirement {row['id']} source_quote is not in the task or a saved user event. "
                             "If this text came from the current Builder task or approved contract, "
                             "keep that existing obligation there instead of adding a new requirement; "
                             "cite only verbatim task/user-event text for genuinely new requirements")
        quotes.append(quote)
        quote_by_id[row["id"]] = quote
    ignored = report.get("ignored_statements", [])
    if not isinstance(ignored, list):
        raise ValueError("ignored_statements must be an array")
    coverage = [requirement_coverage_text(text) for text in [*quotes, *ignored]]
    missing = []
    for sentence in (sentence for source in sources for sentence in cue_sentences(source)):
        normalized = requirement_coverage_text(sentence)
        if any(quote and (quote in normalized or normalized in quote) for quote in coverage):
            continue
        missing.append(sentence)
    if missing:
        raise ValueError("Requirement-like sentences were neither quoted nor explicitly ignored: "
                         + json.dumps(missing, ensure_ascii=False))
    questions = {q["id"] for q in report.get("open_questions", [])}
    for reframe in report.get("proposed_reframes", []):
        if reframe["requirement_id"] not in seen or not reframe["proposal"].strip():
            raise ValueError("A proposed reframe must name an existing requirement and a replacement proposal")
        if reframe["question_id"] not in questions and reframe["question_id"] not in state.get("answers", {}):
            raise ValueError("A proposed reframe needs an explicit user acceptance question")
    validate_assumptions(report.get("proposed_assumptions", []), seen)
    check_delegable(report.get("open_questions", []))
    # A refreshed handoff (one that follows an earlier requirements_gather report)
    # must retain every previous requirement verbatim, or move it to
    # ignored_requirements with a reason citing a saved user event. A vanished
    # requirement with no citation, or a citation the runner cannot resolve, is
    # rejected: only a saved user answer or feedback event authorizes dropping a
    # requirement, never the agent's own say-so. At the point this function runs
    # (from apply_planning, before the new report is installed) state's saved
    # requirements_handoff is still the previous one.
    previous_handoff = (state.get("requirements_handoff") or {}).get("report") or {}
    previous_requirements = previous_handoff.get("requirements") or []
    if previous_requirements:
        ignored_requirements = report.get("ignored_requirements", [])
        if not isinstance(ignored_requirements, list):
            raise ValueError("ignored_requirements must be an array")
        ignored_by_id = {}
        for omission in ignored_requirements:
            rid = omission.get("requirement_id")
            if rid in ignored_by_id:
                raise ValueError(f"Duplicate ignored_requirements entry for {rid}")
            ignored_by_id[rid] = omission
        for previous_row in previous_requirements:
            rid = previous_row["id"]
            previous_quote = str(previous_row.get("source_quote", "")).strip()
            if previous_quote and quote_by_id.get(rid) == previous_quote:
                continue
            omission = ignored_by_id.get(rid)
            if not omission:
                raise ValueError(f"Refreshed handoff dropped requirement {rid} without a user-backed omission "
                                 "in ignored_requirements")
            if not str(omission.get("reason", "")).strip():
                raise ValueError(f"Omission of requirement {rid} needs a reason")
            if not _saved_user_basis(state, omission.get("basis"), omission.get("event_id")):
                raise ValueError(f"Omission of requirement {rid} needs a saved user answer or feedback event, "
                                 "not an agent-authored reason")


def check_requirement_trace(state, report, contract):
    handoff = (state.get("requirements_handoff") or {}).get("report") or {}
    requirements = handoff.get("requirements") or []
    if not requirements:
        return
    trace = report.get("requirement_trace")
    if not isinstance(trace, list):
        raise ValueError("Planner report needs requirement_trace")
    by_id = {}
    for row in trace:
        if not isinstance(row, dict) or row.get("disposition") not in ("covered", "excluded", "superseded"):
            raise ValueError("requirement_trace entries need requirement_id, disposition and evidence")
        rid = row.get("requirement_id")
        if rid in by_id or rid not in {r["id"] for r in requirements}:
            raise ValueError("requirement_trace must contain each known requirement exactly once")
        by_id[rid] = row
    missing = [row["id"] for row in requirements if row["id"] not in by_id]
    if missing:
        raise ValueError("Planner dropped requirements with no trace: " + ", ".join(missing))
    behaviors = set(contract.get("required_behaviors", []))
    criteria = {row["id"] for row in contract.get("acceptance_criteria", [])}
    exclusions = set(contract.get("scope_exclusions", []))
    # Citation IDs may be followed by explanatory prose. Compare whole tokens,
    # and reject unknown IDs in the same ID families so a known ID cannot hide
    # an accidental AC99 citation in the same evidence string.
    id_tokens = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_]+[0-9]+(?![A-Za-z0-9_])")
    families = {re.match(r"[A-Za-z_]+", cid).group().casefold()
                for cid in criteria if re.match(r"[A-Za-z_]+[0-9]+$", cid)}

    def cites_defined_criterion(evidence):
        tokens = id_tokens.findall(evidence)
        cited = [token for token in tokens
                 if re.match(r"[A-Za-z_]+", token).group().casefold() in families]
        known = any(re.search(r"(?<![A-Za-z0-9_])" + re.escape(cid) + r"(?![A-Za-z0-9_])", evidence)
                    for cid in criteria)
        return known and all(token in criteria for token in cited)

    for row in requirements:
        entry = by_id[row["id"]]
        evidence = str(entry.get("evidence", "")).strip()
        disposition = entry["disposition"]
        if disposition == "covered" and evidence not in behaviors and evidence not in criteria and not cites_defined_criterion(evidence):
            raise ValueError(f"Requirement {row['id']} is not covered by a behavior or criterion")
        if disposition == "excluded":
            if evidence not in exclusions:
                raise ValueError(f"Requirement {row['id']} is not present in scope_exclusions")
            # A scope_exclusions match alone is not sufficient: the Planner could
            # otherwise exclude a user requirement by adding its own exclusion.
            # excluded now needs the same saved-user-event citation superseded does.
            if not _cites_saved_user_event(state, evidence):
                raise ValueError(f"Requirement {row['id']} cannot be excluded without a saved user event")
        if disposition == "superseded" and not _cites_saved_user_event(state, evidence):
            raise ValueError(f"Requirement {row['id']} cannot be superseded without a saved user event")
    conflicts = handoff.get("conflicts") or []
    conflict_sets = {frozenset(row.get("requirement_ids") or []) for row in conflicts}
    # A refreshed handoff may no longer call a settled pair a conflict. Preserve
    # its explicit resolution only while the same IDs still cite the same user
    # text; recycled IDs must not inherit an unrelated historical decision.
    current_quotes = {row["id"]: str(row.get("source_quote") or "").strip()
                      for row in requirements}
    for previous in state.get("requirements_history", []):
        prior = previous.get("report") or {}
        prior_quotes = {row["id"]: str(row.get("source_quote") or "").strip()
                        for row in prior.get("requirements", [])}
        for conflict in prior.get("conflicts", []):
            ids = conflict.get("requirement_ids") or []
            if ids and all(prior_quotes.get(rid) and
                           prior_quotes[rid] == current_quotes.get(rid) for rid in ids):
                conflict_sets.add(frozenset(ids))
    resolved = set()
    resolutions = report.get("conflict_resolutions", [])
    if not isinstance(resolutions, list):
        raise ValueError("conflict_resolutions must be an array")
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            raise ValueError("Each conflict resolution needs requirement IDs and a saved user basis")
        ids = resolution.get("requirement_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(rid, str) for rid in ids):
            raise ValueError("Conflict resolution needs nonempty requirement_ids")
        key = frozenset(ids)
        if len(key) != len(ids) or key not in conflict_sets or not key.issubset(by_id):
            raise ValueError("Conflict resolution must name exactly one recorded requirement conflict")
        if key in resolved:
            raise ValueError("Duplicate requirement conflict resolution")
        basis, answer_id = resolution.get("basis"), resolution.get("answer_id")
        if not _saved_user_basis(state, basis, answer_id):
            raise ValueError("Resolving a requirement conflict needs a saved user answer or feedback event")
        event = (state["answers"][answer_id] if basis == "user_answer" else
                 next(row for row in state["brief_feedback"] if row.get("id") == answer_id))
        source = event.get("text", "") if isinstance(event, dict) else ""
        quote = str(resolution.get("source_quote", "")).strip()
        if not quote or quote not in source:
            raise ValueError("Conflict resolution source_quote is not in the cited saved user event")
        if not str(resolution.get("resolution", "")).strip():
            raise ValueError("Conflict resolution needs an explanation of how the saved event settles it")
        resolved.add(key)
    open_questions = contract.get("open_blocking_questions") or []
    for conflict in conflicts:
        ids = conflict.get("requirement_ids") or []
        # A correction replaces the old side, not both sides of a contradiction.
        # For larger conflict sets stay conservative until at most one remains.
        settled = (frozenset(ids) in resolved or (bool(ids) and all(rid in by_id for rid in ids)
                   and sum(by_id[rid]["disposition"] != "superseded" for rid in ids) <= 1
                   and any(by_id[rid]["disposition"] == "superseded" for rid in ids)))
        if not settled and not open_questions:
            raise ValueError("Unresolved requirement conflict must be a blocking question: " + conflict.get("description", ""))
    for reframe in handoff.get("proposed_reframes", []):
        qid = reframe["question_id"]
        if qid not in state.get("answers", {}) and qid not in {q["id"] for q in open_questions}:
            raise ValueError("Unaccepted requirement reframe must remain a blocking question: " + qid)


def invalidate(state, reason):
    state.pop("permission_reuse_context", None)
    state.pop(human.PRIVATE, None)
    state.pop(human.PUBLIC, None)
    if state.get("validation"):
        state.setdefault("validation_archive", []).append({"reason": reason, "validation": state.pop("validation")})
    if state.get("human_reviews"):
        state.setdefault("human_review_archive", []).append(state.pop("human_reviews"))
    state["human_reviews"] = {}
    state.pop("displayed_goal", None)
    state.pop("displayed_handoff", None)
    state.pop("displayed_review", None)
    final = state.pop("planning_final", None)
    if final:
        state.setdefault("planning_final_archive", []).append({
            "reason": reason, "invalidated_at": s.now(), "final_token": final["final_token"],
            "outputs": {key: copy.deepcopy(final[key]) for key in ("artifact", "delta", "graph")},
        })


def install_draft(state, body, *, origin, allow_legacy=False, changes=None, record=None, queue_human=True):
    validate_body(state, body, allow_legacy=allow_legacy)
    revision_guard(state, body, changes or [], origin)
    previous = state.get("goal_contract")
    if previous:
        state.setdefault("contract_history", []).append(copy.deepcopy(previous))
    revision = previous["revision"] + 1 if previous else 1
    contract = {"task_id": state["task_id"], "revision": revision, "body": copy.deepcopy(body)}
    contract.update(hash=s.digest(contract), approval_status="draft", approval_event=None,
                    origin=origin, created_at=s.now(), declared_changes=copy.deepcopy(changes or []))
    state["goal_contract"] = contract
    if origin == "user_cli_edit":
        # An authenticated scope change is new user intent.
        start_clarification_episode(state, "edit_goal:r" + str(revision))
    invalidate(state, "Contract revision changed; revalidate the current artifact")
    if state.get("current_task"):
        state.setdefault("task_archive", []).append(state.pop("current_task"))
    state.pop("next_action", None)
    state["acceptance_criteria"] = [{"id": c["id"], "criterion": c["criterion"],
                                    "status": "unverified", "evidence": ""} for c in body["acceptance_criteria"]]
    state["criteria_revision"] = s.digest(s.criteria_definition(state["acceptance_criteria"]))
    state["pending_questions"] = []
    state.pop("user_request", None)
    questions = body["open_blocking_questions"]
    joint = state.get("settings", {}).get("joint_planning")
    if not queue_human:
        return
    if joint and not questions and origin in ("glm_draft", "glm_revise", "user_cli_edit", "plan", "plan_revise"):
        if origin == "plan":
            state.update(status="RUNNING", phase="PLANNING", next_stage="plan_review")
            return
        if origin == "plan_revise":
            state.update(status="RUNNING", phase="PLANNING", next_stage="plan_finalize")
            return
        if origin == "glm_revise":
            state.update(status="RUNNING", phase="PLANNING", next_stage="astra_finalize")
        else:
            try:
                from . import autocode_planning as planning
            except ImportError:
                import autocode_planning as planning
            planning.start(state)
        return
    record = record or {}
    proposed_by = {"stage": record.get("stage") or origin,
                   **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record}}
    output = record.get("output")
    evidence = {"hashes": {output: s.file_hash(Path(output))} if output and Path(output).is_file() else {}}
    human.queue(state, "clarification" if questions else "goal_approval", proposed_by,
                questions=questions, evidence=evidence,
                status="WAITING_FOR_USER" if questions else "AWAITING_GOAL_APPROVAL",
                phase="DISCOVERING" if questions else "AWAITING_GOAL_APPROVAL",
                next_stage=("requirements" if state.get("settings", {}).get("planning_flow") == "v2"
                            else "astra_discovery") if questions else "astra_plan")


def migrate(state, *, fresh=False):
    """Call only at a saved, idle boundary. Preserve artifacts and execution history.
    A fresh run (``fresh``) first recognizes what kind of job the request is."""
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
    body = {k: [] for k in BODY_SCHEMA["properties"] if k != "task_kind"}
    body.update(intended_outcome=state["task"], intended_user="Unconfirmed",
                acceptance_criteria=[{"id": c["id"], "criterion": c["criterion"],
                    "verification_method": "Unconfirmed; reconstruct from saved evidence", "human_review": False}
                    for c in state.get("acceptance_criteria", [])],
                open_blocking_questions=[{"id": "migration-context", "question": "Reconstruct the goal from the request and saved work",
                    "why": "Existing criteria and agent assumptions have no user approval event",
                    "options": [], "proposed_default": ""}])
    install_draft(state, body, origin="migration_draft; no inferred user approval", queue_human=False)
    first_stage = ("requirements" if state.get("settings", {}).get("planning_flow") == "v2" else
                   "requirements_gather" if "requirements" in state.get("settings", {}).get("roles", {})
                   else "astra_discovery")
    state.update(version=3, phase="DISCOVERING", status="RUNNING", pending_questions=[], next_stage=first_stage)
    if fresh:
        workflows.begin(state, first_stage)


def plan_preview(state):
    """Rule E9 (issue #62): what the run knows, what it would assume, and what only
    the user can decide, at a planning clarification stop. Counts only; no score.
    Bound to the displayed contract revision and the requirements handoff hash.
    Runner-generated permission, blocker and review stops carry a user_request
    and get no preview."""
    contract = state.get("goal_contract")
    questions = state.get("pending_questions") or []
    if (not contract or state.get("status") != "WAITING_FOR_USER" or not questions
            or state.get("user_request")):
        return []
    handoff_entry = state.get("requirements_handoff") or {}
    handoff = handoff_entry.get("report") or {}
    rejected = {ob.get("assumption_id") for ob in state.get("deferred_obligations", [])}
    lines = ["", f"PLAN PREVIEW for {token(contract)} (requirements handoff {handoff_ref(state)})",
             "Answering nothing leaves execution blocked. Nothing here approves the plan."]

    lines += ["", "Known from you:"]
    lines += [f"  [{row['id']}] {row['text']} (you said: \"{row['source_quote']}\")"
              for row in handoff.get("requirements", [])] or ["  (no quoted requirements recorded)"]

    lines += ["", "Known from the codebase:"]
    refs = [ref for ref in handoff.get("source_refs", []) if ref != "task"]
    if refs:
        lines.append("  Files read: " + ", ".join(refs))
    lines += [f"  [{row['question_id']}] {row['resolution']} (source: {', '.join(row['source_refs'])})"
              for row in state.get("machine_resolutions", [])]
    if lines[-1] == "Known from the codebase:":
        lines.append("  (nothing cited from the workspace)")

    structured, legacy = [], []
    for row in (normalize_assumption(raw) for raw in handoff.get("proposed_assumptions", [])):
        if row["legacy"]:
            legacy.append(row)
        elif row["id"] not in rejected:
            structured.append(row)
    planner = []
    for row in contract["body"].get("accepted_assumptions", []):
        if row.get("basis") == "agent_proposed" and row["text"] not in planner:
            planner.append(row["text"])
    lines += ["", "Assumptions I would make:"]
    lines += [f"  [{row['id']}] {row['text']} ({row['category']}; evidence: {row['convention_ref'] or 'none'})"
              for row in structured]
    lines += [f"  Unstructured, from an older run: {row['text']}" for row in legacy]
    lines += [f"  Planner: {text}" for text in planner]
    if not (structured or legacy or planner):
        lines.append("  (none)")

    lines += ["", "Decisions only you can make:"]
    for question in questions:
        lines += [f"  [{question['id']}] {question['question']}", f"    Why: {question['why']}"]
        lines += ["    Option: " + option for option in question.get("options", [])]
        if question.get("proposed_default"):
            lines.append("    Proposed default: " + question["proposed_default"]
                         + ("" if question.get("delegable") else " (not delegable)"))

    obligations = open_obligations(state)
    criteria = contract["body"].get("acceptance_criteria", [])
    lines += ["", "Readiness:",
              f"  Blocking decisions: {len(questions)}",
              f"  Assumptions relied on: {len(structured) + len(legacy) + len(planner)}",
              f"  Acceptance tests in requirements: {len(handoff.get('acceptance_tests', []))}",
              f"  Acceptance criteria in the contract: {len(criteria)}"
              f" ({sum(bool(row.get('human_review')) for row in criteria)} need your review)",
              f"  Open obligations: {sum(ob['kind'] == 'human_decision' for ob in obligations)} for your decision, "
              f"{sum(ob['kind'] == 'remediation' for ob in obligations)} awaiting remediation"]
    lines += ["", "Next: --answer QUESTION_ID=TEXT, --edit-goal body.json, or with "
              f"--review-token '{token(contract)}': --delegate-all or --reject-assumption ASSUMPTION_ID"]
    return lines


def task_kind(state):
    return ((state.get("goal_contract") or {}).get("body") or {}).get("task_kind") or "build"


def render(state):
    contract = state.get("goal_contract")
    if not contract:
        if state.get("settings", {}).get("planning_flow") == "v2":
            public = human.current(state)
            questions = public["questions"] if public else human.internal_questions(state)
            lines = ["No contract yet; resume with the Requirements Planner."]
            for question in questions:
                if not public:
                    lines.append("  Unissued proposal (not an actionable question):")
                lines += [f"  [{question['id']}] {question['question']}", f"    Why: {question['why']}"]
            if public:
                lines += [f"Resolver request ID: {public['request_id']}",
                          f"Resolver request token: {public['request_token']}"]
            return "\n".join(lines)
        return "No contract yet; resume to interview with the Requirements Gatherer."
    body = contract["body"]
    public = human.current(state)
    lines = [f"Build brief r{contract['revision']} ({contract['approval_status']})"]
    if public:
        lines += [f"AutoResolver request: {public['request_id']}",
                  f"AutoResolver token: {public['request_token']}"]
    if public and public["scope"] == "goal_approval":
        lines.append(f"Approval token: {token(contract)}")
    if state.get("discovery_summary"):
        lines += ["", "Planning: " + state["discovery_summary"]]
    lines += plan_preview(state)
    if body.get("task_kind") == "bugfix":
        lines += ["", "Job type: bug fix. Before completion the runner itself checks that a new or changed",
                  "test fails on the original code and passes with the fix, and that no test that",
                  "passed before now fails."]
    elif "task_kind" in body:
        lines += ["", "Job type: build (not a bug fix). In a one-milestone plan, criteria verified by \"test: ...\" are proven by the runner: each named test must pass with the change and not without it."]
    display_order = ("intended_user", "intended_outcome", "end_to_end_flow", "deliverables", "scope_exclusions",
                     "constraints", "permission_boundaries", "accepted_assumptions", "delegated_decisions",
                     "required_behaviors", "important_failure_cases", "acceptance_criteria", "technical_approach",
                      "milestones", "initial_task", "open_blocking_questions")
    for key in display_order:
        if key not in body:
            continue
        value = body[key]
        lines += ["", key.replace("_", " ").capitalize() + ":"]
        if isinstance(value, dict):
            lines.append(json.dumps(value, indent=2))
        elif not isinstance(value, list):
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
                    if not public or row not in public["questions"]:
                        lines.append("  Unissued proposal (not an actionable question):")
                    lines += [f"  [{row['id']}] {row['question']}", f"    Why: {row['why']}"]
                    lines += ["    Option: " + option for option in row["options"]]
                    if row["proposed_default"]:
                        lines.append("    Proposed default: " + row["proposed_default"])
                elif key == "milestones":
                    lines += [f"  [{row['id']}] {row['objective']}",
                              "    Acceptance criteria: " + ", ".join(row["acceptance_criteria"])]
                    if "depends_on" in row:
                        lines.append("    Depends on: " + (", ".join(row["depends_on"]) or "none; can start independently"))
                    if "affected_paths" in row:
                        lines.append("    Owned paths: " + (", ".join(row["affected_paths"]) or "unspecified; serial dispatch"))
                else:
                    lines.append(f"  - {row['text']} (basis: {row['basis']}; answer: {row['answer_id'] or 'none'})")
    declared = contract.get("declared_changes") or []
    if declared:
        lines += ["", "Declared contract changes:"]
        ordered = sorted(declared, key=lambda row: row.get("change") != "permission_changed")
        for row in ordered:
            lines.append(f"  - {row.get('change')}: {row.get('item')} (basis: {row.get('basis')})")
    history = state.get("contract_history", [])
    if history:
        lines += ["", "Contract delta:"] + list(difflib.unified_diff(
            json.dumps(history[-1]["body"], indent=2, sort_keys=True).splitlines(),
            json.dumps(body, indent=2, sort_keys=True).splitlines(), fromfile="previous", tofile="current", lineterm=""))
    if state.get("answers"):
        lines += ["", "Saved user answers:", json.dumps(state["answers"], indent=2)]
    if state.get("brief_feedback"):
        lines += ["", "Saved brief feedback:"] + ["  - " + row["text"] for row in state["brief_feedback"]]
    if public:
        if public["request"]:
            lines += ["", "Decision needed:", json.dumps(public["request"], indent=2)]
        lines += [f"Answer ID: {q['id']} - {q['question']}" for q in public["questions"]]
        lines += [f"Resolver request ID: {public['request_id']}",
                  f"Resolver request token: {public['request_token']}"]
    if state.get("planning"):
        total = state['planning']['astra_calls']
        recovered = state['planning'].get('recovery_review_calls_used', 0)
        limit = state['planning'].get('review_call_limit', state.get('settings', {}).get('planning_review_call_limit', 2))
        budget = (f"{total - recovered}/{limit} ordinary plan-review calls used; "
                  f"AutoResolver recovery calls used: {recovered}; {total} total calls"
                  if recovered else f"{total}/{limit} plan-review calls used")
        if limit == 0:
            budget = f"{total} plan-review calls used; unlimited"
        lines += ["", f"Joint planning: {budget}"]
        for stage, report in state["planning"]["reports"].items():
            lines.append(f"  {stage}: {report['output']}")
        reports = state["planning"]["reports"]
        final = (reports.get("plan_finalize") or reports.get("astra_finalize") or {}).get("report", {})
        for decision in final.get("decisions", []):
            lines += [f"  [{decision['concern_id']}] {decision['decision']}",
                      "    Why: " + decision["rationale"], "    Test: " + decision["acceptance_test"]]
    review = review_token(state) if public else None
    if review:
        lines += ["", f"Review token (current validated artifact): {review}",
                  "Validation: " + json.dumps(state["validation"], indent=2)]
    lines += ["", f"State: {state.get('phase')} / {state['status']}"]
    return "\n".join(lines)


def handoff_ref(state):
    """Short, stable reference to the current requirements handoff report."""
    report = (state.get("requirements_handoff") or {}).get("report")
    return s.digest(report)[:12] if report else "none"


def check_displayed(state, selected):
    """An action the user takes on a preview must name the revision they saw.
    Rejects a stale token, and a requirements handoff refreshed since display."""
    contract = state.get("goal_contract")
    if (not contract or not selected or selected != token(contract) or state.get("displayed_goal") != selected
            or state.get("displayed_handoff") != handoff_ref(state)):
        raise ValueError("Act only on the current displayed revision; show the goal again and pass its token "
                         "with --review-token")


def check_displayed_handoff(state, selected):
    """Question actions bind to the displayed requirements handoff revision.

    Delegation and assumption rejection never grant approval, so they do not
    require the resolver-published goal display; they still refuse a stale
    token or a handoff refreshed since it was displayed."""
    contract = state.get("goal_contract")
    if (not contract or not selected or selected != token(contract)
            or state.get("displayed_handoff") != handoff_ref(state)):
        raise ValueError("Act only on the current displayed revision; show the goal again and pass its token "
                         "with --review-token")


def present(state):
    public = human.current(state)
    state.pop("displayed_goal", None)
    # Preserve the historical display acknowledgement for exact, already-recorded
    # review replays. Rendering still hides controls without current authority.
    recorded_review = any(isinstance(row, dict) and row.get('actor') == 'user_cli'
                          and row.get('token') == state.get('displayed_review')
                          and row in state.get('user_events', [])
                          for row in state.get('human_reviews', {}).values())
    if not recorded_review:
        state.pop("displayed_review", None)
    # Any current request is shown against the contract: --delegate-all and
    # --reject-assumption act on that display at clarification and permission
    # stops. Approval itself still requires the goal-approval status.
    if public and state.get("goal_contract"):
        state["displayed_goal"] = token(state["goal_contract"])
    if state.get("goal_contract"):
        # Question actions bind to the displayed handoff even without a
        # published request; goal approval keeps its stricter display binding.
        state["displayed_handoff"] = handoff_ref(state)
    if public:
        state["displayed_review"] = review_token(state)
    return render(state)


def approve(state, selected):
    contract = state["goal_contract"]
    # Resuming after an environment failure can reach the execution guard with
    # the same reviewed draft. Keep exact-token approval available in that pause.
    if (state["status"] not in ("AWAITING_GOAL_APPROVAL", "PAUSED_GOAL_UNAPPROVED")
            or any(state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair"))
            or not sealed(contract)
            or selected != token(contract) or state.get("displayed_goal") != selected):
        raise ValueError("Approve only the current displayed draft token; show the goal again")
    validate_body(state, contract["body"], ready=True, allow_legacy=True)
    if contract["body"]["open_blocking_questions"] or state.get("pending_questions"):
        raise ValueError("Blocking questions still need answers")
    if open_obligations(state):
        raise ValueError("Rejected assumptions still have unresolved obligations: "
                         + ", ".join(ob["id"] for ob in open_obligations(state)))
    joint = state.get("settings", {}).get("joint_planning")
    if joint and (state.get("planning", {}).get("final_token") != selected or "initial_task" not in contract["body"]):
        raise ValueError("Joint planning requires the Plan Reviewer's final plan before approval")
    current = s.snapshot(Path(state["workspace"])) if joint else None
    event = {"kind": "goal_approval", "actor": "user_cli", "at": s.now(), "token": selected}
    state.setdefault("user_events", []).append(event)
    contract.update(approval_status="approved", approval_event=event)
    state.update(phase="READY_TO_EXECUTE", status="RUNNING", next_stage="astra_plan")
    carried = []
    if checkpoints.enabled(state):
        current = current or s.snapshot(Path(state['workspace']))
        carried = checkpoints.carryforward.carry(state, current)
    if joint:
        if contract['body']['initial_task']['milestone_id'] in carried:
            state.update(next_stage='astra_review',
                         next_action='Preserve carried milestones; assign unfinished work or final integration validation')
            return
        decision = initial_decision(contract["body"])
        kind = assign_task(state, decision, current)
        try:
            from . import autocode_dispatch as dispatch
        except ImportError:
            import autocode_dispatch as dispatch
        state.update(next_action=decision["next_objective"], affected_paths=decision["affected_paths"],
                     next_stage="sol" if kind == "validate" else dispatch.build_stage(state))
        record_decision(state, decision)


def initial_decision(body):
    spec = body["initial_task"]
    return {"status": "CONTINUE", "next_objective": spec["objective"], "affected_paths": spec["affected_paths"],
            "next_task": {k: v for k, v in spec.items() if k not in ("objective", "affected_paths")}}


def feedback(state, text):
    """A free-form brief correction is input to the Plan Reviewer, never authorization to build."""
    no_contract_v2 = not state.get("goal_contract") and state.get("settings", {}).get("planning_flow") == "v2"
    if (state["status"] not in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER", "PAUSED_PLANNING_BUDGET")
            and not no_contract_v2) or not text.strip():
        raise ValueError("Brief feedback needs nonempty text at a conversation checkpoint")
    if state.get("user_request", {}).get("kind") == "human_review":
        raise ValueError("Record the artifact review with --approve-review, not brief feedback")
    contract = state.get("goal_contract")
    event = {"kind": "brief_feedback", "id": "feedback-" + uuid.uuid4().hex[:12], "actor": "user_cli", "at": s.now(),
             "text": text.strip(), "contract_token": token(contract) if contract else state.get("requirements_artifact_token", ""),
             "starts_episode": True}
    state.setdefault("user_events", []).append(event)
    state.setdefault("brief_feedback", []).append(event)
    start_clarification_episode(state, event["id"])
    # A new user clarification restarts requirements discovery. The old
    # report-only repair is bound to the preceding task/contract sources and
    # must remain as evidence rather than consuming this episode's allowance.
    pending = state.pop("pending_report_repair", None)
    if pending:
        state.setdefault("report_repair_archive", []).append({
            "reason": "superseded_by_user_feedback", "feedback_id": event["id"],
            "repair": copy.deepcopy(pending)})
    if contract:
        contract.update(approval_status="draft", approval_event=None)
        invalidate(state, "Brief feedback requires a refreshed draft and explicit approval")
    first_stage = ("requirements" if state.get("settings", {}).get("planning_flow") == "v2" else
                   "requirements_gather" if "requirements" in state.get("settings", {}).get("roles", {}) else "astra_discovery")
    state.update(status="RUNNING", phase="DISCOVERING", next_stage=first_stage, pending_questions=[])


def apply_intervention_feedback(state, receipt, applied_receipt):
    """Save runner-applied feedback without treating it as a checkpoint approval."""
    contract = state.get("goal_contract")
    event = {"kind": "brief_feedback", "id": "intervention-" + receipt["id"], "actor": "user_intervention",
             "at": applied_receipt["applied_at"], "text": receipt["text"],
             "contract_token": receipt.get("observed_goal_token"), "receipt_id": receipt["id"],
             "retained_work": {"current_task": copy.deepcopy(state.get("current_task")),
                               "stages": len(state.get("stages", []))}, "starts_episode": True}
    state.setdefault("user_events", []).append(event)
    state.setdefault("brief_feedback", []).append(event)
    start_clarification_episode(state, event["id"])
    if state.get("status") == "TASK_COMPLETE":
        state.setdefault("completion_archive", []).append({
            "completed_at": state.pop("completed_at", None), "decision": state.pop("final_decision", None),
            "reason": "Queued feedback arrived before later execution"})
        state.pop("completion_actor", None)
    if contract:
        contract.update(approval_status="draft", approval_event=None)
    invalidate(state, "Queued feedback requires Plan Reviewer review, refreshed approval and validation")
    first_stage = ("requirements" if state.get("settings", {}).get("planning_flow") == "v2" else
                   "requirements_gather" if "requirements" in state.get("settings", {}).get("roles", {})
                   else "astra_discovery")
    state.update(status="PAUSED_INTERVENTION", phase="PAUSED_OR_BLOCKED", next_stage=first_stage,
                 pending_questions=[], stop_reason="Queued feedback was applied; explicitly continue to Requirements discovery.")


def answer(state, question_id, text, *, delegated=False):
    if ((state.get("user_request") or {}).get("kind") == "blocker"
            or (state.get(human.PUBLIC) or {}).get("scope") in ("blocker", "operational_exhaustion")):
        raise ValueError("Operational questions require the exact AutoResolver response path, not a requirements answer")
    matches = [q for q in state.get("pending_questions", []) if q["id"] == question_id]
    if len(matches) != 1 or question_id in state.get("answers", {}) or not text.strip():
        raise ValueError("Answer must address one unresolved question with nonempty text")
    q = matches[0]
    if delegated and not q["proposed_default"].strip():
        raise ValueError("This question has no proposed default to delegate")
    if delegated and any(ob["id"] == question_id for ob in open_obligations(state)):
        raise ValueError(f"{question_id} asks about a rejected assumption and cannot be delegated; answer it yourself")
    contract = state.get("goal_contract")
    event = {"kind": "delegated" if delegated else "answer", "actor": "user_cli", "at": s.now(),
             "question_id": question_id, "question": q, "text": q["proposed_default"] if delegated else text,
             "contract_token": token(contract) if contract else state.get("requirements_artifact_token", "")}
    if not delegated:
        # New user intent; delegation only accepts an already-proposed default.
        event["starts_episode"] = True
    state.setdefault("user_events", []).append(event)
    state.setdefault("answers", {})[question_id] = event
    if not delegated:
        start_clarification_episode(state, "answer:" + question_id)
        # An obligation returned to the user is asked under its own id; only the
        # user's own answer discharges it, never a delegated default.
        resolve_obligation(state, question_id, "answer:" + question_id)
    state["pending_questions"] = [row for row in state["pending_questions"] if row["id"] != question_id]
    body = state.get("goal_contract", {}).get("body", {})
    if "open_blocking_questions" in body:
        body["open_blocking_questions"] = [row for row in body["open_blocking_questions"] if row.get("id") != question_id]
    if not state["pending_questions"]:
        state.update(status="RUNNING", phase="DISCOVERING",
                     next_stage="requirements" if state.get("settings", {}).get("planning_flow") == "v2" else "astra_discovery")
        state["discovery_summary"] = ""
    # Answers are inputs to a new draft, never goal approvals.
    if contract:
        contract.update(approval_status="draft", approval_event=None)
        # Re-seal the contract after body changes so approve() can accept it.
        contract["hash"] = s.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        invalidate(state, "A new user answer requires a reviewed draft")


def start_clarification_episode(state, started_by):
    """Begin a new runner-owned clarification episode. Only a saved user event
    that changes intent calls this (a non-delegated answer, brief feedback, an
    applied intervention, or an edited goal). Model output never does, so
    regenerated question IDs or reworded handoffs cannot replenish the one
    investigation pass. A pending investigation from the old episode is moot."""
    state["clarification_episode"] = {"id": "episode-" + uuid.uuid4().hex[:12], "started_by": started_by,
                                      "started_at": s.now(), "investigation_used": False,
                                      "used_at": None, "used_stage": None}
    state.pop("investigation_request", None)
    # A remediation proposed under the old intent must be proposed again, and
    # reviewed again, under the new one; its old hash can no longer discharge it.
    for ob in open_obligations(state):
        if ob.get("remediation"):
            ob.setdefault("superseded_remediations", []).append(
                {"record": ob["remediation"], "hash": ob.get("remediation_hash"), "by": started_by})
            ob.update(remediation=None, remediation_hash=None, status="open")
    return state["clarification_episode"]


def clarification_episode(state):
    """The current episode, created on first use. Legacy runs have none."""
    return state.get("clarification_episode") or start_clarification_episode(state, "initial_task")


def open_obligations(state, kind=None):
    """Unresolved deferred obligations; legacy runs have none."""
    return [ob for ob in state.get("deferred_obligations", [])
            if ob.get("status") != "resolved" and (kind is None or ob.get("kind") == kind)]


def resolve_obligation(state, obligation_id, resolved_by):
    for ob in open_obligations(state):
        if ob["id"] == obligation_id:
            ob.update(status="resolved", resolved_by=resolved_by, resolved_at=s.now())
            return ob
    return None


def delegate_all(state, selected):
    """Bulk-delegate every currently pending question to its proposed default.
    Conservative by construction: a question with no default, or not explicitly
    marked delegable=True, blocks the whole call rather than being silently
    skipped or silently delegated. Never grants approval; inherits answer()'s
    approval invalidation for each question it delegates."""
    check_displayed_handoff(state, selected)
    pending = state.get("pending_questions", [])
    if not pending:
        raise ValueError("No pending questions to delegate")
    obligations = {ob["id"] for ob in open_obligations(state)}
    blocked = [q["id"] for q in pending if not q.get("proposed_default", "").strip() or not q.get("delegable", False)
               or q.get("category", "requested_outcome") in NON_INFERABLE_CATEGORIES or q["id"] in obligations]
    if blocked:
        raise ValueError("These questions cannot be bulk-delegated (no proposed default, not marked delegable, "
                         "a protected or unclassified category, or a rejected assumption): "
                         + ", ".join(blocked))
    for q in list(pending):
        answer(state, q["id"], "accept default", delegated=True)


def reject_assumption(state, assumption_id, selected):
    """Record that the user rejects a specific structured assumption from the
    current requirements handoff. This never removes or rewords any entry in
    requirements[]: check_requirement_handoff and check_requirement_trace are
    responsible for making sure a requirement the rejected assumption supported
    stays covered, or is dropped only through its own saved user-backed omission.
    Never grants approval; invalidates any existing approval like answer() does."""
    if state["status"] not in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER", "PAUSED_PLANNING_BUDGET"):
        raise ValueError("Rejecting an assumption needs an open conversation checkpoint")
    check_displayed_handoff(state, selected)
    handoff = (state.get("requirements_handoff") or {}).get("report") or {}
    rows = {row["id"]: row for row in
            (normalize_assumption(raw) for raw in handoff.get("proposed_assumptions", []))
            if not row["legacy"] and row["id"]}
    row = rows.get(assumption_id)
    if not row:
        raise ValueError("Unknown or legacy assumption id; only a structured assumption from the "
                         "current requirements handoff can be rejected")
    if any(ob.get("assumption_id") == assumption_id and ob.get("status") != "resolved"
           for ob in state.get("deferred_obligations", [])):
        raise ValueError(f"Assumption {assumption_id} already has an open rejection")
    category = row["category"] if row["category"] in ASSUMPTION_CATEGORIES else "requested_outcome"
    kind = "human_decision" if category in NON_INFERABLE_CATEGORIES else "remediation"
    contract = state.get("goal_contract") or {}
    event = {"kind": "reject_assumption", "id": "reject-" + uuid.uuid4().hex[:12], "actor": "user_cli",
             "at": s.now(), "assumption_id": assumption_id, "category": category, "text": row["text"],
             "contract_token": token(contract) if contract else None}
    state.setdefault("user_events", []).append(event)
    obligation = {"id": "obligation-" + uuid.uuid4().hex[:12], "kind": kind, "assumption_id": assumption_id,
                  "category": category, "text": row["text"], "supports": list(row["supports"]),
                  "contract_revision": contract.get("revision"), "created_at": event["at"], "status": "open",
                  "remediation": None, "remediation_hash": None, "resolved_by": None,
                  "reject_event_id": event["id"]}
    state.setdefault("deferred_obligations", []).append(obligation)
    if contract:
        contract.update(approval_status="draft", approval_event=None)
    invalidate(state, "An assumption rejection requires a refreshed draft and explicit approval")
    first_stage = ("requirements_gather" if "requirements" in state.get("settings", {}).get("roles", {})
                   else "astra_discovery")
    state.update(status="RUNNING", phase="DISCOVERING", next_stage=first_stage, pending_questions=[])
    return obligation


def is_operational_response(request):
    """True when a pending decision can be answered without revising the
    approved goal: a scoped permission ask, or a blocker whose own
    proposed_delta explicitly states no goal/scope/criterion/product change
    (for example, more execution time or tool/spending budget to finish
    already-approved verification). Both the CLI and interactive chat must
    use this single check so an operational-only answer never takes a
    different route depending on which surface it was given through."""
    request = request or {}
    if request.get("kind") == "permission":
        return True
    if request.get("kind") != "blocker":
        return False
    proposed_delta = str(request.get("proposed_delta", ""))
    return proposed_delta.startswith((
        "No goal, scope, criterion, or behavior change.",
        "No contract, product, acceptance-criterion, implementation-scope, filesystem, provider or spending change."))


def resolve_permission(state, question_id, text):
    """Record a scoped permission response without revising an approved goal."""
    request = state.get("user_request", {})
    # A blocked execution checkpoint can ask for a scoped retry while keeping
    # the approved goal intact. Only the explicit no-scope-change form uses
    # this path; substantive blocker answers still require a refreshed draft.
    if not is_operational_response(request) or not approved(state):
        raise ValueError("A permission response requires the current approved goal contract")
    matches = [q for q in state.get("pending_questions", []) if q["id"] == question_id]
    if len(matches) != 1 or question_id in state.get("answers", {}) or not text.strip():
        raise ValueError("Permission response must address one unresolved question with nonempty text")
    q = matches[0]
    event = {"kind": "permission_answer", "actor": "user_cli", "at": s.now(),
             "question_id": question_id, "question": q, "text": text,
             "request": copy.deepcopy(request),
             "contract_token": token(state["goal_contract"])}
    state.setdefault("user_events", []).append(event)
    state.setdefault("answers", {})[question_id] = event
    state["pending_questions"] = [row for row in state["pending_questions"] if row["id"] != question_id]
    state.pop("user_request", None)
    state.pop("permission_reuse_context", None)
    if not state["pending_questions"]:
        state.update(status="RUNNING", phase="READY_TO_EXECUTE")


def resolve_passing_checkpoint(state, question_id, text):
    """Record the user's explicit reconciliation choice for a now-passing gate."""
    request = state.get("user_request") or {}
    questions = state.get("pending_questions") or []
    choices = request.get("options") or []
    if (state.get("status") != "WAITING_FOR_USER" or state.get("next_stage") != "astra_review"
            or request.get("kind") != "blocker" or request.get("proposed_delta")
            or len(questions) != 1 or questions[0].get("id") != question_id
            or not choices or text != choices[0]
            or not choices[0].startswith("Reconcile ")
            or ("Sol evidence" not in choices[0] and "Validator evidence" not in choices[0])
            or question_id in state.get("answers", {}) or not approved(state)):
        raise ValueError("Checkpoint reconciliation requires the explicit saved choice and approved goal")
    description = str(request.get("discovered", "")).lower()
    if not all(word in description for word in ("checkpoint", "evidence")) or not ("sol" in description or "validator" in description):
        raise ValueError("The pending request is not a Validator milestone checkpoint")
    current = s.snapshot(Path(state["workspace"]))
    if not checkpoints.evidence_ready(state, current) or missing_human_reviews(state):
        raise ValueError("Current independent evidence or a required human review is still missing")
    event = {"kind": "checkpoint_answer", "actor": "user_cli", "at": s.now(),
             "question_id": question_id, "question": questions[0], "text": text,
             "contract_token": token(state["goal_contract"]),
             "validation_source_revision": state["validation"]["source_revision"]}
    state.setdefault("user_events", []).append(event)
    state.setdefault("answers", {})[question_id] = event
    state.update(status="RUNNING", phase="READY_TO_EXECUTE", pending_questions=[])
    state.pop("user_request", None)
    state.pop("milestone_blocker", None)


def wait_for_user(state, request, *, origin=None, evidence=None, next_stage=None):
    if not request["decision_needed"].strip() or not request["impact"].strip():
        raise ValueError("A user request needs the smallest decision and its impact")
    # Reuse an exact, authenticated decision, not a guessed semantic match or
    # blanket authorization. Denials and qualified answers must also be honored.
    if request.get("kind") == "permission" and approved(state):
        for answer_id, answer in state.get("answers", {}).items():
            if (answer.get("kind") != "permission_answer"
                    or answer.get("actor") != "user_cli"
                    or answer not in state.get("user_events", [])
                    or answer.get("contract_token") != token(state["goal_contract"])
                    or answer.get("request") != request):
                continue
            reused = state.setdefault("permission_reuses", [])
            key = {"answer_id": answer_id, "contract_token": answer["contract_token"]}
            if key in reused:
                raise s.Paused("PAUSED_PERMISSION_RECONCILIATION",
                    f"The decision in saved answer {answer_id} was already returned to the Completion Owner. "
                    "It must honor that answer rather than request the same permission again.")
            reused.append(key)
            state["permission_reuse_context"] = {
                **key, "request": copy.deepcopy(request), "answer": answer["text"],
                "instruction": "This exact decision was already answered. Honor the saved answer, including "
                    "any denial or conditions. It does not authorize broader scope. Continue within the answer "
                    "or explain a materially different unresolved decision; do not ask this question again."}
            state.pop("user_request", None)
            state.pop(human.PRIVATE, None)
            state.pop(human.PUBLIC, None)
            state.update(status="RUNNING", phase="READY_TO_EXECUTE",
                         pending_questions=[], next_stage="astra_review")
            return
    state.pop("permission_reuse_context", None)
    request = copy.deepcopy(request)
    origin = origin or {"stage": "goal_request"}
    evidence = copy.deepcopy(evidence or {})
    output = origin.get("output")
    if output and Path(output).is_file():
        evidence.setdefault("hashes", {})[output] = s.file_hash(Path(output))
    question = {"id": "decision-" + uuid.uuid4().hex[:12], "question": request["decision_needed"],
                "why": request["impact"], "options": request["options"], "proposed_default": ""}
    review_criteria = requested_review_criteria(state, request)
    if review_criteria:
        question.update(review_criteria=review_criteria, review_token=review_token(state))
        evidence.update(review_token=review_token(state), criteria=review_criteria)
        evidence.setdefault("hashes", {}).update(state.get("validation", {}).get("evidence_hashes", {}))
        if request.get("kind") == "human_review":
            request["criteria"] = review_criteria
    scope = request["kind"] if request["kind"] in ("permission", "goal_change", "human_review") else "blocker"
    human.queue(state, scope, origin, request=request, questions=[question], evidence=evidence,
                next_stage=next_stage)


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
    previous_batch = state.get("current_task", {}).get("milestone_ids", [])
    allowed = (set(c for mid in previous_batch for c in milestones[mid]["acceptance_criteria"])
               if spec["milestone_id"] in previous_batch else
               set(milestones.get(spec["milestone_id"], {}).get("acceptance_criteria", [])))
    if milestones and (spec["milestone_id"] not in milestones or
            not set(ids) <= allowed):
        raise ValueError("Task must belong to an approved milestone and its acceptance criteria")
    # The named milestone's contract-declared paths are authoritative ownership:
    # merge them into the task so a planner that names only part of the scope
    # cannot make the builder's contract-legal work look out-of-scope.
    task_paths = list(decision.get("affected_paths", []))
    if task_paths and milestones and spec["milestone_id"] not in previous_batch:
        owned = milestones.get(spec["milestone_id"], {}).get("affected_paths", [])
        task_paths = list(dict.fromkeys(task_paths + owned))
    recovery = state.get("recovery_context") or {}
    previous_task = state.get("current_task") or {}
    if (spec["kind"] == "implement" and recovery.get("timeout_kind")
            and recovery.get("task_id") and recovery["task_id"] == previous_task.get("id")
            and recovery.get("execution_limits")):
        limits = state.get("settings", {}).get("limits", {})
        defaults = {"stage_timeout_seconds": None, "idle_timeout_seconds": 300, "tool_timeout_seconds": 1800}
        failed_limit = {"tool": "tool_timeout_seconds", "idle": "idle_timeout_seconds",
                        "stage": "stage_timeout_seconds"}.get(recovery["timeout_kind"])
        recorded_limits = recovery["execution_limits"]
        compared_limits = ({failed_limit: recorded_limits[failed_limit]}
                           if failed_limit in recorded_limits else recorded_limits)
        same_limits = all(limits.get(key, defaults.get(key)) == value
                          for key, value in compared_limits.items())
        candidate = {**spec, "objective": decision["next_objective"], "affected_paths": decision["affected_paths"]}
        if same_limits and checkpoints.approach(candidate) == checkpoints.approach(previous_task):
            raise ValueError("The timed-out task needs a changed execution plan before another writer; "
                             "the task and timeout limits are unchanged. Preserve completed work and "
                             "split the remaining work or address the diagnosed stall.")
    if checkpoints.enabled(state):
        checkpoints.carryforward.before_assignment(state, spec, decision)
    checkpoints.before_assignment(state, decision, current)
    # After before_assignment, so a milestone accepted while advancing counts.
    checkpoints.require_prerequisites(state, spec["milestone_id"])
    if state.get("current_task"):
        state.setdefault("task_archive", []).append(copy.deepcopy(state["current_task"]))
    contract = state["goal_contract"]
    state["current_task"] = {**copy.deepcopy(spec), "id": "task-" + uuid.uuid4().hex[:12],
        "objective": decision["next_objective"], "affected_paths": task_paths,
        "contract_revision": contract["revision"], "contract_hash": contract["hash"],
        "assigned_at": s.now(), "source_revision": current["revision"], "decision": decision["status"]}
    if spec["milestone_id"] in previous_batch:
        # Rework stays accountable for every member of the integrated wave.
        state["current_task"]["milestone_ids"] = previous_batch
        state["current_task"]["acceptance_criteria"] = list(dict.fromkeys(
            cid for mid in previous_batch for cid in milestones[mid]["acceptance_criteria"]))
    findings.assign(state, state["current_task"], spec, decision)
    if checkpoints.enabled(state):
        checkpoints.progress(state)["rejected_advances"] = 0
        state.pop("milestone_blocker", None)
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
            if c["human_review"] and not review_binding_valid(state, c["id"], current)]


def legacy_review_acceptance(state, criterion, answer_id):
    """Return an authenticated older answer that explicitly accepted a review criterion."""
    answer = state.get("answers", {}).get(answer_id)
    if not isinstance(answer, dict) or answer not in state.get("user_events", []):
        return None
    question = answer.get("question") or {}
    if not isinstance(question, dict):
        return None
    options = question.get("options") or []
    if (answer.get("kind") != "permission_answer" or answer.get("actor") != "user_cli"
            or answer.get("question_id") != answer_id or question.get("id") != answer_id
            or answer.get("contract_token") != token(state["goal_contract"])
            or not isinstance(answer.get("at"), str)
            or not isinstance(options, list) or len(options) != 2
            or not isinstance(options[0], str) or not options[0].startswith(f"Accept {criterion}:")
            or not isinstance(options[1], str) or not options[1].startswith(f"Reject {criterion}:")
            or not isinstance(answer.get("text"), str)
            or not answer["text"].startswith(f"Accept {criterion}.")):
        return None
    return answer


def preserved_review_answers(state, criterion, original):
    """Find later authenticated instructions carrying the old acceptance forward."""
    result = {}
    for answer_id, answer in state.get("answers", {}).items():
        if (not isinstance(answer, dict) or answer not in state.get("user_events", [])
                or answer.get("kind") != "permission_answer" or answer.get("actor") != "user_cli"
                or answer.get("contract_token") != token(state["goal_contract"])
                or not isinstance(answer.get("at"), str) or answer["at"] <= original["at"]):
            continue
        response = answer.get("text")
        if not isinstance(response, str):
            continue
        if f"existing {criterion} acceptance" in response and "do not request another human visual approval" in response.lower():
            result[answer_id] = answer
    return result


def review_binding_valid(state, criterion, current):
    if not current:
        return False
    binding = state.get("human_reviews", {}).get(criterion)
    if (not isinstance(binding, dict) or binding.get("token") != current
            or binding.get("criterion") != criterion or binding not in state.get("user_events", [])):
        return False
    if binding.get("kind") == "human_review":
        return binding.get("actor") == "user_cli"
    if binding.get("kind") != "review_reconciliation" or binding.get("actor") != "runner":
        return False
    original = legacy_review_acceptance(state, criterion, binding.get("answer_id"))
    if not original or binding.get("answer_hash") != s.digest(original):
        return False
    preserved = preserved_review_answers(state, criterion, original)
    receipts = binding.get("preservation_hashes") or {}
    return bool(receipts) and all(
        answer_id in preserved and s.digest(preserved[answer_id]) == digest
        for answer_id, digest in receipts.items())


def reconcile_legacy_review(state, criterion, answer_id, selected, current):
    """Bind an existing user acceptance to current evidence without a new approval."""
    request = state.get("user_request") or {}
    pending = state.get("pending_questions") or []
    required = {c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"] if c["human_review"]}
    if (criterion not in required or state.get("status") != "WAITING_FOR_USER"
            or len(pending) != 1 or pending[0].get("question") != request.get("decision_needed")
            or request.get("kind") != "blocker" or criterion not in request.get("decision_needed", "")
            or "reconcile" not in request.get("decision_needed", "").lower()
            or not request.get("proposed_delta", "").startswith("No contract, criterion, source or permission change.")
            or selected != state.get("displayed_review") or selected != review_token(state)):
        raise ValueError("No exact legacy review reconciliation is pending")
    execution_guard(state)
    val = state.get("validation") or {}
    if (val.get("verdict") != "PASS" or val.get("source_revision") != current["revision"]
            or val.get("contract_revision") != state["goal_contract"]["revision"]
            or val.get("contract_hash") != state["goal_contract"]["hash"]
            or val.get("criteria_revision") != state.get("criteria_revision")
            or (state.get("current_task") and val.get("task_id") != state["current_task"]["id"])
            or (state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled")
                and val.get("reviewer_role") != "sol")
            or not val.get("checks") or any(check.get("exit_code") != 0 for check in val["checks"])
            or val.get("findings") or val.get("unverified_criteria")
            or not any(row.get("id") == criterion and row.get("status") == "PASS" and row.get("evidence_refs")
                       for row in val.get("criterion_results", []))
            or not val.get("evidence_hashes") or any(
                not Path(path).is_file() or s.file_hash(path) != digest
                for path, digest in val["evidence_hashes"].items())):
        raise ValueError("Review reconciliation requires current passing independent evidence")
    original = legacy_review_acceptance(state, criterion, answer_id)
    preserved = preserved_review_answers(state, criterion, original) if original else {}
    if not original or not preserved:
        raise ValueError("No authenticated acceptance and carry-forward instruction match this criterion")
    binding = {"kind": "review_reconciliation", "actor": "runner", "at": s.now(),
               "criterion": criterion, "token": selected, "answer_id": answer_id,
               "answer_hash": s.digest(original),
               "preservation_hashes": {key: s.digest(value) for key, value in preserved.items()}}
    state.setdefault("user_events", []).append(binding)
    state.setdefault("human_reviews", {})[criterion] = binding
    if missing_human_reviews(state):
        raise ValueError("Existing acceptance did not satisfy the current review gate")
    state["pending_questions"] = []
    state.pop("user_request", None)
    state.update(status="RUNNING", phase="READY_TO_EXECUTE", next_stage="astra_review")


def requested_review_criteria(state, request):
    """Bind a review question to the existing artifact token and required IDs."""
    if not approved(state) or not review_token(state):
        return []
    required = {c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"] if c["human_review"]}
    if request.get("kind") == "human_review":
        claimed = request.get("criteria", [])
        return sorted(set(claimed)) if claimed and set(claimed) <= required else []
    # Older SQL runs expressed an artifact review as a permission question.
    # Only recognize this exact review wording with no requested scope change.
    if request.get("kind") == "permission" and not request.get("proposed_delta"):
        match = re.fullmatch(r"Approve or reject ([A-Za-z0-9_.-]+) based on [^\n]+\.",
                             request.get("decision_needed", ""))
        if match and match[1] in required:
            return [match[1]]
    return []


def human_only_pending_validation(state, validation, criterion):
    """A complete technical review whose only missing results are human acceptance.

    Any number of human-review criteria may be pending together, provided
    every technical criterion passes with evidence and the pending set is
    exactly the human set (a single pending criterion remains the common case).
    """
    criteria = state["goal_contract"]["body"]["acceptance_criteria"]
    human = {row["id"] for row in criteria if row["human_review"]}
    rows = validation.get("criterion_results", [])
    results = {row["id"]: row for row in rows}
    pending_ids = {entry.split(":", 1)[0].split(" ", 1)[0]
                   for entry in validation.get("unverified_criteria", [])}
    if (criterion not in human or not human
            or set(results) != {row["id"] for row in criteria} or len(rows) != len(criteria)
            or validation.get("verdict") != "BLOCKED" or not pending_ids or pending_ids != human
            or validation.get("findings") or validation.get("end_to_end_result", {}).get("status") != "PASS"):
        return False
    return all(row.get("evidence_refs") and
               row.get("status") == ("NOT_VERIFIED" if cid in human else "PASS")
               for cid, row in results.items())


def approve_review(state, criterion, selected, current):
    execution_guard(state)
    val = state.get("validation", {})
    human_only_gap = human_only_pending_validation(state, val, criterion)
    if (selected != review_token(state) or selected != state.get("displayed_review")
            or val.get("source_revision") != current["revision"]
            or (val.get("verdict") != "PASS" and not human_only_gap)
            or val.get("contract_revision") != state["goal_contract"]["revision"]
            or val.get("contract_hash") != state["goal_contract"]["hash"]
            or val.get("criteria_revision") != state.get("criteria_revision")
            or (state.get("current_task") and val.get("task_id") != state["current_task"]["id"])
            or not val.get("evidence_hashes") or not val.get("checks")
            or any(c["exit_code"] != 0 for c in val["checks"])
            or any(f.get("blocking", True) for f in val.get("findings", []))
            or not (human_only_gap or any(c["id"] == criterion and c["status"] == "PASS" and c["evidence_refs"]
                       for c in val.get("criterion_results", [])))
            or any(not Path(p).is_file() or s.file_hash(p) != h
                   for p, h in val.get("evidence_hashes", {}).items())):
        raise ValueError("Human approval needs the displayed, current validated artifact")
    required = {c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"] if c["human_review"]}
    if criterion not in required:
        raise ValueError("No such required human-review criterion")
    request = state.get("user_request") or {}
    requested = set(requested_review_criteria(state, request))
    previous = state.get("human_reviews", {}).get(criterion)
    if previous and previous.get("token") == selected and previous in state.get("user_events", []):
        event = previous
    else:
        event = {"kind": "human_review", "actor": "user_cli", "at": s.now(), "criterion": criterion, "token": selected}
        state.setdefault("user_events", []).append(event)
        state.setdefault("human_reviews", {})[criterion] = event
    missing = set(missing_human_reviews(state))
    pending = []
    closed_review_question = False
    for question in state.get("pending_questions", []):
        bound = set(question.get("review_criteria", [])) if question.get("review_token") == selected else set()
        # Reconcile a question saved before review bindings existed only when
        # it is exactly the current request for this criterion and artifact.
        if not bound and requested and question.get("question") == request.get("decision_needed"):
            bound = requested
        if criterion in bound:
            closed_review_question = True
            continue
        pending.append(question)
    state["pending_questions"] = pending
    if requested and criterion in requested and not requested.intersection(missing):
        state.pop("user_request", None)
    if (requested or closed_review_question) and not pending and \
            state.get("status") == "WAITING_FOR_USER" and not state.get("user_request"):
        state.update(status="RUNNING", phase="READY_TO_EXECUTE", next_stage="astra_review")


DECISION_PROVENANCE = """Decision provenance is mandatory for every contract:
- Explicit requirements and corrections inside task/original conversation context use
  basis=original_request with answer_id="". Conversation IDs and message labels are not saved feedback IDs.
- Use basis=user_answer only for IDs present in saved_answers/answers; delegated
  requires a saved answer explicitly marked delegated.
- Use basis=user_feedback only for an exact saved brief_feedback event ID that also
  exists in user_events. If no such event exists, do not use user_feedback.
- Your suggestions and model-written drafts are basis=agent_proposed, answer_id="";
  they are not user approval. Never invent an answer ID or a feedback event.
- delegated_decisions may contain ONLY basis=delegated rows tied to actual saved
  delegated answers. Otherwise return delegated_decisions=[]. Put proposed defaults
  (layout, file structure, question counts, etc.) in accepted_assumptions with
  basis=agent_proposed, not in delegated_decisions.
"""

CONTRACT_REFERENCES = """Contract reference rules:
Define acceptance_criteria as objects with stable IDs (for example AC1, AC2).
For each covered requirement_trace entry, evidence may be an exact required_behaviors
string, an exact acceptance criterion ID, or a short explanation citing a defined
criterion ID as a case-sensitive whole token (for example 'AC1 verifies this').
'AC1' does not match 'AC10', 'XAC1', or 'ac1'. Unknown IDs from a defined ID
family, including a mixture such as 'AC1 and AC99', do not establish coverage.
For superseded requirements, evidence must cite an exact saved answer or feedback
event ID as a whole token; an explanation around that ID is allowed. Retain the
replacement requirement as covered. Do not mark both sides superseded just to
resolve a conflict. A question-free plan may resolve a two-sided conflict by
superseding the old side with a saved correction and covering the replacement.
A superseded trace does not neutralize a contradictory active required_behaviors entry:
reword that entry using the saved correction and an exact contract_changes record.
Keep the old wording in the historical handoff, not as an unconditional active rule.
If saved user feedback or an answer already settles a handoff conflict, record it
in conflict_resolutions: the exact requirement_ids of that conflict, basis
(user_answer or user_feedback), its saved answer_id, a source_quote copied verbatim
from that event, and a substantive resolution explaining how it settles the conflict.
Keep valid requirements covered in requirement_trace; they need not all be superseded.
Preserve these resolutions in later planner/reviewer reports. Use [] when none apply.
An explicit resolution may cite a conflict recorded in requirements_history after a
refreshed handoff removes it, but only when all its requirement IDs still have the
same verbatim source quotes. Do not transfer a resolution to reused or changed IDs.
If no matching current or historical conflict exists, retain the saved decision in
accepted_assumptions instead; an empty conflict_resolutions list then is valid.
Agent assumptions and unrelated user events cannot resolve a conflict. Carry genuinely
unresolved conflicts into open_blocking_questions; do not ask again for a saved decision.
When revising a plan after review, copy required_behaviors, scope_exclusions,
constraints, important_failure_cases, acceptance_criteria (including verification
methods), and permission_boundaries verbatim from goal_contract.body. Add new
items when review identifies a gap; revise technical_approach, milestones, paths,
tests and dependencies as needed. Do not rewrite an existing protected item for
style or detail. A changed or removed protected item requires a saved user answer
or feedback event and an exact contract_changes entry naming the previous item.
Use contract_changes=[] when those protected fields are unchanged. Reviewer
concerns and agent proposals are not saved user authorization.
Each milestones[].acceptance_criteria must contain ONLY those existing ID strings,
for example ["AC1", "AC2"], never descriptions of checks or shell commands.
Every milestone needs a nonempty objective and at least one acceptance criterion ID;
together the milestones must cover all defined acceptance criteria.
Exception for clarification-only discovery: while blocking questions remain,
technical_approach=[] and milestones=[]; retain known requirements and questions.
Do not invent an implementation to fill those arrays before scope is settled.
Set depends_on on EVERY milestone. Use [] when it can start independently from
the same approved contract, and IDs of prerequisite milestones otherwise.
Check shared interfaces, ownership and validation boundaries before declaring
milestones independent. The dependency graph must have no cycles.
Declare affected_paths for every milestone as literal repository-relative files or
directories covering all writes, including tests. Do not use globs, parent paths,
or repository-wide '.'. Shared writes or interface/read dependencies need ordering
edges; disjoint writes alone do not establish semantic independence. If ownership
cannot be established, use [] for affected_paths; the Orchestrator will run it serially.
initial_task must target a milestone with depends_on []; later tasks may start a
milestone only after all of its depends_on milestones are accepted.
Put the check descriptions in acceptance_criteria[].criterion and verification_method.
"""


DISCOVERY_PROMPT = """You are the Requirements Gatherer, the product lead, technical planner and final reviewer.
The Builder implements. The Validator independently validates. First help the user define what to build.
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
technical_approach; and substantial, coherent milestones with IDs, objectives and acceptance_criteria
IDs. Every required criterion must belong to at least one milestone. Include depends_on
on each milestone: [] for work that can begin independently, or prerequisite milestone IDs.
Use source evidence and shared interface decisions to justify independent work; put
unresolved boundaries in the blocking questions rather than guessing.
Return the complete revised contract, including at most three open blocking questions.
The user can send feedback to revise your draft. Use that feedback without inventing
answers; ask a focused follow-up if a consequential decision is still unresolved.
An empty question list asks the runner to present the actual brief for explicit approval,
never to execute. Do not start implementation before that approval.
"""

EXECUTION_PROMPT = """
The EXACT approved goal below controls scope and success. Echo its revision and hash.
Read saved_answers before raising any permission or scope question. A recorded user
answer remains authoritative within its stated scope; cite its answer ID and proceed
when it already covers the work. Preserve denials, conditions and explicit exclusions.
Routine reversible implementation and test-harness corrections needed for the approved
outcome do not need a new approval unless they cross an explicit boundary. Do not turn
each discovered repair into a separate permission request. Diagnose within authorized
scope first; when a real boundary remains, present the concrete minimal scope delta.
Restate acceptance_criteria entries byte-identical from the contract (same ids, criterion
text, verification methods, human_review flags); any rewording is rejected as a criteria
change. Do not weaken criteria, change required behavior or expand scope. The Plan Reviewer may change the
plan inside the goal; a validation task sends preserved implementation straight to the Validator.
The Builder implements only the authorized batch. The Validator validates the actual current artifact
and reports evidence for every criterion and an explicit blocking flag on each finding.
Echo current_task.id as task_id, or the empty string when no task exists yet.
A CONTINUE/REWORK next_task must set milestone_id to an approved milestone id whose
acceptance_criteria list contains every criterion id the task cites; requirements,
acceptance_criteria and validation_plan must all be nonempty.
Human approvals come only from runner user events. Tests alone do not prove behaviors
they do not cover. Unknown/untested/skipped is NOT_VERIFIED, never PASS.
Put optional improvements in deferred_backlog; they cannot delay completion.
If a material ambiguity, contradiction, infeasible constraint, permission need or goal
change appears, STOP at a safe checkpoint and set user_request with the discovery,
impact, smallest decision, options/consequences and proposed contract delta. Do not
continue on an assumed answer. The Builder and Validator send that request to the Plan Reviewer; the Plan Reviewer decides
whether a user decision is needed and presents it with BLOCKED. With no user decision needed use kind=none and empty
strings/lists. Correct an incorrect test only with a documented goal-consistent reason.
"""
