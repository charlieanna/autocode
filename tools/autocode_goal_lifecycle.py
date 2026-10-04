"""The goal lifecycle: install a drafted contract, present it, approve it, assign its tasks, and ask the
user when a decision is needed.

These steps drive the milestone, finding, dispatch, planning and Resolver-request machinery. The
contract itself (schemas, tokens, validation, invalidation) is autocode_goals, which imports nothing
from here, so a module that only reads a contract does not pull that machinery in. See AGENTS.md.
"""
from __future__ import annotations

import copy

try:
    from . import autocode_draft_examples as examples
except ImportError:
    import autocode_draft_examples as examples
import difflib
import json
from pathlib import Path
import re
import uuid

SUPPORTED_VERSION = 3

try:
    from . import autocode_util as s, autocode_workflows as workflows, autocode_milestones as checkpoints
    from . import autocode_findings as findings, autocode_resolver_human as human, autocode_verification_plan as verification_plan
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_progressive_state as progressive_state
    from .autocode_goals import (
        BODY_SCHEMA, BRIEF_FIELDS, LEGACY_BODY_SCHEMA, PLANNING_BODY_SCHEMA, approved, check_delegable,
        handoff_ref, initial_decision, invalidate, missing_human_reviews, open_obligations,
        plan_preview, record_decision, requested_review_criteria, review_token, revision_guard,
        sealed, start_clarification_episode, token, validate_requirements_body)
except ImportError:
    import autocode_util as s, autocode_workflows as workflows, autocode_milestones as checkpoints
    import autocode_findings as findings, autocode_resolver_human as human, autocode_verification_plan as verification_plan
    import autocode_adaptive_planning as adaptive
    import autocode_progressive_state as progressive_state
    from autocode_goals import (
        BODY_SCHEMA, BRIEF_FIELDS, LEGACY_BODY_SCHEMA, PLANNING_BODY_SCHEMA, approved, check_delegable,
        handoff_ref, initial_decision, invalidate, missing_human_reviews, open_obligations,
        plan_preview, record_decision, requested_review_criteria, review_token, revision_guard,
        sealed, start_clarification_episode, token, validate_requirements_body)


def validate_body(state, body, *, ready=False, allow_legacy=False):
    legacy = allow_legacy and not any(key in body for key in BRIEF_FIELDS)
    s.validate_schema(body, PLANNING_BODY_SCHEMA if "initial_task" in body else LEGACY_BODY_SCHEMA if legacy else BODY_SCHEMA)
    if "initial_task" in body and not (body["initial_task"]["kind"] == "none" and body["open_blocking_questions"]):
        first = body["initial_task"]
        verification_plan.require_scaffolding(state.get("workspace"), first["affected_paths"], first["validation_plan"])
        # This is a structural draft probe, not execution admission. The real
        # assignment below approval authenticates the progressive disclosure.
        probe_body = copy.deepcopy(body)
        probe_body["constraints"] = [line for line in probe_body.get("constraints", [])
                                     if not line.startswith(progressive_state.rules.DISCLOSURE_DELEGATION)]
        probe = {"goal_contract": {"body": probe_body, "revision": 0, "hash": "draft"}}
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
        verification_plan.require_scaffolding(state.get("workspace"), milestone.get("affected_paths", []),
            [row["verification_method"] for row in criteria if row["id"] in covered])
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


def install_draft(state, body, *, origin, allow_legacy=False, changes=None, record=None, queue_human=True):
    validate_body(state, body, allow_legacy=allow_legacy)
    changes = revision_guard(progressive_state.planning_revision_state(state), body, changes or [], origin)
    progressive_state.finish_draft(state)
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


def require_supported_checkpoint(state):
    """Refuse a checkpoint this runner cannot safely interpret (#338).

    ``version`` is the state format. A number above SUPPORTED_VERSION was written
    by a newer AutoCode; migrating or resuming it here can invent a launch. The
    registry already classifies those as ``checkpoint_unsupported``.
    """
    version = state.get("version", 2)
    if type(version) is not int or isinstance(version, bool) or version < 1:
        raise s.Paused("PAUSED_UNSUPPORTED_CHECKPOINT",
                       f"Checkpoint version {version!r} is malformed; expected a positive integer")
    if version > SUPPORTED_VERSION:
        raise s.Paused("PAUSED_UNSUPPORTED_CHECKPOINT",
                       f"Checkpoint version {version} is from a newer AutoCode than this runner "
                       f"(supports up to {SUPPORTED_VERSION}); upgrade AutoCode or use a matching checkpoint")


def migrate(state, *, fresh=False):
    """Call only at a saved, idle boundary. Preserve artifacts and execution history.
    A fresh run (``fresh``) first recognizes what kind of job the request is."""
    require_supported_checkpoint(state)
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


def render(state):
    contract = state.get("goal_contract")
    if not contract:
        if state.get("settings", {}).get("planning_flow") == "v2":
            public = human.current(state)
            questions = public["questions"] if public else human.internal_questions(state)
            lines = ["No contract yet; resume with Requirements."]
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
    if workflows.approval_note(state):
        lines += ["", workflows.approval_note(state)]
    if state.get("discovery_summary"):
        lines += ["", "Planning: " + state["discovery_summary"]]
    lines += plan_preview(state)
    if body.get("task_kind") == "bugfix":
        lines += ["", "Job type: bug fix. Before completion the runner itself checks that a new or changed",
                  "test fails on the original code and passes with the fix, and that no test that",
                  "passed before now fails."]
    elif "task_kind" in body:
        lines += ["", "Job type: build (not a bug fix). Criteria verified by \"test: ...\" are proven by the runner at their milestone and at completion: each named test must pass with the change and not without it."]
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
    lines += examples.review_notes(state)
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
        lines += adaptive.review_notes(state["planning"])
    review = review_token(state) if public else None
    if review:
        lines += ["", f"Review token (current validated artifact): {review}",
                  "Validation: " + json.dumps(state["validation"], indent=2)]
    lines += ["", f"State: {state.get('phase')} / {state['status']}"]
    return "\n".join(lines)


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
    # Assignment/eligibility may still reject after seal preflight. Publish no
    # approval, retirement or active pointer unless the whole boundary succeeds.
    candidate = copy.deepcopy(state)
    _approve(candidate, selected)
    state.clear()
    state.update(candidate)


def _approve(state, selected):
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
    prepared_progressive = progressive_state.prepare_seal(state, selected)
    event = {"kind": "goal_approval", "actor": "user_cli", "at": s.now(), "token": selected}
    state.setdefault("user_events", []).append(event)
    contract.update(approval_status="approved", approval_event=event)
    progressive_state.seal(state, selected, prepared=prepared_progressive)
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
                     next_stage="sol" if kind == "validate" else
                                "terra" if progressive_state.enabled(state) else dispatch.build_stage(state))
        record_decision(state, decision)


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
    request = checkpoints.route_review_only_request(state, request, origin)
    if request is None:
        return
    if not request["decision_needed"].strip() or not request["impact"].strip():
        raise ValueError("A user request needs the smallest decision and its impact")
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
                    f"The decision in saved answer {answer_id} was already returned to the Completion Reviewer. "
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


def assign_task(state, decision, current):
    """Persist one bounded handoff tied to the approved brief, not a second brief."""
    spec = decision.get("next_task")
    if spec is None and "milestones" not in state["goal_contract"]["body"]:
        # A recovered old-format stage still uses its original handoff.
        return "implement"
    if not spec or spec["kind"] not in ("implement", "validate"):
        raise ValueError("CONTINUE or REWORK requires a concrete next task")
    if decision["status"] == "REWORK" and not decision["evidence"]:
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
    if spec["kind"] == "validate":
        allowed |= checkpoints.recheckable(state)
    if milestones and (spec["milestone_id"] not in milestones or
            not set(ids) <= allowed):
        raise ValueError("Task must belong to an approved milestone and its acceptance criteria")
    # The named milestone's contract-declared paths are authoritative ownership:
    # merge them into the task so a planner that names only part of the scope
    # cannot make the builder's contract-legal work look out-of-scope.
    task_paths = list(decision.get("affected_paths", []))
    if task_paths and milestones and spec["milestone_id"] not in previous_batch and not progressive_state.enabled(state):
        owned = milestones.get(spec["milestone_id"], {}).get("affected_paths", [])
        task_paths = list(dict.fromkeys(task_paths + owned))
    progressive_state.guard_assignment(state, spec, task_paths)
    verification_plan.require_scaffolding(state.get("workspace"), task_paths, spec["validation_plan"])
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
    progressive_state.bind_task(state)
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
