"""The literals a user's brief states must survive into the goal contract.

A live run (docs/bugs/2026-10-01-reliability-live-cases.md) completed falsely because the
Requirements stage wrote the brief's `ID TEXT [open|done]` into its examples as `1 buy milk open`.
The Builder, its tests, the Validator and the completion gate then all honestly served the corrupted
criteria. Every other handoff has an independent check; this is the mechanical one for the
brief-to-contract transcription, run where the runner checks each planner draft's requirement trace.

A literal is an inline code span in text the user wrote (the task, brief feedback, the user's own
answers); fenced code blocks are left out. The contract keeps a literal when it quotes it verbatim
anywhere, or, for a template (one with a placeholder such as ID or TEXT, an ALL-CAPS word of letters,
or an alternation such as open|done), when an acceptance criterion fills it in (`1 buy milk [open]`).
Whitespace differences never count. Whether every example then agrees with the literal is the Plan
Reviewer's check (units/autoplanner.BRIEF_TRACE_RULE). Imports nothing from AutoCode.
"""
from __future__ import annotations

import re

FENCE = re.compile(r"```.*?(```|$)", re.S)
SPAN = re.compile(r"`([^`\n]+)`")
# An alternation (open|done), a placeholder (an ALL-CAPS word of letters standing alone, so neither
# README.md nor 2024-W54 has one), a run of whitespace, or any other character.
TOKEN = re.compile(r"(?P<alternation>[A-Za-z0-9_-]+(?:\|[A-Za-z0-9_-]+)+)"
                   r"|(?P<placeholder>(?<![\w.])[A-Z][A-Z_]*(?![\w.]))"
                   r"|(?P<space>\s+)|(?P<other>.)", re.S)


def literals(texts) -> list[str]:
    """The inline code spans in ``texts``, in order, without repeats."""
    found = []
    for text in texts:
        for span in SPAN.findall(FENCE.sub(" ", text or "")):
            span = " ".join(span.split())
            if span and span not in found:
                found.append(span)
    return found


def template(literal: str) -> re.Pattern | None:
    """A pattern that a filled-in ``literal`` matches, or None when it has no placeholder or alternation."""
    parts, variable = [], False
    for match in TOKEN.finditer(literal):
        kind, token = match.lastgroup, match.group()
        if kind == "alternation":
            parts.append("(?:" + "|".join(re.escape(part) for part in token.split("|")) + ")")
        elif kind == "placeholder":
            parts.append(r"\S(?:.*?\S)?")
        else:
            parts.append(r"\s+" if kind == "space" else re.escape(token))
        variable = variable or kind in ("alternation", "placeholder")
    return re.compile("".join(parts)) if variable else None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def missing(found: list[str], contract: dict) -> list[str]:
    """The literals in ``found`` that ``contract`` drops."""
    criteria = [" ".join(str(row.get("criterion", "")).split())
                for row in contract.get("acceptance_criteria") or [] if isinstance(row, dict)]
    text = " ".join(" ".join(part.split()) for part in _strings(contract))
    lost = []
    for literal in found:
        pattern = template(literal)
        if literal not in text and not (pattern and any(pattern.search(criterion) for criterion in criteria)):
            lost.append(literal)
    return lost


def error(lost: list[str]) -> str:
    """The repair instruction for a draft that dropped ``lost``."""
    return ("The contract drops literals the user's brief states: " + ", ".join(f"`{item}`" for item in lost)
            + ". Keep each one exactly as the brief writes it: quote it verbatim in the criterion, behavior or "
            "deliverable that covers it, or, for a format with placeholders (ALL-CAPS words) or alternatives (a|b), "
            "give an acceptance criterion whose worked example fills it in without changing anything else")
