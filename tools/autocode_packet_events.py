"""Say each saved person event once in a planning stage's handoff.

A stage's packet carries ``brief_feedback`` and ``saved_answers`` in full, and ``user_events``
repeats the same records: every requirements reply with the model's earlier reply attached,
and a requirements approval as its answer and the gate message that interpreted it, each with
the whole displayed understanding. In a live run on 2026-11-05 that was 12 KB of the Planner's
15 KB ``user_events`` and was sent again to every planning stage.

An event that is exactly a record already in the packet keeps its identity (kind, id, actor,
time, question and token), so provenance rules that name a ``user_events`` id still find it,
and points to where the full record is. Nothing that is not an exact copy is removed.
"""

from __future__ import annotations

try:
    from . import autocode_python_test_roles as test_roles
except ImportError:
    import autocode_python_test_roles as test_roles

IDENTITY = ("kind", "id", "question_id", "actor", "at", "contract_token", "gate_kind", "gate_token", "intent")


def _stub(event: dict, where: str) -> dict:
    return {**{key: event[key] for key in IDENTITY if key in event}, "same_as": where}


def reference_repeats(events, brief_feedback, saved_answers) -> list:
    """``events`` with each exact copy of a ``brief_feedback`` or ``saved_answers`` record referenced."""
    feedback = [row for row in brief_feedback or [] if isinstance(row, dict)]
    answers = saved_answers if isinstance(saved_answers, dict) else {}
    answered = [row.get("question") for row in answers.values() if isinstance(row, dict) and row.get("question")]
    result = []
    for event in events or []:
        if not isinstance(event, dict):
            result.append(event)
        elif event.get("kind") == "protected_tests_roles_corrected":
            result.append(test_roles.correction_summary(event))
        elif event in feedback:
            result.append(_stub(event, "brief_feedback"))
        elif (qid := event.get("question_id")) in answers and answers[qid] == event:
            result.append(_stub(event, f"saved_answers.{qid}"))
        elif (
            isinstance(event.get("questions"), list)
            and event["questions"]
            and all(question in answered for question in event["questions"])
        ):
            # The understanding a gate message interpreted is the question its saved answer holds.
            shown = {key: value for key, value in event.items() if key != "questions"}
            result.append({**shown, "questions_same_as": "saved_answers"})
        else:
            result.append(event)
    return result
