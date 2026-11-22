"""Autoreview owns independent verification and evidence validation, the Design
Reviewer's check of an approved design (autocode_design_check_job), the Figma design
inventory (autocode_design_intake) and a spec-first run's acceptance tests
(autocode_acceptance_tests_job)."""

from __future__ import annotations

try:
    from .. import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import hashlib
import shlex
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..autocode_run_state import RunState

try:
    from .. import autocode_acceptance_reference as acceptance_reference
    from .. import autocode_acceptance_tests_job as acceptance_tests_job
    from .. import autocode_counterexample as counterexample
    from .. import autocode_design_check_job as design_check_job
    from .. import autocode_design_intake as design_intake
    from .. import autocode_goals as goals
    from .. import autocode_launch_inputs as launch_inputs
    from .. import autocode_quota_route as quota_route
    from .. import autocode_recovery_novelty as novelty
    from .. import autocode_role_transport as role_transport
    from .. import autocode_run_status as run_status
    from .. import autocode_task_authority as task_authority
    from .. import autocode_tool_handoff as tool_handoff
    from .. import autocode_toolchain_requirements as toolchain_requirements
    from .. import autocode_variant_patch as variant_patch
    from .. import autocode_verification_commands as command_policy
    from .. import autocode_verification_plan as verification_plan
    from .. import autocode_verify as verify
    from .. import autocode_wrong_implementations as wrong_implementations
except ImportError:
    import autocode_acceptance_reference as acceptance_reference
    import autocode_acceptance_tests_job as acceptance_tests_job
    import autocode_counterexample as counterexample
    import autocode_design_check_job as design_check_job
    import autocode_design_intake as design_intake
    import autocode_goals as goals
    import autocode_launch_inputs as launch_inputs
    import autocode_quota_route as quota_route
    import autocode_recovery_novelty as novelty
    import autocode_role_transport as role_transport
    import autocode_run_status as run_status
    import autocode_task_authority as task_authority
    import autocode_tool_handoff as tool_handoff
    import autocode_toolchain_requirements as toolchain_requirements
    import autocode_variant_patch as variant_patch
    import autocode_verification_commands as command_policy
    import autocode_verification_plan as verification_plan
    import autocode_verify as verify
    import autocode_wrong_implementations as wrong_implementations
from . import autoplanner
from .common import ModelRequest, capped_route, execution_request

PROBE_TIMEOUT = 120  # seconds per design probe; a probe checks one fact about the code, it is not a suite
COMPLETION_REVIEW_STOP = (
    "All required criteria already pass; request completion instead of another implementation batch"
)
SEND_BACK_NOTE = prompts.get("fragments/autoreview/send-back-note.md")
JOBS = {
    design_check_job.STAGE: design_check_job,
    design_intake.STAGE: design_intake,
    acceptance_tests_job.STAGE: acceptance_tests_job,
    wrong_implementations.STAGE: wrong_implementations,
}
# The Architect copies the Plan Reviewer's model but not its effort beyond this: at
# "high", MiMo twice spent its whole reasoning budget on a dense design and returned
# no report at all (2026-09-27, design-review-sound live runs).
ARCHITECT_MAX_EFFORT = "medium"


def architect_route(roles):
    return capped_route(roles.get("plan_reviewer") or roles["task_planner"], ARCHITECT_MAX_EFFORT)


def verification_commands(state: RunState):
    """The same approved command set shown to the Validator and independently replayed."""
    try:
        from .. import autocode_progressive_state as progressive_state
        from .. import autocode_verification_plan as plan
    except ImportError:
        import autocode_progressive_state as progressive_state
        import autocode_verification_plan as plan
    return plan.approved_commands(state, progressive_context=progressive_state.context(state))


def prepare(state: RunState, stage, state_path, schema_dir):
    if stage == design_intake.STAGE:
        state["phase"] = "DISCOVERING"
        role = next(name for name in ("requirements", "planner", "task_planner") if name in state["settings"]["roles"])
        return job_request(state, design_intake, role, role)
    if stage == acceptance_tests_job.STAGE:
        # Spec-first (#1200): the Validator's model writes the acceptance tests, on a route of its own so
        # that session never leaks into validation. The Builder's model stays a different one (cross-model).
        state["phase"] = "EXECUTING"
        roles = state["settings"]["roles"]
        if "acceptance_tester" in roles:
            quota_route.sync_derived_role(state, "acceptance_tester")
        else:
            roles["acceptance_tester"] = deepcopy(roles["tester"])
        role_transport.inherit(state.get("settings"), "acceptance_tester", "tester")
        return job_request(state, acceptance_tests_job, "tester", "acceptance_tester")
    if stage == wrong_implementations.STAGE:
        # #3977: the Tester's model on its acceptance_tester route writes wrong implementations blind to the tests.
        state["phase"] = "REVIEWING"
        roles = state["settings"]["roles"]
        if "acceptance_tester" in roles:
            quota_route.sync_derived_role(state, "acceptance_tester")
        else:
            roles["acceptance_tester"] = deepcopy(roles["tester"])
        role_transport.inherit(state.get("settings"), "acceptance_tester", "tester")
        return job_request(state, wrong_implementations, "tester", "acceptance_tester")
    if stage == design_check_job.STAGE:
        # The Design Reviewer inherits the Plan Reviewer's model (the planner's own when
        # there is none), with effort capped at ARCHITECT_MAX_EFFORT, on a route of
        # its own ("architect") so its session never leaks into later planning.
        state["phase"] = "REVIEWING"
        roles = state["settings"]["roles"]
        if "architect" in roles:
            quota_route.sync_derived_role(state, "architect")
        else:
            roles["architect"] = architect_route(roles)
        source = "plan_reviewer" if "plan_reviewer" in roles else "task_planner"
        role_transport.inherit(state.get("settings"), "architect", source)
        return job_request(state, JOBS[stage], "task_planner", "architect")
    if stage not in ("tester", "review_completion", "review_checkpoint"):
        raise ValueError(f"Autoreview cannot run {stage}")
    request = execution_request(state, stage, state_path, schema_dir)
    authority = task_authority.instruction(state.get("investigation"))
    # #3977: wrong implementations the acceptance tests still accept are the independent Tester's evidence.
    survivors = wrong_implementations.survivors_note(state) if stage == "tester" else ""
    authority = "\n".join(part for part in (authority, survivors) if part)
    if authority:
        prompt = tool_handoff.before_handoff(request.prompt, "\n" + authority)
        request = replace(
            request,
            prompt=prompt,
            metrics={**request.metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4},
        )
    if stage == "review_completion":
        try:
            from .. import autocode_progressive_state as progressive_state
        except ImportError:
            import autocode_progressive_state as progressive_state
        schema = deepcopy(request.schema)
        schema["properties"]["recovery_change"] = deepcopy(novelty.CHANGE_SCHEMA)
        request = replace(request, schema=schema)
        # The Completion Reviewer writes the next task's plan, as the Planner and the Resolver do (#185).
        note = (
            novelty.INSTRUCTION
            + "\n"
            + verification_plan.GIT_STATUS_RULE
            + "\n"
            + verification_plan.SHELL_SYNTAX_RULE
            + "\n"
        )
        if progressive_state.enabled(state):
            schema = deepcopy(request.schema)
            schema["properties"]["progressive_checkpoint"] = {"type": "boolean"}
            request = replace(request, schema=schema)
            note += prompts.get("fragments/autoreview/prepare.md")
        if (((state.get("goal_contract") or {}).get("body") or {}).get("source_trace")) is not None:
            # A contract sealed with a source trace gets a verdict against the sources
            # recorded by the Completion Reviewer (#595); the runner recomputes coverage
            # mechanically either way.
            schema = deepcopy(request.schema)
            schema["properties"]["source_verdict"] = {
                "type": "object",
                "additionalProperties": False,
                "required": ["verdict", "uncovered", "summary"],
                "properties": {
                    "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                    "uncovered": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
            }
            request = replace(request, schema=schema)
            note += prompts.get("fragments/autoreview/source-verdict.md") + "\n"
        prompt = tool_handoff.before_handoff(request.prompt, note)
        request = replace(
            request,
            prompt=prompt,
            metrics={**request.metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4},
        )
    if stage == "review_checkpoint":
        # Under reviewer routing this decision is applied as the Completion Reviewer's, next_task included.
        prompt = tool_handoff.before_handoff(
            request.prompt,
            "\n" + verification_plan.GIT_STATUS_RULE + "\n" + verification_plan.SHELL_SYNTAX_RULE + "\n",
        )
        request = replace(
            request,
            prompt=prompt,
            metrics={**request.metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4},
        )
    if stage == "tester" and cast(dict, state.get("current_task", {})).get("milestone_ids"):
        request.schema["properties"]["milestone_results"] = {
            "type": "array",
            "items": goals.obj(
                {
                    "milestone_id": goals.STRING,
                    "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
                    "summary": goals.STRING,
                    "evidence_refs": goals.STRINGS,
                }
            ),
        }
        request.schema["required"].append("milestone_results")
    return request


def job_request(state: RunState, job, role, route):
    prompt, metrics = job.prompt(
        state,
        autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000),
        autoplanner.engine_for(state["settings"], route),
    )
    # A job whose report shape depends on the run (the Architect revising a review) names its schema.
    schema = job.schema_for(state) if hasattr(job, "schema_for") else job.SCHEMA
    return ModelRequest(role, route, prompt, metrics, schema, True)


def _planned_go_targeted(state: RunState, tests):
    """The approved plan's recognized Go suite command narrowed to these files (#3091).

    With no saved ``--test-command`` the proof must not tree-detect a plain ``go test ./...``
    and drop the env prefix the plan's own command carries."""
    for entry in toolchain_requirements.initial_validation(state):
        for command in verification_plan.commands(entry):
            framework = command_policy.command_framework(command)
            if framework is not None and framework.name == "go":
                narrowed = framework.targeted(list(tests))
                if narrowed:
                    return narrowed
    return None


def apply_job(stage, state: RunState, value, record, workspace, run_dir=None, runtime=None):
    """Autopilot hands a job stage's validated report here; the job decides how the run continues."""
    evidence_dir = Path(record.get("output") or workspace).parent
    if stage == design_intake.STAGE:
        design_intake.apply(state, value, record, workspace)
        return
    clean_run, _ = launch_inputs.runners(
        state, workspace, run_dir or Path(record.get("output") or workspace).parent.parent, verify.scratch_run
    )
    if stage == acceptance_tests_job.STAGE:
        # The runner, not the Tester, shows each case's test failing (or a guard passing) on today's code.
        acceptance_tests_job.apply(
            state,
            value,
            record,
            workspace,
            run_tests=lambda tests: clean_run(
                workspace,
                evidence_dir / "acceptance-proof",
                tests=tests,
                command=command_policy.configured_targeted(state.get("settings"), tests)
                or _planned_go_targeted(state, tests),
                acceptance_preflight=True,
                timeout=verify.DEFAULT_TIMEOUT,
            ),
            evidence_dir=evidence_dir,
            run_reference=lambda selected, tests: acceptance_reference.collect(
                state,
                workspace,
                run_dir,
                evidence_dir / "acceptance-reference",
                selected,
                tests,
                command=command_policy.configured_targeted(state.get("settings"), tests)
                or _planned_go_targeted(state, tests),
                # Which original-code failures count as evidence depends on the task, as in the regression proof.
                new_behavior=goals.task_kind(state) != "bugfix",
            ),
            send_back=lambda body, changes: send_back_plan(state, body, changes, record),
        )
        return
    if stage == wrong_implementations.STAGE:
        # The runner, not the writer, shows each variant wrong (its demo) and runs the acceptance tests on it.
        wrong_implementations.apply(
            state,
            value,
            record,
            workspace,
            run_demo=lambda patch, argv: _study_run(
                clean_run, workspace, evidence_dir, patch, command=shlex.join(argv)
            ),
            run_tests=lambda patch, files: _study_tests(state, clean_run, workspace, evidence_dir, patch, files),
        )
        return
    # The Design Reviewer's concerns and conflicts about today's code carry probes the runner runs itself, on the
    # code and again with each one's counterexample made, where the probe must fail (#1769).
    JOBS[stage].apply(
        state,
        value,
        record,
        workspace,
        run_probe=lambda command: clean_run(
            workspace, evidence_dir / "design-probes", command=command, timeout=PROBE_TIMEOUT
        ),
        run_counterexample=counterexample.runner(
            clean_run, workspace, evidence_dir / "design-probes", timeout=PROBE_TIMEOUT
        ),
    )


def _variant(workspace, evidence_dir, patch):
    """``{"files": overlay}`` for the scratch runner, or ``{"error": why}``; ``patch`` None is the candidate itself.

    The variant's patch is applied to copies of the files it changes (autocode_variant_patch), never as a patch
    file the scratch runner applies with plain ``git apply``, which refuses the hand-written hunks the writer is
    told it may send (no context lines, inexact line counts)."""
    if patch is None:
        return {"files": None}
    name = hashlib.sha256(patch.encode()).hexdigest()
    try:
        return {"files": variant_patch.files(workspace, patch, Path(evidence_dir) / "wrong-implementations" / name)}
    except ValueError as error:
        return {"error": str(error)}


def _study_run(clean_run, workspace, evidence_dir, patch, *, command=None, timeout=PROBE_TIMEOUT):
    """One scratch execution of a variant's demo: ``patch`` None runs it on the candidate itself."""
    variant = _variant(workspace, evidence_dir, patch)
    if "error" in variant:
        return {"exit_code": None, "error": variant["error"]}
    return clean_run(
        workspace,
        evidence_dir / "wrong-implementations",
        files=variant["files"],
        command=command,
        timeout=timeout,
    )


def _study_tests(state: RunState, clean_run, workspace, evidence_dir, patch, files):
    """The failed acceptance tests, or why they could not run, with the variant's patch applied."""
    variant = _variant(workspace, evidence_dir, patch)
    if "error" in variant:
        return {"failed": [], "error": variant["error"]}
    run = clean_run(
        workspace,
        evidence_dir / "wrong-implementations",
        files=variant["files"],
        tests=files,
        command=command_policy.configured_targeted(state.get("settings"), files) or _planned_go_targeted(state, files),
        timeout=verify.DEFAULT_TIMEOUT,
    )
    results = run.get("results")
    if run.get("error") or results is None:
        return {"failed": [], "error": str(run.get("error") or "the runner reported no per-test results")}
    return {"failed": [*(results.get("failed") or [])], "error": ""}


def send_back_plan(state: RunState, body, changes, record):
    """A spec-first case the runner saw already pass goes back to plan review relabelled guard: (#1200),
    and a guard whose test the runner saw fail on today's and the original code goes back relabelled test:.

    The runner builds the draft (no model writes its wording); the Plan Reviewer reviews it and the user
    approves it as any plan. Imported here, not in the job, which may not depend on the goal lifecycle."""
    try:
        from .. import autocode_goal_lifecycle as lifecycle
    except ImportError:
        import autocode_goal_lifecycle as lifecycle
    lifecycle.install_draft(
        state, body, origin=acceptance_tests_job.ORIGIN, changes=changes, record=record, queue_human=False
    )
    autoplanner.start(state)


def completion_review(state: RunState, snapshot):
    """The Completion Owner said CONTINUE although everything it must check already passes.

    The first time for this artifact it is sent back once with SEND_BACK_NOTE (it
    arrives as checkpoint_reason); the model still decides, and the runner never
    completes on its behalf. Saying CONTINUE again for the same artifact pauses
    for the user (PAUSED_COMPLETION_REVIEW). Returns the state fields to set.
    """
    revision = snapshot.get("revision")
    if state.get("completion_sent_back") == revision:
        return run_status.pause({}, "PAUSED_COMPLETION_REVIEW", COMPLETION_REVIEW_STOP)
    state["completion_sent_back"] = revision
    return {"status": "RUNNING", "stop_reason": SEND_BACK_NOTE}
