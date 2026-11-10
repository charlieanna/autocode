"""Bounded replies to a displayed understanding or plan, interpreted by its own agent.

Interpretations never grant execution authority: clients still take the fresh named
gate action. The original message and exact evidence binding remain in user_events.
"""

from __future__ import annotations

import copy
import json
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as identity
    from . import autocode_prompts as prompts
    from . import autocode_requirements_review as review
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as identity
    import autocode_prompts as prompts
    import autocode_requirements_review as review
    import autocode_run_status as run_status
    import autocode_util as util


def pending(state: RunState):
    """The single unanswered exchange, even when operational receipts follow it."""
    return next(
        (
            event
            for event in reversed(state.get("user_events") or [])
            if event.get("kind") == "gate_message" and "reply_after" not in event
        ),
        None,
    )


def _binding(state: RunState, kind):
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    if kind == "requirements":
        unresolved = [q for q in report.get("open_questions") or [] if q.get("id") not in (state.get("answers") or {})]
        if state.get("goal_contract") or not report or unresolved:
            return None
        return {"token": review.review_question(state)["id"], "stage": "requirements_gather"}
    contract = state.get("goal_contract") or {}
    handoff = util.digest(report)[:12] if report else "none"
    if (
        kind != "plan"
        or not contract
        or not identity.sealed(contract)
        or contract.get("approval_status") != "draft"
        or state.get("displayed_goal") != identity.token(contract)
        or state.get("displayed_handoff") != handoff
        or state.get("pending_questions")
    ):
        return None
    # The same selected Plan Reviewer handles replies to the plan it reviewed.
    return {"token": identity.token(contract), "stage": "finalize_plan"}


def offer(state: RunState):
    """Expose only a current gate and its latest still-applicable interpretation."""
    if not (state.get("settings") or {}).get("spec_first") or pending(state):
        return None
    kind = {"WAITING_FOR_USER": "requirements", "AWAITING_GOAL_APPROVAL": "plan"}.get(state.get("status", ""))
    binding = _binding(state, kind)
    if not binding:
        return None
    if kind == "requirements" and state.get("pending_questions") != [review.review_question(state)]:
        return None
    event: dict = next(
        (row for row in reversed(state.get("user_events") or []) if row.get("actor") not in ("core", "runner")), {}
    )
    decision = None
    if (
        event.get("kind") == "gate_message"
        and event.get("intent") == "confirm"
        and event.get("reply_after")
        and event.get("gate_token") == binding["token"]
        and event.get("handoff_sha256") == util.digest(state.get("requirements_handoff"))
    ):
        decision = {"message_id": event["id"], "intent": "confirm"}
    return {"kind": kind, **binding, "decision": decision}


def begin(state: RunState, text):
    """Queue a literal conversational reply without changing Requirements or the plan."""
    if pending(state):
        raise ValueError("Finish the pending gate conversation first")
    gate = offer(state)
    if not gate:
        return False
    if not text.strip() or any(
        state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair")
    ):
        raise ValueError("Gate conversation needs an idle, reconciled run and nonempty text")
    state.setdefault("user_events", []).append(
        {
            "kind": "gate_message",
            "actor": "user_cli",
            "id": "gate-message-" + uuid.uuid4().hex[:12],
            "at": util.now(),
            "text": text,
            "gate_kind": gate["kind"],
            "gate_token": gate["token"],
            "stage": gate["stage"],
            "handoff_sha256": util.digest(state.get("requirements_handoff")),
            "return_stage": state.get("next_stage"),
            "return_status": state["status"],
            "return_phase": state.get("phase"),
            "questions": copy.deepcopy(state.get("pending_questions") or []),
        }
    )
    state["pending_questions"] = []
    run_status.running(state, "PLANNING", next_stage=gate["stage"])
    return True


def _checked(state: RunState):
    event = pending(state)
    binding = _binding(state, event.get("gate_kind")) if event else None
    if (
        not event
        or not binding
        or binding["token"] != event["gate_token"]
        or binding["stage"] != event["stage"]
        or util.digest(state.get("requirements_handoff")) != event["handoff_sha256"]
    ):
        raise ValueError("Gate reply does not match the current displayed evidence")
    return event


def schema(state: RunState):
    """Constrain replies to the runner-owned current message and evidence identity."""
    event = _checked(state)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message_id": {"type": "string", "enum": [event["id"]]},
            "gate_token": {"type": "string", "enum": [event["gate_token"]]},
            "intent": {"type": "string", "enum": ["confirm", "question", "change", "decline"]},
            "assistant_reply": {"type": "string", "minLength": 1, "maxLength": 12000},
        },
        "required": ["message_id", "gate_token", "intent", "assistant_reply"],
    }


def context(state: RunState):
    """Supply saved evidence only; interpreting a reply needs no repository rediscovery."""
    event = _checked(state)
    packet = {
        "stage": event["stage"],
        "task": state.get("task"),
        "workspace": state.get("workspace"),
        "message": event,
        "requirements_handoff": state.get("requirements_handoff"),
        "goal_contract": state.get("goal_contract"),
        "question": review.review_question(state)["question"] if event["gate_kind"] == "requirements" else None,
        "prior_exchanges": [
            {"text": row["text"], "assistant_reply": row["reply_after"]}
            for row in state.get("user_events") or []
            if row.get("kind") == "gate_message"
            and row.get("gate_token") == event["gate_token"]
            and row.get("reply_after")
        ][-8:],
    }
    prompt = prompts.get("gate-chat.md") + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet, ensure_ascii=False)
    return prompt, {
        "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
        "soft_budget_tokens": (state.get("settings") or {}).get("context_soft_tokens", 10000),
    }


def accept(state: RunState, value, record):
    """Record an interpretation, returning to the exact gate without granting approval."""
    event = _checked(state)
    util.validate_schema(value, schema(state))
    if not value["assistant_reply"].strip():
        raise ValueError("Gate reply needs nonempty assistant text")
    event.update(intent=value["intent"], reply_after=value["assistant_reply"], output=record.get("output"))
    if event["gate_kind"] == "plan":
        run_status.awaiting_approval(state, next_stage=event["return_stage"])
    else:
        run_status.waiting_for_user(state, event["return_phase"], next_stage=event["return_stage"])
    state["pending_questions"] = copy.deepcopy(event["questions"])
    return event
