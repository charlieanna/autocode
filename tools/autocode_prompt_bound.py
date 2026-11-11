"""A stage prompt's hard size bound, checked before anything is launched or charged (#3121).

A stage's handoff is meant to be small (``context_soft_tokens`` warns past 10,000 tokens and keeps
the requirements whole). One that reaches megabytes is a defect in what AutoCode put into it, not in
the task: a Resolver prompt that carried every measured file's coverage was 5,992,664 bytes, and the
model returned nothing after a full launch. Past ``STAGE_PROMPT_BYTES`` the runner refuses the launch
with ``STATUS`` and names the handoff field that holds most of the prompt, so the oversized field is
fixed in the core instead of spent on a model.

Utility layer: a pure function over the prompt text; imports nothing from AutoCode.
"""

from __future__ import annotations

import json

STATUS = "PAUSED_PROMPT_SIZE"
# About half a million tokens: far above any healthy stage prompt, below the largest model windows.
STAGE_PROMPT_BYTES = 2 * 1024 * 1024
HANDOFF_MARKER = "CURRENT HANDOFF DATA\n"


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def largest_field(prompt: str) -> tuple[str, int] | None:
    """The handoff field holding most of ``prompt``'s JSON handoff, as a dotted path and its size in bytes.

    It descends while one child holds more than half of its parent; None when the prompt carries no
    readable handoff.
    """
    _, marker, rest = prompt.partition(HANDOFF_MARKER)
    if not marker:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(rest.lstrip())
    except ValueError:
        return None
    path: list[str] = []
    size = _size(value)
    while isinstance(value, (dict, list)) and value:
        children = value.items() if isinstance(value, dict) else enumerate(value)
        key, child, child_size = max(((str(k), v, _size(v)) for k, v in children), key=lambda row: row[2])
        if child_size * 2 <= size:
            break
        path.append(key)
        value, size = child, child_size
    return (".".join(path), size) if path else None


def refusal(prompt: str, *, job: str) -> str | None:
    """Why ``job``'s ``prompt`` may not be launched, or None when it is within the bound."""
    size = len(prompt.encode("utf-8"))
    if size <= STAGE_PROMPT_BYTES:
        return None
    field = largest_field(prompt)
    where = f"; {field[1]} bytes of it are the handoff's {field[0]}" if field else ""
    return (
        f"{job}'s prompt is {size} bytes, over the {STAGE_PROMPT_BYTES}-byte bound for one stage{where}. "
        "No request was launched. An oversized handoff is a defect in what AutoCode sends the stage, "
        "not in the task"
    )
