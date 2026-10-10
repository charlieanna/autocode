"""Settled discussion in review prompts: kept short in the prompt, kept whole in the context artifact.

Feedback and answers a user gave before the current contract was approved are already reflected in that
approved contract, which the Validator and Completion Owner judge against. Their full records still went
into every review prompt and were re-sent on every step: on a long run, feedback and answers were about
half of each Validator and Completion Owner prompt (2026-10-02). For those stages, a settled feedback
event keeps its identity and an excerpt, and a settled planning answer keeps its question, options and
answer (dropping only the question's other metadata). The full records move to the context artifact
(autocode_context.compact), retrievable and hash-pinned. Anything given after the approval or without a
time, and every permission or checkpoint answer (granted under an approved contract, not part of it),
stays whole. Pure: callers pass the handoff in.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

STAGES = ("sol", "astra_review", "astra_checkpoint")
PLANNING_ANSWERS = ("answer", "delegated")
EXCERPT_CHARS = 240
NOTE = (
    "Feedback and answers given before the current contract was approved are already reflected in it. "
    "Here each settled feedback event keeps its id and an excerpt, and each settled answer keeps its question, "
    "options and answer. Their full records are in context_artifact under brief_feedback and saved_answers; "
    "retrieve them before relying on a detail the contract does not state."
)


def _time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def approved_at(base: dict):
    """When the contract this handoff is judged against was approved; None when it is not approved."""
    goal = base.get("goal_contract")
    if not isinstance(goal, dict):
        return None
    event = goal.get("approval_event")
    if not isinstance(event, dict):
        return None
    if goal.get("approval_status") == "approved":
        return _time(event.get("at"))
    return None


def _settled(record, cutoff) -> bool:
    """Given at or before the approval, and still a full record (a shortened one has no text)."""
    moment = _time(record.get("at")) if isinstance(record, dict) and "text" in record and cutoff else None
    return bool(moment) and moment <= cutoff


def _feedback_excerpt(event: dict) -> dict:
    text = str(event.get("text") or "")
    excerpt = text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + "..."
    return {
        "id": event.get("id"),
        "at": event.get("at"),
        "actor": event.get("actor"),
        "excerpt": excerpt,
        "chars": len(text),
    }


def _answer_core(answer: dict) -> dict:
    question = answer.get("question")
    core = {
        "question": question.get("question", "") if isinstance(question, dict) else str(question or ""),
        "answer": answer.get("text", ""),
        "kind": answer.get("kind"),
        "at": answer.get("at"),
    }
    if isinstance(question, dict) and question.get("options"):
        core["options"] = question["options"]
    return core


def condense(base: dict) -> tuple[dict, dict]:
    """A copy of a review handoff with settled feedback and answers shortened, and the full records it moved.
    Returns the handoff unchanged and {} for other stages, an unapproved contract, or when nothing shrinks."""
    cutoff = approved_at(base) if base.get("stage") in STAGES else None
    feedback = base.get("brief_feedback")
    if not isinstance(feedback, list):
        feedback = []
    answers = base.get("saved_answers")
    if not isinstance(answers, dict):
        answers = {}
    settled_feedback = [event for event in feedback if _settled(event, cutoff)]
    settled_answers = {
        key: value
        for key, value in answers.items()
        if _settled(value, cutoff) and value.get("kind") in PLANNING_ANSWERS
    }
    if not settled_feedback and not settled_answers:
        return base, {}
    result = copy.deepcopy(base)
    moved: dict[str, Any] = {}
    if settled_feedback:
        result["brief_feedback"] = [
            _feedback_excerpt(event) if _settled(event, cutoff) else event for event in feedback
        ]
        moved["brief_feedback"] = settled_feedback
    if settled_answers:
        result["saved_answers"] = {
            key: _answer_core(value) if key in settled_answers else value for key, value in answers.items()
        }
        moved["saved_answers"] = settled_answers
    result["settled_history_note"] = NOTE
    if len(json.dumps(result)) >= len(json.dumps(base)):
        return base, {}
    return result, moved
