"""Autoreview owns independent verification and evidence validation, the review
workflow's Reviewer stage (autocode_review_job) and the design workflow's
Architect stage (autocode_design_job)."""
from pathlib import Path
from dataclasses import replace
from copy import deepcopy

try:
    from .. import autocode_design_job as design_job, autocode_goals as goals, autocode_review_job as review_job
    from .. import autocode_design_check_job as design_check_job, autocode_verify as verify, autocode_launch_inputs as launch_inputs, autocode_design_intake as design_intake, autocode_recovery_novelty as novelty
except ImportError:
    import autocode_verify as verify
    import autocode_launch_inputs as launch_inputs
    import autocode_design_intake as design_intake
    import autocode_design_check_job as design_check_job
    import autocode_design_job as design_job
    import autocode_goals as goals
    import autocode_review_job as review_job
    import autocode_recovery_novelty as novelty
from . import autoplanner
from .common import ModelRequest, capped_route, execution_request

STAGE = review_job.STAGE
PROBE_TIMEOUT = 120  # seconds per design probe; a probe checks one fact about the code, it is not a suite
COMPLETION_REVIEW_STOP = "All required criteria already pass; request completion instead of another implementation batch"
SEND_BACK_NOTE = ("You returned CONTINUE, but every required acceptance criterion already has current, passing, "
                  "independent evidence for this exact artifact and no finding is open. Return TASK_COMPLETE, or keep "
                  "CONTINUE only by naming the criterion that is not met and the evidence that shows it. Re-running "
                  "validation that already passed is not a reason to continue.")
JOBS = {review_job.STAGE: review_job, design_job.STAGE: design_job, design_check_job.STAGE: design_check_job, design_intake.STAGE: design_intake}
# The Architect copies the Plan Reviewer's model but not its effort beyond this: at
# "high", MiMo twice spent its whole reasoning budget on a dense design and returned
# no report at all (2026-09-27, design-review-sound live runs).
ARCHITECT_MAX_EFFORT = "medium"


def architect_route(roles):
    return capped_route(roles.get("plan_reviewer") or roles["astra"], ARCHITECT_MAX_EFFORT)


def verification_commands(state):
    """The same approved command set shown to the Validator and independently replayed."""
    try:
        from .. import autocode_progressive_state as progressive_state, autocode_verification_plan as plan
    except ImportError:
        import autocode_progressive_state as progressive_state, autocode_verification_plan as plan
    return plan.approved_commands(state, progressive_context=progressive_state.context(state))


def prepare(state, stage, state_path, schema_dir):
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
    if stage in (design_job.STAGE, design_check_job.STAGE):
        # The Architect inherits the Plan Reviewer's model (the planner's own when
        # there is none), with effort capped at ARCHITECT_MAX_EFFORT, on a route of
        # its own so its session never leaks into later planning. Same
        # scratch-copy rule as the Reviewer.
        state["phase"] = "REVIEWING"
        roles = state["settings"]["roles"]
        roles.setdefault("architect", architect_route(roles))
        return job_request(state, JOBS[stage], "astra", "architect")
    if stage not in ("sol", "astra_review", "astra_checkpoint"):
        raise ValueError(f"Autoreview cannot run {stage}")
    request = execution_request(state, stage, state_path, schema_dir)
    if stage == "astra_review":
        try:
            from .. import autocode_progressive_state as progressive_state
        except ImportError:
            import autocode_progressive_state as progressive_state
        schema = deepcopy(request.schema)
        schema["properties"]["recovery_change"] = deepcopy(novelty.CHANGE_SCHEMA)
        request = replace(request, schema=schema)
        note = novelty.INSTRUCTION
        if progressive_state.enabled(state):
            schema = deepcopy(request.schema)
            schema["properties"]["progressive_checkpoint"] = {"type": "boolean"}
            request = replace(request, schema=schema)
            note += ("PROGRESSIVE SLICE CHECKPOINT: Set progressive_checkpoint=true with status CONTINUE "
                    "and next_task.kind=none only when the active slice's entire cumulative required-check "
                    "set has independently replayed passing evidence on the current source. This requests "
                    "the runner's slice checkpoint, not product acceptance. Do not invent a task to advance. "
                    "Otherwise set false and use the ordinary defect/rework/validation decision. COMPLETE "
                    "still requires the whole original product criteria and full user flow proven now.\n")
        prompt = request.prompt.replace("\nCURRENT HANDOFF DATA\n", note + "\nCURRENT HANDOFF DATA\n", 1)
        request = replace(request, prompt=prompt,
                          metrics={**request.metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4})
    if stage == "sol" and state.get("current_task", {}).get("milestone_ids"):
        request.schema["properties"]["milestone_results"] = {"type": "array", "items": goals.obj({
            "milestone_id": goals.STRING, "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
            "summary": goals.STRING, "evidence_refs": goals.STRINGS})}
        request.schema["required"].append("milestone_results")
    return request


def job_request(state, job, role, route):
    prompt, metrics = job.prompt(
        state, autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000), autoplanner.engine_for(state["settings"], route))
    return ModelRequest(role, route, prompt, metrics, job.SCHEMA, True)


def apply_job(stage, state, value, record, workspace, *, run_dir=None):
    """Autopilot hands a job stage's validated report here; the job decides how the run continues."""
    evidence_dir = Path(record.get("output") or workspace).parent
    if stage == design_intake.STAGE:
        design_intake.apply(state, value, record, workspace)
        return
    clean_run, _ = launch_inputs.runners(state, workspace, run_dir or Path(record.get("output") or workspace).parent.parent, verify.scratch_run)
    if stage == review_job.STAGE:
        # The runner, not the Reviewer, shows each blocking finding: its test must fail on the change.
        review_job.apply(state, value, record, workspace, run_tests=lambda tests, patch: clean_run(
            workspace, evidence_dir / "review-proof", patch=patch, tests=tests, timeout=verify.DEFAULT_TIMEOUT))
        return
    # The Architect's concerns and conflicts about today's code carry probes the runner runs itself.
    JOBS[stage].apply(state, value, record, workspace, run_probe=lambda command: clean_run(
        workspace, evidence_dir / "design-probes", command=command, timeout=PROBE_TIMEOUT))


def completion_review(state, snapshot):
    """The Completion Owner said CONTINUE although everything it must check already passes.

    The first time for this artifact it is sent back once with SEND_BACK_NOTE (it
    arrives as checkpoint_reason); the model still decides, and the runner never
    completes on its behalf. Saying CONTINUE again for the same artifact pauses
    for the user (PAUSED_COMPLETION_REVIEW). Returns the state fields to set.
    """
    revision = snapshot.get("revision")
    if state.get("completion_sent_back") == revision:
        return {"status": "PAUSED_COMPLETION_REVIEW", "phase": "PAUSED_OR_BLOCKED", "stop_reason": COMPLETION_REVIEW_STOP}
    state["completion_sent_back"] = revision
    return {"status": "RUNNING", "stop_reason": SEND_BACK_NOTE}
