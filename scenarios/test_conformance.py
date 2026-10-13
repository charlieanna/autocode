"""The headless conformance client (#1012): its decisions, and the lifecycle scenario end to end.

The decision tests build status views by hand and launch nothing. The end-to-end test drives
conformance-lifecycle through AutoCode's real CLI with the scripted provider.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import catalog, verdict  # noqa: E402
from harness.conformance import ConformanceDriver, ConformanceGap, Moment  # noqa: E402


def view(kind="approve_plan", next_command=None, **need):
    return {"status": "WAITING", "needs": {"kind": kind, **need}, "next_command": next_command, "done": False}


class ClientDecisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.client = ConformanceDriver(
            root / "project",
            root,
            [],
            {},
            autocode=["autocode"],
            max_steps=5,
            timeout_seconds=5,
            explicit_answers={"route-sol": "gpt-6-luna"},
            conformance={},
            scripted=True,
        )
        self.client.run_dir = root / "project/.autocode/runs/r1"

    def named(self, text, kind="approve_plan"):
        return self.client.named_command(view(kind, text))

    def test_the_named_command_carries_its_token_and_this_run(self):
        words, env, source = self.named(
            f"AUTOCODE_APPROVE_GOAL_TOKEN=r2:abc autocode --run-dir {self.client.run_dir} "
            f"--workspace {self.client.project} --approve-goal -"
        )
        self.assertEqual((["--approve-goal", "-"], {"AUTOCODE_APPROVE_GOAL_TOKEN": "r2:abc"}), (words, env))
        self.assertEqual("next_command", source)

    def test_a_command_for_another_run_is_a_gap(self):
        with self.assertRaisesRegex(ConformanceGap, "not this run"):
            self.named("autocode --run-dir /elsewhere/run --approve-goal -")

    def test_only_continue_may_go_without_a_named_command(self):
        self.assertEqual((["--no-chat"], {}, "needs.kind"), self.named(None, "continue"))
        with self.assertRaisesRegex(ConformanceGap, "names no next_command"):
            self.named(None, "resume")

    def test_a_person_fills_only_the_decisions_the_view_leaves_open(self):
        need = {"kind": "answer", "questions": [{"id": "Q1", "proposed_default": "Use UTC"}]}
        self.assertEqual(["--answer", "Q1=Use UTC"], self.client.decide(need, ["--answer", "Q1=ANSWER"]))
        self.assertEqual(
            ["--answer", "route-sol=gpt-6-luna"], self.client.decide(need, ["--answer", "route-sol=MODEL"])
        )
        # Who made each decision is kept for the campaign's counts (#455): the scenario's own answer is the
        # person's, a proposed default is the harness's.
        self.assertEqual(
            [("Q1", "proposed_default", False), ("route-sol", "person", True)],
            [(row["id"], row["source"], row["explicit"]) for row in self.client.answers],
        )
        for word in ("GOAL_FILE", "WHAT CHANGED", "--explain"):
            with self.subTest(word=word), self.assertRaises(ConformanceGap):
                self.client.decide(need, ["--edit-goal", word])
        with self.assertRaisesRegex(ConformanceGap, "name a model"):
            self.client.decide(need, ["--answer", "route-terra=MODEL"])

    def test_an_operational_request_continues_only_through_a_named_retry(self):
        operational = view(
            "answer", "autocode --resolver-message 'WHAT CHANGED'", resolver_scope="operational_exhaustion"
        )
        self.assertIsNone(self.client.person_command(operational))
        operational["needs"]["retry_action"] = "--resume-paused --retry-failed-stage"
        self.assertEqual(
            (["--resume-paused", "--retry-failed-stage"], {}, "needs.retry_action"),
            self.client.person_command(operational),
        )

    def test_a_moment_waits_for_the_stage_the_view_shows_working(self):
        moment = Moment("interrupt", "terra", "Builder", after_failed=True)
        working = {"progress": {"stage": "Builder", "headline": "Builder working", "requirements": {"failed": 0}}}
        self.assertFalse(moment.ready(working))
        working["progress"]["requirements"]["failed"] = 1
        self.assertTrue(moment.ready(working))
        working["progress"]["headline"] = "Next: Builder"
        self.assertFalse(moment.ready(working))


class LifecycleScenarioTests(unittest.TestCase):
    def test_the_whole_lifecycle_completes_through_the_view_only(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                provider=None,
                hybrid=False,
                fake_solution="reference",
                fake_fault=None,
                out=Path(directory),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
                local_docker=False,
                i_authorize_live_model_spend=False,
            )
            result = run.run_one(catalog.load("conformance-lifecycle"), args)
            journal = [
                json.loads(line) for line in (Path(result["evidence"]) / "conformance.jsonl").read_text().splitlines()
            ]
        self.assertEqual(verdict.PASS, result["verdict"], result.get("summary"))
        self.assertEqual(
            ["interrupted", "feedback"], [row["did"] for row in journal if row["did"] in ("interrupted", "feedback")]
        )
        self.assertIn("needs.retry_action", [row["source"] for row in journal])


if __name__ == "__main__":
    unittest.main()
