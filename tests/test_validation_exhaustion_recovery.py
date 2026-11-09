"""Retained exhaustion may end a run, never authorize answers or retries."""

import unittest
from copy import deepcopy
from unittest.mock import patch

import autocode_contract_identity as contracts
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_stop as stop
import autocode_util as util
import autocode_validation_exhaustion_recovery as recovery
import autocode_validation_rounds as rounds


def legacy_checkpoint(*, scope="operational_exhaustion", origin=None, request=None):
    contract = {"task_id": "task", "revision": 1, "body": {"open_blocking_questions": []}}
    contract["hash"] = util.digest(contract)
    approval = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(contract)}
    contract.update(approval_status="approved", approval_event=approval)
    state = {
        "status": "PAUSED_RESOLVER",
        "next_stage": "sol",
        "goal_contract": contract,
        "current_task": {"id": "task-3", "kind": "validate"},
        "validation": {"contract_hash": contract["hash"], "source_revision": "rev", "output": "v2"},
        "findings_ledger": [{"id": "F-1", "source": "sol", "status": "open", "blocking": True}],
        "user_events": [approval],
        "settings": {"roles": {"sol": {"model": "saved-model"}}, "limits": {"max_seconds": 20}},
        "automatic_recoveries_since_resume": 2,
        "stages": [],
    }
    frontier = {
        "contract_hash": contract["hash"],
        "source_revision": "rev",
        "findings": {"F-1": ["reported", None]},
        "passed": [],
        "accepted": [],
    }
    state[rounds.KEY] = [
        {"attempt": {"task_id": f"task-{i}", "after_validation": f"v{i}"}, "frontier": deepcopy(frontier), "at": "then"}
        for i in (0, 1)
    ]
    human.queue(
        state,
        scope,
        origin or {"stage": "sol", "pause_status": "PAUSED_RESOLVER"},
        request=request
        or {
            "kind": "blocker",
            "finding_ids": ["F-1"],
            "decision_needed": "Legacy machinery question",
            "impact": "Validation exhausted",
        },
    )
    with (
        patch.object(human, "_binding", return_value={"runtime_sources": "previous", "interruptions": {}}),
        patch.object(human, "_decision", return_value=("escalate", "Retained internal stop")),
    ):
        human.evaluate(state)
    return state


class ValidationRecovery(unittest.TestCase):
    def test_public_view_replaces_the_obsolete_question_with_automatic_continuation(self):
        state = legacy_checkpoint()
        state["stop_reason"] = "Old machinery question: close the finding yourself"
        before = deepcopy(state)
        with patch.object(run_view, "validation_recovery", recovery):
            view = run_view.view(state)
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertIn("settle the retained validation limit", view["stop_reason"])
        self.assertNotIn("close the finding yourself", view["stop_reason"])
        self.assertIn("automatically", view["explanation"]["what_it_means"])
        self.assertIn("--resume-paused", view["explanation"]["what_the_command_does"])
        self.assertNotIn("--answer", view["explanation"]["explanation"])
        self.assertIsNone(view["chat"]["gate"])
        self.assertIsNone(view["chat"]["input"])
        self.assertEqual(before, state)

    def test_authentic_other_gates_are_not_validation_exhaustion(self):
        cases = (
            {"scope": "permission"},
            {"scope": "blocker"},
            {"origin": {"stage": "terra", "pause_status": "PAUSED_RESOLVER"}},
            {"origin": {"stage": "sol", "pause_status": "PAUSED_TIME_LIMIT"}},
            *(
                {"request": {"kind": kind, "finding_ids": ids, "decision_needed": "A different gate"}}
                for kind, ids in (
                    ("permission", ["F-1"]),
                    ("blocker", []),
                    ("blocker", "F-1"),
                    ("blocker", [1]),
                    ("blocker", ["F-2"]),
                )
            ),
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                state = legacy_checkpoint(**overrides)
                before = deepcopy(state)
                self.assertIsNotNone(human.retained(state))
                self.assertIsNone(recovery.pending(state))
                self.assertFalse(recovery.settle(state, "rev", at="now"))
                self.assertEqual(before, state)

    def test_offer_is_pure_and_does_not_authorize_the_stale_answer(self):
        state = legacy_checkpoint()
        before = deepcopy(state)
        with patch.object(human, "_binding", side_effect=AssertionError("view reads no frontier")):
            issued = recovery.pending(state)
        self.assertEqual(state[human.PUBLIC], issued)
        self.assertEqual(before, state)
        with patch.object(human, "_binding", return_value={"runtime_sources": "fixed"}):
            self.assertIsNone(human.current(state))

    def test_settlement_preserves_history_authority_and_open_findings(self):
        state = legacy_checkpoint()
        before = deepcopy(state)
        key = state[human.PUBLIC]["request_id"]
        self.assertTrue(recovery.settle(state, "rev", at="now"))
        outcome = stop.outcome(state)
        self.assertEqual("could_not_finish", outcome["result"])
        self.assertEqual(key, outcome["evidence"]["superseded_request_id"])
        self.assertEqual(["F-1"], outcome["evidence"]["finding_ids"])
        self.assertEqual(2, outcome["evidence"]["rounds_without_progress"])
        for name in (
            "settings",
            "goal_contract",
            "user_events",
            "findings_ledger",
            rounds.KEY,
            "automatic_recoveries_since_resume",
            "stages",
        ):
            self.assertEqual(before[name], state[name], name)
        self.assertEqual("superseded", state["resolver"]["human_escalations"][key]["status"])
        self.assertIn("without requesting a human", state["resolver"]["human_escalations"][key]["superseded_reason"])
        self.assertEqual([], state["pending_questions"])
        self.assertNotIn("user_request", state)
        saved = deepcopy(state)
        self.assertFalse(recovery.settle(state, "rev", at="later"))
        self.assertEqual(saved, state)

    def test_an_unreadable_current_frontier_cannot_authorize_the_retained_request(self):
        state = legacy_checkpoint()
        self.assertIsNotNone(human.retained(state))
        with patch.object(human, "_binding", side_effect=ValueError("unreadable frontier")):
            self.assertIsNone(human.current(state))

    def test_failed_withdrawal_keeps_the_checkpoint(self):
        state = legacy_checkpoint()
        before = deepcopy(state)
        with patch.object(human, "supersede_operational", return_value=False):
            self.assertFalse(recovery.settle(state, "rev", at="now"))
        self.assertEqual(before, state)

    def test_changed_live_source_or_pending_input_cannot_end_the_old_frontier(self):
        for revision, interruptions in (("changed", {}), ("rev", {"pending": True}), ("rev", {"pause": True})):
            with self.subTest(revision=revision, interruptions=interruptions):
                state = legacy_checkpoint()
                before = deepcopy(state)
                with patch.object(human, "pending_interruptions", return_value=interruptions):
                    self.assertFalse(recovery.settle(state, revision, at="now"))
                self.assertEqual(before, state)

    def test_tampered_consumed_or_unrelated_requests_are_not_offered(self):
        changes = {
            "token": lambda s: s[human.PUBLIC].update(request_token="forged"),
            "receipt": lambda s: s["resolver"]["human_escalations"][s[human.PUBLIC]["request_id"]].update(
                receipt_hash="forged"
            ),
            "consumed": lambda s: s["resolver"]["human_escalations"][s[human.PUBLIC]["request_id"]].update(
                status="consumed"
            ),
            "question": lambda s: s["pending_questions"][0].update(question="another question"),
            "request": lambda s: s["user_request"].update(finding_ids=["F-2"]),
            "extra question": lambda s: s["pending_questions"].append({"id": "real-requirement"}),
            "unapproved": lambda s: s["goal_contract"].update(approval_status="draft"),
            "already admitted": lambda s: (s["current_task"].update(id="task-1"), s["validation"].update(output="v1")),
            "closed finding": lambda s: s["findings_ledger"][0].update(status="resolved"),
            "new progress": lambda s: s["validation"].update(criterion_results=[{"id": "C1", "status": "PASS"}]),
            "new contract": lambda s: s["validation"].update(contract_hash="other"),
            "too few rounds": lambda s: s[rounds.KEY].pop(),
            **{
                key: lambda s, key=key: s.update({key: {"pending": True}})
                for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
            },
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                state = legacy_checkpoint()
                change(state)
                before = deepcopy(state)
                self.assertIsNone(recovery.pending(state))
                self.assertFalse(recovery.settle(state, "rev", at="now"))
                self.assertEqual(before, state)
