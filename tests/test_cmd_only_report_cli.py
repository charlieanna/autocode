"""A Plan Reviewer whose final message is a shell command, through the public CLI (issue #512).

Through a Codex transport, a model ended its Plan Reviewer stage with ``{"cmd": "ls ... && wc -l ..."}``
instead of its report. The runner rejected it as "$: missing summary", repaired it from nothing in fresh
requests that did the same, and paused. SCENARIO_FAKE_CMD_ONLY scripts that model
(scenarios/harness/fake_codex.py; docs/bugs/2026-10-07-cmd-only-final-message.md). Each test drives
greenfield-greeting-cli end to end with the scripted model, so this module is in tests/suite_slow.json;
the pure rules are in test_cmd_only_report.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_cmd_only_report as cmd_only

RAN = "cmd-only-final-ran"  # what the scripted command would leave in the project, were it ever run


class CommandOnlyFinalCLI(unittest.TestCase):
    def setUp(self):
        from harness import catalog
        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="cmd-only-final-", dir=results)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.scenario = catalog.load("greenfield-greeting-cli")
        for name in ("config", "codex-home"):
            (self.root / name).mkdir()

    def drive(self, fault, *, tool=False):
        """Run the scenario with ``SCENARIO_FAKE_CMD_ONLY=fault``: as Codex, or with ``tool`` through a
        config-registered tool that writes a report file and keeps no session."""
        from harness import hybrid, profiles
        from harness.driver import Driver, default_autocode, fake_setup
        from harness.project import materialize
        self.project = materialize(self.scenario.seed, self.root / "project")
        flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        if tool:
            providers = self.root / "config" / "autocode" / "providers"
            providers.mkdir(parents=True)
            fake = [sys.executable, str(self.root / "bin" / "codex")]
            (providers / f"{hybrid.STANDIN}.toml").write_text(hybrid.toml(hybrid.standin_tool(fake)))
            flags = profiles.flags({"provider": hybrid.STANDIN, "models": hybrid.STANDIN_MODELS})
        env.update(XDG_CONFIG_HOME=str(self.root / "config"), CODEX_HOME=str(self.root / "codex-home"),
                   SCENARIO_FAKE_CMD_ONLY=fault)
        self.driver = Driver(self.project, self.root, flags, env, autocode=default_autocode(), max_steps=30,
                             timeout_seconds=300)
        return self.driver.drive(self.scenario.brief)

    def reviews(self):
        """The Plan Reviewer's attempts and their corrections and repairs, in order."""
        return [row for row in self.driver.state()["stages"]
                if row["stage"] in ("astra_challenge", "astra_challenge_report_repair")]

    def assert_rejected_as_command(self, row):
        self.assertTrue(row.get("rejected"), row["stage"])
        self.assertEqual(cmd_only.ERROR, row["rejection_reason"])
        self.assertFalse((self.project / RAN).exists(), "the command in a final message was run")

    def thread(self, row):
        events = [json.loads(line) for line in Path(row["events"]).read_text().splitlines() if line.strip()]
        return next(event["thread_id"] for event in events if event.get("type") == "thread.started")

    def test_one_command_final_is_corrected_in_the_reviewers_own_session(self):
        view = self.drive("astra_challenge:1")
        self.assertEqual("TASK_COMPLETE", view.get("status"), view.get("stop_reason"))
        first, correction = self.reviews()
        self.assert_rejected_as_command(first)
        self.assertEqual(["resume", self.thread(first)],
                         correction["command"][correction["command"].index("resume"):][:2])
        self.assertEqual(cmd_only.CORRECTION, Path(correction["prompt"]).read_text())
        self.assertFalse(correction.get("rejected"))
        # The correction spent none of the full repair attempts.
        self.assertEqual(0, self.driver.state()["report_repair_history"][-1]["attempts"])

    def test_a_reviewer_that_keeps_returning_a_command_stops_at_the_existing_bound(self):
        view = self.drive("astra_challenge")
        self.assertFalse(view["done"])
        self.assertEqual("PAUSED_REPEATED_FAILURE", view.get("status"), view.get("stop_reason"))
        self.assertIn(cmd_only.ERROR, view["stop_reason"])
        rows = self.reviews()
        # The original attempt, its one correction in the same session, then the two full repairs.
        self.assertEqual(["astra_challenge"] + ["astra_challenge_report_repair"] * 3, [row["stage"] for row in rows])
        self.assertEqual([False, True, False, False], ["resume" in row["command"] for row in rows])
        for row in rows:
            self.assert_rejected_as_command(row)
        self.assertIn(cmd_only.REPAIR_INSTRUCTION, Path(rows[-1]["prompt"]).read_text())
        state = self.driver.state()
        self.assertEqual(state["settings"]["report_repair"]["max_attempts"], state["pending_report_repair"]["attempts"])
        self.assertNotEqual("approved", state["goal_contract"]["approval_status"])

    def test_a_tool_without_sessions_gets_the_targeted_full_repair(self):
        view = self.drive("astra_challenge:1", tool=True)
        self.assertEqual("TASK_COMPLETE", view.get("status"), view.get("stop_reason"))
        first, repair = self.reviews()
        self.assert_rejected_as_command(first)
        self.assertNotIn("resume", repair["command"])
        self.assertIn(cmd_only.REPAIR_INSTRUCTION, Path(repair["prompt"]).read_text())
        self.assertEqual(1, self.driver.state()["report_repair_history"][-1]["attempts"])


if __name__ == "__main__":
    unittest.main()
