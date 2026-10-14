"""Spec-first runs (#1200): the user confirms what Requirements understood before any plan exists.

Opt-in per new run (``--spec-first``, saved as ``settings.spec_first``). On the default flow
requirements_gather goes straight to astra_discovery, and a run with no open question never shows
the user its requirements until goal approval. With the setting, every accepted handoff stops the
run with one runner-written question beside the handoff's own open questions: answer ``plan`` to
start planning, or say what to change and Requirements runs again with that answer in its
saved_answers. The question id carries the handoff's digest, so a new handoff asks again.

Published through the ordinary clarification request, like the bug Investigator's questions
(autocode_bug_questions): resolver_human admits these exact questions with ``accepts``. Imports
only the shared helpers, so the goal lifecycle, AutoResolver and the CLI can all use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

SETTING = "spec_first"
STAGE = "requirements_gather"
NEXT = "astra_discovery"
PREFIX = "requirements-review-"
APPROVE = "plan"
# The review question shows at most this many entries per list; the full handoff stays in the run.
SHOWN = 8


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def add_argument(parser) -> None:
    parser.add_argument(
        "--spec-first",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Stop after Requirements on every new handoff: answer 'plan' to start planning, or say what "
        "to change (#1200). New runs with joint planning on the default flow; off by default",
    )


def new_run_setting(requested, joint: bool, v2: bool) -> bool:
    """Off unless asked for; refused where the default flow's requirements_gather does not run."""
    if requested is True and (not joint or v2):
        raise ValueError("--spec-first needs joint planning and the default planning flow")
    return requested is True


def resume_refused(saved_settings: dict, requested) -> bool:
    """A saved run keeps its flow; repeating its own choice is harmless, no flag changes nothing."""
    return requested is not None and bool(requested) != bool(saved_settings.get(SETTING))


def _report(state: RunState) -> dict:
    return (state.get("requirements_handoff") or {}).get("report") or {}


def _lines(title: str, rows) -> list[str]:
    rows = [str(row).strip() for row in rows or [] if str(row).strip()]
    if not rows:
        return []
    more = [f"  … and {len(rows) - SHOWN} more"] if len(rows) > SHOWN else []
    return [f"{title}:", *(f"  - {row}" for row in rows[:SHOWN]), *more]


def review_question(state: RunState) -> dict:
    """The runner's question for the current handoff: what Requirements understood, and 'plan' to go on."""
    report = _report(state)
    assumptions = [
        row.get("text", row) if isinstance(row, dict) else row for row in report.get("proposed_assumptions") or []
    ]
    text = "\n".join(
        [
            "Requirements understood this. Answer 'plan' to start planning, or say what to change.",
            f"Outcome: {str(report.get('intended_outcome', '')).strip()}",
            *_lines("Behaviors", report.get("required_behaviors")),
            *_lines("Acceptance examples", report.get("acceptance_tests")),
            *_lines("Constraints", report.get("constraints")),
            *_lines("Left out", report.get("ignored_statements")),
            *_lines("Assumptions", assumptions),
        ]
    )
    return {
        # Each Requirements run archives the previous handoff, so a correction that leaves the report
        # unchanged still asks again under a new id (an answered id cannot be answered twice).
        "id": PREFIX + util.digest({"report": report, "round": len(state.get("requirements_history") or [])})[:16],
        "question": text,
        "why": "Spec-first run: nothing is planned until you confirm what Requirements understood.",
        # Free text, not a label: a correction is an answer too (options would only accept their labels).
        "options": [],
        "proposed_default": APPROVE,
        "kind": "decision",
        "category": "requested_outcome",
        "delegable": False,  # confirming the requirements is the user's own call
    }


def questions(state: RunState) -> list[dict]:
    """The handoff's unanswered open questions, then the review question; [] when there is nothing to confirm."""
    if not enabled(state) or not state.get("requirements_handoff") or state.get("goal_contract"):
        return []
    answered = state.get("answers") or {}
    review = review_question(state)
    if review["id"] in answered:
        return []
    return [*(row for row in _report(state).get("open_questions") or [] if row.get("id") not in answered), review]


def wait(state: RunState, record: dict, queue) -> None:
    """After an accepted handoff, stop for the user (``queue`` is resolver_human.queue); no-op when off."""
    rows = questions(state)
    if not rows:
        return
    output = record.get("output")
    pins = {output: util.file_hash(output)} if output and Path(output).is_file() else {}
    origin = {"stage": STAGE, **{key: record[key] for key in ("output", "source_revision", "task_id") if key in record}}
    queue(
        state, "clarification", origin, questions=rows, evidence={"hashes": pins}, phase="DISCOVERING", next_stage=NEXT
    )


def accepts(state: RunState, proposal: dict) -> bool:
    """Only this run's own review questions, for the current handoff, before any contract exists."""
    rows = proposal.get("questions") or []
    declared = questions(state)
    return (
        proposal.get("scope") == "clarification"
        and state.get("next_stage") == NEXT
        and bool(rows)
        and len({row.get("id") for row in rows}) == len(rows)
        and all(row in declared for row in rows)
    )


def after_answers(state: RunState) -> None:
    """A review answer other than 'plan' is new requirements input: Requirements runs again, not the Planner."""
    if not enabled(state) or state.get("goal_contract") or state.get("pending_questions"):
        return
    answer = (
        (state.get("answers") or {}).get(review_question(state)["id"]) if state.get("requirements_handoff") else None
    )
    if answer and state.get("next_stage") == NEXT and answer.get("text", "").strip().lower() != APPROVE:
        state.update({"next_stage": STAGE, "phase": "DISCOVERING"})
