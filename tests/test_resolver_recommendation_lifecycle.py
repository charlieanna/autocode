"""Authenticated Resolver wording corrections use the ordinary draft/approval boundary."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_planning_artifacts as artifacts
import autocode_resolver_human as human
import autocode_resolver_recommendation as recommendation
import autocode_source_log as source_log
import autocode_support as support
from goal_fixtures import body


class ResolverRecommendationLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        (self.root / "greet.py").write_text("print('Hello, Ada')\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "greet.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.state = {
            "task_id": "task-fixture",
            "task": "Build a greeting tool",
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "next_stage": "astra_plan",
            "settings": {},
            "answers": {},
            "user_events": [],
            "stages": [],
            "history": [],
            "iteration": 1,
            "active_seconds": 17,
            "failure_history": {"retained": {"count": 3}},
        }
        source_log.seed(self.state)
        contract = body(human=True)
        contract["initial_task"] = {
            "objective": "Implement greeting",
            "affected_paths": ["greet.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Deliver CLI"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["Execute greeting and invalid-input regression checks"],
        }
        lifecycle.install_draft(self.state, contract, origin="test")
        human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        self.state["current_task"] = {"id": "T1"}
        self.patch = {
            "acceptance_criteria": [{"id": "C1", "criterion": "Greeting behavior and invalid-input errors are clear"}]
        }

    def publish(self, *, text=recommendation.ACCEPTANCE, action="provide_information", raw_changes=None, source=None):
        self.state.update(status="RUNNING", next_stage="astra_resolve")
        self.output = self.run / "resolver.json"
        self.report = {
            "status": "BLOCKED",
            "contract_revision": self.state["goal_contract"]["revision"],
            "contract_hash": self.state["goal_contract"]["hash"],
            "task_id": "T1",
            "diagnosis": "The criterion wording needs a precise correction",
            "user_request": {
                "kind": "contradiction",
                "decision_needed": "Accept the recommended criterion correction?",
                "impact": "A revised draft needs separate plan approval",
                "options": [recommendation.ACCEPTANCE, "Leave paused"],
                "discovered": "The criterion is ambiguous",
                "proposed_delta": "Clarify C1",
                "recommended_revision": self.patch,
            },
        }
        self.report.update(raw_changes or {})
        self.output.write_text(json.dumps(self.report))
        source = source or support.snapshot(self.root)["revision"]
        record = {
            "stage": "astra_resolve",
            "output": str(self.output),
            "source_revision": source,
            "exit_code": 0,
            "changed_files": [],
        }
        self.state["stages"].append(record)
        lifecycle.wait_for_user(
            self.state,
            copy.deepcopy(self.report["user_request"]),
            origin=record,
            evidence={"diagnosis": self.report["diagnosis"], "output": str(self.output)},
            next_stage="astra_review",
        )
        self.assertEqual("escalate", human.evaluate(self.state))
        self.published = human.current(self.state)
        human.respond_operational(
            self.state,
            self.published["request_id"],
            self.published["request_token"],
            action,
            text,
        )

    def test_acceptance_installs_one_unapproved_draft_and_preserves_provenance(self):
        self.publish()
        previous = copy.deepcopy(self.state["goal_contract"])
        self.assertTrue(lifecycle.apply_resolver_recommendation(self.state, run_dir=self.run))
        contract = self.state["goal_contract"]
        self.assertEqual(previous["revision"] + 1, contract["revision"])
        self.assertEqual("resolver_recommendation", contract["origin"])
        self.assertFalse(goals.approved(self.state))
        self.assertEqual(recommendation.compose(previous["body"], self.patch), contract["body"])
        self.assertEqual(previous, self.state["contract_history"][-1])
        self.assertFalse(human.response_holds_current_frontier(self.state))
        self.assertNotIn("pending_human_response", self.state["resolver"])
        resolution = self.state["resolver"]["human_response_resolutions"][self.published["request_id"]]
        self.assertEqual("hold", resolution["action"])
        feedback = self.state["brief_feedback"][-1]
        self.assertIn(feedback, self.state["user_events"])
        self.assertIn(self.patch["acceptance_criteria"][0]["criterion"], feedback["text"])
        self.assertEqual(self.published["request_id"], feedback["resolver_request_id"])
        self.assertEqual(feedback["text"], source_log.sources(self.state)[-1]["text"])
        self.assertEqual(
            [{"item": "C1", "change": "reworded", "basis": "user_feedback", "answer_id": feedback["id"]}],
            contract["declared_changes"],
        )
        self.assertEqual(17, self.state["active_seconds"])
        self.assertEqual({"retained": {"count": 3}}, self.state["failure_history"])
        self.assertEqual("escalate", human.evaluate(self.state))
        self.assertEqual("goal_approval", human.current(self.state)["scope"])
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        saved = copy.deepcopy(self.state)
        self.assertFalse(lifecycle.apply_resolver_recommendation(self.state, run_dir=self.run))
        self.assertEqual(saved, self.state)

    def test_other_information_and_leave_paused_are_not_revisions(self):
        initial = copy.deepcopy(self.state)
        for action, text in (
            ("provide_information", "Please explain the recommendation"),
            ("leave_paused", recommendation.ACCEPTANCE),
        ):
            with self.subTest(action=action):
                self.state = copy.deepcopy(initial)
                self.publish(action=action, text=text)
                before = copy.deepcopy(self.state)
                self.assertFalse(lifecycle.apply_resolver_recommendation(self.state, run_dir=self.run))
                self.assertEqual(before, self.state)

    def test_stale_source_report_contract_or_response_is_refused(self):
        self.publish()
        original = copy.deepcopy(self.state)
        source_text, output_text = (self.root / "greet.py").read_text(), self.output.read_text()
        for change in ("source", "report", "contract", "actor", "receipt", "stage"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(original)
                (self.root / "greet.py").write_text(source_text + ("# stale\n" if change == "source" else ""))
                self.output.write_text(output_text + ("\nchanged" if change == "report" else ""))
                if change == "contract":
                    self.state["goal_contract"]["body"]["constraints"].append("A new constraint")
                elif change == "actor":
                    self.state["resolver"]["pending_human_response"]["actor"] = "model"
                elif change == "receipt":
                    self.state["resolver"]["human_escalations"][self.published["request_id"]]["receipt_hash"] = "forged"
                elif change == "stage":
                    self.state["stages"][-1]["changed_files"] = ["greet.py"]
                before = copy.deepcopy(self.state)
                with self.assertRaises(ValueError):
                    lifecycle.apply_resolver_recommendation(self.state, run_dir=self.run)
                self.assertEqual(before, self.state)

    def test_retained_report_must_belong_to_the_approved_contract(self):
        self.publish(raw_changes={"contract_revision": 99})
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "retained Resolver report"):
            lifecycle.apply_resolver_recommendation(self.state, run_dir=self.run)
        self.assertEqual(before, self.state)

    def test_original_source_brief_and_risk_seals_are_not_bypassed(self):
        initial = copy.deepcopy(self.state)
        for field, message in (
            ("source_trace", "Invalid source-trace"),
            ("brief_acceptance", "Invalid protected original-brief"),
            ("risk_acceptance", "Invalid protected original-risk"),
        ):
            with self.subTest(field=field):
                self.state = copy.deepcopy(initial)
                # A retained malformed seal is never repaired or dropped by a
                # criterion-only acceptance. Ordinary draft validation rejects it.
                contract = self.state["goal_contract"]
                contract["body"][field] = {}
                contract["hash"] = support.digest({key: contract[key] for key in ("task_id", "revision", "body")})
                contract["approval_event"]["token"] = goals.token(contract)
                self.publish()
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, message):
                    lifecycle.apply_resolver_recommendation(copy.deepcopy(self.state), run_dir=self.run)
                self.assertEqual(before, self.state)

    def test_joint_planning_reviews_before_any_approval(self):
        self.state["settings"]["joint_planning"] = True
        self.state["planning"] = {
            "astra_calls": 2,
            "reports": {},
            "final_token": goals.token(self.state["goal_contract"]),
        }
        self.publish()
        self.assertTrue(lifecycle.apply_resolver_recommendation(self.state, run_dir=self.run))
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual("PLANNING", self.state["phase"])
        self.assertFalse(goals.approved(self.state))
        self.assertFalse(human.current(self.state))
        self.assertFalse(human.response_holds_current_frontier(self.state))

    def prepare_v2(self):
        self.state["settings"].update(joint_planning=True, planning_flow="v2")
        artifacts.prepare(
            self.state, "requirements", {"requirements": {}, "summary": "Original requirements"}, run_dir=None
        )
        artifacts.flush_pending(self.state, self.run)
        artifacts.prepare(
            self.state,
            "plan",
            {"contract": self.state["goal_contract"]["body"], "summary": "Original plan"},
            run_dir=self.run,
        )
        artifacts.flush_pending(self.state, self.run)
        return copy.deepcopy(self.state["planning_artifacts"]["plan"])

    def test_v2_uses_recorded_plan_predecessor_and_defers_writes_until_commit(self):
        prior = self.prepare_v2()
        self.publish()
        before_files = {str(path): path.read_bytes() for path in self.run.glob("planning/*.json")}
        self.assertTrue(lifecycle.apply_resolver_recommendation(self.state, run_dir=self.run))
        self.assertEqual("plan_review", self.state["next_stage"])
        self.assertEqual(before_files, {str(path): path.read_bytes() for path in self.run.glob("planning/*.json")})
        artifacts.commit_pending(
            self.state, self.run, lambda value: support.atomic_json(self.run / "state.json", value)
        )
        entry = self.state["planning_artifacts"]["plan"]
        delta = json.loads((self.run / entry["delta"]["path"]).read_text())
        self.assertEqual(prior["artifact"]["sha256"], delta["input_sha256"])
        self.assertEqual(
            entry["artifact"]["path"],
            artifacts.verify_predecessor(self.state, "plan_review", self.run)["artifact"]["path"],
        )
        report = json.loads((self.run / entry["artifact"]["path"]).read_text())
        self.assertEqual("resolver_recommendation", report["origin"])

    def test_missing_v2_predecessor_leaves_the_callers_state_and_files_unchanged(self):
        prior = self.prepare_v2()
        self.publish()
        (self.run / prior["artifact"]["path"]).unlink()
        before = copy.deepcopy(self.state)
        before_files = {str(path): path.read_bytes() for path in self.run.glob("planning/*.json")}
        with self.assertRaisesRegex(ValueError, "prior plan artifact hash"):
            lifecycle.apply_resolver_recommendation(copy.deepcopy(self.state), run_dir=self.run)
        self.assertEqual(before, self.state)
        self.assertEqual(before_files, {str(path): path.read_bytes() for path in self.run.glob("planning/*.json")})
