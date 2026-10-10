"""The routing table: one-line prompts and the workflow each should be recognized as.

``scenarios/routing.toml``::

    seed = "review-planted-defects"      # catalog scenario whose seed is the workspace
    known_failure = "..."                # optional, as in scenario.toml

    [[prompt]]
    text = "Review PR #184 before I merge it."
    workflow = "review"
"""

from __future__ import annotations

import tomllib
from pathlib import Path

TABLE = Path(__file__).resolve().parent.parent / "routing.toml"
# The five user-facing kinds of job (README, "Workflows"). Investigation and
# tradeoff questions are both "discuss": conversation, not a build.
WORKFLOWS = ("build", "bugfix", "review", "design", "discuss")


def load(path: Path = TABLE) -> dict:
    table = tomllib.loads(path.read_text())
    prompts = table.get("prompt") or []
    if not prompts:
        raise ValueError(f"{path}: no [[prompt]] entries")
    for prompt in prompts:
        if prompt.get("workflow") not in WORKFLOWS:
            raise ValueError(
                f"{path}: prompt {prompt.get('text')!r} wants workflow {prompt.get('workflow')!r}, "
                f"not one of {WORKFLOWS}"
            )
        if "\n" in prompt.get("text", "\n"):
            raise ValueError(f"{path}: prompts are one line each: {prompt.get('text')!r}")
    return {"seed": table["seed"], "known_failure": table.get("known_failure", ""), "prompts": prompts}
