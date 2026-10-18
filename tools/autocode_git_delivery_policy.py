"""Sealed-plan schema and policy for explicit local atomic Git delivery."""

import re

SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["kind", "message"],
    "properties": {
        "kind": {"type": "string", "enum": ["atomic_commit"]},
        "message": {"type": "string", "minLength": 1},
    },
    "description": "Null when no local commit is requested. Required when the requested plan promises a local atomic commit. The controller commits only approved scoped changes after verification, then independently verifies the committed revision. No push or merge authority.",
}


def requirement(body):
    """Prose can refuse legacy completion, but can never authorize a Git write."""
    spec = body.get("git_delivery")
    if spec is not None:
        if (
            not isinstance(spec, dict)
            or set(spec) != {"kind", "message"}
            or spec["kind"] != "atomic_commit"
            or not isinstance(spec["message"], str)
            or not spec["message"].strip()
            or "\x00" in spec["message"]
        ):
            raise ValueError("Git delivery requires an atomic_commit kind and a nonempty commit message")
        return spec
    for text in [*body.get("technical_approach", []), *body.get("deliverables", []), *body.get("constraints", [])]:
        for sentence in re.split(r"[.\n]", text):
            if re.search(r"\b(?:atomic|single) commit\b", sentence, re.I) and not re.search(
                r"\b(?:no|not|never|without)\b", sentence, re.I
            ):
                raise ValueError(
                    "The plan promises a commit without structured git_delivery authority; revise and approve the plan before completion"
                )
    return None
