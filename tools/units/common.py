"""Typed model request shared by units; transport and persistence belong to the runner."""
from dataclasses import dataclass
try:
    from .. import autocode_goals as goals, autocode_support as support, autocode_workflow as workflow
except ImportError:
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


def execution_request(state, stage, state_path, schema_dir):
    goals.execution_guard(state)
    state["phase"] = "EXECUTING"
    role = autoplanner.role_for(state, stage)
    prompt, metrics = support.context_packet(state, stage, state_path)
    schema = goals.role_schema(support.read(
        schema_dir / "v2" / f"{role}-{'decision' if role == 'astra' else 'report'}.schema.json"), role)
    if stage == "astra_checkpoint":
        schema = workflow.checkpoint_schema(schema_dir)
    elif stage == "terra" and workflow.final_only(state):
        schema = workflow.implementation_schema(schema_dir)
    return ModelRequest(role, autoplanner.route_for(state, stage, role), prompt, metrics, schema, role == "terra")
