"""Admit initial coordination only at the source's current public scope handoff."""

from __future__ import annotations

try:
    from . import autocode_scope_question as scope_question
except ImportError:
    import autocode_scope_question as scope_question


def pending_scope(view: dict) -> bool:
    """Requirements and business questions must settle before repository fan-out."""
    scope = view.get("scope") or {}
    need = view.get("needs") or {}
    questions = need.get("questions") or []
    identity = scope.get("identity")
    return bool(
        view.get("status") == "WAITING_FOR_USER"
        and scope.get("external_parts")
        and not scope.get("decision")
        and isinstance(identity, str)
        and identity
        and need.get("kind") == "answer"
        and need.get("resolver_scope") == "clarification"
        and len(questions) == 1
        and isinstance(questions[0], dict)
        and questions[0].get("id") == scope_question.PREFIX + identity
    )
