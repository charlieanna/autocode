"""Exact-bound engineering revisions, expanded before ordinary planning validation."""

from __future__ import annotations

import copy

FIELDS = frozenset({"technical_approach", "milestones", "initial_task", "deliverables", "end_to_end_flow"})
RULE = """
REVISE ONLY CHANGED FIELDS. Prefer contract={"revise_contract":retained_contract,
"changes":{},"retain_requirement_trace":true} for this Planner revision. Copy the
current token exactly. Put only changed engineering fields (technical_approach,
milestones, initial_task, deliverables, end_to_end_flow) inside changes; each field
replaces its saved value in full. Do not repeat unchanged fields. The runner retains
all other contract fields byte-for-byte. For criteria changes put the existing
acceptance_criteria delta beside changes; omit it to retain every criterion.
With retain_requirement_trace=true return requirement_trace=[] to explicitly reuse
the validated draft trace; coverage is checked again against your revised contract.
If that trace is unavailable or needs edits, omit retain_requirement_trace and
supply your complete requirement_trace. Concern responses and evidence remain
required. Protected changes needing user authority use the full contract form and
existing receipts. This syntax grants neither change authority nor plan approval.
"""


def model_schema(body, token):
    """Offer sparse revisions alongside the compatible full contract form."""
    criteria = body["properties"]["acceptance_criteria"]
    delta = next(branch for branch in criteria["anyOf"] if branch.get("type") == "object")
    reference = {
        "type": "object",
        "properties": {
            "revise_contract": {"type": "string", "enum": [token]},
            "changes": {
                "type": "object",
                "properties": {key: copy.deepcopy(value) for key, value in body["properties"].items() if key in FIELDS},
                "additionalProperties": False,
            },
            "acceptance_criteria": copy.deepcopy(delta),
            "retain_requirement_trace": {"type": "boolean", "enum": [True]},
        },
        "required": ["revise_contract", "changes"],
        "additionalProperties": False,
    }
    return {"anyOf": [reference, body]}


def offered(schema):
    body = schema.get("properties", {}).get("contract", {})
    return any("revise_contract" in branch.get("properties", {}) for branch in body.get("anyOf", []))


def expand(value, stage, current_token, saved_body, trace):
    """Copy only from the exact current contract; never infer missing model intent."""
    body = value["contract"]
    changes = body.get("changes")
    if (
        stage != "glm_revise"
        or not current_token
        or body.get("revise_contract") != current_token
        or not isinstance(saved_body, dict)
        or set(body) - {"revise_contract", "changes", "acceptance_criteria", "retain_requirement_trace"}
        or not isinstance(changes, dict)
        or set(changes) - FIELDS
    ):
        raise ValueError("Plan revision reference is stale, malformed or not a Planner revision")
    result = copy.deepcopy(value)
    result["contract"] = {**copy.deepcopy(saved_body), **copy.deepcopy(changes)}
    if "acceptance_criteria" in body:
        if not isinstance(body["acceptance_criteria"], dict):
            raise ValueError("Plan revision reference requires a criteria delta")
        result["contract"]["acceptance_criteria"] = copy.deepcopy(body["acceptance_criteria"])
    if "retain_requirement_trace" in body:
        if body["retain_requirement_trace"] is not True or trace is None or value.get("requirement_trace") != []:
            raise ValueError("Plan revision reference requires an available validated trace and empty explicit trace")
        result["requirement_trace"] = copy.deepcopy(trace)
    return result
