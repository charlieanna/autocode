"""The context packet each model stage is given: its instruction, the policies, and the current
handoff data from the contract, milestones, findings, workflow and regression proof.

It sits above those modules, so autocode_support, which they use, no longer imports them for it. The
prompt text (STABLE, COMMON, ...) stays in autocode_support, where the planning unit reads it too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_execution_prompt as execution_prompt
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_execution_prompt as execution_prompt
    import autocode_prompts as prompts


try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import json
import shlex
from pathlib import Path

try:
    from . import autocode_coordination_context as coordination_context
    from . import autocode_design_manifest as design_manifest
    from . import autocode_milestone_replan as replan
    from . import autocode_package_writes as package_writes
    from . import autocode_protected_oracles as protected_oracles
    from . import autocode_review_source as review_source
    from . import autocode_stage_roles as stage_roles
    from . import autocode_support as support
    from . import autocode_visual_evidence as visual
    from .autocode_util import criteria_definition
except ImportError:
    import autocode_coordination_context as coordination_context
    import autocode_design_manifest as design_manifest
    import autocode_milestone_replan as replan
    import autocode_package_writes as package_writes
    import autocode_protected_oracles as protected_oracles
    import autocode_review_source as review_source
    import autocode_stage_roles as stage_roles
    import autocode_support as support
    import autocode_visual_evidence as visual
    from autocode_util import criteria_definition


def context_packet(state: RunState, stage, state_path, *, response_schema=None):
    try:
        from . import autocode_milestones as checkpoints
    except ImportError:
        import autocode_milestones as checkpoints
    try:
        from . import autocode_workflow as workflow
    except ImportError:
        import autocode_workflow as workflow
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
    coordinated = coordination_context.context(state)
    if coordinated:
        base["coordinated_member"] = coordinated
    elif stage in ("requirements_gather", "requirements", "recognize_workflow"):
        try:
            from . import autocode_catalog_context as source_catalog
        except ImportError:
            import autocode_catalog_context as source_catalog
        if catalog := source_catalog.context(state):
            base["source_catalog"] = catalog
    if stage in ("requirements_gather", "requirements"):
        try:
            from . import autocode_project_memory_lifecycle as project_memory
        except ImportError:
            import autocode_project_memory_lifecycle as project_memory
        base["project_memory"] = project_memory.context(state)
    current = source_scope.snapshot(Path(state["workspace"]), state, base_snapshot=support.snapshot)
    base.update(
        workspace=state["workspace"],
        source_revision=current["revision"],
        git_head=current["head"],
        current_task=state.get("current_task"),
        execution_limits=state["settings"].get("limits", {}),
        execution_engine=stage_roles.engine_for(state["settings"], stage_roles.role_for(state, stage)),
    )
    if stage == "terra":
        base["builder_artifact_policy"] = {
            "evidence_directory": (state.get("recovery_context") or {}).get("diagnostic_directory")
            or str(Path(state_path).parent / "evidence"),
            "instruction": prompts.get("fragments/stage-context/context-packet-04.md"),
        }
        # A package run's Builder may change only files inside the package (#711).
        write_scope = package_writes.context(state["settings"])
        if write_scope:
            base["write_scope"] = write_scope
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
        changed_code = (state.get("regression_proof") or {}).get("changed_code_evidence")
        if changed_code and changed_code.get("status") != "PASS":
            base["changed_code_evidence"] = changed_code
            try:
                from . import autocode_changed_code_rework as changed_code_rework
            except ImportError:
                import autocode_changed_code_rework as changed_code_rework
            guidance = changed_code_rework.guidance(state, current["revision"])
            if guidance:
                base["changed_code_investigation"] = guidance
        repair = state.get("repair_plan") or {}
        if any(
            task.get("id") == cast(dict, state.get("current_task", {})).get("id") for task in repair.get("tasks", [])
        ):
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
        attached_source = review_source.context(state, current)
        if attached_source:
            base["review_source"] = attached_source
        try:
            from . import autocode_regression as regression
        except ImportError:
            import autocode_regression as regression
        proof = regression.handoff(state)
        if proof:
            base["regression_proof"] = proof
            proof_note = regression.PROMPT_NOTES["passed" if proof["verdict"] == "PASS" else "open"][
                "validator" if stage == "sol" else "owner"
            ] + regression.mutation_note(proof)
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
    if stage in ("requirements_gather", "requirements"):
        instruction += prompts.get("fragments/autoplanner/project-memory.md")
    if manifest_context:
        instruction += (
            design_manifest.INSTRUCTION_SCOPED_BASE
            if state["settings"].get("design_manifest", {}).get("scope_admission")
            else design_manifest.INSTRUCTION
        )
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
        execution_instruction = goals.EXECUTION_PROMPT + execution_prompt.criteria_instruction(response_schema)
        instruction = (
            goals.DISCOVERY_PROMPT + goals.JOB_TYPE_POLICY + goals.DECISION_PROVENANCE
            if stage == "astra_discovery"
            else instruction + execution_instruction
        )
        if stage == "astra_discovery":
            # A non-joint run has no Plan Reviewer; the discovery draft's criteria
            # still cite the run's human sources verbatim (#595).
            try:
                from . import autocode_brief_obligations as brief_obligations
                from . import autocode_source_trace as source_trace
            except ImportError:
                import autocode_brief_obligations as brief_obligations
                import autocode_source_trace as source_trace
            records = brief_obligations.sources(state)
            if records:
                base["source_inventory"] = [
                    {"id": row["id"], "kind": row["kind"], "text": row["text"]}
                    for row in source_trace.quotable(records)
                ]
                # Full brief once in source_inventory; do not also paste it under task (#1337).
                base["task"] = brief_obligations.handoff_task(base["task"], base["source_inventory"])
                instruction += source_trace.rule(records)
        if stage in ("astra_plan", "astra_review"):
            instruction += support.ASTRA_DECISIONS
    # No previous transcripts or history array is forwarded; exact goals are never truncated.
    milestone_policy = support.MILESTONE_POLICY
    if checkpoints.enabled(state):
        base["milestone_checkpoint"] = checkpoints.summary(state)
        base["milestone_checkpoint"]["current_evidence_ready"] = checkpoints.evidence_ready(state, current)
        base["current_milestone"] = checkpoints.scope(state)
        milestone_policy += checkpoints.POLICY
        if cast(dict, state.get("current_task", {})).get("milestone_ids"):
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
            instruction = workflow.CHECKPOINT + execution_instruction
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
