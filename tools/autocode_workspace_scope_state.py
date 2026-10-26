"""Runner-owned grounded external work and the human intent it belongs to.

Validation supplies the parts; model-only refreshes cannot erase them. Only a new
human turn or authenticated feedback permits a fresh interpretation.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def _text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def current(state: RunState) -> dict:
    """Grounded obligations for this turn, including discoveries after Requirements.

    Old runs retain their Requirements report. New records are written only by
    ``record`` and never borrow a previous turn's scope or decision.
    """
    saved = state.get("workspace_scope")
    if isinstance(saved, dict):
        return saved if saved.get("turn") == len(state.get("turns") or []) else {}
    return ((state.get("requirements_handoff") or {}).get("report") or {}).get("workspace_scope") or {}


def record(state: RunState, scope, report_record, *, stage, dropped=()):
    """Retain validated external work; a later model report cannot silently erase it."""
    previous = current(state)
    if state.get("workspace_scope") and previous.get("intent") != intent(state):
        # A new human turn/correction permits a fresh interpretation; another
        # model report within the same intent cannot remove discovered work.
        previous = {}
    parts = {
        (_text(part.get("target")), _text(part.get("work"))): copy.deepcopy(part)
        for part in previous.get("external_parts") or []
    }
    for part in scope.get("external_parts") or []:
        parts[(_text(part.get("target")), _text(part.get("work")))] = copy.deepcopy(part)
    state["workspace_scope"] = {
        "turn": len(state.get("turns") or []),
        "intent": intent(state),
        "spans_repositories": bool(parts),
        "external_parts": list(parts.values()),
        "dropped": list(dict.fromkeys([*(previous.get("dropped") or []), *dropped])),
        "origin": {
            "stage": stage,
            "output": report_record.get("output"),
            "source_revision": report_record.get("source_revision"),
        },
    }


def intent(state: RunState) -> dict:
    """Human corrections, unlike regenerated reports or answers, renew scope review."""
    return {
        "turn": len(state.get("turns") or []),
        "feedback": [
            event.get("id")
            for event in state.get("brief_feedback") or []
            if event.get("actor") in ("user_cli", "user_intervention") and event in state.get("user_events", [])
        ],
    }


def identity(state: RunState) -> str:
    """A scope choice covers only the displayed work in the current user turn."""
    return util.digest(
        {
            "intent": intent(state),
            "parts": sorted(
                (_text(part.get("target")), _text(part.get("work")))
                for part in current(state).get("external_parts") or []
            ),
        }
    )[:16]
