"""A boundary pause can restore its authenticated scope question without answering it."""

import copy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_scope_pause as scope_pause
import autocode_stop as stop
import autocode_util as util


class ScopePauseTests(unittest.TestCase):
    def setUp(self):
        question = {
            "id": "workspace-scope-current",
            "question": "Assign the discovered peer?",
            "why": "Peer ownership",
            "proposed_default": "",
            "delegable": False,
            "kind": "decision",
            "category": "requested_outcome",
            "options": [],
        }
        contract = {"task_id": "fixture", "revision": 1, "body": {"open_blocking_questions": [question]}}
        contract.update(hash=util.digest(contract), approval_status="draft", approval_event=None)
        self.state = {
            "task_id": "fixture",
            "task": "Fix history",
            "status": "RUNNING",
            "next_stage": "draft_plan",
            "settings": {},
            "answers": {},
            "user_events": [],
            "active_seconds": 17,
            "goal_contract": contract,
        }
        human.queue(
            self.state,
            "clarification",
            {"stage": "draft_plan"},
            questions=[question],
            phase="DISCOVERING",
            next_stage="draft_plan",
        )
        self.assertEqual("escalate", human.evaluate(self.state))
        self.question = copy.deepcopy(self.state["pending_questions"])
        receipt = {"id": "owned-pause", "kind": "pause", "applied_at": "before"}
        self.state["applied_interventions"] = [receipt]
        stop.boundary_effects(self.state, [receipt], lambda: "before")
        self.assertEqual("PAUSED_INTERVENTION", self.state["status"])

    def test_offer_is_read_only_and_resume_restores_the_exact_question_without_an_answer(self):
        before = copy.deepcopy(self.state)
        self.assertTrue(scope_pause.can_resume(self.state))
        self.assertEqual(before, self.state)
        self.assertTrue(scope_pause.resume(self.state, at="now", acknowledge=stop.acknowledge_pause))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(self.question, self.state["pending_questions"])
        self.assertIsNotNone(human.current(self.state))
        self.assertEqual({}, self.state["answers"])
        self.assertEqual([], self.state["user_events"])
        self.assertEqual(17, self.state["active_seconds"])
        self.assertEqual("draft", self.state["goal_contract"]["approval_status"])
        self.assertEqual("now", self.state["pause_intent"]["acknowledged_at"])
        self.assertEqual("now", self.state["applied_interventions"][0]["resumed_at"])
        self.assertFalse(scope_pause.can_resume(self.state))

    def test_runtime_only_receipt_refresh_restores_scope_without_changing_the_contract(self):
        binding = human._binding(self.state)
        changed = {**binding, "runtime_sources": "repaired-runtime"}
        before = copy.deepcopy(self.state)
        with patch.object(human, "_binding", return_value=changed):
            self.assertTrue(scope_pause.can_resume(self.state))
            self.assertEqual(before, self.state)
            self.assertTrue(scope_pause.resume(self.state, at="now", acknowledge=stop.acknowledge_pause))
            self.assertIsNotNone(human.current(self.state))
        self.assertEqual(before["goal_contract"], self.state["goal_contract"])
        self.assertEqual(self.question, self.state["pending_questions"])
        self.assertEqual({}, self.state["answers"])

    def test_changed_authority_or_independent_hold_is_never_restored(self):
        original = copy.deepcopy(self.state)
        changes = {
            "source": lambda s: s.update(source_revision="changed"),
            "contract": lambda s: s["goal_contract"]["body"].update(summary="changed"),
            "runner_check": lambda s: s.update(active_runner_check={"active": True}),
            "uncertain": lambda s: s.update(uncertain_artifacts={"output": "unconfirmed"}),
            "repair": lambda s: s.update(pending_report_repair={"attempt": "pending"}),
            "job_failure": lambda s: s.update(job_failure={"stage": "failed"}),
            "resumed": lambda s: s["applied_interventions"][0].update(resumed_at="already"),
            "acknowledged": lambda s: s["pause_intent"].update(acknowledged_at="already"),
            "unrelated": lambda s: s["applied_interventions"].append({"id": "other", "kind": "pause"}),
            "non_scope": lambda s: s[human.PUBLIC]["questions"][0].update(id="business-question"),
            "settings": lambda s: s["settings"].update(max_seconds=999),
            "accounting": lambda s: s.update(active_seconds=18),
            "feedback": lambda s: s["user_events"].append({"text": "change the task"}),
            "active": lambda s: s.update(active_stage={"stage": "draft_plan"}),
            "stop": lambda s: s.update(stop_intent={"request_ids": ["stop"]}),
            "held": lambda s: s["pause_intent"].update(held_pause={"status": "PAUSED_COST_LIMIT"}),
            "unmatched": lambda s: s["pause_intent"].update(request_ids=["other"]),
            "consumed": lambda s: s["resolver"]["human_escalations"][s[human.PUBLIC]["request_id"]].update(
                status="consumed"
            ),
            "forged": lambda s: s[human.PUBLIC].update(request_token="forged"),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self.state = copy.deepcopy(original)
                change(self.state)
                before = copy.deepcopy(self.state)
                self.assertFalse(scope_pause.can_resume(self.state))
                ack = Mock()
                self.assertFalse(scope_pause.resume(self.state, at="now", acknowledge=ack))
                ack.assert_not_called()
                self.assertEqual(before, self.state)

    def test_public_view_names_resume_before_scope_assignment_and_keeps_the_question(self):
        before = copy.deepcopy(self.state)
        need = run_view.needs(self.state)
        self.assertEqual("resume", need["kind"])
        self.assertEqual("--resume-paused", need["action"])
        self.assertFalse(need["confirmation_required"])
        self.assertEqual(before, self.state)

    def test_explicit_resume_stops_at_restored_scope_without_launching_or_clearing_it(self):
        import autocode as runner
        import autocode_action_authority as authority
        import autocode_args
        from autocode_run_actions import Decided

        parser = autocode_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)
        args = parser.parse_args(["--resume-paused"])
        fake = Mock()
        fake.now.return_value = "now"
        result = authority.settle(
            fake, args, parser, self.state, Path("state.json"), Path("run"), Path("workspace"), Decided()
        )
        self.assertEqual(2, result)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(self.question, self.state["pending_questions"])
        self.assertEqual({}, self.state["answers"])
        fake.write_json.assert_called_once()
