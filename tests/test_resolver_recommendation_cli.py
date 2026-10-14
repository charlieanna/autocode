"""Resolver recommendations through actual provider reports and public CLI actions."""

import json
import shutil
import unittest
from pathlib import Path

from autocode_taskrun import TaskRun, TaskRunError

from . import test_subprocess

ACCEPT = "Accept recommended revision"
CORRECTED = "The CLI greets a nonempty name and refuses empty names."


class ResolverRecommendationCLITests(unittest.TestCase):
    launch = test_subprocess.SubprocessFlow.launch
    new_run_engine_args = ("--engine", "codex")

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        self.trace = self.root / "recommendation-provider.jsonl"
        self.probe = self.root / "provider-launches.jsonl"
        self.env.update(
            AUTOCODE_FIXTURE_MODE="rework",
            AUTOCODE_FIXTURE_INCOMPLETE_REWORK="1",
            AUTOCODE_REGISTRY_LAUNCH_PROBE=str(self.probe),
            RECOMMENDATION_TRACE=str(self.trace),
        )
        provider = self.root / "fixture-bin" / "codex"
        provider.rename(provider.with_name("greeting-provider.py"))
        shutil.copy2(Path(__file__).with_name("resolver_recommendation_provider.py"), provider)
        provider.chmod(0o755)

    def start(self, *, joint=False, mode="valid", blocker=True):
        self.env["RECOMMENDATION_FIXTURE"] = mode
        self.launch(
            [
                "Build greeting",
                "--chat",
                *(
                    ["--joint-planning", "--no-adaptive-planning", "--plan-reviewer-model", "gpt-6-astra"]
                    if joint
                    else []
                ),
            ],
            2,
            answers="CLI\nyes\n",
        )
        self.run_dir = next((self.project / ".autocode/runs").iterdir())
        self.run = TaskRun(
            self.project, self.run_dir, command=tuple(self.entry), env=self.env, cwd=self.root, timeout=90
        )
        self.before = self.run.status()
        self.need = self.before["needs"]
        if blocker:
            self.assertEqual("blocker", self.need.get("resolver_scope"), self.before.get("stop_reason"))
        self.assertIn("astra_resolve", [row["stage"] for row in self.calls()])
        if mode == "valid":
            self.assertEqual(ACCEPT, self.need.get("recommended_revision_response"))
            self.assertEqual(
                {"acceptance_criteria": [{"id": "C1", "criterion": CORRECTED}]},
                self.need.get("recommended_revision"),
            )

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def receipt(self):
        return (self.run_dir / "state.json").read_bytes(), self.probe.read_bytes()

    def respond(self, text=ACCEPT, *, token=None, request=None):
        return self.run.respond_operational(
            request or self.need["resolver_request_id"], token or self.need["resolver_token"], text
        )

    def test_accepted_revision_runs_plan_review_and_still_requires_fresh_approval(self):
        self.start(joint=True)
        old = self.before["approved_contract"]
        calls = self.calls()
        launches = self.probe.read_bytes()
        after = self.respond()
        self.assertIsNone(after.get("approved_contract"))
        self.assertFalse(after["done"])
        self.assertEqual("PLANNING", after["phase"])
        self.assertNotEqual("approve_plan", after["needs"]["kind"])
        self.assertNotIn("displayed_plan", after)
        self.assertEqual(launches, self.probe.read_bytes())
        self.assertIn(CORRECTED, self.launch(["--run-dir", str(self.run_dir), "--show-goal"], 0).stdout)
        expected = dict(old["body"]["acceptance_criteria"][0], criterion=CORRECTED)

        accepted = self.receipt()
        self.respond()
        self.assertEqual(accepted, self.receipt(), "a replay must not create another draft")
        with self.assertRaises(TaskRunError):
            self.run.approve_plan(old["token"])
        self.assertEqual(accepted, self.receipt())

        reviewed = self.run.advance()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"], reviewed.get("stop_reason"))
        later = self.calls()[len(calls) :]
        self.assertIn("astra_challenge", [row["stage"] for row in later])
        self.assertNotIn("terra", [row["stage"] for row in later])
        token = reviewed["needs"]["token"]
        self.assertNotEqual(old["token"], token)
        displayed = reviewed["displayed_plan"]
        [criterion] = displayed["acceptance_criteria"]
        for field in ("id", "criterion", "verification_method", "human_review"):
            self.assertEqual(expected[field], criterion[field])
        launches = self.probe.read_bytes()
        approved = self.run.approve_plan(token)
        self.assertEqual(token, approved["approved_contract"]["token"])
        self.assertEqual(launches, self.probe.read_bytes(), "approval alone must not run the Builder")

    def test_non_joint_acceptance_publishes_an_unapproved_draft_without_launching(self):
        self.start()
        launches = self.probe.read_bytes()
        after = self.respond()
        self.assertIsNone(after.get("approved_contract"))
        self.assertEqual("approve_plan", after["needs"]["kind"], after.get("stop_reason"))
        self.assertNotEqual(self.before["approved_contract"]["token"], after["needs"]["token"])
        expected = dict(self.before["approved_contract"]["body"]["acceptance_criteria"][0], criterion=CORRECTED)
        self.assertEqual([expected], after["displayed_plan"]["acceptance_criteria"])
        self.assertEqual(launches, self.probe.read_bytes())

    def test_chat_acceptance_saves_the_same_unapproved_draft_without_launching(self):
        self.start()
        launches = self.probe.read_bytes()
        self.launch(["--run-dir", str(self.run_dir), "--chat"], 2, answers=ACCEPT + "\n")
        after = self.run.status()
        self.assertIsNone(after.get("approved_contract"))
        self.assertEqual("approve_plan", after["needs"]["kind"], after.get("stop_reason"))
        self.assertEqual(CORRECTED, after["displayed_plan"]["acceptance_criteria"][0]["criterion"])
        self.assertEqual(launches, self.probe.read_bytes())

    def test_ordinary_information_does_not_accept_the_recommendation(self):
        self.start()
        launches = self.probe.read_bytes()
        old = self.before["approved_contract"]
        after = self.respond("yes, accept recommended revision")
        self.assertEqual(old, after["approved_contract"])
        self.assertEqual("resume", after["needs"]["kind"])
        self.assertIn("--edit-goal", after["needs"]["reason"])
        self.assertEqual(launches, self.probe.read_bytes())
        self.run.resume_paused()
        self.assertEqual(launches, self.probe.read_bytes(), "information must not authorize another attempt")

    def test_missing_recommendation_refuses_without_consuming_the_request(self):
        self.start(mode="missing")
        before = self.receipt()
        with self.assertRaises(TaskRunError):
            self.respond()
        self.assertEqual(before, self.receipt())
        self.assertEqual(self.need, self.run.status()["needs"])
        self.respond("Keep the approved criterion")

    def test_unknown_criterion_report_never_publishes_a_recommendation(self):
        self.start(mode="unknown", blocker=False)
        self.assertNotIn("recommended_revision", self.need)
        self.assertIn("unknown criterion", self.before.get("stop_reason", ""))
        self.assertEqual(
            "Contract holds", self.before["approved_contract"]["body"]["acceptance_criteria"][0]["criterion"]
        )
        resolver = next(index for index, row in enumerate(self.calls()) if row["stage"] == "astra_resolve")
        self.assertNotIn("terra", [row["stage"] for row in self.calls()[resolver + 1 :]])

    def test_forged_tokens_changed_source_and_changed_diagnosis_cannot_accept(self):
        self.start()
        before = self.receipt()
        for kwargs in ({"token": self.need["resolver_token"] + "forged"}, {"request": "forged-request"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TaskRunError):
                self.respond(**kwargs)
            self.assertEqual(before, self.receipt())
        source = self.project / "greet.py"
        original = source.read_bytes()
        source.write_bytes(original + b"\n# Changed after the Resolver diagnosis.\n")
        with self.assertRaises(TaskRunError):
            self.respond()
        self.assertEqual(before, self.receipt())
        source.write_bytes(original)

        output = Path(next(row["output"] for row in reversed(self.calls()) if row["stage"] == "astra_resolve"))
        diagnosis = output.read_bytes()
        output.write_bytes(diagnosis + b"\n ")
        with self.assertRaises(TaskRunError):
            self.respond()
        self.assertEqual(before, self.receipt())
        output.write_bytes(diagnosis)
        self.assertEqual(self.need, self.run.status()["needs"])
        self.respond()
