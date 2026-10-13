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
Imports nothing from AutoCode beyond the shared helpers.
"""

from __future__ import annotations

import hashlib

try:
    from . import autocode_conversation as conversation
except ImportError:
    import autocode_conversation as conversation

KEY = "source_log"


def _record(ident, kind, text):
    return {"id": ident, "kind": kind, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def task_records(task):
    """The records a run's initial task seeds, in the same projection sources() used."""
    texts = conversation.task_user_texts(task) if task else None
    return [
        _record(f"task:{i}", "conversation_user" if texts is not None else "task", text)
        for i, text in enumerate(texts if texts is not None else [task])
        if text
    ]


def seed(state):
    """Record the run's initial task texts once. Never rewrites an existing log."""
    if KEY not in state:
        state[KEY] = task_records(state.get("task") or "")


def save_feedback(state, event):
    """The one boundary that saves a brief-feedback event: both ledgers, then the log.

    goals.feedback, goals.apply_intervention_feedback and follow_up.accept all call
    this; the source record is written exactly when its ledger event is."""
    state.setdefault("user_events", []).append(event)
    state.setdefault("brief_feedback", []).append(event)
    if KEY not in state or not event.get("id") or not event.get("text"):
        return
    kind = "user_intervention" if event.get("actor") == "user_intervention" else "user_feedback"
    state[KEY].append(_record("feedback:" + event["id"], kind, event["text"]))


def save_answer(state, question_id, event):
    """The one boundary that saves a question answer: the ledger, then the log.

    Only a user's own answer is a brief source: delegated defaults, permission
    answers and checkpoint answers are quotable elsewhere but were never part of
    brief_obligations.sources()."""
    state.setdefault("answers", {})[question_id] = event
    if KEY not in state or event.get("kind") != "answer" or event.get("actor") != "user_cli":
        return
    if not event.get("text"):
        return
    state[KEY].append(_record("answer:" + question_id, "user_answer", event["text"]))


def records(state):
    log = state.get(KEY)
    return log if isinstance(log, list) else None


def sources(state):
    """The live sources from the log, or None when the run predates it.

    The last record for an id wins, mirroring the answer ledger's replacement."""
    log = records(state)
    if log is None:
        return None
    feedback = state.get("brief_feedback") or []
    user_events = state.get("user_events") or []
    answers = state.get("answers") or {}
    live, order = {}, []
    for record in log:
        if not isinstance(record, dict) or not record.get("id") or not record.get("text"):
            continue
        ident, kind, text = record["id"], record["kind"], record["text"]
        if kind in ("user_feedback", "user_intervention"):
            event_id = ident.removeprefix("feedback:")
            found = any(
                event.get("id") == event_id and event.get("text") == text and event in user_events
                for event in feedback
            )
        elif kind == "user_answer":
            saved = answers.get(ident.removeprefix("answer:"))
            found = isinstance(saved, dict) and saved.get("text") == text
        else:
            # Task texts are immutable once logged; a follow-up's rewrite of
            # state["task"] is exactly what the log protects them from.
            found = kind in ("task", "conversation_user")
        if not found:
            continue
        if ident not in live:
            order.append(ident)
        live[ident] = {"id": ident, "kind": kind, "text": text}
    return [live[ident] for ident in order]
