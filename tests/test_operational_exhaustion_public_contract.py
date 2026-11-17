"""Public exhaustion contracts using only APIs that also exist on master."""

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_operational_disposition as disposition
import autocode_resolver_human as human
import autocode_resolver_runtime as runtime
import autocode_run_view as run_view
import autocode_support as support
import autocode_util as util


class OperationalExhaustionPublicContractTests(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = Path(scratch).resolve()
        self.run = self.workspace / ".autocode" / "runs" / "fixture"
        self.state = {
            "task": "Review the requested change",
            "task_id": None,
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "status": "PAUSED_PROVIDER_UNCERTAIN",
            "next_stage": "requirements_gather",
            "iteration": 1,
            "settings": {"limits": {"max_cost_usd": 0, "planning_calls": 1}},
            "user_events": [],
            "stages": [],
            "history": [],
            "failure_history": {},
            "active_stage": {
                "stage": "requirements_gather",
                "role": "requirements",
                "iteration": 1,
                "events": str(self.run / "requirements.jsonl"),
                "output": str(self.run / "requirements.json"),
                "finished_at": "2026-11-15T16:04:36+00:00",
                "started_at": "2026-11-15T16:04:35+00:00",
                "exit_code": 1,
                "accounted": True,
            },
        }
        self.binding_extra = {}
        self.enterContext(patch.object(human, "_binding", side_effect=self.binding))
        self.enterContext(
            patch.object(human, "pending_interruptions", return_value={"pending": [], "pause_requested": False})
        )
        self.enterContext(
            patch.object(util, "runner_identity", return_value={"path": str(self.workspace), "commit": "fixture"})
        )
        self.spawn = self.enterContext(
            patch.object(subprocess, "Popen", side_effect=AssertionError("no process authorized"))
        )
        self.runner = SimpleNamespace(recovery_count=lambda state: 0, MAX_AUTOMATIC_RECOVERIES=3)

    def tearDown(self):
        self.spawn.assert_not_called()

    def binding(self, state):
        return {
            "task_id": state.get("task_id"),
            "task": state.get("task"),
            "workspace": state.get("workspace"),
            "run_dir": state.get("run_dir"),
            "current_task": util.digest(state.get("current_task")),
            "contract": None,
            "settings": util.digest(state.get("settings")),
            "user_events": util.digest(state.get("user_events", [])),
            "next_stage": state.get("next_stage"),
            "failure_history": util.digest(state.get("failure_history", {})),
            "active_stage": util.digest(state.get("active_stage")),
            "pending_report_repair": util.digest(state.get("pending_report_repair")),
            "accounting": {},
            "interruptions": {"pending": [], "pause_requested": False},
            "runtime_sources": "authentic-fixture-runtime",
            **self.binding_extra,
        }

    def legacy(self):
        origin = {
            "stage": self.state["next_stage"],
            "pause_status": "PAUSED_PROVIDER_UNCERTAIN",
            "budget": {"category": "operational_recovery", "kind": None, "origin": "unknown", "limit": None},
        }
        receipt = runtime._operational_receipt(
            self.state,
            self.run,
            "hold",
            "Retained failed attempt",
            {"status": origin["pause_status"], "stage": origin["stage"], "budget": origin["budget"]},
        )
        human.queue(
            self.state,
            "operational_exhaustion",
            origin,
            request={
                "kind": "blocker",
                "discovered": "Provider exited without a report",
                "impact": "Saved failed attempt retained",
                "decision_needed": "Provide corrective information",
                "options": ["Provide corrective information", "Leave paused"],
            },
            evidence={"resolver_receipt_id": receipt},
            next_stage=origin["stage"],
        )
        identity = {
            "version": 1,
            "issuer": "resolver",
            "binding": self.binding(self.state),
            "proposal": copy.deepcopy(self.state["resolver_human_proposal"]),
            "action": "escalate",
            "reason": "Authentic retained legacy operational request",
        }
        self.state["resolver"].setdefault("human_escalations", {})
        return human._publish(self.state, identity)

    def assert_blocked_public(self):
        before = copy.deepcopy(self.state)
        view = run_view.view(self.state, run_dir=self.run, workspace=self.workspace)
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertFalse(view["done"])
        self.assertEqual(before, self.state)
        return view

    def test_failed_requirements_attempt_is_reported_without_a_machinery_question_or_action(self):
        attempt = copy.deepcopy(self.state["active_stage"])
        self.assertTrue(
            runtime.record_operational_exhaustion(
                self.runner,
                self.state,
                self.run,
                support.Paused("PAUSED_PROVIDER_UNCERTAIN", "fixture planner exited1"),
            )
        )
        if self.state.get("resolver_human_proposal"):
            human.evaluate(self.state)
        self.assertIsNone(human.current(self.state))
        self.assertEqual([], self.state.get("pending_questions", []))
        view = self.assert_blocked_public()
        # The person is told which job stopped and why, from the saved facts, never a machinery detail
        # to act on; the runner's own cause, which can name an operator flag, stays in the receipt.
        said = (
            "The Requirements step stopped: its model provider ended the call without a confirmed result. "
            + disposition.MESSAGE
        )
        self.assertEqual(said, self.state["stop_reason"])
        self.assertEqual(said, view["recovery"]["what_happened"])
        self.assertNotIn("--", said)
        # Never a dead end: a new conversation is offered, and it takes no command from this run.
        self.assertIn("start a new chat", view["recovery"]["what_it_means"])
        fresh = [row for row in view["recovery"]["actions"] if row["kind"] == "new_conversation"]
        self.assertEqual(1, len(fresh))
        self.assertNotIn("args", fresh[0])
        self.assertEqual(attempt, self.state["active_stage"])
        self.assertEqual(1, self.state["settings"]["limits"]["planning_calls"])
        self.assertEqual(0, self.state["settings"]["limits"]["max_cost_usd"])

    def test_authentic_legacy_machinery_reply_is_refused_without_saving_a_response(self):
        public = self.legacy()
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state,
                public["request_id"],
                public["request_token"],
                "provide_information",
                "The environment was repaired",
            )
        self.assertEqual(before, self.state)
        self.assert_blocked_public()

    def test_stale_legacy_publication_cannot_be_rebound_into_fresh_person_authority(self):
        public = self.legacy()
        self.binding_extra["runtime_sources"] = "different-current-runtime"
        before = copy.deepcopy(self.state)
        self.assertIsNone(human.rebind_stale(self.state, public["request_id"], public["request_token"]))
        self.assertEqual(before, self.state)
        self.assert_blocked_public()
