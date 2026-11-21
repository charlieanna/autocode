"""Spec-first runs (#1200): the user confirms what Requirements understood before any plan exists.

Opt-in per new run (``--spec-first``, saved as ``settings.spec_first``). On the default flow a
complete handoff with no open question and no unconfirmed assumption goes straight to draft_plan.
With the setting, every accepted handoff stops the
run with one runner-written question beside the handoff's own open questions: answer ``plan`` to
start planning, or say what to change and Requirements runs again with that answer in its
saved_answers. The question id carries the handoff's digest, so a new handoff asks again.

On every flow (#3603) a handoff's unanswered open questions stop the run before any plan, and the
answers send Requirements back with them (``after_answers``); before, the default flow passed a
complete handoff's questions to the Planner, which drafted a plan before the person could answer.
A handoff whose assumptions the person has not confirmed (``autocode_assumption_ledger.unconfirmed``:
a user-visible behavior Requirements chose, or a convention it did not read in the workspace) stops
the default flow at the same single review question, which lists every assumption and why it was
made. Approving it records which assumptions the person confirmed (``confirmed_assumption_ids``);
an unattended ``--skip-requirements`` run records them as delegated by that choice instead of
stopping. A run whose questions and assumptions are all settled stops nowhere.

Readiness is read per turn (``current_turn_has_contract``): a follow-up keeps the finished turn's
goal contract, and its own Requirements questions and confirmation still stop it until its own
plan exists.
The question says what approving starts for the job Requirements named (``job``, #1846): a plan for
a build, an investigation for a bug, the review or the design work for those. A discussion Requirements
answers itself is never asked to be approved (autocode_requirements_chat.answer_now).

On every flow, a handoff that has only questions so far (``questions_only``: open questions and no
behaviors or acceptance tests yet, as the Requirements prompt asks while a vague request is still
being clarified) stops the run the same way with the handoff's own questions, and Requirements runs
again with the answers. Before that, the default flow refused such a handoff as invalid output: a
live ``program plan`` on "build an adaptive learning engine" asked three good questions and stalled
until a person resumed it.

Published through the ordinary clarification request: resolver_human admits these exact questions
with ``accepts``. Imports
only the shared helpers, so the goal lifecycle, AutoResolver and the CLI can all use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import re
from pathlib import Path

try:
    from . import autocode_contract_identity as identity
    from . import autocode_scope_question as scope_question
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as identity
    import autocode_scope_question as scope_question
    import autocode_util as util

SETTING = "spec_first"
STAGE = "requirements_gather"
NEXT = "draft_plan"
PREFIX = "requirements-review-"
APPROVE = "plan"
# The review question shows at most this many entries per list; the full handoff stays in the run.
SHOWN = 8


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def current_turn_has_contract(state: RunState) -> bool:
    """Whether the current turn's own plan exists: a goal contract installed during this turn.

    A finished run keeps its goal contract when a follow-up (autocode_follow_up.accept) opens the next
    turn, so ``goal_contract`` alone says only that some earlier turn was planned. The follow-up's own
    receipt (the turn's ``event_id`` in ``user_events``) names the contract that stood when the person
    said it. Until the Planner installs a different one, the turn is still gathering its requirements
    and stops for its own questions and confirmation like a first request. A first request, and a turn
    saved before turns carried that receipt, have only the contract they planned themselves.
    """
    contract = state.get("goal_contract")
    if not contract:
        return False
    turns = state.get("turns") or []
    event_id = turns[-1].get("event_id") if turns and isinstance(turns[-1], dict) else None
    if not event_id:
        return True
    receipt = next(
        (row for row in state.get("user_events") or [] if isinstance(row, dict) and row.get("id") == event_id), None
    )
    if receipt is None or "contract_token" not in receipt:
        return True
    try:
        return identity.token(contract) != receipt["contract_token"]
    except (KeyError, TypeError):
        return True


# Who confirmed an assumption: the person approving the review question, or the person's own choice
# at the start of an unattended run (autocode_skip_requirements.DELEGATED) to take every default.
CONFIRMED_BY_REVIEW = "requirements_review"
CONFIRMED_BY_SKIP = "skip_requirements"


def current_turn_handoff(state: RunState) -> dict:
    """The current turn's accepted Requirements handoff, or {} before this turn has one.

    A follow-up turn keeps the previous turn's handoff in ``requirements_handoff`` until its own
    ``requirements_gather`` runs, so ``check_requirement_handoff`` can compare against it;
    ``previous.requirements_output`` names that earlier output path. Once this turn accepts a
    handoff, the earlier one moves to ``requirements_history``."""
    handoff = state.get("requirements_handoff")
    if not isinstance(handoff, dict):
        return {}
    turns = state.get("turns") or []
    prior_output = (
        (turns[-1].get("previous") or {}).get("requirements_output") if turns and isinstance(turns[-1], dict) else None
    )
    if (
        prior_output
        and handoff.get("output") == prior_output
        and not any(
            isinstance(row, dict) and row.get("output") == prior_output
            for row in state.get("requirements_history") or []
        )
    ):
        return {}
    return handoff


def _structured_assumptions(report: dict) -> list[dict]:
    return [row for row in report.get("proposed_assumptions") or [] if isinstance(row, dict) and row.get("id")]


def confirmed_assumption_ids(state: RunState) -> frozenset[str]:
    """The current handoff's assumption ids the person confirmed, each bound to the exact text confirmed.

    Each confirmation is saved on the handoff it confirmed (``confirmed_assumptions``) and stays with
    it in ``requirements_history``, so an assumption Requirements repeats word for word after a
    correction is not asked again, and one it rewords is."""
    confirmed = set()
    for handoff in [*(state.get("requirements_history") or []), current_turn_handoff(state)]:
        record = handoff.get("confirmed_assumptions") if isinstance(handoff, dict) else None
        for row in (record or {}).get("assumptions") or []:
            if isinstance(row, dict):
                confirmed.add((row.get("id"), row.get("text")))
    return frozenset(
        row["id"] for row in _structured_assumptions(_report(state)) if (row["id"], row.get("text")) in confirmed
    )


def confirm_assumptions(state: RunState, *, source: str, question_id: str | None = None) -> None:
    """Record every assumption of the current handoff as confirmed by ``source``."""
    handoff = current_turn_handoff(state)
    if not handoff:
        return
    rows = [
        {"id": row.get("id"), "text": row.get("text")} if isinstance(row, dict) else {"id": None, "text": str(row)}
        for row in _report(state).get("proposed_assumptions") or []
    ]
    if not rows:
        return
    # The ids that needed the person when this record was written: the standing reads them later and
    # never re-derives them through the ledger, whose answer changes with the files a stage read.
    asked = [row.get("id") for row in unconfirmed_assumptions(state) if isinstance(row, dict) and row.get("id")]
    handoff["confirmed_assumptions"] = {
        "source": source,
        **({"question_id": question_id} if question_id else {}),
        "assumptions": rows,
        "asked": asked,
    }


# Where an assumption's standing comes from (#3898), shown beside ``confirmed`` in the status view.
NEEDS_CONFIRMATION = "needs_confirmation"
CONVENTION_NOT_APPROVED = "convention_not_approved"
CONFIRMED_BY_PERSON = "confirmed_by_person"
APPROVED_WITH_UNDERSTANDING = "approved_with_understanding"
SKIPPED_REVIEW = "skipped_review"
STANDINGS = (
    NEEDS_CONFIRMATION,
    CONVENTION_NOT_APPROVED,
    CONFIRMED_BY_PERSON,
    APPROVED_WITH_UNDERSTANDING,
    SKIPPED_REVIEW,
)


def _record_standing(record: dict, assumption_id) -> str:
    """The standing one saved confirmation gives an assumption it holds."""
    if record.get("source") == CONFIRMED_BY_SKIP:
        return SKIPPED_REVIEW
    # A record saved before ``asked`` existed never shows a row as the person's own confirmation.
    if record.get("source") == CONFIRMED_BY_REVIEW and assumption_id in (record.get("asked") or []):
        return CONFIRMED_BY_PERSON
    return APPROVED_WITH_UNDERSTANDING


def assumption_standing(state: RunState) -> dict[str, str]:
    """Each current assumption id's standing, read from the saved confirmations (#3898).

    The latest saved record holding the row's exact id and text decides, from its source and the ids
    it recorded as needing the person (``asked``). A later approval that did not ask about a row the
    person had already confirmed word for word keeps that confirmation. A row in no record needs the
    person, or is a project convention nobody has approved yet."""
    standing: dict[tuple, str] = {}
    for handoff in [*(state.get("requirements_history") or []), current_turn_handoff(state)]:
        record = handoff.get("confirmed_assumptions") if isinstance(handoff, dict) else None
        if not isinstance(record, dict):
            continue
        for row in record.get("assumptions") or []:
            if not isinstance(row, dict):
                continue
            key = (row.get("id"), row.get("text"))
            value = _record_standing(record, row.get("id"))
            if value == APPROVED_WITH_UNDERSTANDING and standing.get(key) == CONFIRMED_BY_PERSON:
                continue
            standing[key] = value
    rows = _structured_assumptions(_report(state))
    if any((row["id"], row.get("text")) not in standing for row in rows):
        pending = {row.get("id") for row in unconfirmed_assumptions(state) if isinstance(row, dict)}
    else:
        pending = set()
    return {
        row["id"]: standing.get(
            (row["id"], row.get("text")),
            NEEDS_CONFIRMATION if row["id"] in pending else CONVENTION_NOT_APPROVED,
        )
        for row in rows
    }


def confirmed_by_review(state: RunState, question_id: str) -> bool:
    """Whether the current handoff's assumptions were confirmed by approving ``question_id``."""
    record = current_turn_handoff(state).get("confirmed_assumptions") or {}
    return record.get("source") == CONFIRMED_BY_REVIEW and record.get("question_id") == question_id


def _load_assumption_ledger():
    try:
        from . import autocode_assumption_ledger as ledger
    except ImportError:
        try:
            import autocode_assumption_ledger as ledger
        except ImportError:
            return None
    return ledger


def unconfirmed_assumptions(state: RunState) -> list[dict]:
    """The current handoff's assumptions that still need the person (autocode_assumption_ledger)."""
    rows = _report(state).get("proposed_assumptions") or []
    ledger = _load_assumption_ledger()
    if not rows or ledger is None:
        return []
    return ledger.unconfirmed(
        rows,
        confirmed_ids=confirmed_assumption_ids(state),
        verify=ledger.verifier(state, STAGE),
        cites_person=ledger.cites_person(state),
    )


def delegate_assumptions(state: RunState) -> None:
    """An unattended run (``--skip-requirements``) records its assumptions as delegated, never stops on them.

    The person chose at the start to take every default (autocode_skip_requirements), so the
    assumptions are recorded as confirmed by that choice, under its own source, where a reviewer sees
    them; a run with nobody watching does not wait for a confirmation nobody will give."""
    if (state.get("settings") or {}).get(CONFIRMED_BY_SKIP) and unconfirmed_assumptions(state):
        confirm_assumptions(state, source=CONFIRMED_BY_SKIP)


def awaits_confirmation(state: RunState) -> bool:
    """Whether the current handoff stops for the person to confirm its assumptions."""
    if (current_turn_handoff(state).get("confirmed_assumptions") or {}).get("source") == CONFIRMED_BY_SKIP:
        return False
    return bool(unconfirmed_assumptions(state))


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
    return current_turn_handoff(state).get("report") or {}


# The jobs that build nothing (autocode_workflows.WORKFLOWS), named here so this module imports only util.
READ_ONLY_JOBS = ("review", "design", "discuss")
JOBS = ("build", "bugfix", *READ_ONLY_JOBS)


def job(state: RunState, report: dict | None = None) -> str | None:
    """The job this run is for: the named or pinned kind, else the one Requirements named (#1846)."""
    known = (state.get("workflow") or {}).get("kind")
    if known:
        return known
    named = (_report(state) if report is None else report).get("job")
    return named if named in JOBS else None


def builds_code(kind: str | None) -> bool:
    """Whether a job needs behaviors and acceptance tests; an unknown job is held to a build's handoff."""
    return kind not in READ_ONLY_JOBS


def confirmation_job(state: RunState) -> str | None:
    """What the understanding confirmation starts, preserving historical offers."""
    kind = job(state)
    if kind == "bugfix" and (state.get("settings") or {}).get("implementation_flow") == "standard":
        return "build"
    return kind


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


def handoff_complete(state: RunState, report: dict | None = None) -> bool:
    """Whether ``report`` already names the outcome and, for a build or fix, its behaviors and acceptance tests."""
    target = _report(state) if report is None else report
    if not str(target.get("intended_outcome") or "").strip():
        return False
    if builds_code(job(state, target)):
        return bool(target.get("required_behaviors") and target.get("acceptance_tests"))
    return True


# A question that asks only whether the whole summary or understanding is right (#3304): AutoCode's own
# review question confirms the understanding once, so asking it again in open_questions would ask twice.
_SUMMARY_CONFIRMATION_OPTIONS = frozenset({"yes", "no", "y", "n", "correct", "confirm", "plan"})
_SUMMARY_CONFIRMATION = re.compile(
    r"^\s*(?:does|is)\s+(?:this|that)"
    r"(?:\s+(?:summary|understanding|scope|spec|specification|interpretation))?\s+"
    r"(?:right|correct|accurate|good|ok|okay|"
    r"what\s+you\s+(?:want|wanted|had\s+in\s+mind|expected)|"
    r"look\s+(?:right|correct|accurate|good|ok|okay)|"
    r"match\s+(?:what\s+you\s+(?:want|wanted|had\s+in\s+mind|expected)|your\s+(?:intent|expectations)))"
    r"\s*\??\s*$",
    re.IGNORECASE,
)


def is_summary_confirmation(question) -> bool:
    """Whether ``question`` asks only to confirm the whole summary, offering no product fork (#3304)."""
    if not isinstance(question, dict):
        return False
    options = {str(option).strip().casefold() for option in question.get("options") or [] if str(option).strip()}
    if not options <= _SUMMARY_CONFIRMATION_OPTIONS:
        return False
    return bool(_SUMMARY_CONFIRMATION.match(str(question.get("question") or "")))


def without_summary_confirmations(state: RunState, report: dict) -> tuple[list[dict], list[str]]:
    """``report``'s open questions without redundant whole-summary confirmations once the handoff is complete.

    While Requirements is still clarifying (no outcome, behaviors or acceptance tests yet) or when a
    question backs a proposed reframe, every question stays. On a complete handoff, a bare "Is this
    right?" duplicates AutoCode's single review confirmation (#3304)."""
    rows = list(report.get("open_questions") or [])
    if not rows or not handoff_complete(state, report):
        return rows, []
    reframes = {row.get("question_id") for row in report.get("proposed_reframes") or [] if isinstance(row, dict)}
    kept, dropped = [], []
    for row in rows:
        if isinstance(row, dict) and row.get("id") not in reframes and is_summary_confirmation(row):
            dropped.append(str(row.get("id") or ""))
        else:
            kept.append(row)
    return kept, dropped


def _row(row) -> str:
    """Display authored content without guessing which whitespace is meaningful.

    Every section can contain examples. Keep spaces and make control characters
    visible so a quoted newline or tab cannot silently change the confirmation.
    """
    return str(row).strip().replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _lines(title: str, rows, *, limit: int | None = SHOWN) -> list[str]:
    rows = [_row(row) for row in rows or [] if str(row).strip()]
    if not rows:
        return []
    shown = rows if limit is None else rows[:limit]
    more = [f"  … and {len(rows) - len(shown)} more"] if len(rows) > len(shown) else []
    # A blank line before each section keeps a Markdown client from folding the title into the list above.
    return ["", f"{title}:", *(f"  - {row}" for row in shown), *more]


def _assumption(row) -> str:
    """An assumption and why Requirements made it: the person confirms the reason as well as the choice."""
    if not isinstance(row, dict):
        return str(row)
    text = _row(row.get("text") or "")
    rationale = _row(row.get("rationale") or "")
    return f"{text} (why: {rationale})" if text and rationale else text


def review_question(state: RunState) -> dict:
    """The runner's question for the current handoff: what Requirements understood, and 'plan' to go on."""
    report = _report(state)
    # Every assumption is shown, never cut to SHOWN: approving confirms each one (confirm_assumptions).
    assumptions = [_assumption(row) for row in report.get("proposed_assumptions") or []]
    # ignored_statements also carries procedural source text and headings, not
    # only excluded work. Its treatment is explained by the saved summary.
    statements = report.get("ignored_statements") or []
    notes = str(report.get("summary") or "").strip() if statements else ""
    kind = confirmation_job(state)
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
            *_lines("Assumptions", assumptions, limit=None),
            "",
            CONFIRMATION.get(
                kind or "", "Approve this understanding to prepare a reviewed plan, or tell me what to change."
            ),
        ]
    )
    return {
        # Each Requirements run archives the previous handoff, so a correction that leaves the report
        # unchanged still asks again under a new id (an answered id cannot be answered twice).
        "id": PREFIX + util.digest({"report": report, "round": len(state.get("requirements_history") or [])})[:16],
        "question": text,
        "why": (
            "Spec-first run: nothing "
            + ("is planned" if builds_code(job(state)) else "starts")
            + " until you confirm what Requirements understood."
            if enabled(state)
            else "Requirements made assumptions you have not confirmed: nothing "
            + ("is planned" if builds_code(job(state)) else "starts")
            + " until you confirm them or say what to change."
        ),
        # Free text, not a label: a correction is an answer too (options would only accept their labels).
        "options": [],
        "proposed_default": APPROVE,
        "kind": "decision",
        "category": "requested_outcome",
        "delegable": False,  # confirming the requirements is the user's own call
    }


def questions(state: RunState) -> list[dict]:
    """The handoff's unanswered open questions, then the review question (spec-first, or assumptions the
    person has not confirmed); [] when nothing waits."""
    handoff = current_turn_handoff(state)
    report = handoff.get("report") or {}
    answered = state.get("answers") or {}
    planned = current_turn_has_contract(state)
    open_questions, _ = without_summary_confirmations(state, report)
    unresolved = [row for row in open_questions if row.get("id") not in answered]
    if not planned and unresolved:
        # On every flow: no plan is drafted while Requirements still has a question for the person
        # (#3603). Clarify the requested outcome before choosing where its work belongs.
        # This answers no scope question and leaves all discovered parts intact.
        return unresolved
    try:
        from . import autocode_catalog_context as source_catalog
        from . import autocode_coordination_context as coordination
    except ImportError:
        import autocode_catalog_context as source_catalog
        import autocode_coordination_context as coordination
    if (
        handoff
        and not planned
        and (enabled(state) or awaits_confirmation(state))
        and job(state) == "bugfix"
        and not coordination.joint_planning(state)
        and not coordination.context(state)
        and source_catalog.context(state)
    ):
        # The catalog permits read-only diagnosis, not skipping the source's
        # requested confirmation. Members inherit that confirmed task intent.
        review = review_question(state)
        answer = answered.get(review["id"])
        if not answer:
            return [review]
        if answer.get("text", "").strip().lower() != APPROVE:
            return []  # after_answers sends the correction back to Requirements
    # Work that belongs to another repository is settled with the person before any plan, on every flow (#2066).
    scope = scope_question.question(state)
    if scope:
        return [scope]
    if not handoff or planned:
        return []
    if coordination.joint_planning(state) or (
        job(state) == "bugfix" and (coordination.context(state) or source_catalog.context(state))
    ):
        # The complete repository and joint plans carry clear requirements
        # into the single approval. Business questions were answered above.
        return []
    review = review_question(state)
    if review["id"] in answered:
        return []
    if enabled(state) or awaits_confirmation(state):
        # One confirmation of the whole understanding, listing every assumption; never a second one.
        return [review]
    return []


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


def answered_since_handoff(state: RunState) -> list[str]:
    """The current handoff's open questions the person answered after Requirements wrote it.

    A handoff records the answers it was written with (``answers_seen``, autocode_requirements_handoff),
    so a question Requirements repeats after its answer does not send Requirements back again."""
    handoff = current_turn_handoff(state)
    seen = set(handoff.get("answers_seen") or [])
    answered = state.get("answers") or {}
    open_questions, _ = without_summary_confirmations(state, handoff.get("report") or {})
    return [
        qid for row in open_questions if isinstance(qid := row.get("id"), str) and qid in answered and qid not in seen
    ]


def after_answers(state: RunState, *, queue=None) -> None:
    """A review answer other than 'plan', or answers to the handoff's own open questions, are new requirements
    input: Requirements runs again with them, not the Planner, on every flow (#3603). Approving the review
    records the assumptions it listed as confirmed by the person."""
    handoff = current_turn_handoff(state)
    if current_turn_has_contract(state) or state.get("pending_questions") or not handoff:
        return
    review = review_question(state)
    answer = (state.get("answers") or {}).get(review["id"])
    if answer and answer.get("text", "").strip().lower() == APPROVE and not answer.get("delegated"):
        confirm_assumptions(state, source=CONFIRMED_BY_REVIEW, question_id=review["id"])
    if answer:
        needs_refresh = answer.get("text", "").strip().lower() != APPROVE
    else:
        needs_refresh = bool(answered_since_handoff(state))
    if needs_refresh and state.get("next_stage") == NEXT:
        state.update({"next_stage": STAGE, "phase": "DISCOVERING"})
    elif queue is not None and state.get("next_stage") == NEXT and questions(state):
        # Confirmation establishes intent; it cannot settle an independent
        # repository assignment or business question before the Planner.
        wait(state, handoff, queue)


def check_pre_contract_handoff(state: RunState, selected) -> bool:
    """Validate ``selected`` against the current turn's handoff digest before a goal contract exists (#3650)."""
    if current_turn_has_contract(state):
        return False
    report = current_turn_handoff(state).get("report")
    ref = util.digest(report)[:12] if isinstance(report, dict) else "none"
    if not selected or ref == "none" or selected != ref:
        raise ValueError(
            "Act only on the current displayed revision; show the goal again and pass its token with --review-token"
        )
    return True


def active_handoff_report(state: RunState) -> dict:
    """The current turn's handoff report before a goal contract exists, else ``requirements_handoff.report``."""
    handoff = state.get("requirements_handoff") if current_turn_has_contract(state) else current_turn_handoff(state)
    return (handoff or {}).get("report") or {}


def reject_stage(state: RunState) -> str:
    """The stage an assumption rejection restarts at."""
    settings = state.get("settings") or {}
    if "requirements" in (settings.get("roles") or {}) or (
        not current_turn_has_contract(state) and settings.get("joint_planning")
    ):
        return STAGE
    return NEXT
