"""The run's append-only log of human source texts (#595).

brief_obligations.sources() used to derive source ids positionally over state that
later events rewrite: a follow-up nests the earlier task inside the new one
(autocode_follow_up), so task:0 names a different text after the turn and a sealed
source binding can no longer be checked against what it named. This module records
each human text once, in arrival order, with its hash, and never rewrites a record:
task:0 means the same bytes for the life of the run. Answers and feedback append at
the same boundary that saves their ledger event.

A record is live only while the ledger event it was saved from is still recorded:
feedback while its event is in brief_feedback and user_events, an answer while it
is the saved answer for its question. Tampering with a ledger therefore still
drops the source, exactly as positional derivation did.

States saved before the log existed have no source_log key and fall back to
positional derivation, so contracts sealed before this change verify as before.
Imports nothing from AutoCode beyond the shared helpers; RunState is imported for
type checking only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import hashlib

try:
    from . import autocode_conversation as conversation
    from . import autocode_linked_designs as linked_designs
    from . import autocode_linked_issues as linked_issues
    from . import autocode_requirements_chat as requirements_chat
except ImportError:
    import autocode_conversation as conversation
    import autocode_linked_designs as linked_designs
    import autocode_linked_issues as linked_issues
    import autocode_requirements_chat as requirements_chat

KEY: Final = "source_log"


def _record(ident, kind, text):
    return {"id": ident, "kind": kind, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def _existing_linked_ids(state: RunState) -> set[str]:
    return {
        row["id"]
        for row in sources(state) or ()
        if row.get("kind") in (linked_issues.LINKED_ISSUE_KIND, linked_designs.LINKED_DESIGN_KIND)
    }


def _append_linked_issues(state: RunState, parent_id: str, text: str) -> None:
    workspace = state.get("workspace")
    if not workspace or KEY not in state or not text:
        return
    seen = _existing_linked_ids(state)
    state[KEY].extend(linked_issues.fetch_linked_records(workspace, parent_id, text, existing_ids=seen))
    seen = _existing_linked_ids(state)
    state[KEY].extend(linked_designs.fetch_linked_records(workspace, parent_id, text, existing_ids=seen))


def task_records(task):
    """The records a run's initial task seeds, in the same projection sources() used."""
    texts = conversation.task_user_texts(task) if task else None
    return [
        _record(f"task:{i}", "conversation_user" if texts is not None else "task", text)
        for i, text in enumerate(texts if texts is not None else [task])
        if text
    ]


def seed(state: RunState):
    """Record the run's initial task texts once, plus any origin GitHub issue/PR links they name."""
    if KEY not in state:
        initial = task_records(state.get("task") or "")
        state[KEY] = initial
        for row in list(initial):
            _append_linked_issues(state, row["id"], row["text"])


def save_feedback(state: RunState, event):
    """The one boundary that saves a brief-feedback event: both ledgers, then the log.

    goals.feedback, goals.apply_intervention_feedback and follow_up.accept all call
    this; the source record is written exactly when its ledger event is."""
    requirements_chat.retain_reply(state, event)
    state.setdefault("user_events", []).append(event)
    state.setdefault("brief_feedback", []).append(event)
    if KEY not in state or not event.get("id") or not event.get("text"):
        return
    kind = "user_intervention" if event.get("actor") == "user_intervention" else "user_feedback"
    ident = "feedback:" + event["id"]
    state[KEY].append(_record(ident, kind, event["text"]))
    _append_linked_issues(state, ident, event["text"])


def save_answer(state: RunState, question_id, event):
    """The one boundary that saves a question answer: the ledger, then the log.

    Only a user's own answer is a brief source: delegated defaults, permission
    answers and checkpoint answers are quotable elsewhere but were never part of
    brief_obligations.sources()."""
    state.setdefault("answers", {})[question_id] = event
    if KEY not in state or event.get("kind") != "answer" or event.get("actor") != "user_cli":
        return
    if not event.get("text"):
        return
    ident = "answer:" + question_id
    state[KEY].append(_record(ident, "user_answer", event["text"]))
    _append_linked_issues(state, ident, event["text"])


def records(state: RunState):
    log = state.get(KEY)
    return log if isinstance(log, list) else None


def _linked_issue_authentic(record: dict, live: dict) -> bool:
    parent = live.get(record.get("parent_id"))
    if not isinstance(parent, dict) or not isinstance(record.get("url"), str):
        return False
    text = record.get("text") or ""
    if hashlib.sha256(text.encode()).hexdigest() != record.get("sha256"):
        return False
    ident_lower = record["id"].lower()
    for link in linked_issues.extract_links(parent["text"]):
        if link["id"].lower() == ident_lower or (
            link["route"] == "issues"
            and ident_lower == linked_issues.source_id(link["owner"], link["repo"], "pull", link["number"]).lower()
        ):
            return True
    return False


def _linked_design_authentic(record: dict, live: dict) -> bool:
    parent = live.get(record.get("parent_id"))
    path = record.get("path")
    if not isinstance(parent, dict) or not isinstance(path, str):
        return False
    text = record.get("text") or ""
    if hashlib.sha256(text.encode()).hexdigest() != record.get("sha256"):
        return False
    if record.get("id") != linked_designs.source_id(path):
        return False
    return path in linked_designs.extract_paths(parent["text"])


def _live_records(state: RunState):
    log = records(state)
    if log is None:
        return None
    feedback = state.get("brief_feedback") or []
    user_events = state.get("user_events") or []
    answers = state.get("answers") or {}
    live: dict[str, dict] = {}
    order = []
    for record in log:
        if not isinstance(record, dict) or not record.get("id") or not record.get("text"):
            continue
        ident, kind, text = record["id"], record["kind"], record["text"]
        if kind in ("user_feedback", "user_intervention"):
            event_id = ident.removeprefix("feedback:")
            found = any(
                event.get("id") == event_id and event.get("text") == text and event in user_events for event in feedback
            )
        elif kind == "user_answer":
            saved = answers.get(ident.removeprefix("answer:"))
            found = isinstance(saved, dict) and saved.get("text") == text
        elif kind == linked_issues.LINKED_ISSUE_KIND:
            found = _linked_issue_authentic(record, live)
        elif kind == linked_designs.LINKED_DESIGN_KIND:
            found = _linked_design_authentic(record, live)
        else:
            # Task texts are immutable once logged; a follow-up's rewrite of
            # state["task"] is exactly what the log protects them from.
            found = kind in ("task", "conversation_user")
        if not found:
            continue
        if ident not in live:
            order.append(ident)
        live[ident] = record
    return [live[ident] for ident in order]


def sources(state: RunState):
    """The live sources from the log, or None when the run predates it.

    The last record for an id wins, mirroring the answer ledger's replacement."""
    live = _live_records(state)
    if live is None:
        return None
    return [{"id": row["id"], "kind": row["kind"], "text": row["text"]} for row in live]


def linked_issue_records(state: RunState) -> list[dict]:
    """Authenticated live linked-issue/PR metadata records (with url, fetched_at, sha256) for handoffs."""
    live = _live_records(state) or []
    return [
        {key: row[key] for key in ("id", "kind", "url", "fetched_at", "sha256", "text") if key in row}
        for row in live
        if row.get("kind") == linked_issues.LINKED_ISSUE_KIND
    ]


def linked_design_records(state: RunState) -> list[dict]:
    """Authenticated live workspace design-document records named by user messages."""
    live = _live_records(state) or []
    return [
        {key: row[key] for key in ("id", "kind", "path", "sha256", "text") if key in row}
        for row in live
        if row.get("kind") == linked_designs.LINKED_DESIGN_KIND
    ]
