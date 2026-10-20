"""Requirements that asks before it specifies: a questions-only handoff on the default flow.

Only model output is scripted. The first Requirements report names the outcome and asks one
decision question with empty behaviors and acceptance tests, as the Requirements prompt asks
while a request is still being clarified (live, 2026-10-20: ``program plan`` on "build an
adaptive learning engine" asked three such questions). Once the person's answer is saved, the
report is the scenario's complete one. This provider never reads or writes AutoCode run state.
"""

from __future__ import annotations

QUESTION_ID = "GREETING_FORM"
QUESTION = "Should the greeting be a library function, a command line, or both?"


def requirements(report: dict, data: dict) -> dict:
    """Ask until the person's own answer is in the saved answers; then hand over the complete report."""
    saved = (data.get("saved_answers") or {}).get(QUESTION_ID) or {}
    if isinstance(saved, dict) and str(saved.get("text") or "").strip():
        return report
    return {
        **report,
        "required_behaviors": [],
        "acceptance_tests": [],
        "open_questions": [
            {
                "id": QUESTION_ID,
                "question": QUESTION,
                "why": "The request does not say how the greeting is invoked, which changes what is built.",
                "options": [],
                "proposed_default": "",
                "kind": "decision",
                "category": "requested_outcome",
                "delegable": False,
            }
        ],
    }
