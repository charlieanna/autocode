"""An opt-in Requirements convention question for public project-memory controls.

Only model output is scripted. The test answers the real issued request through
AutoCode's public CLI; this provider never reads or writes AutoCode run state.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

QUESTION_ID = "PROJECT_TEST_NAMES"
QUESTION = "Which naming convention should future repository tests follow?"
ANSWER = "Use descriptive behavior-based unittest test method names."


def requirements(report: dict, data: dict) -> dict:
    """Ask only when neither this run's human answer nor historical memory supplies it."""
    saved = (data.get("saved_answers") or {}).get(QUESTION_ID) or {}
    answered = isinstance(saved, dict) and saved.get("kind") == "answer" and saved.get("text") == ANSWER
    memory = data.get("project_memory") or {}
    remembered = any(
        isinstance(row, dict) and row.get("question") == QUESTION and row.get("answer") == ANSWER
        for row in memory.get("conventions") or []
    )
    asked = not answered and not remembered
    questions = (
        [
            {
                "id": QUESTION_ID,
                "question": QUESTION,
                "why": "The request does not state the team's reusable naming preference for future repository tests.",
                "options": [],
                "proposed_default": "",
                "kind": "decision",
                "category": "technical",
                "delegable": False,
            }
        ]
        if asked
        else []
    )
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("project-memory-requests.jsonl")
    with trace.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "stage": "requirements_gather",
                    "asked": asked,
                    "saved_answer_used": answered,
                    "memory_convention_used": remembered,
                    "memory_test_command": memory.get("test_command"),
                }
            )
            + "\n"
        )
    return {
        **report,
        "open_questions": questions,
        # A tag names an actually issued question, never a manufactured answer.
        # Completion may persist it only after the person's matching public answer.
        "project_conventions": [QUESTION_ID] if asked or answered else [],
    }
