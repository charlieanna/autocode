"""Autoreview owns independent verification and evidence validation."""
try:
    from .. import autocode_goals as goals
except ImportError:
    import autocode_goals as goals
from .common import execution_request


def prepare(state, stage, state_path, schema_dir):
    if stage not in ("sol", "astra_review", "astra_checkpoint"):
        raise ValueError(f"Autoreview cannot run {stage}")
    request = execution_request(state, stage, state_path, schema_dir)
    if stage == "sol" and state.get("current_task", {}).get("milestone_ids"):
        request.schema["properties"]["milestone_results"] = {"type": "array", "items": goals.obj({
            "milestone_id": goals.STRING, "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
            "summary": goals.STRING, "evidence_refs": goals.STRINGS})}
        request.schema["required"].append("milestone_results")
    return request
