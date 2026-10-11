"""Saved route advisories through real CLI processes with the offline provider."""

import json
import unittest

from . import test_subprocess


class RouteAdvisoryCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    new_run_engine_args = ("--engine", "codex")

    def test_saved_flag_change_warns_once_and_keeps_status_json_readable(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        initial = self.launch(["Build greeting", "--no-chat"], 2)
        self.assertNotIn("Model-route advisory", initial.stderr)
        run = next((self.project / ".autocode/runs").iterdir())
        waiting = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        attempts = waiting["usage"]["accounting"]["attempts"]
        args = ["--run-dir", str(run), "--no-chat"]
        result = self.launch([*args, "--astra-model", "gpt-5.6-terra"], 2)
        self.assertIn("Model-route advisory: selected Planner -> Builder", result.stderr)
        self.assertIn("gpt-5.6-terra / gpt-5.6-terra", result.stderr)
        changed = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual(attempts, changed["usage"]["accounting"]["attempts"])
        unchanged = self.launch([*args, "--astra-model", "gpt-5.6-terra", "--astra-reasoning-effort", "high"], 2)
        self.assertNotIn("Model-route advisory", unchanged.stderr)
        shown = self.launch(["--run-dir", str(run), "--status"], 0)
        view = json.loads(shown.stdout)["view"]
        self.assertEqual("gpt-5.6-terra", view["routes"]["astra"]["model"])
        self.assertNotIn("Model-route advisory", shown.stderr)

    def test_quota_model_answer_warns_after_save_and_rejected_answer_does_not(self):
        self.env.update(
            AUTOCODE_FIXTURE_MODE="no-human",
            AUTOCODE_FIXTURE_QUOTA_STAGE="astra_plan",
            AUTOCODE_FIXTURE_QUOTA_MODEL="gpt-5.6-sol",
        )
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run = next((self.project / ".autocode/runs").iterdir())
        shown = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual("route-astra", shown["needs"]["route"]["question_id"])
        self.assertEqual("quota", shown["needs"]["route"]["cause"])
        state_path = run / "state.json"
        before = state_path.read_bytes()
        args = ["--run-dir", str(run), "--answer", "route-astra=gpt-5.6-terra"]
        refused = self.launch([*args, "--resolver-token", "stale"], 2)
        self.assertNotIn("Model-route advisory", refused.stderr)
        self.assertEqual(before, state_path.read_bytes())
        result = self.launch(args, 0)
        self.assertIn("Model-route advisory: selected Planner -> Builder", result.stderr)
        self.assertIn("Saved; no agent launched", result.stdout)
        view = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        self.assertEqual("gpt-5.6-terra", view["routes"]["astra"]["model"])
        self.assertEqual("resume", view["needs"]["kind"])


if __name__ == "__main__":
    unittest.main()
