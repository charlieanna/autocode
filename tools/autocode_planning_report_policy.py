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
    if "concerns" not in fields or "contract" in fields:
        return dict(rules), {}, ""
    return (
        {name: "" for name in rules},
        {"contract_author_rules": dict(rules)},
        prompts.get("fragments/autoplanner/concern-only-contract-audit.md"),
    )
