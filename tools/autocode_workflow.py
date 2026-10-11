"""Opt-in reviewer routing for the existing runner; no provider or state store."""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import copy
from pathlib import Path

try:
    from . import autocode_completion as completion_gate
    from . import autocode_design_coverage as design_coverage
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_support as support
except ImportError:
    import autocode_completion as completion_gate
    import autocode_design_coverage as design_coverage
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_support as support

MODE = "glm_first_v1"
FINAL_MODE = "glm_final_audit_v2"
FINAL_APPROVAL = (
    "The Builder owns technical planning, implementation, tests, routine fixes and continuation "
    "across approved milestones. The Validator runs only for a concrete Builder debugging escalation. "
    "The Plan Reviewer runs only for the final full-task independent audit and any necessary final "
    "audit recheck. Preserve the approved goal, criteria, evidence checks, human approvals, "
    "permissions, models, sessions and limits; no automatic milestone GPT review."
)
APPROVAL_TEXT = (
    "The Builder owns substantial implementation, tests and routine fixes. The Plan Reviewer performs "
    "independent milestone validation and completion judgment in one read-only call. "
    "The Validator is a targeted escalation only. Existing Validator reviewer references mean the "
    "independent reviewer responsibility, now assigned to the Plan Reviewer; evidence requirements, "
    "scope, permissions, human reviews and execution limits are unchanged."
)


def enabled(state):
    return state.get("settings", {}).get("workflow", {}).get("mode") in (MODE, FINAL_MODE)


def final_only(state):
    return state.get("settings", {}).get("workflow", {}).get("mode") == FINAL_MODE


def guard(state):
    if state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled") and enabled(state):
        raise support.Paused(
            "PAUSED_WORKFLOW_CONFLICT", "Milestone checkpoints require Builder → Validator → Plan Reviewer routing"
        )
    if not enabled(state):
        return
    policy = state["settings"]["workflow"]
    event = policy.get("approval_event", {})
    if (
        event.get("kind") != "workflow_approval"
        or event.get("actor") != "user"
        or event.get("policy") != (FINAL_APPROVAL if final_only(state) else APPROVAL_TEXT)
        or event not in state.get("user_events", [])
        or event.get("contract_hash") != state.get("goal_contract", {}).get("hash")
    ):
        raise support.Paused("PAUSED_WORKFLOW_APPROVAL", "Reviewer routing needs approval for this goal revision")


def review_stage(state):
    guard(state)
    if final_only(state):
        return "terra"
    return "astra_checkpoint" if enabled(state) else "sol"


def activate_final(state, *, approval_source):
    if state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled"):
        raise ValueError("Enforced milestone checkpoints cannot use final-only review routing")
    if (
        state.get("active_stage")
        or state.get("pending_report_repair")
        or state.get("uncertain_artifacts")
        or state["status"] == "RUNNING"
    ):
        raise ValueError("Activate final-only routing only at a reconciled idle checkpoint")
    if not goals.approved(state):
        raise ValueError("An approved goal is required")
    if final_only(state):
        guard(state)
        return
    old = copy.deepcopy(state["settings"].get("workflow"))
    event = {
        "kind": "workflow_approval",
        "actor": "user",
        "at": support.now(),
        "policy": FINAL_APPROVAL,
        "contract_hash": state["goal_contract"]["hash"],
        "source": approval_source,
    }
    state.setdefault("user_events", []).append(event)
    state["settings"]["workflow"] = {"mode": FINAL_MODE, "approval_event": event}
    state.setdefault("configuration_changes", []).append(
        {
            "at": support.now(),
            "reason": FINAL_APPROVAL,
            "previous_workflow": old,
            "previous_next_stage": state.get("next_stage"),
        }
    )
    if state["status"] not in ("TASK_COMPLETE", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
        state["next_stage"] = "terra"
    # Preserve the approved current task and all already completed reports. The Builder
    # consumes the saved review and owns subsequent in-scope technical planning.


def implementation_schema(schema_dir):
    schema = goals.role_schema(support.read(schema_dir / "v2/terra-report.schema.json"), "terra")
    review = goals.role_schema(support.read(schema_dir / "v2/sol-report.schema.json"), "sol")
    schema["properties"]["continuation"] = goals.obj(
        {
            "action": {
                "type": "string",
                "enum": ["CONTINUE", "REQUEST_FINAL_AUDIT", "ESCALATE_SOL", "WAITING_FOR_USER"],
            },
            "plan": goals.STRINGS,
            "next_task": goals.INITIAL_TASK,
            "reason": goals.STRING,
            "question": goals.STRING,
            "self_assessment": review,
        }
    )
    schema["required"].append("continuation")
    return schema


def dispatch_guard(state, stage, workspace):
    guard(state)
    if not final_only(state):
        return
    if stage not in ("sol", "astra_checkpoint"):
        return
    saved = state.get("final_audit_request" if stage == "astra_checkpoint" else "targeted_consultation") or {}
    if (
        saved.get("contract_hash") != state["goal_contract"]["hash"]
        or saved.get("task_id") != state.get("current_task", {}).get("id", "")
        or saved.get("source_revision")
        != source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
    ):
        raise support.Paused(
            "PAUSED_STALE_HANDOFF", "GPT dispatch lacks a current explicit escalation/final-audit request"
        )
    if stage == "astra_checkpoint" and (
        not saved.get("evidence_hashes")
        or any(not Path(p).is_file() or support.file_hash(p) != h for p, h in saved["evidence_hashes"].items())
    ):
        raise support.Paused("PAUSED_STALE_HANDOFF", "Final-audit self-check evidence is missing or changed")


def apply_implementation(runner, state, value, record, workspace, run_dir):
    support.validate_schema(value, implementation_schema(runner.SCHEMA_DIR))
    goals.execution_guard(state, value)
    c = value["continuation"]
    action = c["action"]
    if record["source_revision"] != source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]:
        raise support.Paused("PAUSED_STALE_HANDOFF", "Implementation handoff is stale")
    if action == "WAITING_FOR_USER":
        raise ValueError("WAITING_FOR_USER needs a material structured user_request")
    state["plan"] = c["plan"]
    state.setdefault("implementation_handoffs", []).append(
        {
            "at": support.now(),
            "output": record["output"],
            "action": action,
            "reason": c["reason"],
            "source_revision": record["source_revision"],
        }
    )
    state.pop("final_audit_request", None)
    if action == "CONTINUE":
        spec = c["next_task"]
        decision = {
            "status": "CONTINUE",
            "next_objective": spec["objective"],
            "affected_paths": spec["affected_paths"],
            "next_task": {k: v for k, v in spec.items() if k not in ("objective", "affected_paths")},
        }
        lifecycle.assign_task(state, decision, {"revision": record["source_revision"]})
        state.update(next_stage="terra", next_action=spec["objective"], affected_paths=spec["affected_paths"])
        state["iteration"] += 1
        goals.record_decision(state, decision)
    elif action == "ESCALATE_SOL":
        if not c["question"].strip() or not c["reason"].strip():
            raise ValueError("A Validator escalation needs a specific question and evidence of the blocker")
        state["targeted_consultation"] = {
            "question": c["question"],
            "reason": c["reason"],
            "task_id": value["task_id"],
            "contract_hash": value["contract_hash"],
            "source_revision": record["source_revision"],
        }
        state["next_stage"] = "sol"
        state["iteration"] += 1
    elif action == "REQUEST_FINAL_AUDIT":
        if value["untested_behavior"]:
            raise ValueError("Untested behavior must be addressed or remain explicitly blocked, not final-audit ready")
        # Verify the self-check through the SAME evidence validator, but only on a
        # disposable state. It never becomes independent validation in live state.
        probe = copy.deepcopy(state)
        runner._apply_result(probe, "self_check", c["self_assessment"], record, workspace, run_dir)
        decision = {
            "status": "COMPLETE",
            "contract_hash": value["contract_hash"],
            "contract_revision": value["contract_revision"],
            "task_id": value["task_id"],
            "user_request": value["user_request"],
            "acceptance_criteria": [
                {**a, "status": "verified", "evidence": "Builder self-check; not independent"}
                for a in state["acceptance_criteria"]
            ],
        }
        if not completion_gate.completion_ready(
            probe,
            decision,
            source_scope.snapshot(workspace, state, base_snapshot=support.snapshot),
            require_human_reviews=False,
            require_independent=False,
        ):
            raise ValueError("Final audit requires current executed self-check evidence for every approved criterion")
        state["final_audit_request"] = {**probe["validation"], "requested_at": support.now(), "independent": False}
        state["next_stage"] = "astra_checkpoint"


FINAL_POLICY = prompts.get("fragments/workflow/final-policy.md")

FINAL_CHECKPOINT = prompts.get("fragments/workflow/final-checkpoint.md")


def activate(state, *, approval_source):
    """Called only by an explicit operator action at an idle, reconciled boundary."""
    if state.get("settings", {}).get("milestone_checkpoints", {}).get("enabled"):
        raise ValueError("Enforced milestone checkpoints require the separate Validator review")
    if state.get("active_stage") or state.get("pending_report_repair") or state.get("uncertain_artifacts"):
        raise ValueError("Finish/reconcile the in-flight stage before changing workflow")
    if state.get("status") == "RUNNING" or not goals.approved(state):
        raise ValueError("Workflow activation requires an idle approved goal")
    if enabled(state):
        guard(state)
        return
    event = {
        "kind": "workflow_approval",
        "actor": "user",
        "at": support.now(),
        "policy": APPROVAL_TEXT,
        "contract_hash": state["goal_contract"]["hash"],
        "source": approval_source,
    }
    state.setdefault("user_events", []).append(event)
    state["settings"]["workflow"] = {"mode": MODE, "approval_event": event}
    state.setdefault("configuration_changes", []).append(
        {
            "at": support.now(),
            "reason": APPROVAL_TEXT,
            "previous_next_stage": state.get("next_stage"),
            "previous_workflow": "astra_terra_sol_astra",
        }
    )
    if state.get("next_stage") == "sol":
        state["next_stage"] = "astra_checkpoint"


def checkpoint_schema(schema_dir):
    validation = goals.role_schema(support.read(schema_dir / "v2/sol-report.schema.json"), "sol")
    decision = goals.role_schema(support.read(schema_dir / "v2/astra-decision.schema.json"), "astra")
    return goals.obj(
        {
            "validation": validation,
            "decision": decision,
            "consult_sol": goals.obj(
                {"requested": {"type": "boolean"}, "question": goals.STRING, "reason": goals.STRING}
            ),
        }
    )


def rollback(state):
    """Restore legacy routing at a safe checkpoint without restoring old task state."""
    if (
        state.get("active_stage")
        or state.get("pending_report_repair")
        or state.get("uncertain_artifacts")
        or state.get("status") == "RUNNING"
    ):
        raise ValueError("Pause and reconcile active work before rolling back routing")
    if not enabled(state):
        return
    old = state["settings"].pop("workflow")
    state.setdefault("configuration_changes", []).append(
        {"at": support.now(), "reason": "Explicit rollback to legacy Validator routing", "previous_workflow": old}
    )
    if state.get("validation", {}).get("reviewer_role") == "astra":
        state.setdefault("validation_archive", []).append(
            {"reason": "Routing rollback requires Validator revalidation", "validation": state.pop("validation")}
        )
        state["human_reviews"] = {}
        state.pop("displayed_review", None)
    if state.get("next_stage") == "astra_checkpoint" or state.get("status") == "TASK_COMPLETE":
        if state.get("status") == "TASK_COMPLETE":
            state.setdefault("completion_archive", []).append(
                {"completed_at": state.pop("completed_at", None), "decision": state.pop("final_decision", None)}
            )
        state.update(
            next_stage="sol",
            status="PAUSED_WORKFLOW_ROLLBACK",
            phase="PAUSED_OR_BLOCKED",
            stop_reason="Legacy routing restored; resume the same run for Validator validation",
        )


POLICY = prompts.get("fragments/workflow/policy.md")

CHECKPOINT = prompts.get("completion-checkpoint.md")


def apply_checkpoint(runner, state, value, record, workspace, run_dir):
    guard(state)
    if not enabled(state) or record.get("role") != "astra":
        raise ValueError("Independent checkpoint must run under the approved Plan Reviewer role")
    support.validate_schema(
        value, design_coverage.extend_schema(checkpoint_schema(runner.SCHEMA_DIR), state, "astra_checkpoint")
    )
    current = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    if record.get("source_revision") != current["revision"] or record.get("changed_files"):
        raise support.Paused("PAUSED_STALE_VALIDATION", "Checkpoint does not match a read-only current artifact")
    validation, decision, consult = value["validation"], value["decision"], value["consult_sol"]
    if final_only(state):
        dispatch_guard(state, "astra_checkpoint", workspace)
        if consult["requested"]:
            raise ValueError(
                "Final audit returns findings to the Builder; a Validator escalation is only Builder-requested"
            )
    if validation["user_request"] != decision["user_request"]:
        raise ValueError("Validation and decision must agree on the pending user decision")
    if consult["requested"] and (
        not consult["question"].strip()
        or not consult["reason"].strip()
        or decision["status"] != "CONTINUE"
        or decision["next_task"]["kind"] != "validate"
        or decision["user_request"]["kind"] != "none"
    ):
        raise ValueError("A Validator escalation requires a concrete question and a non-completion validate decision")

    if not consult["requested"] and (consult["question"] or consult["reason"]):
        raise ValueError("Unused Validator escalation must be empty")
    stages, history = len(state.get("stages", [])), len(state.get("history", []))
    # Reuse existing validators and goal transitions transactionally. These are two
    # logical results from ONE provider call, so persist only its single receipt.
    runner._apply_result(state, "sol", validation, record, workspace, run_dir)
    state["validation"]["reviewer_role"] = "astra"
    state["validation"]["final_audit"] = final_only(state)
    if consult["requested"]:
        goals.execution_guard(state, decision)
        if support.criteria_definition(decision["acceptance_criteria"]) != support.criteria_definition(
            state["acceptance_criteria"]
        ):
            raise ValueError("Consultation cannot change acceptance criteria")
        state["targeted_consultation"] = {
            **copy.deepcopy(consult),
            "task_id": validation["task_id"],
            "source_revision": current["revision"],
            "contract_hash": validation["contract_hash"],
        }
        state["next_stage"] = "sol"
        state["iteration"] += 1
        goals.record_decision(state, decision)
    else:
        runner._apply_result(state, "astra_review", decision, record, workspace, run_dir)
        state.pop("targeted_consultation", None)
    state["stages"] = state.get("stages", [])[:stages]
    state["history"] = state.get("history", [])[:history]
    runner.save_record(state, record)
