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
NULLABLE_RULE = """
REVISE ONLY CHANGED FIELDS. Prefer contract.revise_contract=retained_contract for
this Planner revision. Copy the current token exactly. The strict output schema
requires every offered changes field: use null for unchanged engineering fields,
and a complete replacement value only for a field you actually change. Do not use
[] or {} as an unchanged placeholder: every non-null value replaces its saved
value in full, including an empty list. Null retains that exact saved field.
Use acceptance_criteria=null to retain every criterion, or supply the existing
criteria delta for actual criterion edits. With retain_requirement_trace=true,
return requirement_trace=[] to reuse the available validated trace. Otherwise use
retain_requirement_trace=null and supply your complete requirement_trace; null
does not authorize trace reuse. The runner retains all other contract fields
byte-for-byte and runs every ordinary validation and source-coverage check.
Concern responses and evidence remain required. Protected changes needing user
authority use the full contract form and existing receipts. This syntax grants
neither change authority nor plan approval.
"""


def _nullable(schema):
    branches = schema.get("anyOf", [])
    return isinstance(branches, list) and {"type": "null"} in branches


def _reference_properties(schema):
    contract = schema.get("properties", {}).get("contract", {})
    for branch in contract.get("anyOf", []):
        properties = branch.get("properties", {})
        if "revise_contract" in properties:
            return properties
    return {}


def rule(schema=None):
    """Old pinned reference schemas keep their original omission-only instructions."""
    if schema is None:
        return NULLABLE_RULE
    properties = _reference_properties(schema)
    changes = properties.get("changes", {}).get("properties", {})
    return NULLABLE_RULE if any(_nullable(value) for value in changes.values()) else RULE


def normalize_nullable_reference(value, schema):
    """Decode only null no-ops explicitly offered by this report's pinned schema.

    Canonical references still use omission. Full contracts, old pins, unknown
    fields and stale references retain their ordinary refusal semantics.
    """
    body = value.get("contract")
    properties = _reference_properties(schema)
    if not isinstance(body, dict) or not properties:
        return value
    if body.get("revise_contract") not in properties["revise_contract"].get("enum", []):
        return value
    result = copy.deepcopy(value)
    body = result["contract"]
    changes = body.get("changes")
    offered = properties.get("changes", {}).get("properties", {})
    if isinstance(changes, dict):
        for field in FIELDS & changes.keys():
            if changes[field] is None and _nullable(offered.get(field, {})):
                del changes[field]
    for field in ("acceptance_criteria", "retain_requirement_trace"):
        if field in body and body[field] is None and _nullable(properties.get(field, {})):
            del body[field]
    return result


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
                "properties": {
                    key: {"anyOf": [copy.deepcopy(value), {"type": "null"}]}
                    for key, value in body["properties"].items()
                    if key in FIELDS
                },
                "additionalProperties": False,
            },
            "acceptance_criteria": {"anyOf": [copy.deepcopy(delta), {"type": "null"}]},
            "retain_requirement_trace": {"anyOf": [{"type": "boolean", "enum": [True]}, {"type": "null"}]},
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
    # Keep the admission order and short-circuiting: diagnostics identify the
    # first refusal, never a reported token, field name, value or saved body.
    if stage != "revise_plan":
        reason = "wrong_stage"
    elif not current_token:
        reason = "current_reference_unavailable"
    elif body.get("revise_contract") != current_token:
        reason = "reference_mismatch"
    elif not isinstance(saved_body, dict):
        reason = "saved_contract_unavailable"
    elif set(body) - {"revise_contract", "changes", "acceptance_criteria", "retain_requirement_trace"}:
        reason = "unexpected_reference_fields"
    elif not isinstance(changes, dict):
        reason = "changes_not_object"
    elif set(changes) - FIELDS:
        reason = "unexpected_change_fields"
    else:
        reason = None
    if reason:
        raise ValueError(
            f"Plan revision reference is stale, malformed or not a Planner revision [plan_revision_reference:{reason}]"
        )
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
