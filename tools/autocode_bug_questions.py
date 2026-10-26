"""Report-bound clarification for a bug investigation, including reproduced follow-ups.

Pure policy shared by the Investigator, human publication and answer routing.
The bug job owns ``investigation``; its retained output hash binds these questions
to one accepted report. Existing human request receipts bind answers to the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_scope_question as scope_question
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_scope_question as scope_question
    import autocode_util as util
    import autocode_workflows as workflows


def has_questions(state: RunState):
    """A reproduced diagnosis still cannot settle a missing human scope decision."""
    found = state.get("investigation") or {}
    return (
        workflows.kind(state) == "bugfix"
        and found.get("outcome") in ("reproduced", "not_reproduced")
        and (bool(found.get("questions")) or scope_question.question(state) is not None)
    )


def questions(state: RunState):
    """Stable ordinary questions for the exact retained diagnosis report."""
    if not has_questions(state):
        return []
    scope = scope_question.question(state)
    found = state["investigation"]
    report = found.get("output_hash") or util.digest(found)
    return ([scope] if scope else []) + [
        {
            "id": "investigation-" + util.digest({"report": report, "index": index, "question": text})[:16],
            "question": text,
            "why": "The Investigator needs this information before the reported bug can be resolved.",
            "options": [],
            "proposed_default": "",
            "kind": "decision",
            "category": "requested_outcome",
            "delegable": False,
        }
        for index, text in enumerate(found["questions"])
    ]


def matches_questions(state: RunState, rows):
    declared = questions(state)
    return (
        state.get("next_stage") == workflows.INVESTIGATE_STAGE
        and bool(rows)
        and len({row.get("id") for row in rows}) == len(rows)
        and all(row in declared for row in rows)
    )


def evidence(state: RunState):
    found = state.get("investigation") or {}
    output, digest = found.get("output"), found.get("output_hash")
    return {"hashes": {output: digest}} if output and digest else {"hashes": {}}


def repeated_answers(state: RunState, rows):
    answered = {
        str((answer.get("question") or {}).get("question", "")).strip()
        for answer in state.get("answers", {}).values()
        if answer.get("actor") == "user_cli"
        and answer in state.get("user_events", [])
        and str((answer.get("question") or {}).get("id", "")).startswith("investigation-")
    }
    return [text for text in rows if text.strip() in answered]


def answer_frontier(state: RunState, default_stage):
    if has_questions(state) and state.get("next_stage") == workflows.INVESTIGATE_STAGE:
        return {"phase": "INVESTIGATING", "next_stage": workflows.INVESTIGATE_STAGE}
    return {"phase": "DISCOVERING", "next_stage": default_stage}


def accepts(state: RunState, proposal):
    """Only actual questions from a successfully recorded Investigator report."""
    if proposal.get("scope") != "clarification" or not matches_questions(state, proposal.get("questions") or []):
        return False
    found = state["investigation"]
    pins = evidence(state)["hashes"]
    if (
        not pins
        or not found.get("source_revision")
        or any(proposal.get("evidence", {}).get("hashes", {}).get(path) != digest for path, digest in pins.items())
    ):
        return False
    return any(
        (row.get("original_stage") or row.get("stage")) == workflows.INVESTIGATE_STAGE
        and row.get("output") == found.get("output")
        and row.get("exit_code") == 0
        and row.get("source_revision") == found.get("source_revision")
        and not row.get("rejected")
        for row in state.get("stages", [])
    )
