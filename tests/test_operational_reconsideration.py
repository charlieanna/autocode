"""Offline reconsideration of unanswered, resolver-owned planning-cap requests."""

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_configure
import autocode_planning as planning

from . import test_operational_recovery as fixtures
from . import test_subprocess

runner, s, resolver = fixtures.runner, fixtures.s, fixtures.resolver
human = runner.resolver_human


class ReconsiderationTests(unittest.TestCase):
    timeout = fixtures.OperationalRecoveryTests.timeout
    charge = fixtures.OperationalRecoveryTests.charge

    def setUp(self):
        fixtures.OperationalRecoveryTests.setUp(self)
        self.state["stages"][0].update(
            stage="astra_discovery_report_repair", original_stage="astra_discovery", report_only=True, exit_code=0
        )

    def publish(self, status="PAUSED_PLANNING_BUDGET"):
        self.assertTrue(
            resolver.record_operational_exhaustion(
                runner, self.state, self.run, s.Paused(status, "Saved planning cap reached")
            )
        )
        runner.write_json(self.run / "state.json", self.state)
        self.assertEqual("operational_exhaustion", human.current(self.state)["scope"])

    def reconsider(self):
        return resolver.reconsider_operational_request(runner, self.state, self.run, self.root)

    def assert_unchanged(self):
        before = copy.deepcopy(self.state)
        saved = (self.run / "state.json").read_bytes()
        artifacts = {p: p.read_bytes() for p in (self.run / "resolver").glob("*.json")}
        with patch.object(runner, "run_role") as launch:
            self.assertFalse(self.reconsider())
            launch.assert_not_called()
        self.assertEqual(before, self.state)
        self.assertEqual(saved, (self.run / "state.json").read_bytes())
        self.assertEqual(artifacts, {p: p.read_bytes() for p in (self.run / "resolver").glob("*.json")})

    def test_repaired_discovery_withdraws_once_and_charges_preserved_counts(self):
        self.publish()
        original = copy.deepcopy(self.state)
        issued = human.current(self.state)
        with patch.object(runner, "run_role") as launch:
            self.assertTrue(self.reconsider())
            launch.assert_not_called()
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("PLANNING", self.state["phase"])
        self.assertEqual("superseded", self.state["resolver"]["human_escalations"][issued["request_id"]]["status"])
        self.assertNotIn(human.PUBLIC, self.state)
        self.assertEqual([], self.state["pending_questions"])
        for key in ("settings", "goal_contract", "answers", "user_events", "active_seconds", "no_progress_batches"):
            self.assertEqual(original.get(key), self.state.get(key))
        self.assertEqual(2, self.state["planning"]["astra_calls"])
        self.assertEqual(1, len(self.state["planning"]["recovery_review_grants"]))
        self.assertEqual(self.state, s.read(self.run / "state.json"))
        self.assert_unchanged()
        self.charge()
        self.assertEqual(3, self.state["planning"]["astra_calls"])
        self.assertEqual(1, self.state["planning"]["recovery_review_calls_used"])
        self.assertTrue(self.state["planning"]["recovery_review_grants"][0]["consumed"])

    def test_raw_mixed_permission_wrong_origin_and_stale_requests_stay_blocked(self):
        self.publish()
        pristine = copy.deepcopy(self.state)
        cases = {
            "raw": lambda: self.state.pop(human.PUBLIC),
            "mixed": lambda: self.state["pending_questions"].append({"id": "material", "question": "Choose scope"}),
            "stale_settings": lambda: self.state["settings"]["roles"]["astra"].update(model="changed"),
            "unsealed_contract": lambda: self.state["goal_contract"]["body"].update(intended_outcome="unsealed change"),
            "active": lambda: self.state.update(active_stage={"stage": "astra_challenge"}),
            "repair": lambda: self.state.update(pending_report_repair={"attempts": 1}),
            "uncertain": lambda: self.state.update(uncertain_artifacts=["unknown"]),
            "pause_intent": lambda: self.state.update(pause_intent={"at": "now"}),
            "private_permission": lambda: self.state.update({human.PRIVATE: {"request": {"kind": "permission"}}}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.state = copy.deepcopy(pristine)
                mutate()
                self.assert_unchanged()
        self.state = copy.deepcopy(pristine)
        public = human.current(self.state)
        human.supersede_operational(self.state, "fixture replaced by real permission request")
        human.queue(
            self.state, "permission", {"stage": "astra_challenge"}, request={**public["request"], "kind": "permission"}
        )
        runner.write_json(self.run / "state.json", self.state)
        self.assertEqual("permission", human.current(self.state)["scope"])
        self.assert_unchanged()

    def test_wrong_origin_explicit_cap_and_sealed_material_questions(self):
        pristine = copy.deepcopy(self.state)
        for case in ("wrong_origin", "explicit", "material"):
            with self.subTest(case=case):
                self.state = copy.deepcopy(pristine)
                if case == "explicit":
                    self.state["planning"].update(review_call_limit=2, review_call_limit_origin="user_explicit")
                if case == "material":
                    contract = self.state["goal_contract"]
                    contract["body"]["open_blocking_questions"] = [{"id": "Q1", "question": "Choose scope"}]
                    contract["hash"] = s.digest({k: contract[k] for k in ("task_id", "revision", "body")})
                self.publish("PAUSED_RESOLVER_OPERATIONAL" if case == "wrong_origin" else "PAUSED_PLANNING_BUDGET")
                self.assert_unchanged()

    def test_answered_and_leave_paused_never_reconsider(self):
        self.publish()
        pristine = copy.deepcopy(self.state)
        for action, text in (("provide_information", "The lookup was repaired"), ("leave_paused", "")):
            with self.subTest(action=action):
                self.state = copy.deepcopy(pristine)
                public = human.current(self.state)
                human.respond_operational(self.state, public["request_id"], public["request_token"], action, text)
                human.review_operational_response(self.state)
                runner.write_json(self.run / "state.json", self.state)
                self.assert_unchanged()

    def test_stale_proof_pause_and_intervention_are_failure_atomic(self):
        self.publish()
        for origin in self.origins:
            Path(origin["before_ref"]).write_text("{}")
        self.assert_unchanged()
        with patch.object(runner.interventions, "pending", return_value=[{"id": "queued"}]):
            self.assert_unchanged()
        (self.run / "pause-requested").touch()
        self.assert_unchanged()

    def test_false_or_raising_proof_never_persists_candidate_withdrawal(self):
        self.publish()
        for result in (False, s.Paused("PAUSED_TIMEOUT_RECOVERY", "budget exhausted")):
            with self.subTest(result=result), patch.object(resolver, "operational_boundary") as proof:
                if isinstance(result, Exception):
                    proof.side_effect = result
                else:
                    proof.return_value = result
                self.assert_unchanged()
                self.assertFalse(proof.call_args.kwargs["persist"])


class ReconsiderationCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    timeout = fixtures.OperationalRecoveryTests.timeout
    new_run_engine_args = ("--engine", "codex")

    def prepare(self, explicit=False):
        self.launch(["Build greeting", "--no-chat"], 2)
        self.run, self.state = self.saved()
        self.root = self.project
        contract = self.state["goal_contract"]
        contract["body"]["open_blocking_questions"] = []
        contract["hash"] = s.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        self.state.pop(human.PUBLIC, None)
        self.state.pop("user_request", None)
        self.state.update(status="PAUSED_PLANNING_BUDGET", next_stage="astra_challenge", pending_questions=[])
        # The job recognizer runs first; take the Planner's discovery stage by name.
        discovery = next(row for row in self.state["stages"] if row["stage"] == "astra_discovery")
        discovery.update(stage="astra_discovery_report_repair", original_stage="astra_discovery", report_only=True)
        autocode_configure.configure_codex_joint(self.state["settings"], SimpleNamespace(), planning=planning)
        self.state["planning"] = {
            "astra_calls": 2,
            "reports": {
                "astra_discovery": {"output": discovery["output"], "report": {"summary": "accepted repaired discovery"}}
            },
        }
        if explicit:
            self.state["planning"].update(review_call_limit=2, review_call_limit_origin="user_explicit")
        self.timeout(1)
        self.timeout(2)
        resolver.record_operational_exhaustion(
            runner, self.state, self.run, s.Paused("PAUSED_PLANNING_BUDGET", "Saved cap reached")
        )
        runner.write_json(self.run / "state.json", self.state)

    def test_cli_continuation_reconsiders_but_status_is_read_only(self):
        self.prepare()
        before = (self.run / "state.json").read_bytes()
        self.launch(["--run-dir", str(self.run), "--status"], 0)
        self.assertEqual(before, (self.run / "state.json").read_bytes())
        # A different unit exits at the handoff before any provider is launched.
        self.launch(["--run-dir", str(self.run), "--no-chat", "--unit", "autocode"], 0)
        _, state = self.saved()
        self.assertEqual("RUNNING", state["status"])
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertEqual(1, len(state["planning"]["recovery_review_grants"]))
        self.assertNotIn(human.PUBLIC, state)

    def test_cli_explicit_cap_keeps_original_request_and_state_bytes(self):
        self.prepare(explicit=True)
        before = (self.run / "state.json").read_bytes()
        self.launch(["--run-dir", str(self.run), "--no-chat", "--resume-paused"], 2)
        self.assertEqual(before, (self.run / "state.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
