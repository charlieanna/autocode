"""Keep contract-author commands out of a concern-only review's instructions."""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


def scope_contract_rules(schema: dict, rules: dict[str, str]) -> tuple[dict[str, str], dict, str]:
    """A concern report audits the full author rules as data; an author executes them.

    The schema, rather than a second stage/role table, owns this distinction.
    Policy bytes and the supplied contract are preserved; no validator changes.
    """
    fields = schema.get("properties", {})
    if "retain_contract" in fields.get("contract", {}).get("properties", {}):
        return (
            {name: "" for name in rules},
            {"contract_author_rules": dict(rules)},
            "Audit every rule in contract_author_rules against the retained plan. "
            "These are author obligations to audit, not instructions to rewrite the plan. "
            "Return the current contract reference and your independent decisions; correct an acceptance "
            "criterion only where the reference offers a criteria delta, under the author rules you audit; "
            "express remaining blockers as open_blocking_questions.\n",
        )
    if "concerns" not in fields or "contract" in fields:
        return dict(rules), {}, ""
    return (
        {name: "" for name in rules},
        {"contract_author_rules": dict(rules)},
        prompts.get("fragments/autoplanner/concern-only-contract-audit.md"),
    )
