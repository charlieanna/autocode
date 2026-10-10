"""The context packet each model stage is given: its instruction, the policies, and the current
handoff data from the contract, milestones, findings, workflow and regression proof.

It sits above those modules, so autocode_support, which they use, no longer imports them for it. The
prompt text (STABLE, COMMON, ...) stays in autocode_support, where the planning unit reads it too.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import json
import shlex
from pathlib import Path

try:
    from . import autocode_design_manifest as design_manifest
    from . import autocode_milestone_replan as replan
    from . import autocode_protected_oracles as protected_oracles
    from . import autocode_support as support
    from . import autocode_visual_evidence as visual
    from .autocode_util import criteria_definition
except ImportError:
    import autocode_design_manifest as design_manifest
    import autocode_milestone_replan as replan
    import autocode_protected_oracles as protected_oracles
    import autocode_support as support
    import autocode_visual_evidence as visual
    from autocode_util import criteria_definition


def context_packet(state, stage, state_path):
    try:
        from . import autocode_milestones as checkpoints
    except ImportError:
        import autocode_milestones as checkpoints
    try:
        from . import autocode_workflow as workflow
    except ImportError:
        import autocode_workflow as workflow
    try:
        from . import autocode_planning as planning
    except ImportError:
        import autocode_planning as planning
    criteria = state.get("acceptance_criteria", [])
    base = {
        "task": state["task"],
        "state_file": str(state_path),
        "stage": stage,
        "criteria_revision": state.get("criteria_revision"),
        "acceptance_criteria": criteria_definition(criteria),
        "next_action": state.get("next_action"),
        "plan": state.get("plan", []),
        "checkpoint_reason": state.get("stop_reason"),
        "recovery_context": state.get("recovery_context"),
        "evidence_locations": state.get("evidence_locations", []),
        "private_source_exceptions": state.get("private_source_exceptions", []),
        "context_policy": prompts.get("fragments/stage-context/context-packet.md"),
    }
    current = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=support.snapshot)
    base.update(
        workspace=state["workspace"],
        source_revision=current["revision"],
        git_head=current["head"],
        current_task=state.get("current_task"),
        execution_limits=state["settings"].get("limits", {}),
        execution_engine=planning.engine_for(state["settings"], planning.role_for(state, stage)),
    )
    if stage == "terra":
        base["builder_artifact_policy"] = {
            "evidence_directory": (state.get("recovery_context") or {}).get("diagnostic_directory")
            or str(Path(state_path).parent / "evidence"),
            "instruction": prompts.get("fragments/stage-context/context-packet-04.md"),
        }
    protected = protected_oracles.context(
        state["settings"], (state.get("current_task") or {}).get("affected_paths", [])
    )
    if protected:
        base["protected_tests"] = protected
    manifest_context = design_manifest.context(state["settings"], stage=stage, current_task=state.get("current_task"))
    if manifest_context:
        base["design_manifest"] = manifest_context
    capture_context = visual.context(state, current)
    if capture_context:
        base["implementation_captures"] = capture_context
    prerequisites = state["settings"].get("task_preflight", {}).get("body", {})
    if prerequisites.get("design"):
        base["design_readiness"] = prerequisites["design"]
        base["design_readiness_instruction"] = prompts.get("fragments/stage-context/context-packet-02.md")
    figma_file = state["settings"].get("figma_file")
    if figma_file:
        base["figma_file"] = figma_file
    try:
        from . import autocode_findings as findings_ledger
    except ImportError:
        import autocode_findings as findings_ledger
    if state.get("findings_ledger"):
        # Both reviewers' open findings, each with its identity and assigned fix task.
        base["open_findings"] = findings_ledger.handoff(state)
    if stage in ("sol", "astra_review", "astra_checkpoint"):
        source = "sol" if stage == "sol" else "astra"
        base["review_identity_policy"] = {
            "own_open_finding_ids": [r["id"] for r in findings_ledger.open_entries(state, source)],
            "instruction": prompts.get("fragments/stage-context/context-packet-05.md"),
        }
    if stage == "terra":
        base.update(
            affected_paths=state.get("affected_paths", []), actionable_findings=state.get("unresolved_findings", [])
        )
        repair = state.get("repair_plan") or {}
        if any(task.get("id") == state.get("current_task", {}).get("id") for task in repair.get("tasks", [])):
            base["repair_plan"] = repair
    elif stage in ("sol", "astra_checkpoint"):
        impl = state.get("implementation", {})
        base.update(
            implementation=impl,
            actual_changes=state.get("changed_files", []),
            source_snapshot=state.get("source_snapshot"),
            diff_ref=state.get("diff_ref"),
        )
        if stage == "astra_checkpoint":
            base.update(validation=state.get("validation"), unresolved_findings=state.get("unresolved_findings", []))
    else:
        impl = state.get("implementation", {})
        validation = state.get("validation", {})
        base.update(
            implementation=impl, validation=validation, unresolved_findings=state.get("unresolved_findings", [])
        )
    if stage in ("sol", "astra_checkpoint", "astra_review"):
        try:
            from . import autocode_regression as regression
        except ImportError:
            import autocode_regression as regression
        proof = regression.handoff(state)
        if proof:
            base["regression_proof"] = proof
            proof_note = regression.PROMPT_NOTES["passed" if proof["verdict"] == "PASS" else "open"][
                "validator" if stage == "sol" else "owner"
            ]
        else:
            proof_note = ""
    else:
        proof_note = ""
    import sys

    try:
        from . import autocode_output_policy as output_policy
    except ImportError:
        import autocode_output_policy as output_policy
    base["output_transport"] = output_policy.context(state.get("settings") or {})
    base["capture_command"] = shlex.join(
        [
            sys.executable,
            str(Path(__file__).with_name("autocode.py")),
            "capture",
            "--mode",
            output_policy.mode(state.get("settings") or {}),
        ]
    )
    base["baseline_compare_command"] = shlex.join(
        [sys.executable, str(Path(__file__).with_name("autocode.py")), "compare-baseline"]
    )
    instruction = support.STABLE.get(stage, "") + proof_note
    if manifest_context:
        instruction += design_manifest.INSTRUCTION
    if capture_context:
        instruction += visual.INSTRUCTION
        base["visual_capture_command"] = shlex.join(
            [sys.executable, str(Path(__file__).with_name("autocode.py")), "visual-capture"]
        )
    if stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
        try:
            from . import autocode_progressive_state as progressive_state
            from . import autocode_verification_plan as verification_plan
        except ImportError:
            import autocode_progressive_state as progressive_state
            import autocode_verification_plan as verification_plan
        progressive = progressive_state.context(state)
        base["verification_obligations"] = verification_plan.obligations(state, progressive_context=progressive)
        instruction += prompts.get("fragments/stage-context/context-packet-03.md")
        if progressive:
            base["progressive_verification"] = {
                **progressive,
                "required_commands": verification_plan.approved_commands(state, progressive_context=progressive),
            }
            instruction += prompts.get("fragments/stage-context/context-packet-06.md")
    if figma_file:
        try:
            from . import autocode_figma as figma
        except ImportError:
            import autocode_figma as figma
        instruction += figma.instructions(state["settings"], stage=stage, current_task=state.get("current_task"))
    if state.get("version", 2) >= 3:
        try:
            from . import autocode_goals as goals
        except ImportError:
            import autocode_goals as goals
        base.update(
            goal_contract=state.get("goal_contract"),
            saved_answers=state.get("answers", {}),
            brief_feedback=state.get("brief_feedback", []),
            pending_questions=state.get("pending_questions", []),
            user_request=state.get("user_request"),
            agent_request=state.get("agent_request"),
            permission_reuse_context=state.get("permission_reuse_context"),
            human_reviews=state.get("human_reviews", {}),
            deferred_backlog=state.get("deferred_backlog", []),
            preserved_checkpoint=state.get("pre_goal_checkpoint"),
        )
        base["milestone_status"] = goals.milestone_status(state, current)
        base["prior_validation_reports"] = [
            {k: entry["validation"].get(k) for k in ("output", "source_revision", "contract_revision", "verdict")}
            for entry in state.get("validation_archive", [])
        ]
        instruction = (
            goals.DISCOVERY_PROMPT + goals.JOB_TYPE_POLICY + goals.DECISION_PROVENANCE
            if stage == "astra_discovery"
            else instruction + goals.EXECUTION_PROMPT
        )
        if stage in ("astra_plan", "astra_review"):
            instruction += support.ASTRA_DECISIONS
    # No previous transcripts or history array is forwarded; exact goals are never truncated.
    milestone_policy = support.MILESTONE_POLICY
    if checkpoints.enabled(state):
        base["milestone_checkpoint"] = checkpoints.summary(state)
        base["milestone_checkpoint"]["current_evidence_ready"] = checkpoints.evidence_ready(state, current)
        base["current_milestone"] = checkpoints.scope(state)
        milestone_policy += checkpoints.POLICY
        if state.get("current_task", {}).get("milestone_ids"):
            milestone_policy += prompts.get("fragments/stage-context/context-packet-07.md")
        elif stage == "sol":
            milestone_policy += prompts.get("fragments/stage-context/context-packet-10.md")
        checkpoint = base["milestone_checkpoint"]
        pending_replan = replan.constraint(checkpoint["current"], checkpoint["limits"])
        if pending_replan and stage in ("astra_plan", "astra_review"):
            # State the gate the decision must pass, and drop the general rule it refuses (#459).
            instruction = instruction.replace(
                replan.GENERAL_VALIDATE_RULE, replan.validate_rule(checkpoint["current"], checkpoint["limits"])
            )
            milestone_policy += pending_replan
    if workflow.enabled(state):
        workflow.guard(state)
        base["workflow"] = state["settings"]["workflow"]
        base["targeted_consultation"] = state.get("targeted_consultation")
        milestone_policy = workflow.POLICY
        if stage == "astra_checkpoint":
            instruction = workflow.CHECKPOINT + goals.EXECUTION_PROMPT
        elif stage == "sol":
            instruction += prompts.get("fragments/stage-context/context-packet-11.md")
        if workflow.final_only(state):
            milestone_policy = workflow.FINAL_POLICY
            base["final_audit_request"] = state.get("final_audit_request")
            base["consultation_reports"] = state.get("consultation_reports", [])[-1:]
            if stage == "astra_checkpoint":
                instruction += workflow.FINAL_CHECKPOINT
    if stage in ("sol", "astra_checkpoint", "astra_review"):
        try:
            from . import autocode_bug_job as bug_job
        except ImportError:
            import autocode_bug_job as bug_job
        artifact = bug_job.diagnosis_artifact(state, state["workspace"])
        if artifact:
            base["runner_artifacts"] = [artifact]
            instruction += prompts.get("fragments/stage-context/context-packet-08.md")
    try:
        from . import autocode_context
    except ImportError:
        import autocode_context
    full_data_bytes = len(json.dumps(base, indent=2).encode())
    base, externalized = autocode_context.compact(base, state_path)
    prompt = (
        instruction
        + milestone_policy
        + support.COMMON
        + support.BASELINE_POLICY
        + "\nCURRENT HANDOFF DATA\n"
        + json.dumps(base)
    )
    return prompt, {
        "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
        "estimate_method": prompts.get("fragments/stage-context/context-packet-09.md"),
        "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000),
        "externalized_fields": externalized,
        "handoff_bytes_saved": full_data_bytes - len(json.dumps(base).encode()),
    }
