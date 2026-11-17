"""Authentic historical requests, never permission for today's machinery publisher."""

import copy
from pathlib import Path
from unittest.mock import patch

import autocode_operational_disposition as disposition
import autocode_resolver_human as human
import autocode_resolver_runtime as runtime
import autocode_run_view as run_view
import autocode_util as util


def assert_operational_block(test, state, pause):
    """A known machinery failure stays retained without asking for recovery."""
    test.assertEqual(pause, state["status"])
    test.assertIsNone(human.current(state))
    test.assertEqual([], state.get("pending_questions", []))
    row = disposition.held(state)
    test.assertIsNotNone(row)
    test.assertEqual(pause, row["receipt"]["evidence"]["status"])
    shown = run_view.view(state)
    test.assertFalse(shown["done"])
    test.assertEqual("blocked", shown["recovery"]["category"])
    test.assertIsNone(shown["needs"])
    test.assertIsNone(shown["next_command"])
    test.assertIsNone(shown["chat"]["input"])
    return row["receipt"]["id"]


def assert_view_blocked(test, view, pause):
    """The public view of exhausted internal recovery (#3682): stopped, not done, nothing to answer or type."""
    test.assertEqual(pause, view["status"], view.get("stop_reason"))
    test.assertFalse(view["done"])
    test.assertEqual("blocked", view["recovery"]["category"], view["recovery"])
    test.assertIsNone(view["needs"])
    test.assertIsNone(view["next_command"])
    test.assertNotIn("--", view["stop_reason"] or "")


def legacy_publication(
    state, run_dir, *, pause=None, request=None, questions=None, budget=None, scope="operational_exhaustion"
):
    if pause is not None:
        state["status"] = pause
    status, stage = state["status"], state["next_stage"]
    budget = disposition.budget(state, status) if budget is None else copy.deepcopy(budget)
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
        scope,
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


def save_legacy_checkpoint(state, run_dir):
    """Write historical bytes without running today's human-boundary migration.

    Tests of saved old requests must reach the real CLI with that old checkpoint.
    The production writer normalizes it before the invocation being tested.
    Atomic persistence retains fixture evidence; no production policy is patched.
    """
    util.atomic_json(Path(run_dir) / "state.json", state)
