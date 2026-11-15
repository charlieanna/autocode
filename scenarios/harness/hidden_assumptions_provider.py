"""Requirements that finds two product forks in the code a complete-looking brief leaves open.

Only model output is scripted. ``feature-hidden-assumptions`` asks for ``envfile merge``; the brief
reads as complete, but the code shows two user-visible choices it does not make: ``load()`` keeps a
repeated key's FIRST value inside one file (so which file wins across files is open), and ``show``
refuses a file with no entries (so whether an empty merge is an error is open). The first
Requirements report asks both, each with options and a recommended default that follows the code;
the scenario's person ([person] answers) picks the other option for both. Once both answers are
saved, the report is the scenario's complete one. The repository's conventions (the ``--sorted``
spelling and the ``fail()`` error helper) are settled from the code and never asked.
This provider never reads or writes AutoCode run state.
"""

from __future__ import annotations

QUESTIONS = (
    {
        "id": "DUPLICATE_KEYS",
        "question": "When the same key is set in more than one input file, which value should merge keep?",
        "why": "load() keeps the first value of a key repeated inside one file; the request does not say "
        "which file wins across files, and the merged output differs.",
        "options": [
            "The first file's value, as load() keeps the first value inside one file",
            "The last file's value: later files override earlier ones",
        ],
        "proposed_default": "The first file's value, as load() keeps the first value inside one file",
        "kind": "decision",
        "category": "behavior",
        "delegable": False,
    },
    {
        "id": "EMPTY_INPUT",
        "question": "When the input files have no entries at all, should merge fail as show does for an empty "
        "file, or print nothing?",
        "why": "show exits 1 with 'no entries' for an empty file; the request does not say what an empty "
        "merge does, and scripts calling it see a different exit status.",
        "options": [
            "Fail with an error, as show does for an empty file",
            "Print nothing and succeed cleanly",
        ],
        "proposed_default": "Fail with an error, as show does for an empty file",
        "kind": "decision",
        "category": "behavior",
        "delegable": False,
    },
)


def requirements(report: dict, data: dict) -> dict:
    """Ask both forks until the person's answers are saved; then hand over the complete report."""
    saved = data.get("saved_answers") or {}
    answered = all(
        isinstance(saved.get(row["id"]), dict) and str(saved[row["id"]].get("text") or "").strip() for row in QUESTIONS
    )
    if answered:
        return report
    return {
        **report,
        "required_behaviors": [],
        "acceptance_tests": [],
        "open_questions": [dict(row, options=list(row["options"])) for row in QUESTIONS],
    }
