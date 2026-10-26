"""Report schema compatibility, review identities and ID-only decoding."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy

try:
    from . import autocode_design_coverage as design_coverage
except ImportError:
    import autocode_design_coverage as design_coverage


def normalize_absent_authority(value, schema, record):
    """Default absent optional fields without changing supplied values.

    Strict provider schemas require optional fields that older reports omit.
    The loader preserves the raw report before calling this pure normalizer.
    Older schemas and unrelated stages must not gain optional fields.
    """
    result = copy.deepcopy(value)
    properties = schema.get("properties", {})
    for target, fields in (
        (result, properties),
        (result.get("contract"), properties.get("contract", {}).get("properties", {})),
    ):
        kind = fields.get("git_delivery", {}).get("type")
        if isinstance(target, dict) and isinstance(kind, list) and "null" in kind:
            target.setdefault("git_delivery", None)
    if "recovery_change" in properties:
        result.setdefault("recovery_change", None)
    # A trace row's proxy_reason (#2068) is null except on a proxied row; reports written before it, and
    # fixtures that never mark a proxy, leave it out.
    trace_row = properties.get("requirement_trace", {}).get("items", {}).get("properties", {})
    if "proxy_reason" in trace_row and isinstance(result.get("requirement_trace"), list):
        for row in result["requirement_trace"]:
            if isinstance(row, dict):
                row.setdefault("proxy_reason", None)
    stage = str(record.get("original_stage") or record.get("stage", "")).removesuffix("_report_repair")
    if stage in ("requirements_gather", "requirements") and "project_conventions" in properties:
        result.setdefault("project_conventions", [])
    request = result.get("user_request")
    kind = properties.get("user_request", {}).get("properties", {}).get("recommended_revision", {}).get("type")
    nullable = kind == "null" or isinstance(kind, list) and "null" in kind
    if stage == "astra_resolve" and isinstance(request, dict) and nullable:
        request.setdefault("recommended_revision", None)
    return result


def review_generation_schema(schema, state: RunState, stage):
    """Constrain runner-owned identity at generation, not by accepting bad reports."""
    result = design_coverage.extend_schema(copy.deepcopy(schema), state, stage)
    if stage not in ("sol", "astra_review", "astra_checkpoint"):
        return result
    props = result.get("properties", {})
    contract = state.get("goal_contract") or {}
    for field, value in (
        ("contract_hash", contract.get("hash")),
        ("contract_revision", contract.get("revision")),
        ("task_id", (state.get("current_task") or {}).get("id", "")),
    ):
        if field in props and value is not None:
            props[field] = {**props[field], "enum": [value]}
    source = "sol" if stage == "sol" else "astra"
    own = [r["id"] for r in state.get("findings_ledger", []) if r.get("source") == source and r.get("status") == "open"]
    for field in ("findings", "finding_dispositions"):
        fields = props.get(field, {}).get("items", {}).get("properties", {})
        if "id" in fields:
            fields["id"] = {**fields["id"], "enum": ["", *own]}
    criteria = state.get("acceptance_criteria") or []
    item = props.get("acceptance_criteria", {}).get("items", {})
    fields = item.get("properties", {})
    if criteria and fields and stage in COMPLETION_STAGES:
        fields["id"] = {**fields["id"], "enum": list(dict.fromkeys(row["id"] for row in criteria))}
        fields.pop("criterion", None)
        item["required"] = [key for key in item.get("required", []) if key != "criterion"]
    return result


COMPLETION_STAGES = {"astra_review"}


def review_validation_schema(schema, state: RunState | None, record, value):
    """Validate one report shape and its full ordered IDs before report repair ends.

    New providers produce ID-only rows; legacy reports must still copy every
    literal exactly. This also accepts ID-only responses against older saved schemas.
    """
    stage = record.get("original_stage") or record.get("stage")
    if state and stage in ("sol", "astra_checkpoint"):
        design_coverage.report_refs(state, value.get("validation", value))
    criteria = (state or {}).get("acceptance_criteria") or []
    if stage not in COMPLETION_STAGES or not criteria or "acceptance_criteria" not in schema.get("properties", {}):
        return schema
    rows = value.get("acceptance_criteria")
    if (
        not isinstance(rows, list)
        or not all(isinstance(row, dict) for row in rows)
        or [row.get("id") for row in rows] != [row["id"] for row in criteria]
    ):
        raise ValueError("Review report must contain each approved criterion ID once, in order")
    has_text = ["criterion" in row for row in rows]
    if any(has_text) and not all(has_text):
        raise ValueError("Review report mixes ID-only and legacy criterion rows")
    if all(has_text) and any(
        row["criterion"] != approved["criterion"] for row, approved in zip(rows, criteria, strict=False)
    ):
        raise ValueError("Review report criterion text conflicts with the approved contract; repair the copied text")
    result = copy.deepcopy(schema)
    item = result["properties"]["acceptance_criteria"]["items"]
    if all(has_text):
        item["properties"]["criterion"] = {"type": "string"}
        item["required"] = list(dict.fromkeys([*item.get("required", []), "criterion"]))
    else:
        item["properties"].pop("criterion", None)
        item["required"] = [key for key in item.get("required", []) if key != "criterion"]
    return result


def hydrate_review_report(value, state: RunState | None, record):
    """Fill runner-owned text after validation for the canonical saved report.

    The loader preserves the original response in its existing reported_output
    artifact, so every downstream reader can use the complete canonical shape.
    """
    stage = record.get("original_stage") or record.get("stage")
    if stage not in COMPLETION_STAGES or not (state or {}).get("acceptance_criteria"):
        return value
    rows = value.get("acceptance_criteria", [])
    if not rows or any("criterion" in row for row in rows):
        return value
    result = copy.deepcopy(value)
    for row, approved in zip(
        result["acceptance_criteria"], cast("RunState", state)["acceptance_criteria"], strict=False
    ):
        row["criterion"] = approved["criterion"]
    return result
