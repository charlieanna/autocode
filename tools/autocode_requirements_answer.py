"""Requirements answers a discussion itself: the discuss job with nothing to write (owner request, 2026-11-09).

Every task that changes something goes through the full pipeline (#3010). A discussion changes nothing,
so planning, approving and building it spent a Planner, a Builder and two reviews on a reply. Instead,
Requirements' own reply is recorded as the turn's answer under ``state["answer"]``, the record the discuss
job has always written (autocode_discuss_job renders it, autocode_chat_history and the status view show it),
with ``answered_by: "requirements_gather"``; the run finishes the turn. The person's next message is a
follow-up turn that starts at Requirements again (autocode_follow_up), and may ask for a change.

Pure over the state dict; imports only autocode_run_status, so the Requirements chat policy can use it
without importing the discuss job's probe machinery.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_run_status as run_status

STAGE = "requirements_gather"


def conversational(report: dict) -> bool:
    """Whether a Requirements report asks only to discuss: the discuss job with nothing to write.

    A discussion whose answer is to be written into a file describes that file in required_behaviors
    and acceptance_tests (the Requirements prompt says so), and is planned, built and verified like
    any other change. With neither, nothing would be planned or built, so Requirements' own reply
    answers it (answer_from_requirements)."""
    return (
        (report or {}).get("job") == "discuss"
        and not report.get("required_behaviors")
        and not report.get("acceptance_tests")
    )


def answer_from_requirements(state: RunState, report: dict, record: dict) -> None:
    """Record Requirements' reply as this turn's answer and finish the turn: nothing is planned or built.

    The reply is the chat's assistant_reply, or the report's summary on a run without a chat. Its
    source_refs were already checked against the workspace when the handoff was accepted, so each one
    is kept as a file the answer was read from, not as a claim a probe showed."""
    text = str(report.get("assistant_reply") or "").strip() or str(report.get("summary") or "").strip()
    # The person's own messages and answers are cited by ID; they are what was asked, not where it was read.
    asked = {"task", *(state.get("answers") or {})}
    asked.update(str(event.get("id")) for event in state.get("user_events") or [] if event.get("id"))
    sources = [
        str(ref).strip()
        for ref in report.get("source_refs") or []
        if str(ref).strip() and str(ref) not in asked and not str(ref).startswith(("http://", "https://"))
    ]
    state["answer"] = {
        "answer": text,
        "evidence": [
            {"claim": "Read while answering", "source": ref, "example": "", "probe": ""}
            for ref in dict.fromkeys(sources)
        ],
        "questions": [],
        "note_path": "",
        "output": record.get("output"),
        "probes": [],
        "answered_by": STAGE,
    }
    run_status.complete(state, completed_at=dt.datetime.now(dt.UTC).isoformat())
