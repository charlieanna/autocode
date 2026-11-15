"""Authentic historical requests, never permission for today's machinery publisher."""

import copy
from unittest.mock import patch

import autocode_operational_disposition as disposition
import autocode_resolver_human as human
import autocode_resolver_runtime as runtime


def legacy_publication(state, run_dir, *, pause=None, request=None, questions=None):
    if pause is not None:
        state["status"] = pause
    status, stage = state["status"], state["next_stage"]
    budget = disposition.budget(state, status)
    receipt = runtime._operational_receipt(
        state,
        run_dir,
        "hold",
        "Legacy operational recovery exhausted",
        {
            "status": status,
            "stage": stage,
            "budget": budget,
            "plan_review_calls": (state.get("planning") or {}).get("plan_review_calls"),
            "recovery_calls_used": (state.get("planning") or {}).get("recovery_review_calls_used", 0),
        },
    )
    human.queue(
        state,
        "operational_exhaustion",
        {"pause_status": status, "stage": stage, "budget": budget},
        request=request
        or {
            "kind": "blocker",
            "discovered": "Legacy recovery exhausted",
            "impact": "Attempts and work retained",
            "decision_needed": "Provide corrective information or leave paused",
            "options": ["Provide information", "Leave paused"],
            "proposed_delta": "",
        },
        questions=questions,
        evidence={"resolver_receipt_id": receipt},
        next_stage=stage,
    )
    identity = {
        "version": human.VERSION,
        "issuer": "resolver",
        "binding": human._binding(state),
        "proposal": copy.deepcopy(state[human.PRIVATE]),
        "action": "escalate",
        "reason": "Legacy operational hold",
    }
    state["resolver"].setdefault("human_escalations", {})
    return human._publish(state, identity)


def legacy_response(state, public, action="provide_information", text="Cause inspected"):
    """Seed only the old consumed history; the real CLI still uses today's policy."""
    assert human.retained(state) == public
    proposal = state["resolver"]["human_escalations"][public["request_id"]]["identity"]["proposal"]
    record = disposition.diagnostic(state, proposal["evidence"]["resolver_receipt_id"])
    assert record is not None and "disposition" not in record["receipt"]["evidence"]
    with patch.object(disposition, "refused", return_value=False):
        event = human.respond_operational(state, public["request_id"], public["request_token"], action, text)
        human.review_operational_response(state)
    return event
