"""Keep contract-author commands out of a concern-only review's instructions."""

from __future__ import annotations

import copy

try:
    from . import autocode_prompts as prompts
    from . import autocode_roles as roles
except ImportError:
    import autocode_prompts as prompts
    import autocode_roles as roles


def concern_rule(schema: dict) -> str:
    """Keep emitted answers tied to the actual review without narrowing its audit."""
    if {"responses", "decisions"} & schema.get("properties", {}).keys():
        return prompts.get("fragments/autoplanner/concern-rows.md")
    return ""


def concern_schema(schema: dict, review: dict | None) -> dict:
    """Constrain generation only; old reports still use the ordinary ingestion schema.

    A missing review is not an empty review. Only the current saved review can
    supply the allowed IDs; no concern is inferred from a criterion or an audit.
    """
    fields = schema.get("properties", {})
    row_fields = {"responses", "decisions"} & fields.keys()
    if not row_fields or not isinstance(review, dict) or not isinstance(review.get("concerns"), list):
        return schema
    concerns = review["concerns"]
    if any(
        not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip() for row in concerns
    ):
        return schema
    ids = [row["id"] for row in concerns]
    if len(ids) != len(set(ids)):
        return schema
    result = copy.deepcopy(schema)
    for field in row_fields:
        rows = result["properties"][field]
        rows["minItems"] = rows["maxItems"] = len(ids)
        if ids:
            # String schema objects are shared across fields, even after deepcopy.
            # Replace this property so the enum cannot leak into rationale or evidence.
            props = rows["items"]["properties"]
            props["concern_id"] = {**props["concern_id"], "enum": ids}
    return result


def concern_validation_schema(schema: dict, record: dict) -> dict:
    """Keep the loader compatible with audit rows that ordinary planning discards.

    Only undo the exact generation binding above, and only for planning reports
    with contracts. Row shapes and every other constraint stay intact. The
    planning consumer still requires exactly one substantive answer per real ID.
    """
    stage = str(record.get("original_stage") or record.get("stage", "")).removesuffix("_report_repair")
    fields = schema.get("properties", {})
    if stage not in roles.ALL_PLANNING_STAGES or "contract" not in fields:
        return schema
    result = copy.deepcopy(schema)
    for field in {"responses", "decisions"} & fields.keys():
        rows = result["properties"][field]
        props = rows.get("items", {}).get("properties", {})
        identity = props.get("concern_id", {})
        ids = identity.get("enum", [])
        if (
            identity.get("type") == "string"
            and isinstance(ids, list)
            and all(isinstance(cid, str) and cid for cid in ids)
            and rows.get("minItems") == rows.get("maxItems") == len(ids)
        ):
            rows.pop("minItems")
            rows.pop("maxItems")
            props["concern_id"] = {key: value for key, value in identity.items() if key != "enum"}
    return result


def scope_contract_rules(schema: dict, rules: dict[str, str]) -> tuple[dict[str, str], dict, str]:
    """A concern report audits the full author rules as data; an author executes them.

    The schema, rather than a second stage/role table, owns this distinction.
    Policy bytes and the supplied contract are preserved; no validator changes.
    """
    fields = schema.get("properties", {})
    row_rule = concern_rule(schema)
    if "retain_contract" in fields.get("contract", {}).get("properties", {}):
        return (
            {name: "" for name in rules},
            {"contract_author_rules": dict(rules)},
            "Audit every rule in contract_author_rules against the retained plan. "
            "These are author obligations to audit, not instructions to rewrite the plan. "
            "Return the current contract reference and your independent decisions; correct an acceptance "
            "criterion only where the reference offers a criteria delta, under the author rules you audit; "
            "express remaining blockers as open_blocking_questions.\n" + row_rule,
        )
    if "concerns" not in fields or "contract" in fields:
        return dict(rules), {}, row_rule
    return (
        {name: "" for name in rules},
        {"contract_author_rules": dict(rules)},
        prompts.get("fragments/autoplanner/concern-only-contract-audit.md"),
    )
