"""Schema-specific criterion instructions for approved-goal execution reports."""

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


def criteria_instruction(schema: dict | None) -> str:
    """Describe only execution result rows, never proposed contracts or next-task IDs.

    Callers supply the final response shape, including the existing generation-only
    ID projection. Inspecting it does not mutate the schema or approved contract.
    Checkpoint decisions nest the same result rows under ``decision``.
    """
    properties = (schema or {}).get("properties", {})
    instructions = []
    for path, container in (
        ("acceptance_criteria", properties),
        ("decision.acceptance_criteria", properties.get("decision", {}).get("properties", {})),
    ):
        fields = container.get("acceptance_criteria", {}).get("items", {}).get("properties", {})
        if "id" not in fields:
            continue
        definitions = [key for key in ("id", "criterion", "verification_method", "human_review") if key in fields]
        if definitions == ["id"]:
            instructions.append(prompts.get("fragments/goals/criteria-id-output.md").format(path))
        else:
            instructions.append(
                prompts.get("fragments/goals/criteria-definition-output.md").format(path, ", ".join(definitions))
            )
    return "".join(instructions)
