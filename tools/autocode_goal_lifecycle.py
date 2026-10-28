"""The goal lifecycle: install a drafted contract, present it, approve it, assign its tasks, and ask the
user when a decision is needed.

These steps drive the milestone, finding, dispatch, planning and Resolver-request machinery. The
contract itself (schemas, tokens, validation, invalidation) is autocode_goals, which imports nothing
from here, so a module that only reads a contract does not pull that machinery in. See AGENTS.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_component_plan as component_plan
    from . import autocode_roles as roles
    from . import autocode_source_scope as source_scope
    from . import autocode_technical_questions as technical_questions
except ImportError:
    import autocode_component_plan as component_plan
    import autocode_roles as roles
    import autocode_source_scope as source_scope
    import autocode_technical_questions as technical_questions


import copy

try:
    from . import autocode_base_examples as base_examples
    from . import autocode_draft_examples as examples
except ImportError:
    import autocode_base_examples as base_examples
    import autocode_draft_examples as examples
import difflib
import json
import re
import uuid
from pathlib import Path

try:
    from . import autocode_interaction_timing as interaction_timing
except ImportError:
    import autocode_interaction_timing as interaction_timing

SUPPORTED_VERSION = 3

try:
    from . import autocode_adaptive_planning as adaptive
    from . import autocode_approval_view as approval_view
    from . import autocode_brief_obligations as brief_obligations
    from . import autocode_criterion_changes as criterion_changes
    from . import autocode_current_behavior as current_behavior
    from . import autocode_design_plan as design_plan
    from . import autocode_finding_rescope as finding_rescope
    from . import autocode_findings as findings
    from . import autocode_goal_file as goal_file
    from . import autocode_milestones as checkpoints
    from . import autocode_native_test_names as native_test_names
    from . import autocode_operational_change as operational_change
    from . import autocode_plan_chat as plan_chat
    from . import autocode_plan_retention as plan_retention
    from . import autocode_planning_cycle as planning_cycle
    from . import autocode_progressive_state as progressive_state
    from . import autocode_proof_declarations as proof_declarations
    from . import autocode_recovery_context as recovery_context
    from . import autocode_resolver_human as human
    from . import autocode_resolver_recommendation as recommendation
    from . import autocode_risk_obligations as risk_obligations
    from . import autocode_source_log as source_log
    from . import autocode_task_feasibility as task_feasibility
    from . import autocode_test_cases as test_cases
    from . import autocode_trace_coverage as trace_coverage
    from . import autocode_util as s
    from . import autocode_verification_plan as verification_plan
    from . import autocode_workflows as workflows
    from .autocode_goals import (
        BODY_SCHEMA,
        BRIEF_FIELDS,
        LEGACY_BODY_SCHEMA,
        PLANNING_BODY_SCHEMA,
        approved,
        check_delegable,
        handoff_ref,
        initial_decision,
        invalidate,
        missing_human_reviews,
        open_obligations,
        plan_preview,
        record_decision,
        requested_review_criteria,
        review_token,
        revision_guard,
        sealed,
        start_clarification_episode,
        token,
        validate_requirements_body,
    )
except ImportError:
    import autocode_adaptive_planning as adaptive
    import autocode_approval_view as approval_view
    import autocode_brief_obligations as brief_obligations
    import autocode_criterion_changes as criterion_changes
    import autocode_current_behavior as current_behavior
    import autocode_design_plan as design_plan
    import autocode_finding_rescope as finding_rescope
    import autocode_findings as findings
    import autocode_goal_file as goal_file
    import autocode_milestones as checkpoints
    import autocode_native_test_names as native_test_names
    import autocode_operational_change as operational_change
    import autocode_plan_chat as plan_chat
    import autocode_plan_retention as plan_retention
    import autocode_planning_cycle as planning_cycle
    import autocode_progressive_state as progressive_state
    import autocode_proof_declarations as proof_declarations
    import autocode_recovery_context as recovery_context
    import autocode_resolver_human as human
    import autocode_resolver_recommendation as recommendation
    import autocode_risk_obligations as risk_obligations
    import autocode_source_log as source_log
    import autocode_task_feasibility as task_feasibility
    import autocode_test_cases as test_cases
    import autocode_trace_coverage as trace_coverage
    import autocode_util as s
    import autocode_verification_plan as verification_plan
    import autocode_workflows as workflows
    from autocode_goals import (
        BODY_SCHEMA,
        BRIEF_FIELDS,
        LEGACY_BODY_SCHEMA,
        PLANNING_BODY_SCHEMA,
        approved,
        check_delegable,
        handoff_ref,
        initial_decision,
        invalidate,
        missing_human_reviews,
        open_obligations,
        plan_preview,
        record_decision,
        requested_review_criteria,
        review_token,
        revision_guard,
        sealed,
        start_clarification_episode,
        token,
        validate_requirements_body,
    )


def validate_body(state: RunState, body, *, ready=False, allow_legacy=False, origin=None):
    """Refuse a contract body this run cannot hold. ``origin`` is the draft's (install_draft's, or the saved
    contract's at approval): the user's own edit is not held to the Go test names read from their brief."""
    legacy = allow_legacy and not any(key in body for key in BRIEF_FIELDS)
    schema = PLANNING_BODY_SCHEMA if "initial_task" in body else LEGACY_BODY_SCHEMA if legacy else BODY_SCHEMA
    s.validate_schema(body, design_plan.body_schema(schema, (state.get("settings") or {}).get("design_manifest")))
    component_plan.validate(
        state.get("task", ""), (state.get("settings") or {}).get("regression", {}).get("test_root"), body
    )
    brief_obligations.validate_body(state, body, ready=ready)
    risk_obligations.validate_body(state, body, ready=ready)
    if "initial_task" in body and not (body["initial_task"]["kind"] == "none" and body["open_blocking_questions"]):
        first = body["initial_task"]
        verification_plan.require_scaffolding(state.get("workspace"), first["affected_paths"], first["validation_plan"])
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
    if not test_cases.design_only(state):
        proof_declarations.validate(criteria)
    criterion_ids = {row["id"] for row in criteria}
    for milestone in milestones:
        covered = milestone["acceptance_criteria"]
        if (
            not milestone["objective"].strip()
            or not covered
            or len(set(covered)) != len(covered)
            or not set(covered) <= criterion_ids
        ):
            raise ValueError("Each milestone needs an objective and existing acceptance criterion IDs")
        verification_plan.require_scaffolding(
            state.get("workspace"),
            milestone.get("affected_paths", []),
            [row["verification_method"] for row in criteria if row["id"] in covered],
        )
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
            raise ValueError(
                f"initial_task milestone {first['milestone_id']} has unmet prerequisites; "
                "start with a milestone whose depends_on is []"
            )
    if "initial_task" in body and not (body["initial_task"]["kind"] == "none" and body["open_blocking_questions"]):
        validate_initial_task(state, body)
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
            if not any(
                event.get("id") == row["answer_id"] and event in state.get("user_events", [])
                for event in state.get("brief_feedback", [])
            ):
                raise ValueError("A feedback-based decision needs an actual saved feedback event")
        elif row["answer_id"]:
            raise ValueError("An inferred decision cannot cite a fabricated answer")
    if any(row["basis"] != "delegated" for row in body["delegated_decisions"]):
        raise ValueError("Delegation must be explicitly recorded by the user")
    if ready or not questions:
        if not criteria or any(
            not row["criterion"].strip() or not row["verification_method"].strip() for row in criteria
        ):
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
        # A Go test the user asked for is proven only under its own name (#498), whatever a review accepted.
        native_test_names.check(state, body, origin=origin)
    design_plan.validate((state.get("settings") or {}).get("design_manifest"), body, ready=ready)


def validate_initial_task(state: RunState, body):
    """Preflight the first assignment with draft ownership, without approving or admitting work."""
    first = body.get("initial_task")
    if not first or (first["kind"] == "none" and body.get("open_blocking_questions")):
        return
    slice_definition = progressive_state.draft_slice(state, body)
    # Draft structure does not grant the native launch or image authority needed
    # by an actual first assignment. Keep progressive ownership checks pure too.
    paths = task_feasibility.first_task(
        body,
        state.get("settings") or {},
        workspace=state.get("workspace"),
        owned_paths=slice_definition["paths"] if slice_definition is not None else None,
    )
    if slice_definition is not None:
        progressive_state.guard_slice_assignment(body, first, paths, slice_definition)


def apply_requirements_report(state: RunState, value, *, artifact_sha256, record=None):
    """Accept a v2 report and retain only its advisory project-question tags."""
    try:
        from . import autocode_project_memory_lifecycle as project_memory_lifecycle
    except ImportError:
        import autocode_project_memory_lifecycle as project_memory_lifecycle
    apply_requirements(state, value["requirements"], artifact_sha256=artifact_sha256, record=record)
    project_memory_lifecycle.capture(state, value)


def apply_requirements(state: RunState, body, *, artifact_sha256, record=None):
    """Install a v2 requirements handoff and privately stage any clarification."""
    validate_requirements_body(state, body)
    state["requirements_artifact_token"] = "requirements:" + artifact_sha256
    state["requirements_body"] = copy.deepcopy(body)
    state["pending_questions"] = []
    state.pop("user_request", None)
    questions = copy.deepcopy(body["open_blocking_questions"])
    remaining = technical_questions.settle(state, questions, state["requirements_artifact_token"])
    if len(remaining) != len(questions):
        state.update({"status": "RUNNING", "phase": "DISCOVERING", "next_stage": "requirements"})
        return
    if questions:
        record = record or {}
        origin = {
            "stage": record.get("stage") or "requirements",
            **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record},
        }
        output = record.get("output")
        evidence = {"hashes": {output: s.file_hash(Path(output))} if output and Path(output).is_file() else {}}
        human.queue(
            state,
            "clarification",
            origin,
            questions=questions,
            evidence=evidence,
            phase="DISCOVERING",
            next_stage="requirements",
        )
    else:
        state.update({"status": "RUNNING", "phase": "PLANNING", "next_stage": "plan"})


# Drafts no model writes at install, so a refusal of their wording has no author to repair it: the Plan
# Reviewer's adaptive approval installs the draft the Planner already handed in (checked then, or saved before
# verification_plan.refuse_new_plan existed), and a bug job's small correction is built by the runner from the
# Investigator's report (autocode_bug_job.ORIGIN), and a spec-first run's correction relabels test: cases the runner saw
# already pass as guard: (autocode_acceptance_tests_job.ORIGIN); plan review and the user's approval still follow.
# The compact bug-fix draft (autocode_compact_planning.ORIGIN) is the runner's too (#2021).
UNAUTHORED_DRAFTS = (
    "adaptive_review_approval",
    "bugfix_small_correction",
    "acceptance_tests_correction",
    "bugfix_compact_draft",
)


def install_draft(state: RunState, body, *, origin, allow_legacy=False, changes=None, record=None, queue_human=True):
    body = brief_obligations.prepare_body(state, body, origin, record)
    body = risk_obligations.prepare_body(state, body, origin, record)
    if not allow_legacy or any(key in body for key in BRIEF_FIELDS):  # a legacy body has no such box
        body = current_behavior.prepare_body(state, body, origin)
    validate_body(state, body, allow_legacy=allow_legacy, origin=origin)
    # Here, not in validate_body, which approval runs again on a saved draft. Human-review criteria too:
    # autocode_dispatch.task_for puts their methods in other milestones' task plans, which reach the Validator.
    if origin not in UNAUTHORED_DRAFTS:
        steps, requirements = verification_plan.task_rows(body.get("initial_task"), "initial_task")
        verification_plan.refuse_new_plan(
            [
                (f"Acceptance criterion {row['id']}'s verification_method", row["verification_method"])
                for row in body["acceptance_criteria"]
            ]
            + steps,
            requirements,
        )
        rewrites = workflows.design_rewrites(state, body)
        if rewrites:
            raise ValueError(
                "A design job writes its design; it does not change what an earlier turn of this conversation "
                "wrote unless the user's message asks for that file: "
                + "; ".join(f"affected_paths {path!r} would let the Builder change {done}" for path, done in rewrites)
                + ". Read those files, and keep them out of every milestone's and the initial_task's affected_paths."
            )
    changes = revision_guard(progressive_state.planning_revision_state(state), body, changes or [], origin)
    progressive_state.finish_draft(state)
    previous = state.get("goal_contract")
    if previous:
        state.setdefault("contract_history", []).append(copy.deepcopy(previous))
    # Numbering continues past contracts a follow-up archived (autocode_follow_up.plan_afresh).
    revision = 1 + max(
        [0, *(row.get("revision") or 0 for row in state.get("contract_history", []) if isinstance(row, dict))]
    )
    contract = {"task_id": state["task_id"], "revision": revision, "body": copy.deepcopy(body)}
    contract.update(
        hash=s.digest(contract),
        approval_status="draft",
        approval_event=None,
        origin=origin,
        created_at=s.now(),
        declared_changes=copy.deepcopy(changes or []),
    )
    state["goal_contract"] = contract
    if origin in ("user_cli_edit", "resolver_recommendation"):
        # An authenticated scope change is new user intent.
        start_clarification_episode(
            state, ("edit_goal" if origin == "user_cli_edit" else origin) + ":r" + str(revision)
        )
    invalidate(state, "Contract revision changed; revalidate the current artifact")
    if state.get("current_task"):
        state.setdefault("task_archive", []).append(state.pop("current_task"))
    state.pop("next_action", None)
    state["acceptance_criteria"] = [
        {"id": c["id"], "criterion": c["criterion"], "status": "unverified", "evidence": ""}
        for c in body["acceptance_criteria"]
    ]
    state["criteria_revision"] = s.digest(s.criteria_definition(state["acceptance_criteria"]))
    state["pending_questions"] = []
    state.pop("user_request", None)
    questions = body["open_blocking_questions"]
    joint = state.get("settings", {}).get("joint_planning")
    if not queue_human:
        return
    remaining = technical_questions.settle(state, questions, token(contract))
    if len(remaining) != len(questions):
        contract["body"]["open_blocking_questions"] = copy.deepcopy(remaining)
        contract["hash"] = s.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        questions = remaining
        if not questions:
            # The default is planning input, never a ready or approved plan.
            planning_cycle.internal_answer_restart(state)
            state.update(
                {
                    "status": "RUNNING",
                    "phase": "DISCOVERING",
                    "next_stage": "requirements" if roles.planning_flow(state) == "v2" else "astra_discovery",
                }
            )
            return
    if (
        joint
        and not questions
        and origin in ("glm_draft", "glm_revise", "user_cli_edit", "resolver_recommendation", "plan", "plan_revise")
    ):
        if origin == "plan":
            state.update({"status": "RUNNING", "phase": "PLANNING", "next_stage": "plan_review"})
            return
        if origin == "plan_revise":
            state.update({"status": "RUNNING", "phase": "PLANNING", "next_stage": "plan_finalize"})
            return
        if origin == "glm_revise":
            state.update({"status": "RUNNING", "phase": "PLANNING", "next_stage": "astra_finalize"})
        else:
            planning_cycle.start(state)
        return
    record = record or {}
    proposed_by = {
        "stage": record.get("stage") or origin,
        **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record},
    }
    output = record.get("output")
    evidence = {"hashes": {output: s.file_hash(Path(output))} if output and Path(output).is_file() else {}}
    human.queue(
        state,
        "clarification" if questions else "goal_approval",
        proposed_by,
        questions=questions,
        evidence=evidence,
        status="WAITING_FOR_USER" if questions else "AWAITING_GOAL_APPROVAL",
        phase="DISCOVERING" if questions else "AWAITING_GOAL_APPROVAL",
        next_stage=("requirements" if roles.planning_flow(state) == "v2" else "astra_discovery")
        if questions
        else "astra_plan",
    )


def apply_resolver_recommendation(state: RunState, *, run_dir=None):
    """Apply one authenticated acceptance as an unapproved, ordinarily reviewed draft.

    The caller owns the transaction's candidate copy and persistence. No files are
    written here: v2 planning artifacts are prepared for its normal atomic commit.
    Other information and leave-paused responses retain their ordinary hold path.
    """
    resolver = state.get("resolver") or {}
    response = resolver.get("pending_human_response")
    if (
        not isinstance(response, dict)
        or response.get("action") != "provide_information"
        or not recommendation.accepts(response.get("text"))
    ):
        return False
    entry = (resolver.get("human_escalations") or {}).get(response.get("request_id")) or {}
    identity = entry.get("identity") or {}
    proposal = identity.get("proposal") or {}
    origin, evidence = proposal.get("origin") or {}, proposal.get("evidence") or {}
    selected = (proposal.get("request") or {}).get("recommended_revision")
    if proposal.get("scope") != "blocker" or origin.get("stage") != "astra_resolve" or selected is None:
        raise ValueError("Accept recommended revision requires a current Resolver criterion recommendation")
    request_id = response.get("request_id")
    binding = identity.get("binding")
    previous = origin.get("pause_status") or proposal.get("previous_status")
    paused = previous if str(previous).startswith("PAUSED_") else "PAUSED_RESOLVER"
    if (
        identity.get("version") != human.VERSION
        or identity.get("issuer") != "resolver"
        or identity.get("action") != "escalate"
        or request_id != s.digest(identity)
        or entry.get("receipt_hash") != request_id
        or entry.get("status") != "consumed"
        or entry.get("response") != response
        or response not in resolver.get("human_responses", [])
        or response.get("actor") != "user_cli"
        or response.get("request_token") != s.digest({"request_id": request_id, "binding": binding})
        or state.get("status") != paused
        or binding != human._binding(state)
        or not human._evidence_valid(proposal)
    ):
        raise ValueError("Recommended revision acceptance has a stale or unauthenticated request binding")
    current = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=s.snapshot)
    output = evidence.get("output")
    contract = state.get("goal_contract") or {}
    record = next(
        (
            row
            for row in reversed(state.get("stages", []))
            if row.get("stage") == "astra_resolve" and row.get("output") == output
        ),
        None,
    )
    if (
        not approved(state)
        or not output
        or origin.get("output") != output
        or not (evidence.get("hashes") or {}).get(output)
        or not record
        or record.get("exit_code") != 0
        or record.get("rejected")
        or record.get("changed_files")
        or record.get("source_revision") != current["revision"]
        or origin.get("source_revision") != current["revision"]
    ):
        raise ValueError("Recommended revision requires the current approved contract and read-only Resolver evidence")
    raw = s.read_object(Path(output))
    if (
        raw.get("status") != "BLOCKED"
        or raw.get("contract_revision") != contract["revision"]
        or raw.get("contract_hash") != contract["hash"]
        or raw.get("task_id") != (state.get("current_task") or {}).get("id", "")
        or raw.get("diagnosis") != evidence.get("diagnosis")
        or (raw.get("user_request") or {}).get("recommended_revision") != selected
    ):
        raise ValueError("Recommended revision no longer matches the retained Resolver report and contract")
    revised = recommendation.compose(contract["body"], selected)
    event_id = "feedback-resolver-" + request_id
    feedback = {
        "kind": "brief_feedback",
        "id": event_id,
        "actor": "user_cli",
        "at": s.now(),
        "text": response["text"].strip()
        + "\nAccepted acceptance-criterion wording:\n"
        + "\n".join(row["id"] + ": " + row["criterion"] for row in selected["acceptance_criteria"]),
        "contract_token": token(contract),
        "resolver_request_id": request_id,
        "resolver_response": copy.deepcopy(response),
        "recommended_revision": copy.deepcopy(selected),
        "resolver_output": output,
        "resolver_output_sha256": evidence["hashes"][output],
    }
    changes = [
        {"item": row["id"], "change": "reworded", "basis": "user_feedback", "answer_id": event_id}
        for row in selected["acceptance_criteria"]
    ]
    # First retain the old response audit at its old frontier. Installing the
    # draft then changes that frontier; its approval gate must not inherit a hold.
    human.review_operational_response(state)
    source_log.save_feedback(state, feedback)
    install_draft(state, revised, origin="resolver_recommendation", changes=changes)
    state.pop("stop_reason", None)
    try:
        from . import autocode_planning_artifacts as planning_artifacts
    except ImportError:
        import autocode_planning_artifacts as planning_artifacts
    planning_artifacts.prepare_resolver_recommendation(state, run_dir=run_dir)
    return True


def require_supported_checkpoint(state: RunState):
    """Refuse a checkpoint this runner cannot safely interpret (#338).

    ``version`` is the state format. A number above SUPPORTED_VERSION was written
    by a newer AutoCode; migrating or resuming it here can invent a launch. The
    registry already classifies those as ``checkpoint_unsupported``.
    """
    version = state.get("version", 2)
    if type(version) is not int or isinstance(version, bool) or version < 1:
        raise s.Paused(
            "PAUSED_UNSUPPORTED_CHECKPOINT", f"Checkpoint version {version!r} is malformed; expected a positive integer"
        )
    if version > SUPPORTED_VERSION:
        raise s.Paused(
            "PAUSED_UNSUPPORTED_CHECKPOINT",
            f"Checkpoint version {version} is from a newer AutoCode than this runner "
            f"(supports up to {SUPPORTED_VERSION}); upgrade AutoCode or use a matching checkpoint",
        )


def migrate(state: RunState, *, fresh=False):
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
    state["pre_goal_checkpoint"] = {
        k: copy.deepcopy(state.get(k))
        for k in ("status", "next_stage", "acceptance_criteria", "criteria_revision", "plan", "next_action")
    }
    # A new request has no saved work to reconstruct. Its first real draft must
    # come from planning; inventing a blocking question here makes models ask
    # for redundant migration permission. Saved legacy runs remain conservative.
    if not fresh:
        body: dict = {
            k: []
            for k in BODY_SCHEMA["properties"]
            if k not in ("task_kind", "brief_acceptance", "risk_acceptance", "source_trace", "git_delivery")
        }
        body.update(
            intended_outcome=state["task"],
            intended_user="Unconfirmed",
            acceptance_criteria=[
                {
                    "id": c["id"],
                    "criterion": c["criterion"],
                    "verification_method": "Unconfirmed; reconstruct from saved evidence",
                    "human_review": False,
                }
                for c in state.get("acceptance_criteria", [])
            ],
            open_blocking_questions=[
                {
                    "id": "migration-context",
                    "question": "Reconstruct the goal from the request and saved work",
                    "why": "Existing criteria and agent assumptions have no user approval event",
                    "options": [],
                    "proposed_default": "",
                }
            ],
        )
        install_draft(state, body, origin="migration_draft; no inferred user approval", queue_human=False)
    first_stage = (
        "requirements"
        if roles.planning_flow(state) == "v2"
        else "requirements_gather"
        if "requirements" in state.get("settings", {}).get("roles", {})
        else "astra_discovery"
    )
    state.update(
        {"version": 3, "phase": "DISCOVERING", "status": "RUNNING", "pending_questions": [], "next_stage": first_stage}
    )
    if fresh and workflows.requirements_first(state):
        # Requirements is the front door of every run that has it: its report names the job (#1860).
        workflows.begin_requirements(state)
    elif fresh:
        workflows.begin(state, first_stage)


ANSWER_KINDS = {
    "delegated": "the proposed default, delegated",
    "permission_answer": "a permission decision",
    "checkpoint_answer": "a checkpoint decision",
}


def answer_lines(answers) -> list[str]:
    """One line per saved answer, the question and what was answered (#2363). The record itself,
    with its token and timestamps, stays in the run state and the source log."""
    lines = []
    for key, row in answers.items() if isinstance(answers, dict) else []:
        if not isinstance(row, dict):
            lines.append(f"  - {key}: {row}")
            continue
        question = row.get("question")
        asked = str(question.get("question") or "").strip() if isinstance(question, dict) else ""
        how = ANSWER_KINDS.get(str(row.get("kind") or ""))
        answered = str(row.get("text") or "").strip() or "(no text)"
        lines.append(f"  - {key}: {asked} -> {answered}" if asked else f"  - {key} -> {answered}")
        if how:
            lines[-1] += f" ({how})"
    return lines


def render(state: RunState, run_dir=None):
    """The brief as a person reads it; ``run_dir``, when given, completes the approve command."""
    contract = state.get("goal_contract")
    if not contract:
        if roles.planning_flow(state) == "v2":
            public = human.current(state)
            questions = public["questions"] if public else human.internal_questions(state)
            lines = ["No contract yet; resume with Requirements."]
            for question in questions:
                if not public:
                    lines.append("  Unissued proposal (not an actionable question):")
                lines += [f"  [{question['id']}] {question['question']}", f"    Why: {question['why']}"]
            if public:
                lines += [
                    f"Resolver request ID: {public['request_id']}",
                    f"Resolver request token: {public['request_token']}",
                ]
            return "\n".join(lines)
        return "No contract yet; resume to interview with the Requirements Gatherer."
    body = contract["body"]
    public = human.current(state)
    asks_approval = bool(public and public["scope"] == "goal_approval")
    lines = [f"Build brief r{contract['revision']} ({contract['approval_status']})"]
    if asks_approval:
        lines.append(approval_view.intro(contract["revision"]))
    if public:
        lines += [f"AutoResolver request: {public['request_id']}", f"AutoResolver token: {public['request_token']}"]
    if asks_approval:
        lines += [f"Approval token: {token(contract)}", approval_view.token_note()]
    if workflows.approval_note(state):
        lines += ["", workflows.approval_note(state)]
    if state.get("discovery_summary"):
        lines += ["", "Planning: " + state["discovery_summary"]]
    lines += plan_preview(state)
    if body.get("task_kind") == "bugfix":
        lines += [
            "",
            "Job type: bug fix. Before completion the runner itself checks that a new or changed",
            "test fails on the original code and passes with the fix, and that no test that",
            "passed before now fails.",
        ]
    elif "task_kind" in body and test_cases.design_only(state):
        lines += ["", f"Job type: design (not a bug fix): {approval_view.DESIGN}."]
    elif "task_kind" in body:
        lines += [
            "",
            'Job type: build (not a bug fix). Criteria verified by "test: ..." are proven by the runner at their milestone and at completion: each named test '
            + approval_view.RESTORE["build"]
            + ".",
        ]
    display_order = (
        "intended_user",
        "intended_outcome",
        "end_to_end_flow",
        "deliverables",
        "scope_exclusions",
        "constraints",
        "permission_boundaries",
        "accepted_assumptions",
        "delegated_decisions",
        "required_behaviors",
        "important_failure_cases",
        "acceptance_criteria",
        "technical_approach",
        "git_delivery",
        "milestones",
        "initial_task",
        "open_blocking_questions",
    )
    for key in display_order:
        if key not in body or body[key] is None:
            continue
        value = body[key]
        lines += ["", key.replace("_", " ").capitalize() + ":"]
        if isinstance(value, dict):
            for name, item in value.items():
                label = "  " + name.replace("_", " ").capitalize() + ":"
                if isinstance(item, list):
                    lines += [label + ("" if item else " (none)")] + [f"    - {row}" for row in item]
                else:
                    lines.append(f"{label} {item}")
        elif not isinstance(value, list):
            lines.append(value)
        elif not value:
            lines.append("  (none declared)")
        else:
            for row in value:
                if isinstance(row, str):
                    lines.append("  - " + row)
                elif key == "acceptance_criteria":
                    lines += [
                        f"  [{row['id']}] {row['criterion']}",
                        f"    Verify: {row['verification_method']}",
                        "    Human review: " + ("required" if row["human_review"] else "not required"),
                    ]
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
                    lines += [
                        f"  [{row['id']}] {row['objective']}",
                        "    Acceptance criteria: " + ", ".join(row["acceptance_criteria"]),
                    ]
                    if "depends_on" in row:
                        lines.append(
                            "    Depends on: " + (", ".join(row["depends_on"]) or "none; can start independently")
                        )
                    if "affected_paths" in row:
                        lines.append(
                            "    Owned paths: " + (", ".join(row["affected_paths"]) or "unspecified; serial dispatch")
                        )
                else:
                    lines.append(f"  - {row['text']} (basis: {row['basis']}; answer: {row['answer_id'] or 'none'})")
    lines += design_plan.render((state.get("settings") or {}).get("design_manifest"), body)
    lines += ["", "Verification obligations (declarations, not execution proof):"]
    lines += approval_view.field_lines(
        verification_plan.obligations(state, progressive_context=progressive_state.context(state))
    )
    lines += examples.review_notes(state)
    lines += base_examples.notes(state)
    declared = list(contract.get("declared_changes") or [])
    # A line of the Planner's own changed as the Plan Reviewer's blocking concern asked (revision guard) stays on
    # the approval screen when the final review keeps it without declaring it again: the person approving this
    # plan is the first to see it. Only this cycle's drafts count, back to the draft the cycle began with.
    for revision in reversed(state.get("contract_history") or []):
        if (
            not isinstance(revision, dict)
            or revision.get("approval_status") != "draft"
            or revision.get("approval_event")
            or revision.get("origin") == "user_cli_edit"
        ):
            break
        declared += [
            dict(row, revision=revision.get("revision"))
            for row in revision.get("declared_changes") or []
            if isinstance(row, dict) and (row.get("review_concerns") or row.get("change") == current_behavior.CHANGE)
        ]
        if revision.get("origin") in ("glm_draft", "astra_discovery", "plan"):
            break
    if declared:
        lines += ["", "Declared contract changes:"]
        ordered = sorted(declared, key=lambda row: row.get("change") != "permission_changed")
        for row in ordered:
            replaced = (
                f" -> {row['replacement']}"
                if row.get("change") in ("permission_changed", "reworded", current_behavior.CHANGE)
                and row.get("replacement")
                else ""
            )
            asked = (
                "; Plan Reviewer concern " + ", ".join(str(cid) for cid in row["review_concerns"])
                if row.get("review_concerns")
                else ""
            )
            earlier = f"; revision {row['revision']}" if row.get("revision") else ""
            lines.append(
                f"  - {row.get('change')}: {row.get('item')}{replaced} (basis: {row.get('basis')}{asked}{earlier})"
            )
    history = state.get("contract_history", [])
    if history:
        lines += ["", "Contract delta:"] + list(
            difflib.unified_diff(
                json.dumps(history[-1]["body"], indent=2, sort_keys=True).splitlines(),
                json.dumps(body, indent=2, sort_keys=True).splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
            )
        )
    if state.get("answers"):
        lines += ["", "Saved user answers:"] + answer_lines(state["answers"])
    if state.get("brief_feedback"):
        lines += ["", "Saved brief feedback:"] + ["  - " + row["text"] for row in state["brief_feedback"]]
    if public:
        if (public["request"] or {}).get("kind") == "human_review":
            lines += [""] + approval_view.review_request(
                body,
                public["request"],
                state.get("validation"),
                criterion_changes.attribute(body, state.get("code_checkpoints")),
                run_dir if run_dir is not None else state.get("run_dir"),
            )
        elif public["request"]:
            lines += ["", "Decision needed:", json.dumps(public["request"], indent=2)]
        lines += [f"Answer ID: {q['id']} - {q['question']}" for q in public["questions"]]
        lines += [f"Resolver request ID: {public['request_id']}", f"Resolver request token: {public['request_token']}"]
    if state.get("planning"):
        total = state["planning"]["astra_calls"]
        recovered = state["planning"].get("recovery_review_calls_used", 0)
        limit = state["planning"].get(
            "review_call_limit", state.get("settings", {}).get("planning_review_call_limit", 2)
        )
        budget = (
            f"{total - recovered}/{limit} ordinary plan-review calls used; "
            f"AutoResolver recovery calls used: {recovered}; {total} total calls"
            if recovered
            else f"{total}/{limit} plan-review calls used"
        )
        if limit == 0:
            budget = f"{total} plan-review calls used; unlimited"
        origin = (state.get("settings") or {}).get("planning_review_call_limit_origin")
        if origin:
            budget += f" (allowance from {origin})"
        if (state.get("planning") or {}).get("review_depth_reduced"):
            budget += "; counts across revisions (small clear brief)"
        lines += ["", f"Joint planning: {budget}"]
        for stage, report in state["planning"]["reports"].items():
            lines.append(f"  {stage}: {report['output']}")
        reports = state["planning"]["reports"]
        final = (reports.get("plan_finalize") or reports.get("astra_finalize") or {}).get("report", {})
        for decision in final.get("decisions", []):
            lines += [
                f"  [{decision['concern_id']}] {decision['decision']}",
                "    Why: " + decision["rationale"],
                "    Test: " + decision["acceptance_test"],
            ]
        lines += adaptive.review_notes(state["planning"])
    review = review_token(state) if public else None
    if review:
        lines += ["", f"Review token (current validated artifact): {review}"]
        lines += approval_view.validation_summary(state["validation"] or {})
    if asks_approval:
        # The last screen: the decision in brief, then the limits and the commands (#381).
        lines += [""] + approval_view.summary(
            body,
            contract["revision"],
            test_cases.proof_cases(state, all_due=True),
            design_only=test_cases.design_only(state),
            proxied=trace_coverage.proxied_rows(plan_retention.validated_trace(state)),
        )
        lines += goal_file.approval_lines(state)
        lines += [""] + approval_view.actions(
            token(contract), state.get("settings") or {}, state.get("iteration", 0), run_dir
        )
    lines += ["", f"State: {state.get('phase')} / {state['status']}"]
    return "\n".join(lines)


def present(state: RunState, run_dir=None):
    try:
        from . import autocode_changeset_barrier as changeset_barrier
    except ImportError:
        import autocode_changeset_barrier as changeset_barrier
    public = human.current(state)
    if not plan_chat.pending(state):
        state.pop("displayed_goal", None)
    # Preserve the historical display acknowledgement for exact, already-recorded
    # review replays. Rendering still hides controls without current authority.
    recorded_review = any(
        isinstance(row, dict)
        and row.get("actor") == "user_cli"
        and row.get("token") == state.get("displayed_review")
        and row in state.get("user_events", [])
        for row in state.get("human_reviews", {}).values()
    )
    if not recorded_review:
        state.pop("displayed_review", None)
    # Any current request is shown against the contract: --delegate-all and
    # --reject-assumption act on that display at clarification and permission
    # stops. Approval itself still requires the goal-approval status.
    if (public or changeset_barrier.held_approval(state)) and state.get("goal_contract"):
        state["displayed_goal"] = token(state["goal_contract"])
    if state.get("goal_contract"):
        # Question actions bind to the displayed handoff even without a
        # published request; goal approval keeps its stricter display binding.
        state["displayed_handoff"] = handoff_ref(state)
    if public:
        state["displayed_review"] = review_token(state)
    if state.get("goal_contract"):
        # Advisory base-revision check (#676): runs the planned commands on the base revision once for each
        # distinct set of planned examples; render() only reads what this recorded.
        base_examples.check(state)
    rendered = render(state, run_dir)
    interaction_timing.presented(state, public, at=s.now())
    return rendered


def approve(state: RunState, selected, *, initial_assignment_proof=None, source=None):
    # Assignment/eligibility may still reject after seal preflight. Publish no
    # approval, retirement or active pointer unless the whole boundary succeeds.
    # ``source`` marks an approval the person chose when starting the run (--skip-to-build, #1731).
    candidate = copy.deepcopy(state)
    _approve(candidate, selected, initial_assignment_proof=initial_assignment_proof, source=source)
    cast(dict, state).clear()
    state.update(candidate)


def approval_boundary_refusal(state: RunState):
    """Read-only boundary check; approval still validates the contract, token and issued request."""
    # Resuming after an environment failure can reach the execution guard with
    # the same reviewed draft. Keep exact-token approval available in that pause.
    if state.get("status") not in ("AWAITING_GOAL_APPROVAL", "PAUSED_GOAL_UNAPPROVED") or any(
        state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair")
    ):
        return "Approve only the current displayed draft token; show the goal again"
    return None


def _approve(state: RunState, selected, *, initial_assignment_proof=None, source=None):
    if reason := approval_boundary_refusal(state):
        raise ValueError(reason)
    contract = cast(dict, state["goal_contract"])
    if not sealed(contract) or selected != token(contract) or state.get("displayed_goal") != selected:
        raise ValueError("Approve only the current displayed draft token; show the goal again")
    validate_body(state, contract["body"], ready=True, allow_legacy=True, origin=contract.get("origin"))
    if contract["body"]["open_blocking_questions"] or state.get("pending_questions"):
        raise ValueError("Blocking questions still need answers")
    if open_obligations(state):
        raise ValueError(
            "Rejected assumptions still have unresolved obligations: "
            + ", ".join(ob["id"] for ob in open_obligations(state))
        )
    joint = state.get("settings", {}).get("joint_planning")
    if joint and (
        cast(dict, state.get("planning", {})).get("final_token") != selected or "initial_task" not in contract["body"]
    ):
        raise ValueError("Joint planning requires the Plan Reviewer's final plan before approval")
    prepared_progressive = progressive_state.prepare_seal(state, selected)
    recorded_under = finding_rescope.previous_approved(state)
    event = {"kind": "goal_approval", "actor": "user_cli", "at": s.now(), "token": selected}
    if source:
        event["source"] = source
    state.setdefault("user_events", []).append(event)
    contract.update(approval_status="approved", approval_event=event)
    progressive_state.seal(state, selected, prepared=prepared_progressive)
    # Open findings follow the criteria this revision moved to other milestones (#447).
    finding_rescope.on_approval(
        state, recorded_under, contract_token=selected, at=event["at"], new_id=lambda: findings.allocate_id(state)
    )
    state.update({"phase": "READY_TO_EXECUTE", "status": "RUNNING", "next_stage": "astra_plan"})
    # Approval authorizes the selected inventory, including Git-ignored deliverables.
    current = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=s.snapshot) if joint else None
    carried = []
    if checkpoints.enabled(state):
        current = current or source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=s.snapshot)
        carried = checkpoints.carryforward.carry(state, current)
    if joint:
        if contract["body"]["initial_task"]["milestone_id"] in carried:
            state.update(
                {
                    "next_stage": "astra_review",
                    "next_action": "Preserve carried milestones; assign unfinished work or final integration validation",
                }
            )
            recovery_context.revision_approved(state)
            return
        decision = initial_decision(contract["body"])
        proof = initial_assignment_proof(state, snapshot=current) if initial_assignment_proof is not None else None
        kind = assign_task(state, decision, current, visual_proof=proof)
        try:
            from . import autocode_dispatch as dispatch
        except ImportError:
            import autocode_dispatch as dispatch
        state.update(
            {
                "next_action": decision["next_objective"],
                "next_stage": "sol"
                if kind == "validate"
                else "terra"
                if progressive_state.enabled(state)
                else dispatch.build_stage(state),
            }
        )
        record_decision(state, decision)
    recovery_context.revision_approved(state)


def resolve_passing_checkpoint(state: RunState, question_id, text):
    """Record the user's explicit reconciliation choice for a now-passing gate."""
    request = state.get("user_request") or {}
    questions = state.get("pending_questions") or []
    choices = request.get("options") or []
    if (
        state.get("status") != "WAITING_FOR_USER"
        or state.get("next_stage") != "astra_review"
        or request.get("kind") != "blocker"
        or request.get("proposed_delta")
        or len(questions) != 1
        or questions[0].get("id") != question_id
        or not choices
        or text != choices[0]
        or not choices[0].startswith("Reconcile ")
        or ("Sol evidence" not in choices[0] and "Validator evidence" not in choices[0])
        or question_id in state.get("answers", {})
        or not approved(state)
    ):
        raise ValueError("Checkpoint reconciliation requires the explicit saved choice and approved goal")
    description = str(request.get("discovered", "")).lower()
    if not all(word in description for word in ("checkpoint", "evidence")) or not (
        "sol" in description or "validator" in description
    ):
        raise ValueError("The pending request is not a Validator milestone checkpoint")
    current = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=s.snapshot)
    if not checkpoints.evidence_ready(state, current) or missing_human_reviews(state):
        raise ValueError("Current independent evidence or a required human review is still missing")
    event = {
        "kind": "checkpoint_answer",
        "actor": "user_cli",
        "at": s.now(),
        "question_id": question_id,
        "question": questions[0],
        "text": text,
        "contract_token": token(state["goal_contract"]),
        "validation_source_revision": cast(dict, state["validation"])["source_revision"],
    }
    state.setdefault("user_events", []).append(event)
    state.setdefault("answers", {})[question_id] = event
    state.update({"status": "RUNNING", "phase": "READY_TO_EXECUTE", "pending_questions": []})
    state.pop("user_request", None)
    state.pop("milestone_blocker", None)


def wait_for_user(state: RunState, request, *, origin=None, evidence=None, next_stage=None):
    request = checkpoints.route_review_only_request(state, request, origin)
    if request is None:
        return
    if not request["decision_needed"].strip() or not request["impact"].strip():
        raise ValueError("A user request needs the smallest decision and its impact")
    if request.get("kind") == "permission" and approved(state):
        for answer_id, answer in state.get("answers", {}).items():
            if (
                answer.get("kind") != "permission_answer"
                or answer.get("actor") != "user_cli"
                or answer not in state.get("user_events", [])
                or answer.get("contract_token") != token(state["goal_contract"])
                or answer.get("request") != request
            ):
                continue
            reused = state.setdefault("permission_reuses", [])
            key = {"answer_id": answer_id, "contract_token": answer["contract_token"]}
            if key in reused:
                raise s.Paused(
                    "PAUSED_PERMISSION_RECONCILIATION",
                    f"The decision in saved answer {answer_id} was already returned to the Completion Reviewer. "
                    "It must honor that answer rather than request the same permission again.",
                )
            reused.append(key)
            state["permission_reuse_context"] = {
                **key,
                "request": copy.deepcopy(request),
                "answer": answer["text"],
                "instruction": "This exact decision was already answered. Honor the saved answer, including "
                "any denial or conditions. It does not authorize broader scope. Continue within the answer "
                "or explain a materially different unresolved decision; do not ask this question again.",
            }
            state.pop("user_request", None)
            state.pop(human.PRIVATE, None)
            state.pop(human.PUBLIC, None)
            state.update(
                {
                    "status": "RUNNING",
                    "phase": "READY_TO_EXECUTE",
                    "pending_questions": [],
                    "next_stage": "astra_review",
                }
            )
            return
    state.pop("permission_reuse_context", None)
    origin = origin or {"stage": "goal_request"}
    evidence = copy.deepcopy(evidence or {})
    output = origin.get("output")
    if output and Path(output).is_file():
        evidence.setdefault("hashes", {})[output] = s.file_hash(Path(output))
    question = {
        "id": "decision-" + uuid.uuid4().hex[:12],
        "question": request["decision_needed"],
        "why": request["impact"],
        "options": request["options"],
        "proposed_default": "",
    }
    operational_change.bind(state, request, question)
    review_criteria = requested_review_criteria(state, request)
    if review_criteria:
        question.update(review_criteria=review_criteria, review_token=review_token(state))
        evidence.update(review_token=review_token(state), criteria=review_criteria)
        evidence.setdefault("hashes", {}).update(cast(dict, state.get("validation", {})).get("evidence_hashes", {}))
        if request.get("kind") == "human_review":
            request["criteria"] = review_criteria
    scope = request["kind"] if request["kind"] in ("permission", "goal_change", "human_review") else "blocker"
    human.queue(state, scope, origin, request=request, questions=[question], evidence=evidence, next_stage=next_stage)


def assign_task(state: RunState, decision, current, *, draft_slice=None, visual_proof=None):
    """Persist one bounded handoff tied to the approved brief, not a second brief."""
    spec = decision.get("next_task")
    if spec is None and "milestones" not in cast(dict, state["goal_contract"])["body"]:
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
    body = cast(dict, state["goal_contract"])["body"]
    ids = spec["acceptance_criteria"]
    if len(set(ids)) != len(ids) or not set(ids) <= {c["id"] for c in body["acceptance_criteria"]}:
        raise ValueError("Task criteria must reference the approved brief")
    milestones = {m["id"]: m for m in body.get("milestones", [])}
    previous_batch = (state.get("current_task") or {}).get("milestone_ids", [])
    allowed = (
        set(c for mid in previous_batch for c in milestones[mid]["acceptance_criteria"])
        if spec["milestone_id"] in previous_batch
        else set(milestones.get(spec["milestone_id"], {}).get("acceptance_criteria", []))
    )
    if spec["kind"] == "validate":
        allowed |= checkpoints.recheckable(state, current=current, visual_proof=visual_proof)
    if milestones and (spec["milestone_id"] not in milestones or not set(ids) <= allowed):
        raise ValueError("Task must belong to an approved milestone and its acceptance criteria")
    # A review's affected paths may describe the completed milestone, not the next
    # task. Approved ownership permits its new outputs, never the previous owner's.
    task_paths = list(decision.get("affected_paths", []))
    exact_native_repair = checkpoints.visual_checkpoint.exact_repair_paths(state, current, visual_proof, decision)
    if not task_paths and spec["kind"] == "validate":
        # A validate task writes nothing; it checks what its milestone owns, or in a progressive
        # run what the active slice owns (#615).
        task_paths = list(
            draft_slice["paths"]
            if draft_slice is not None
            else progressive_state.require_active(state)["definition"]["paths"]
            if progressive_state.enabled(state)
            else milestones.get(spec["milestone_id"], {}).get("affected_paths", [])
        )
    if (
        task_paths
        and exact_native_repair is None
        and milestones
        and spec["milestone_id"] not in previous_batch
        and draft_slice is None
        and not progressive_state.enabled(state)
    ):
        owned = milestones.get(spec["milestone_id"], {}).get("affected_paths", [])
        # A serial repair of the current milestone may discover another source
        # file needed for its existing criteria. Do not discard the reviewer's
        # repair paths and send contradictory requirements to the Builder.
        # Advancing milestones and parallel ownership retain the approved paths.
        serial_repair = (
            decision["status"] == "REWORK"
            and len(milestones) == 1
            and spec["milestone_id"] == (state.get("current_task") or {}).get("milestone_id")
            and not any(
                state.get(key) for key in ("parent_run", "parent_batch", "orchestration_batch", "orchestration_history")
            )
        )
        if owned:
            # Retain the milestone's existing source scope: replacing it with
            # a narrower repair list invalidates the incident's evidence pins.
            paths = list(owned)
            if serial_repair:
                previous = state.get("current_task") or {}
                paths.extend(previous.get("affected_paths") or [])
                if decision["next_objective"] != previous.get("objective") or any(
                    spec[key] != previous.get(key) for key in ("requirements", "validation_plan")
                ):
                    paths.extend(task_paths)
            task_paths = list(dict.fromkeys(paths))
    if draft_slice is not None:
        progressive_state.guard_slice_assignment(body, spec, task_paths, draft_slice)
    else:
        progressive_state.guard_assignment(state, spec, task_paths)
    verification_plan.require_scaffolding(state.get("workspace"), task_paths, spec["validation_plan"])
    recovery = state.get("recovery_context") or {}
    previous_task = state.get("current_task") or {}
    if (
        spec["kind"] == "implement"
        and recovery.get("timeout_kind")
        and recovery.get("task_id")
        and recovery["task_id"] == previous_task.get("id")
        and recovery.get("execution_limits")
    ):
        limits = state.get("settings", {}).get("limits", {})
        defaults = {"stage_timeout_seconds": None, "idle_timeout_seconds": 300, "tool_timeout_seconds": 1800}
        failed_limit = {
            "tool": "tool_timeout_seconds",
            "idle": "idle_timeout_seconds",
            "stage": "stage_timeout_seconds",
        }.get(recovery["timeout_kind"])
        recorded_limits = recovery["execution_limits"]
        compared_limits: dict = (
            {failed_limit: recorded_limits[failed_limit]} if failed_limit in recorded_limits else recorded_limits
        )
        same_limits = all(limits.get(key, defaults.get(key)) == value for key, value in compared_limits.items())
        candidate = {**spec, "objective": decision["next_objective"], "affected_paths": task_paths}
        if same_limits and checkpoints.approach(candidate) == checkpoints.approach(previous_task):
            raise ValueError(
                "The timed-out task needs a changed execution plan before another writer; "
                "the task and timeout limits are unchanged. Preserve completed work and "
                "split the remaining work or address the diagnosed stall."
            )
    if checkpoints.enabled(state):
        checkpoints.carryforward.before_assignment(state, spec, decision)
    normalized_decision = {**decision, "affected_paths": task_paths}
    checkpoints.before_assignment(state, normalized_decision, current, visual_proof=visual_proof)
    # After before_assignment, so a milestone accepted while advancing counts.
    checkpoints.require_prerequisites(
        state, spec["milestone_id"], current=current, visual_proof=visual_proof, decision=normalized_decision
    )
    if state.get("current_task"):
        state.setdefault("task_archive", []).append(copy.deepcopy(state["current_task"]))
    contract = cast(dict, state["goal_contract"])
    state["current_task"] = {
        **copy.deepcopy(spec),
        "id": "task-" + uuid.uuid4().hex[:12],
        "objective": decision["next_objective"],
        "affected_paths": task_paths,
        "contract_revision": contract["revision"],
        "contract_hash": contract["hash"],
        "assigned_at": s.now(),
        "source_revision": current["revision"],
        "decision": decision["status"],
    }
    progressive_state.bind_task(state)
    if spec["milestone_id"] in previous_batch:
        # Rework stays accountable for every member of the integrated wave.
        cast(dict, state["current_task"])["milestone_ids"] = previous_batch
        cast(dict, state["current_task"])["acceptance_criteria"] = list(
            dict.fromkeys(cid for mid in previous_batch for cid in milestones[mid]["acceptance_criteria"])
        )
    findings.assign(state, state["current_task"], spec, decision)
    if checkpoints.enabled(state):
        checkpoints.progress(state)["rejected_advances"] = 0
        state.pop("milestone_blocker", None)
    state["affected_paths"] = list(task_paths)
    return spec["kind"]
