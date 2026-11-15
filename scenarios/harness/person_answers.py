"""A scenario's person: answers by topic, for questions whose ids a live model chooses itself.

``[fake] answers`` names questions by id, which only a scripted model's questions have in advance,
so a live run could only ever take AutoCode's proposed default and an oracle could not tell whether
the person's own decision, where it differs from that default, reached the delivered code. A
``[person]`` table answers by what a question is about instead, in fake and live runs alike:

    [[person.answers]]
    match = "duplicate|repeated|same key|conflict"   # regex, searched in the question's text
    choose = "last|later|override"                    # regex, picks the first offered option it finds
    answer = "The last file's value wins."            # free text: a question without options, or a correction

A question with ``options`` takes exactly one of those labels or its ``proposed_default``
(docs/task-run.md, needs ``answer``), so ``choose`` picks among the labels the view offers and the
driver never types text the CLI would refuse. A question without options takes ``answer``. Requirements'
own "does this match what you want" confirmation (``requirements-review-*``) is answered with the
``answer`` texts of every entry it mentions that has not yet been applied, so the person corrects a
listed assumption once instead of approving it; with none it is approved as before.

The harness drives the CLI and never imports tools/; this module is pure and reads nothing.
"""

from __future__ import annotations

import re

KEYS = {"match", "choose", "answer"}


def parse(scenario_id: str, table: object) -> tuple[dict, ...]:
    """The validated ``[person] answers`` entries, in order; empty when the scenario declares none."""
    if table is None:
        return ()
    if not isinstance(table, dict) or set(table) - {"answers"}:
        raise ValueError(f"{scenario_id}: [person] holds only an answers list")
    entries = table.get("answers", [])
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{scenario_id}: [person] answers is a nonempty list of {{match, choose, answer}}")
    parsed = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - KEYS or not {"match", "answer"} <= set(entry):
            raise ValueError(f"{scenario_id}: each [person] answers entry has match, answer and optional choose")
        if not all(isinstance(entry.get(key, "x"), str) and entry.get(key, "x").strip() for key in KEYS):
            raise ValueError(f"{scenario_id}: [person] answers values are nonempty strings")
        for key in ("match", "choose"):
            if key in entry:
                try:
                    re.compile(entry[key])
                except re.error as error:
                    raise ValueError(f"{scenario_id}: [person] answers {key} {entry[key]!r}: {error}") from None
        parsed.append(dict(entry))
    return tuple(parsed)


def _text(question: dict) -> str:
    return " ".join(str(question.get(key) or "") for key in ("question", "why"))


def entry_for(entries, question: dict) -> dict | None:
    """The first entry whose ``match`` is found in the question's text, or None."""
    text = _text(question)
    return next((entry for entry in entries if re.search(entry["match"], text, re.I)), None)


def answer_for(entries, question: dict) -> str | None:
    """The person's answer to an ordinary question, or None when no entry is about it.

    With options: the first offered label ``choose`` finds (``answer`` when it is itself a label);
    None when the person's choice is not among them, so the caller does not type a refused answer.
    Without options: ``answer``.
    """
    entry = entry_for(entries, question)
    if entry is None:
        return None
    options = [option for option in question.get("options") or [] if isinstance(option, str) and option.strip()]
    if not options:
        return entry["answer"]
    if entry["answer"] in options:
        return entry["answer"]
    pattern = entry.get("choose")
    return next((option for option in options if pattern and re.search(pattern, option, re.I)), None)


def corrections_for(entries, question: dict, applied: set[int]) -> tuple[list[int], str]:
    """For a Requirements confirmation: the unapplied entries it mentions and the person's correction.

    An entry whose line in the confirmation already says the person's choice (its ``choose`` is
    found on a line ``match`` is found on) agrees with what Requirements understood: it counts as
    applied with no correction, so a confirmation that already has it right is approved.
    """
    lines = [line for line in re.split(r"\n|(?<=[.;])\s+", _text(question)) if line.strip()]
    hits, agreed = [], []
    for number, entry in enumerate(entries):
        if number in applied:
            continue
        mentioned = [line for line in lines if re.search(entry["match"], line, re.I)]
        if not mentioned:
            continue
        choose = entry.get("choose")
        if choose and any(re.search(choose, line, re.I) for line in mentioned):
            agreed.append(number)
        else:
            hits.append(number)
    return [*hits, *agreed], " ".join(entries[number]["answer"] for number in hits)
