"""Public CLI regression for the live Completion Reviewer permission loop (#1735)."""

import json
import unittest

from tests import test_subprocess as cli_tests


class PermissionLoopCliTests(unittest.TestCase):
    setUp = cli_tests.SubprocessFlow.setUp
    launch = cli_tests.SubprocessFlow.launch
    new_run_engine_args = cli_tests.SubprocessFlow.new_run_engine_args

    def status(self, run):
        result = self.launch(["--run-dir", str(run), "--status"], 0)
        return json.loads(result.stdout)["view"]

    def test_paraphrased_permission_answers_stop_and_plain_resume_does_not_dispatch(self):
        self.env["AUTOCODE_FIXTURE_PERMISSION_LOOP"] = "1"
        probe = self.root / "provider-launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        self.launch(["Build a greeting tool", "--chat"], 2, answers="CLI\nyes\n")
        run = next((self.project / ".autocode/runs").iterdir())
        view = self.status(run)
        for _ in range(3):
            need = view["needs"]
            self.assertEqual("permission", need.get("resolver_scope"), {"status": view["status"], "needs": need})
            question = need["questions"][0]["id"]
            self.launch(
                [
                    "--run-dir",
                    str(run),
                    "--answer",
                    question + "=Execute the correction",
                    "--resolver-token",
                    need["resolver_token"],
                ],
                0,
            )
            self.launch(["--run-dir", str(run), "--no-chat"], 2)
            view = self.status(run)
        self.assertEqual("PAUSED_PERMISSION_LOOP", view["status"], view["needs"])
        self.assertEqual(3, view["needs"]["permission_decisions"])
        self.assertIn("--feedback", view["next_command"])
        before = view["usage"]["accounting"]["attempts"]
        calls = probe.read_bytes()
        for flags in (["--resume-paused"], ["--resume-paused", "--max-cost-usd", "10"]):
            self.launch(["--run-dir", str(run), *flags], 2)
            current = self.status(run)
            self.assertEqual("PAUSED_PERMISSION_LOOP", current["status"])
            self.assertEqual(before, current["usage"]["accounting"]["attempts"], "Usage must remain retained")
            self.assertEqual(calls, probe.read_bytes(), "No additional provider may launch")
