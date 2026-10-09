"""Typed model request shared by units; transport and persistence belong to the runner."""
import copy
from dataclasses import dataclass

try:
    from .. import autocode_assignment as assignment
    from .. import autocode_bug_job as bug_job
    from .. import autocode_check_replay as check_replay
    from .. import autocode_goals as goals
    from .. import autocode_stage_access as stage_access
    from .. import autocode_stage_context as stage_context
    from .. import autocode_support as support
    from .. import autocode_test_cases as test_cases
    from .. import autocode_test_examples as test_examples
    from .. import autocode_workflow as workflow
except ImportError:
    import autocode_assignment as assignment
    import autocode_bug_job as bug_job
    import autocode_check_replay as check_replay
    import autocode_goals as goals
    import autocode_stage_access as stage_access
    import autocode_stage_context as stage_context
    import autocode_support as support
    import autocode_test_cases as test_cases
    import autocode_test_examples as test_examples
    import autocode_workflow as workflow
from . import autoplanner


@dataclass(frozen=True)
class ModelRequest:
    role: str
    route_role: str
    prompt: str
    metrics: dict
    schema: dict
    allow_write: bool


EFFORTS = ("low", "medium", "high", "xhigh", "max")

def launch_sandbox(stage, allow_write):
    """The OS sandbox for a stage launch; source integrity is checked after the stage.

    workspace-write for the Builder and write-enabled jobs; read-only for planning;
    workspace-write for judging stages that must write operational evidence under
    .autocode/ (their source contract stays enforced by the after-stage snapshot,
    not the sandbox)."""
    return "workspace-write" if allow_write or stage in stage_access.JUDGING_STAGES else "read-only"


def capped_route(route, cap="medium"):
    """A copy of ``route`` whose reasoning effort is at most ``cap`` (unset or unknown become ``cap``).

    Read-only job stages (Architect, Analyst) copy a planning route's model but not its
    highest efforts: at "high" a model twice spent its whole reasoning budget on a job
    report and returned nothing (2026-09-27)."""
    route = copy.deepcopy(route)
    effort = route.get("reasoning_effort")
    if effort not in EFFORTS or EFFORTS.index(effort) > EFFORTS.index(cap):
        route["reasoning_effort"] = cap
    return route


def execution_request(state, stage, state_path, schema_dir):
    goals.execution_guard(state)
    state["phase"] = "EXECUTING"
    role = autoplanner.role_for(state, stage)
    prompt, metrics = stage_context.context_packet(state, stage, state_path)
    if stage == "terra":
        prompt = test_examples.add_to_prompt(prompt, state["workspace"], state.get("current_task"))
        prompt = prompt.replace("\nCURRENT HANDOFF DATA\n", test_cases.builder_note(state) + bug_job.builder_note(state)
                                + assignment.BUILD_OUTPUT_NOTE
                                + "\nCURRENT HANDOFF DATA\n", 1)
        metrics = {**metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4}
    if stage == "sol":
        prompt = prompt.replace("\nCURRENT HANDOFF DATA\n", check_replay.VALIDATOR_NOTE + bug_job.validator_note(state)
                                + "\nCURRENT HANDOFF DATA\n", 1)
        metrics = {**metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4}
    schema = goals.role_schema(support.read(
        schema_dir / "v2" / f"{role}-{'decision' if role == 'astra' else 'report'}.schema.json"), role)
    if stage == "astra_checkpoint":
        schema = workflow.checkpoint_schema(schema_dir)
    elif stage == "terra" and workflow.final_only(state):
        schema = workflow.implementation_schema(schema_dir)
    return ModelRequest(role, autoplanner.route_for(state, stage, role), prompt, metrics, schema, role == "terra")
