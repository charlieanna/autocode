"""Durable conversation turns at a task's saved execution boundary.

Receiving text changes no requirements or execution authority. Requirements answers
one queued message at a time; questions restore the exact checkpoint. The normal
feedback path alone applies a classified change after the answer is recorded.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as identity
    from . import autocode_message_context as message_context
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_contract_identity as identity
    import autocode_message_context as message_context
    import autocode_run_status as run_status

# Completed runs reject new submissions. A previously accepted message may still
# reach this boundary after the last in-flight result has been committed.
CHECKPOINTS = {"RUNNING", "AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER", "TASK_COMPLETE"}
COMPLETION_KEYS = ("completed_at", "completion_actor", "final_decision")
QUESTION_KEYS = ("pending_questions", "user_request", "resolver_human_proposal", "resolver_human_request")
CHECKPOINT_KEYS = ("status", "phase", "next_stage", "stop_reason", *COMPLETION_KEYS, *QUESTION_KEYS)


def checkpoint_allowed(state: RunState):
    """Settings may rebind a genuine clarification before queued input is applied."""
    return state.get("status") in CHECKPOINTS or (
        state.get("status") == "RESOLVER_PENDING"
        and (state.get("resolver_human_proposal") or {}).get("scope") == "clarification"
        and (state.get("resolver_human_request") or {}).get("scope") in (None, "clarification")
    )


def _token(state: RunState):
    contract = state.get("goal_contract")
    if not contract:
        return "none"
    if not identity.sealed(contract):
        raise ValueError("Conversation needs an intact saved contract")
    return identity.token(contract)


def receive(state: RunState, receipt):
    """Keep one exact user message once, including retries after its answer."""
    identifier, text = receipt.get("id"), receipt.get("text")
    if not isinstance(identifier, str) or not identifier or not isinstance(text, str) or not text.strip():
        raise ValueError("Conversation needs a request identity and nonempty text")
    context = message_context.checked(receipt.get("context"))
    events = state.setdefault("user_events", [])
    for event in events:
        if event.get("live") and event.get("request_id") == identifier:
            if event["text"] != text or event.get("context") != context:
                raise ValueError("Conversation request identity already names different text")
            return event
    event = {
        "kind": "run_message",
        "actor": "user_intervention",
        "id": "run-message-" + identifier,
        "request_id": identifier,
        "text": text,
        "at": receipt.get("submitted_at"),
        "live": True,
        **({"context": context} if context is not None else {}),
    }
    events.append(event)
    return event


def active(state: RunState):
    """The conversation currently occupying the Requirements stage, if any."""
    return next(
        (
            row
            for row in state.get("user_events", [])
            if row.get("kind") == "plan_message" and row.get("live") and "reply_after" not in row
        ),
        None,
    )


def _queued(state: RunState):
    """Select the message eligible at this checkpoint without changing it."""
    event = next((row for row in state.get("user_events", []) if row.get("kind") == "run_message"), None)
    if (
        not checkpoint_allowed(state)
        or (
            not (state.get("settings") or {}).get("spec_first")
            and ((event or {}).get("context") or {}).get("kind") != "coordinated_plan"
        )
        or any(state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair"))
        or any(
            cast(dict, state.get(key) or {}).get("scope") == "operational_exhaustion"
            for key in ("resolver_human_proposal", "resolver_human_request")
        )
        or any(row.get("kind") == "plan_message" and "reply_after" not in row for row in state.get("user_events", []))
    ):
        return None
    return event


def _stage(event):
    return "finalize_plan" if (event.get("context") or {}).get("kind") == "coordinated_plan" else "requirements_gather"


def resume_stage(state: RunState):
    """Describe a queued handoff after explicit resume; never release a pause."""
    intent = state.get("pause_intent") or {}
    if (
        state.get("status") != "PAUSED_INTERVENTION"
        or (intent.get("held_pause") and not intent.get("acknowledged_at"))
        or any(row.get("kind") == "stop" for row in state.get("applied_interventions", []))
    ):
        return None
    event = _queued(cast("RunState", {**state, "status": "RUNNING"}))
    if event is None:
        return None
    try:
        _token(state)
    except (KeyError, ValueError):
        return None
    return _stage(event)


def activate(state: RunState):
    """Start one queued conversation only at a clear, non-protected checkpoint."""
    event = _queued(state)
    if event is None:
        return False
    selected = _token(state)
    event.update(
        kind="plan_message",
        contract_token=selected,
        return_checkpoint={key: deepcopy(cast(dict, state)[key]) for key in CHECKPOINT_KEYS if key in state},
        return_stage=state.get("next_stage"),
    )
    for key in (*COMPLETION_KEYS, *QUESTION_KEYS):
        cast(dict, state).pop(key, None)
    run_status.running(
        state,
        "PLANNING",
        next_stage=_stage(event),
    )
    return True


def context(state: RunState):
    """Give Requirements the message and saved task context, without rewriting it."""
    event = active(state)
    if event is None or event["contract_token"] != _token(state):
        raise ValueError("No current live conversation")
    return {
        "stage": state["next_stage"],
        "task": state.get("task"),
        "workspace": state.get("workspace"),
        "goal_contract": deepcopy(state.get("goal_contract")),
        "requirements_handoff": deepcopy(state.get("requirements_handoff")),
        "current_task": deepcopy(state.get("current_task")),
        "planning_reports": {
            stage: {
                key: json.dumps(value, ensure_ascii=False)[:12000]
                for key, value in (entry.get("report") or {}).items()
                if key != "contract"
            }
            for stage, entry in (state.get("planning") or {}).get("reports", {}).items()
        },
        "message": deepcopy(event),
        "prior_exchanges": [
            {"text": row["text"], "assistant_reply": row["reply_after"]}
            for row in state.get("user_events", [])
            if row.get("kind") == "plan_message" and row.get("reply_after")
        ][-8:],
    }


def accept(state: RunState, value, record):
    """Save the answer and restore the checkpoint; never approve or apply a change."""
    event = active(state)
    if (
        event is None
        or state.get("status") != "RUNNING"
        or state.get("next_stage")
        != (
            "finalize_plan" if (event.get("context") or {}).get("kind") == "coordinated_plan" else "requirements_gather"
        )
        or value.get("message_id") != event["id"]
        or value.get("contract_token") != event["contract_token"]
        or event["contract_token"] != _token(state)
        or any(state.get(key) for key in QUESTION_KEYS)
    ):
        raise ValueError("Conversation response does not match the saved task and message")
    checkpoint = event["return_checkpoint"]
    if not checkpoint_allowed(checkpoint) or set(checkpoint) - set(CHECKPOINT_KEYS):
        raise ValueError("Conversation cannot replace an unrelated checkpoint")
    reply = value.get("assistant_reply")
    if (
        value.get("intent")
        not in (
            ("confirm", "question", "change", "decline")
            if (event.get("context") or {}).get("kind") == "coordinated_plan"
            else ("question", "change")
        )
        or not isinstance(reply, str)
        or not reply.strip()
        or len(reply) > 12000
    ):
        raise ValueError("Conversation response needs an intent and a bounded nonempty answer")
    routing = message_context.routing(event, value)
    event.update(intent=value["intent"], reply_after=reply, output=record.get("output"), **routing)
    for key in CHECKPOINT_KEYS:
        cast(dict, state).pop(key, None)
    state.update(deepcopy(checkpoint))
    return event


def correction_admitted(state: RunState, text):
    """Only this answered change can enter feedback from its restored work checkpoint."""
    event = next(
        (row for row in reversed(state.get("user_events", [])) if row.get("live") and row.get("reply_after")),
        None,
    )
    if not event or not (
        event.get("kind") == "plan_message"
        and not event.get("context")
        and event.get("intent") == "change"
        and event.get("reply_after")
        and not event.get("forwarded")
        and event.get("text") == text
        and checkpoint_allowed(state)
    ):
        return False
    checkpoint = event.get("return_checkpoint") or {}
    return all(state.get(key) == checkpoint.get(key) for key in CHECKPOINT_KEYS) and event.get(
        "contract_token"
    ) == _token(state)


def archive_completion(state: RunState):
    """Retain a completed result before an admitted live correction revises it."""
    if state.get("status") == "TASK_COMPLETE":
        state.setdefault("completion_archive", []).append(
            {
                "completed_at": state.pop("completed_at", None),
                "decision": state.pop("final_decision", None),
                "reason": "Message accepted before completion requested a reviewed change",
            }
        )
        state.pop("completion_actor", None)
