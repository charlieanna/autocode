"""Typed model request shared by units; transport and persistence belong to the runner."""
import copy
from dataclasses import dataclass
try:
    from .. import autocode_goals as goals, autocode_support as support, autocode_workflow as workflow
    from .. import autocode_test_examples as test_examples, autocode_test_cases as test_cases
except ImportError:
    import autocode_test_examples as test_examples
    import autocode_test_cases as test_cases
    import autocode_goals as goals
    import autocode_support as support
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
    prompt, metrics = support.context_packet(state, stage, state_path)
    if stage == "terra":
        prompt = test_examples.add_to_prompt(prompt, state["workspace"], state.get("current_task"))
        prompt = prompt.replace("\nCURRENT HANDOFF DATA\n", test_cases.builder_note(state) + "\nCURRENT HANDOFF DATA\n", 1)
        metrics = {**metrics, "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4}
    schema = goals.role_schema(support.read(
        schema_dir / "v2" / f"{role}-{'decision' if role == 'astra' else 'report'}.schema.json"), role)
    if stage == "astra_checkpoint":
        schema = workflow.checkpoint_schema(schema_dir)
    elif stage == "terra" and workflow.final_only(state):
        schema = workflow.implementation_schema(schema_dir)
    return ModelRequest(role, autoplanner.route_for(state, stage, role), prompt, metrics, schema, role == "terra")
