"""The literals a user's brief states must survive into the goal contract.

A live run (docs/bugs/2026-10-01-reliability-live-cases.md) completed falsely because the
Requirements stage wrote the brief's `ID TEXT [open|done]` into its examples as `1 buy milk open`.
The Builder, its tests, the Validator and the completion gate then all honestly served the corrupted
criteria. Every other handoff has an independent check; this is the mechanical one for the
brief-to-contract transcription, run where the runner checks each planner draft's requirement trace
and on each plan file a person loads with --goal-file (autocode_goal_file).

A literal is an inline code span in text the user wrote (the task, brief feedback, the user's own
answers); fenced code blocks and leading documentary file:line labels are left out. Inline
paths, output strings and file-scope constraints are still literals. The contract keeps a literal when it quotes it verbatim
anywhere, or, for a template (one with a placeholder such as ID or TEXT, an ALL-CAPS word of letters,
or an alternation such as open|done), when an acceptance criterion fills it in (`1 buy milk [open]`).
Whitespace differences never count.

Keeping a template somewhere is not enough when another worked example renders it differently: the live
case quoted nothing wrong, it wrote `1 buy milk open`. ``disagreements`` compares each worked example (an
inline code span of a criterion) with the templates its criterion cites (#2941; autocode_source_trace runs
it on every citing criterion). An example that keeps a template's fields in its order, or every one of its
format markers in another order, renders that template; when the template itself does not match it, its
brackets, separators, field order or casing changed, and the draft goes back to the planner. Only a template
with a fixed word or an alternation to recognize an example by is compared, an example that is a brief
literal itself or a filled-in other one is not, and semantic agreement stays the Plan Reviewer's check
(units/autoplanner.BRIEF_TRACE_RULE). Imports nothing from AutoCode beyond prompts and source metadata.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
    from . import autocode_source_metadata as source_metadata
except ImportError:
    import autocode_prompts as prompts
    import autocode_source_metadata as source_metadata


import itertools
import re

FENCE = re.compile(r"```.*?(```|$)", re.S)
SPAN = re.compile(r"`([^`\n]+)`")
# An alternation (open|done), a placeholder (an ALL-CAPS word of letters standing alone, so neither
# README.md nor 2024-W54 has one), a run of whitespace, or any other character.
TOKEN = re.compile(
    r"(?P<alternation>[A-Za-z0-9_-]+(?:\|[A-Za-z0-9_-]+)+)"
    r"|(?P<placeholder>(?<![\w.])[A-Z][A-Z_]*(?![\w.]))"
    r"|(?P<space>\s+)|(?P<other>.)",
    re.S,
)


def literals(texts, *, protected_locations=()) -> list[str]:
    """The inline code spans in ``texts``, in order, without repeats."""
    found = []
    for text in texts:
        content = source_metadata.without_location_labels(
            FENCE.sub(" ", text or ""), protected_locations=protected_locations
        )
        for span in SPAN.findall(content):
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


# A rendering is recognized by its fixed parts; placeholders alone would match any example. Templates past these
# sizes are left to the Plan Reviewer so a long example cannot make the comparison backtrack for long.
MAX_PLACEHOLDERS = 3
MAX_REORDERED_FIELDS = 5
MAX_EXAMPLE = 200
# Fields the template separates by a space stay separated by one: `--project NAME` is not rendered by
# `--project-file x`.
_SEPARATOR = r"\W*"
_SPACED = r"\W*\s\W*"


def _fields(literal: str):
    """``literal``'s fields in order (a placeholder, an alternation's values or a fixed word), its format
    markers (the characters other than letters, digits and spaces between them) and, for each gap between
    two fields, whether the template puts a space in it."""
    fields, markers, spaced, word, space = [], [], [], "", False
    for match in TOKEN.finditer(literal + " "):  # the trailing space ends a final word
        kind, token = match.lastgroup, match.group()
        if kind == "other" and token.isalnum():
            word += token
            continue
        pending = [("word", word)] if word else []
        word = ""
        if kind == "alternation":
            pending.append(("alternation", tuple(token.split("|"))))
        elif kind == "placeholder":
            pending.append(("placeholder", ()))
        elif kind == "other":
            markers.append(token)
        for field in pending:
            if fields:
                spaced.append(space)
            fields.append(field)
            space = False
        space = space or kind == "space"
    return fields, markers, spaced


def _relaxed(fields, spaced) -> re.Pattern:
    """A pattern for ``fields`` in this order with any casing and any separators, spaced where ``spaced`` says."""
    parts = []
    for kind, value in fields:
        if kind == "placeholder":
            parts.append(r"\S(?:.*?\S)?")
        else:
            options = value if kind == "alternation" else (value,)
            parts.append(r"(?<![^\W_])(?:" + "|".join(re.escape(option) for option in options) + r")(?![^\W_])")
    pattern = parts[0]
    for gap, part in zip(spaced, parts[1:], strict=True):
        pattern += (_SPACED if gap else _SEPARATOR) + part
    return re.compile(_SEPARATOR + pattern + _SEPARATOR, re.I)


def _anchored(fields) -> bool:
    """Whether a template has a fixed word or an alternation to recognize its renderings by."""
    return any(kind != "placeholder" for kind, _ in fields)


def disagreements(cited, examples, known=()) -> list[tuple[str, str]]:
    """The ``(example, literal)`` pairs where a worked example renders a ``cited`` template other than as written.

    ``known`` are every literal of the sources: an example equal to one, or filling in another template, is that
    literal's rendering (a command beside an output format), not this one's."""
    known = {*known, *cited}
    others = [template(literal) for literal in known if _anchored(_fields(literal)[0])]
    found = []
    for literal in cited:
        fields, markers, spaced = _fields(literal)
        strict = template(literal)
        if strict is None or not _anchored(fields):
            continue
        if sum(kind == "placeholder" for kind, _ in fields) > MAX_PLACEHOLDERS:
            continue
        in_order = _relaxed(fields, spaced)
        reordered = []
        if markers and len(fields) <= MAX_REORDERED_FIELDS:
            orders = dict.fromkeys(itertools.permutations(fields))
            reordered = [_relaxed(order, [True] * (len(order) - 1)) for order in orders]
        for example in examples:
            if len(example) > MAX_EXAMPLE or example in known or strict.search(example):
                continue
            if any(pattern and pattern.fullmatch(example) for pattern in others):
                continue
            moved = all(marker in example for marker in markers) and any(p.fullmatch(example) for p in reordered)
            if in_order.fullmatch(example) or moved:
                found.append((example, literal))
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            # A criterion's source citations (#595) are verbatim brief copies by
            # construction; counting them as preservation would mask every drop.
            if key == "sources":
                continue
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def missing(found: list[str], contract: dict) -> list[str]:
    """The literals in ``found`` that ``contract`` drops."""
    criteria = [
        " ".join(str(row.get("criterion", "")).split())
        for row in contract.get("acceptance_criteria") or []
        if isinstance(row, dict)
    ]
    text = " ".join(" ".join(part.split()) for part in _strings(contract))
    lost = []
    for literal in found:
        pattern = template(literal)
        if literal not in text and not (pattern and any(pattern.search(criterion) for criterion in criteria)):
            lost.append(literal)
    return lost


def check(texts, contract: dict) -> None:
    """Refuse ``contract`` when it drops a literal of ``texts``, with the repair instruction (``error``)."""
    lost = missing(literals(texts), contract)
    if lost:
        raise ValueError(error(lost))


def error(lost: list[str]) -> str:
    """The repair instruction for a draft that dropped ``lost``."""
    return (
        "The contract drops literals the user's brief states: "
        + ", ".join(f"`{item}`" for item in lost)
        + ". Keep each one exactly as the brief writes it: quote it verbatim in the criterion, behavior or "
        "deliverable that covers it, or, for a format with placeholders (ALL-CAPS words) or alternatives (a|b), "
        "give an acceptance criterion whose worked example fills it in without changing anything else"
    )


def rule(found: list[str]) -> str:
    """The planner instruction that names the literals its draft must keep, so it need not be sent back."""
    return (
        prompts.get("fragments/brief-literals/rule-02.md")
        + ", ".join(f"`{item}`" for item in found)
        + prompts.get("fragments/brief-literals/rule.md")
    )
