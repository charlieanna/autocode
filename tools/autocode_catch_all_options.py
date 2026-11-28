"""Drop a question's catch-all option ("Something else, which you describe in your own words") (#4223).

A person can always answer a Requirements question in their own words (``autocode_answer_options.allows_custom``),
so an option that only says so tells them nothing and takes a slot meant for a real reading. Requirements'
prompt forbids it, but a model that sees "two to four options" as a target sometimes fills the last slot with
one. The option is a mechanical defect, so the runner removes it without a model call (architecture rule 8)
and the handoff records what it removed. No answer is refused for it.

An option is a catch-all only when it is one of the stock openings below and the rest of its text says
nothing but "describe it": "Other users keep their lists" or "None of these files change" is a reading
and stays, and so does a bare "Other" or "None of the above", which can be a category or "change
nothing". Pure: imports nothing from AutoCode.
"""

from __future__ import annotations

import re

_OPENING = re.compile(
    r"(something else|anything else|other|others|none of (?:these|those|the above|them)|"
    r"(?:a |an )?(?:different|another) (?:option|answer|choice|reading|approach))\b",
)
# After the opening, only an invitation to answer freely: "which you describe in your own words",
# "(please specify)", "tell us what you want".
_FILLER_WORDS = frozenset(
    (
        "a",
        "an",
        "and",
        "as",
        "at",
        "below",
        "by",
        "describe",
        "describing",
        "details",
        "detail",
        "else",
        "explain",
        "for",
        "here",
        "how",
        "i",
        "in",
        "instead",
        "it",
        "let",
        "me",
        "mean",
        "own",
        "please",
        "say",
        "so",
        "specify",
        "state",
        "tell",
        "that",
        "the",
        "them",
        "then",
        "this",
        "to",
        "type",
        "us",
        "want",
        "what",
        "which",
        "words",
        "write",
        "you",
        "your",
        "yourself",
    )
)


def is_catch_all(option) -> bool:
    """Whether ``option`` only invites the person to answer in their own words."""
    if not isinstance(option, str):
        return False
    text = " ".join(option.casefold().replace("’", "'").split()).strip(" .!*_`\"'")
    opening = _OPENING.match(text)
    if not opening:
        return False
    rest = re.findall(r"[a-z']+", text[opening.end() :])
    if not rest:
        # A bare "Other" or "None of the above" can be a real answer (a category, "change nothing").
        return opening.group(1) in ("something else", "anything else")
    return all(word in _FILLER_WORDS for word in rest)


def without_catch_all(questions) -> tuple[list, list[dict]]:
    """``questions`` with every catch-all option removed, and what was removed.

    A ``proposed_default`` that named a removed option is cleared, since recommending "something else"
    recommends nothing. A question left with no option becomes an open question, which takes free text."""
    kept, dropped = [], []
    for question in questions or []:
        options = question.get("options") if isinstance(question, dict) else None
        if not isinstance(options, list) or not any(is_catch_all(option) for option in options):
            kept.append(question)
            continue
        removed = [option for option in options if is_catch_all(option)]
        changed = {**question, "options": [option for option in options if not is_catch_all(option)]}
        if question.get("proposed_default") in removed:
            changed["proposed_default"] = ""
        kept.append(changed)
        dropped += [{"question": str(question.get("id") or ""), "option": option} for option in removed]
    return kept, dropped
