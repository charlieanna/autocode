"""Spec-first runs (#1200): the user confirms what Requirements understood before any plan exists.

Opt-in per new run (``--spec-first``, saved as ``settings.spec_first``). On the default flow a
complete handoff goes straight to astra_discovery, and a run with no open question never shows
the user its requirements until goal approval. With the setting, every accepted handoff stops the
run with one runner-written question beside the handoff's own open questions: answer ``plan`` to
start planning, or say what to change and Requirements runs again with that answer in its
saved_answers. The question id carries the handoff's digest, so a new handoff asks again.
The question says what approving starts for the job Requirements named (``job``, #1846): a plan for
a build, an investigation for a bug, the review or the design work for those. A question the Analyst
answers is never asked to be approved (autocode_requirements_chat.answer_now).

On every flow, a handoff that has only questions so far (``questions_only``: open questions and no
behaviors or acceptance tests yet, as the Requirements prompt asks while a vague request is still
being clarified) stops the run the same way with the handoff's own questions, and Requirements runs
again with the answers. Before that, the default flow refused such a handoff as invalid output: a
live ``program plan`` on "build an adaptive learning engine" asked three good questions and stalled
until a person resumed it.

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
    from . import autocode_scope_question as scope_question
    from . import autocode_util as util
except ImportError:
    import autocode_scope_question as scope_question
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


# The jobs that build nothing (autocode_workflows.WORKFLOWS), named here so this module imports only util.
READ_ONLY_JOBS = ("review", "design", "discuss")
JOBS = ("build", "bugfix", *READ_ONLY_JOBS)


def job(state: RunState, report: dict | None = None) -> str | None:
    """The job this run is for: the recognized or pinned kind, else the one Requirements named (#1846)."""
    known = (state.get("workflow") or {}).get("kind")
    if known:
        return known
    named = (_report(state) if report is None else report).get("job")
    return named if named in JOBS else None


def builds_code(kind: str | None) -> bool:
    """Whether a job needs behaviors and acceptance tests; an unknown job is held to a build's handoff."""
    return kind not in READ_ONLY_JOBS


# What approving the understanding starts, by job; anything not listed is planned as a build.
CONFIRMATION = {
    "bugfix": "Approve this understanding to investigate the bug and prepare a reviewed fix plan, "
    "or tell me what to change.",
    "review": "Approve this understanding to start the review, or tell me what to change. The review changes no files.",
    "design": "Approve this understanding to start the design work, or tell me what to change. Nothing is built.",
}


def questions_only(report: dict) -> bool:
    """A report with open questions and no behaviors or acceptance tests yet: Requirements is still clarifying."""
    return bool(report.get("open_questions")) and not (
        report.get("required_behaviors") and report.get("acceptance_tests")
    )


def _lines(title: str, rows) -> list[str]:
    rows = [str(row).strip() for row in rows or [] if str(row).strip()]
    if not rows:
        return []
    more = [f"  … and {len(rows) - SHOWN} more"] if len(rows) > SHOWN else []
    # A blank line before each section keeps a Markdown client from folding the title into the list above.
    return ["", f"{title}:", *(f"  - {row}" for row in rows[:SHOWN]), *more]


def review_question(state: RunState) -> dict:
    """The runner's question for the current handoff: what Requirements understood, and 'plan' to go on."""
    report = _report(state)
    assumptions = [
        row.get("text", row) if isinstance(row, dict) else row for row in report.get("proposed_assumptions") or []
    ]
    # ignored_statements also carries procedural source text and headings, not
    # only excluded work. Its treatment is explained by the saved summary.
    statements = report.get("ignored_statements") or []
    notes = str(report.get("summary") or "").strip() if statements else ""
    text = "\n".join(
        [
            "Here’s what I understand. Does this match what you want?",
            "",
            f"Outcome: {str(report.get('intended_outcome', '')).strip()}",
            *_lines("Behaviors", report.get("required_behaviors")),
            *_lines("Acceptance examples", report.get("acceptance_tests")),
            *_lines("Constraints", report.get("constraints")),
            *_lines("Other source statements", statements),
            *(["", f"Notes: {notes}"] if notes else []),
            *_lines("Assumptions", assumptions),
            "",
            CONFIRMATION.get(
                job(state) or "", "Approve this understanding to prepare a reviewed plan, or tell me what to change."
            ),
        ]
    )
    return {
        # Each Requirements run archives the previous handoff, so a correction that leaves the report
        # unchanged still asks again under a new id (an answered id cannot be answered twice).
        "id": PREFIX + util.digest({"report": report, "round": len(state.get("requirements_history") or [])})[:16],
        "question": text,
        "why": "Spec-first run: nothing "
        + ("is planned" if builds_code(job(state)) else "starts")
        + " until you confirm what Requirements understood.",
        # Free text, not a label: a correction is an answer too (options would only accept their labels).
        "options": [],
        "proposed_default": APPROVE,
        "kind": "decision",
        "category": "requested_outcome",
        "delegable": False,  # confirming the requirements is the user's own call
    }


def questions(state: RunState) -> list[dict]:
    """The handoff's unanswered open questions, then (spec-first) the review question; [] when nothing waits."""
    # Work that belongs to another repository is settled with the person before any plan, on every flow (#2066).
    scope = scope_question.question(state)
    if scope:
        return [scope]
    if not state.get("requirements_handoff") or state.get("goal_contract"):
        return []
    answered = state.get("answers") or {}
    unresolved = [row for row in _report(state).get("open_questions") or [] if row.get("id") not in answered]
    if not enabled(state):
        # The default flow stops only while Requirements is still clarifying. A complete handoff with open
        # questions goes to the Planner, whose draft carries them (autocode_planning_clarification).
        return unresolved if questions_only(_report(state)) else []
    review = review_question(state)
    if review["id"] in answered:
        return []
    # Establish the requested outcome before offering to plan anything.
    return unresolved or [review]


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
    """Only this run's current review questions, including new scope on a retained contract."""
    rows = proposal.get("questions") or []
    declared = questions(state)
    return (
        proposal.get("scope") == "clarification"
        and state.get("next_stage") == NEXT
        and bool(rows)
        and len({row.get("id") for row in rows}) == len(rows)
        and all(row in declared for row in rows)
    )


def recover_scope(state: RunState, queue) -> None:
    """A guarded legacy/resumed execution returns to the same public scope decision."""
    if state.get("status") != "PAUSED_SCOPE_DECISION" or not scope_question.question(state):
        return
    record = (state.get("workspace_scope") or {}).get("origin") or state.get("requirements_handoff") or {}
    wait(state, record, queue)


def after_answers(state: RunState) -> None:
    """A review answer other than 'plan', or the answers to a questions-only handoff, are new requirements
    input: Requirements runs again, not the Planner."""
    if state.get("goal_contract") or state.get("pending_questions") or not state.get("requirements_handoff"):
        return
    if enabled(state):
        answer = (state.get("answers") or {}).get(review_question(state)["id"])
        needs_refresh = (answer and answer.get("text", "").strip().lower() != APPROVE) or (
            not answer and bool(_report(state).get("open_questions"))
        )
    else:
        needs_refresh = questions_only(_report(state))
    if needs_refresh and state.get("next_stage") == NEXT:
        state.update({"next_stage": STAGE, "phase": "DISCOVERING"})
