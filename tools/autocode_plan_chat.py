"""Interpret messages about a displayed plan without granting execution authority.

Pending and answered exchanges live in user_events, outside the requirements
source log. Only a classified change is forwarded by Requirements as feedback.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as identity
    from . import autocode_prompts as prompts
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as identity
    import autocode_prompts as prompts
    import autocode_run_status as run_status
    import autocode_util as util

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "message_id": {"type": "string"},
        "contract_token": {"type": "string"},
        "intent": {"type": "string", "enum": ["question", "change"]},
        "assistant_reply": {"type": "string"},
    },
    "required": ["message_id", "contract_token", "intent", "assistant_reply"],
}


def pending(state: RunState):
    """Return the unanswered exchange even when operational receipts follow it."""
    return next(
        (
            row
            for row in reversed(state.get("user_events") or [])
            if row.get("kind") == "plan_message" and "reply_after" not in row
        ),
        None,
    )


def _check_plan(state: RunState, selected, *, held=False):
    contract = state.get("goal_contract")
    report = (state.get("requirements_handoff") or {}).get("report")
    handoff = util.digest(report)[:12] if report else "none"
    if (
        not contract
        or not identity.sealed(contract)
        or not selected
        or selected != identity.token(contract)
        or (held and not identity.approved(state))
        or (not held and (selected != state.get("displayed_goal") or handoff != state.get("displayed_handoff")))
        or contract.get("approval_status") != ("approved" if held else "draft")
    ):
        raise ValueError("Plan conversation needs the current sealed plan and its displayed draft or saved approval")


def begin(state: RunState, text: str) -> bool:
    """Interpret a plan message at approval or its approved acceptance-test hold."""
    held = state.get("status") == "PAUSED_ACCEPTANCE_TESTS"
    if (
        not (state.get("settings") or {}).get("spec_first")
        or state.get("status") not in ("AWAITING_GOAL_APPROVAL", "PAUSED_ACCEPTANCE_TESTS")
        or not state.get("goal_contract")
        or (held and (state.get("goal_contract") or {}).get("approval_status") != "approved")
    ):
        return False
    if not text.strip():
        raise ValueError("Plan conversation needs nonempty text")
    selected = identity.token(state["goal_contract"]) if held else state.get("displayed_goal")
    _check_plan(state, selected, held=held)
    if any(state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair")) or pending(
        state
    ):
        raise ValueError("Finish the pending stage before discussing this plan")
    state.setdefault("user_events", []).append(
        {
            "kind": "plan_message",
            "actor": "user_cli",
            "id": "plan-message-" + uuid.uuid4().hex[:12],
            "at": util.now(),
            "text": text,
            "contract_token": selected,
            "return_stage": state.get("next_stage") or "astra_plan",
            **({"return_pause": {"status": state["status"], "reason": state.get("stop_reason", "")}} if held else {}),
        }
    )
    run_status.running(state, "PLANNING", next_stage="requirements_gather")
    return True


def context(state: RunState):
    """Bind a bounded discussion to the saved reviewed plan and user message."""
    message = pending(state)
    if not message:
        raise ValueError("No pending plan message")
    _check_plan(state, message["contract_token"], held=bool(message.get("return_pause")))
    prior = [
        {"text": row["text"], "assistant_reply": row["reply_after"]}
        for row in state.get("user_events") or []
        if row.get("kind") == "plan_message" and row.get("reply_after")
    ][-8:]
    reports = {
        stage: {
            key: json.dumps(value, ensure_ascii=False)[:12000]
            for key, value in (entry.get("report") or {}).items()
            if key != "contract"
        }
        for stage, entry in (state.get("planning") or {}).get("reports", {}).items()
    }
    packet = {
        "stage": "requirements_gather",
        "task": state.get("task"),
        "workspace": state.get("workspace"),
        "goal_contract": state["goal_contract"],
        "message": message,
        "prior_exchanges": prior,
        "requirements_handoff": state.get("requirements_handoff"),
        "reviewed_plan_explanation": reports,
    }
    prompt = prompts.get("plan-chat.md") + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet, ensure_ascii=False)
    return prompt, {
        "estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
        "soft_budget_tokens": (state.get("settings") or {}).get("context_soft_tokens", 10000),
    }


def accept(state: RunState, value: dict, record: dict):
    """Accept only the pending exchange's answer; preserve the plan and its sources."""
    util.validate_schema(value, SCHEMA)
    message = pending(state)
    if not message or value["message_id"] != message["id"] or value["contract_token"] != message["contract_token"]:
        raise ValueError("Plan response does not match the pending message")
    checkpoint = message.get("return_pause")
    if checkpoint and checkpoint.get("status") != "PAUSED_ACCEPTANCE_TESTS":
        raise ValueError("Plan response cannot replace an unrelated operational pause")
    _check_plan(state, value["contract_token"], held=bool(checkpoint))
    if not value["assistant_reply"].strip() or len(value["assistant_reply"]) > 12000:
        raise ValueError("Plan response needs a nonempty answer of at most 12000 characters")
    message.update(intent=value["intent"], reply_after=value["assistant_reply"], output=record.get("output"))
    if checkpoint:
        run_status.pause(state, checkpoint["status"], checkpoint["reason"], next_stage=message["return_stage"])
    else:
        run_status.awaiting_approval(state, next_stage=message["return_stage"])
    return message
