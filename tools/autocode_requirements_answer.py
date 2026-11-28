"""Requirements answers a discussion itself: the discuss job with nothing to write (owner request, 2026-11-09).

Every task that changes something goes through the full pipeline (#3010). A discussion changes nothing,
so planning, approving and building it spent a Planner, a Builder and two reviews on a reply. Instead,
Requirements' own reply is recorded as the turn's answer under ``state["answer"]``, the record the discuss
job wrote before it was removed (``render`` here shows it, autocode_chat_history and the status view show it),
with ``answered_by: "requirements_gather"``; the run finishes the turn. The person's next message is a
follow-up turn that starts at Requirements again (autocode_follow_up), and may ask for a change.

Which reports Requirements answers itself is ``answers_itself``, read by the chat policy that answers and by
the acceptance gate that first holds such a report to findings that answer the request
(autocode_discussion_answer, #4237): a reply that only restated the request used to complete the turn.

Pure over the state dict; imports only autocode_run_status and autocode_discussion_answer, so the
Requirements chat policy can use it without importing the discuss job's probe machinery.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_discussion_answer as discussion_answer
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_discussion_answer as discussion_answer
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


def answers_itself(report: dict, *, known_kind, answered) -> bool:
    """Whether Requirements answers this report's discussion itself once it is accepted.

    The run's job is still open or is ``discuss`` (``known_kind``), the report is ``conversational`` under
    that job, and every open question it asks is among ``answered`` (the person's answer ids). The chat
    policy answers exactly these (autocode_requirements_chat.answer_now), and acceptance holds exactly these
    to findings that answer the request (autocode_discussion_answer)."""
    if known_kind not in (None, "discuss") or not conversational({**report, "job": known_kind or report.get("job")}):
        return False
    return all(row.get("id") in answered for row in report.get("open_questions") or [])


# A small turn's message is only greeting or small talk: exact word sequences after contraction
# expansion, and nothing else. Conservative toward the strict path: a message that states or
# plausibly names code work (imperative change language, named files or artifacts) matches nothing
# here and keeps the strict report contract, because a task whose report silently loses
# required_behaviors is the corrupted-criteria failure mode.
_CONTRACTIONS = {
    "what's": "what is",
    "whats": "what is",
    "how's": "how is",
    "hows": "how is",
    "it's": "it is",
    "its": "it is",
    "that's": "that is",
    "thats": "that is",
    "i'm": "i am",
    "im": "i am",
}
SMALL_TURN_MESSAGES = frozenset(
    {
        ("hi",),
        ("hello",),
        ("hey",),
        ("yo",),
        ("hiya",),
        ("howdy",),
        ("greetings",),
        ("sup",),
        ("hi", "there"),
        ("hello", "there"),
        ("hey", "there"),
        ("good", "morning"),
        ("good", "afternoon"),
        ("good", "evening"),
        ("good", "night"),
        ("how", "are", "you"),
        ("how", "are", "you", "doing"),
        ("how", "are", "you", "today"),
        ("how", "is", "it", "going"),
        ("how", "is", "everything"),
        ("what", "is", "up"),
        ("what", "is", "your", "name"),
        ("who", "are", "you"),
        ("what", "can", "you", "do"),
        ("nice", "to", "meet", "you"),
        ("thanks",),
        ("thank", "you"),
        ("thank", "you", "very", "much"),
        ("thanks", "a", "lot"),
        ("bye",),
        ("goodbye",),
        ("see", "you"),
        ("see", "you", "later"),
        ("take", "care"),
        ("ok",),
        ("okay",),
        ("sounds", "good"),
        ("great",),
        ("cool",),
        ("nice",),
        ("awesome",),
        ("that", "is", "all"),
        ("never", "mind"),
        ("all", "good"),
        ("no", "thanks"),
    }
)


def names_no_goal(message) -> bool:
    """Whether a turn's saved message is only greeting or small talk: it names no software goal.

    Narrow and deterministic over the turn's own message, never over model text: only an exact
    greeting or small-talk sequence matches, so a message that states or plausibly names code work
    is never treated as a small turn. The missing-field tolerance in the Requirements acceptance
    gate (autocode_requirements_handoff.no_goal_first_answer) is keyed to this."""
    words: list[str] = []
    for token in re.findall(r"[a-z']+", str(message or "").lower()):
        words.extend(_CONTRACTIONS.get(token, token).split())
    return tuple(words) in SMALL_TURN_MESSAGES


def answer_from_requirements(state: RunState, report: dict, record: dict) -> None:
    """Record Requirements' reply as this turn's answer and finish the turn: nothing is planned or built.

    The reply is the chat's assistant_reply, or the report's summary on a run without a chat, followed by
    its findings (autocode_discussion_answer.lines). Its source_refs were already checked against the
    workspace when the handoff was accepted: each finding's evidence is kept as the source its statement
    was read from, and every other one as a file read while answering, not as a claim a probe showed."""
    text = str(report.get("assistant_reply") or "").strip() or str(report.get("summary") or "").strip()
    findings = discussion_answer.lines(report)
    if findings:
        text = (text + "\n\n" if text else "") + "\n".join(findings)
    # The person's own messages and answers are cited by ID; they are what was asked, not where it was read.
    asked = {"task", *(state.get("answers") or {})}
    asked.update(str(event.get("id")) for event in state.get("user_events") or [] if event.get("id"))
    sources = [
        str(ref).strip()
        for ref in report.get("source_refs") or []
        if str(ref).strip() and str(ref) not in asked and not str(ref).startswith(("http://", "https://"))
    ]
    evidence: list[dict] = []
    for row in discussion_answer.rows(report):
        claim = str(row.get("statement") or "").strip()
        if row.get("kind") == "evidence_gap" and not claim.lower().startswith("not settled"):
            claim = "Not settled: " + claim
        evidence += [
            {"claim": claim, "source": str(ref).strip(), "example": "", "probe": ""}
            for ref in row.get("evidence") or []
            if str(ref).strip() and str(ref) not in asked and not str(ref).startswith(("http://", "https://"))
        ]
    cited = {discussion_answer.ref_path(row["source"]) for row in evidence}
    evidence += [
        {"claim": "Read while answering", "source": ref, "example": "", "probe": ""}
        for ref in dict.fromkeys(sources)
        if discussion_answer.ref_path(ref) not in cited
    ]
    state["answer"] = {
        "answer": text,
        "evidence": evidence,
        "questions": [],
        "note_path": "",
        "output": record.get("output"),
        "probes": [],
        "answered_by": STAGE,
    }
    run_status.complete(state, completed_at=dt.datetime.now(dt.UTC).isoformat())


def owns(state: RunState) -> bool:
    """Whether this run's turn ended with a discussion Requirements answered (autocode_jobs.ended_in)."""
    return (state.get("workflow") or {}).get("kind") == "discuss" and bool(state.get("answer"))


def render(state: RunState) -> str:
    """The completion summary of an answered discussion: the answer, where it was read and what is still open."""
    found = state.get("answer") or {}
    lines = ["ANSWER — nothing was changed", "", str(found.get("answer", "")), "", "Evidence:"]
    probed = {row["claim"] for row in found.get("probes") or []}
    lines += [
        f"  - {row['claim']} ({row['source']})"
        + ("; shown by running: " + row["probe"] if row["claim"] in probed else "; read from the source, not run")
        for row in found.get("evidence") or []
    ]
    lines += ["Question for you: " + question for question in found.get("questions") or []]
    if found.get("note_path"):
        lines.append("Note written: " + str(Path(state.get("workspace", "")) / found["note_path"]))
    if found.get("output"):
        lines.append("Requirements report: " + str(found["output"]))
    if found.get("questions"):
        lines.append("Reply with --follow-up TEXT to answer them in this run.")
    return "\n".join(lines)
