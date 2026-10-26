"""Autoreview owns independent verification and evidence validation, the review
workflow's Reviewer stage (autocode_review_job), the design workflow's
Architect stage (autocode_design_job) and a spec-first run's acceptance tests
(autocode_acceptance_tests_job)."""

from __future__ import annotations

try:
    from .. import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..autocode_run_state import RunState

try:
    from .. import autocode_acceptance_tests_job as acceptance_tests_job
    from .. import autocode_counterexample as counterexample
    from .. import autocode_design_check_job as design_check_job
    from .. import autocode_design_intake as design_intake
    from .. import autocode_design_job as design_job
    from .. import autocode_goals as goals
    from .. import autocode_launch_inputs as launch_inputs
    from .. import autocode_recovery_novelty as novelty
    from .. import autocode_review_job as review_job
    from .. import autocode_review_mutation as review_mutation
    from .. import autocode_review_proof as review_proof
    from .. import autocode_role_transport as role_transport
    from .. import autocode_run_status as run_status
    from .. import autocode_task_authority as task_authority
    from .. import autocode_verification_plan as verification_plan
    from .. import autocode_verify as verify
except ImportError:
    import autocode_acceptance_tests_job as acceptance_tests_job
    import autocode_counterexample as counterexample
    import autocode_design_check_job as design_check_job
    import autocode_design_intake as design_intake
    import autocode_design_job as design_job
    import autocode_goals as goals
    import autocode_launch_inputs as launch_inputs
    import autocode_recovery_novelty as novelty
    import autocode_review_job as review_job
    import autocode_review_mutation as review_mutation
    import autocode_review_proof as review_proof
    import autocode_role_transport as role_transport
    import autocode_run_status as run_status
    import autocode_task_authority as task_authority
    import autocode_verification_plan as verification_plan
    import autocode_verify as verify
from . import autoplanner
from .common import ModelRequest, capped_route, execution_request

STAGE = review_job.STAGE
PROBE_TIMEOUT = 120  # seconds per design probe; a probe checks one fact about the code, it is not a suite
COMPLETION_REVIEW_STOP = (
    "All required criteria already pass; request completion instead of another implementation batch"
)
SEND_BACK_NOTE = prompts.get("fragments/autoreview/send-back-note.md")
JOBS = {
    review_job.STAGE: review_job,
    design_job.STAGE: design_job,
    design_check_job.STAGE: design_check_job,
    design_intake.STAGE: design_intake,
    acceptance_tests_job.STAGE: acceptance_tests_job,
}
# The Architect copies the Plan Reviewer's model but not its effort beyond this: at
# "high", MiMo twice spent its whole reasoning budget on a dense design and returned
# no report at all (2026-09-27, design-review-sound live runs).
ARCHITECT_MAX_EFFORT = "medium"


def architect_route(roles):
    return capped_route(roles.get("plan_reviewer") or roles["astra"], ARCHITECT_MAX_EFFORT)


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
        role = next(name for name in ("requirements", "glm", "astra") if name in state["settings"]["roles"])
        return job_request(state, design_intake, role, role)
    if stage == review_job.STAGE:
        # The Reviewer runs on the Validator's route with write access, so it can
        # make and test a scratch copy of its own; the runner rejects the report
        # if the workspace itself changed (review_job.apply).
        state["phase"] = "REVIEWING"
        return job_request(state, review_job, "sol", autoplanner.route_for(state, stage, "sol"))
    if stage == acceptance_tests_job.STAGE:
        # Spec-first (#1200): the Validator's model writes the acceptance tests, on a route of its own so
        # that session never leaks into validation. The Builder's model stays a different one (cross-model).
        state["phase"] = "EXECUTING"
        roles = state["settings"]["roles"]
        roles.setdefault("acceptance_tester", deepcopy(roles["sol"]))
        role_transport.inherit(state.get("settings"), "acceptance_tester", "sol")
        return job_request(state, acceptance_tests_job, "sol", "acceptance_tester")
    if stage in (design_job.STAGE, design_check_job.STAGE):
        # The Architect inherits the Plan Reviewer's model (the planner's own when
        # there is none), with effort capped at ARCHITECT_MAX_EFFORT, on a route of
        # its own so its session never leaks into later planning. Same
        # scratch-copy rule as the Reviewer.
        state["phase"] = "REVIEWING"
        roles = state["settings"]["roles"]
        roles.setdefault("architect", architect_route(roles))
        source = "plan_reviewer" if "plan_reviewer" in roles else "astra"
        role_transport.inherit(state.get("settings"), "architect", source)
        return job_request(state, JOBS[stage], "astra", "architect")
    if stage not in ("sol", "astra_review", "astra_checkpoint"):
        raise ValueError(f"Autoreview cannot run {stage}")
    request = execution_request(state, stage, state_path, schema_dir)
    authority = task_authority.instruction(state.get("investigation"))
    if authority:
        prompt = request.prompt.replace("\nCURRENT HANDOFF DATA\n", "\n" + authority + "\nCURRENT HANDOFF DATA\n", 1)
        request = replace(
            request,
            prompt=prompt,
            metrics={**request.metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4},
        )
    if stage == "astra_review":
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
        prompt = request.prompt.replace("\nCURRENT HANDOFF DATA\n", note + "\nCURRENT HANDOFF DATA\n", 1)
        request = replace(
            request,
            prompt=prompt,
            metrics={**request.metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4},
        )
    if stage == "astra_checkpoint":
        # Under reviewer routing this decision is applied as the Completion Reviewer's, next_task included.
        prompt = request.prompt.replace(
            "\nCURRENT HANDOFF DATA\n",
            "\n"
            + verification_plan.GIT_STATUS_RULE
            + "\n"
            + verification_plan.SHELL_SYNTAX_RULE
            + "\n\nCURRENT HANDOFF DATA\n",
            1,
        )
        request = replace(
            request,
            prompt=prompt,
            metrics={**request.metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4},
        )
    if stage == "sol" and cast(dict, state.get("current_task", {})).get("milestone_ids"):
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


def apply_job(stage, state: RunState, value, record, workspace, *, run_dir=None):
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
                workspace, evidence_dir / "acceptance-proof", tests=tests, timeout=verify.DEFAULT_TIMEOUT
            ),
            evidence_dir=evidence_dir,
            send_back=lambda body, changes: send_back_plan(state, body, changes, record),
        )
        return
    if stage == review_job.STAGE:
        # The runner, not the Reviewer, shows each blocking finding: its test must fail on the change.
        review_job.apply(
            state,
            value,
            record,
            workspace,
            run_tests=lambda tests, patch: review_proof.collect(
                workspace, evidence_dir / "review-proof", tests, patch, clean_run, timeout=verify.DEFAULT_TIMEOUT
            ),
            run_mutation_check=lambda patch: review_mutation.collect(
                workspace, evidence_dir / "review-mutation", patch, clean_run
            ),
        )
        return
    # The Architect's concerns and conflicts about today's code carry probes the runner runs itself, on the
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


def send_back_plan(state: RunState, body, changes, record):
    """A spec-first case the runner saw already pass goes back to plan review relabelled guard: (#1200).

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
