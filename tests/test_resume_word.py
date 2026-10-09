"""`autocode resume` continues a paused or blocked run without --resume-paused; plain `autocode` only shows it.

Offline: the goal tests' Git fixture, the real CLI entry in-process and a fake provider that stops at
the first stage admission. No model is called."""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_support as s

from tests import test_goals


class ResumeWordTests(unittest.TestCase):
    # The goal tests' Git fixture and CLI driver; binding the class here would run its tests again.
    setUp, approve, draft, decision, validation, invoke = (getattr(test_goals.GoalTests, name) for name in (
        "setUp", "approve", "draft", "decision", "validation", "invoke"))

    def provider(self):
        calls = []

        def run_role(**kwargs):
            calls.append(kwargs["state"]["next_stage"])
            raise s.Paused("PAUSED_TEST_LAUNCH", "Offline stage admission verified")
        return calls, run_role

    def main(self, *argv, role):
        """autocode as a user types it in the project, naming no run."""
        s.atomic_json(self.run / "state.json", self.state)
        with patch.object(sys, "argv", ["autocode", *argv]), patch.object(Path, "cwd", return_value=self.root), \
             patch.object(s, "assert_no_legacy_process"), patch.object(s, "local_settings", return_value=self.local), \
             patch.object(runner, "run_role", side_effect=role), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = runner.main()
            except SystemExit as exit_:
                code = exit_.code
        self.state = s.read(self.run / "state.json")
        return code

    def paused(self, status="PAUSED_INVALID_OUTPUT"):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        self.validation()
        self.state.update(status=status, phase="PAUSED_OR_BLOCKED", next_stage="astra_review",
                          stop_reason="Completion report could not be produced")

    def test_plain_autocode_holds_the_pause_and_resume_acknowledges_it(self):
        self.paused()
        calls, provider = self.provider()
        self.assertEqual(2, self.main("--no-chat", role=provider))
        self.assertEqual(([], "PAUSED_INVALID_OUTPUT"), (calls, self.state["status"]),
                         "a bare relaunch never acknowledges a pause")
        self.assertEqual(2, self.main("resume", "--no-chat", role=provider))
        self.assertEqual(["astra_review"], calls, "resume admitted the next stage")
        self.assertEqual("PAUSED_TEST_LAUNCH", self.state["status"])

    def test_resume_with_the_run_dir_acknowledges_it_too(self):
        self.paused()
        calls, provider = self.provider()
        self.assertEqual(2, self.invoke("resume", "--no-chat", role=provider))
        self.assertEqual(["astra_review"], calls)

    def held_then_resumed(self, status):
        """A plain relaunch launches nothing at ``status``; `autocode resume` admits the next stage."""
        self.paused(status)
        calls, provider = self.provider()
        self.assertEqual(2, self.main("--no-chat", role=provider))
        self.assertEqual(([], status), (calls, self.state["status"]), "a bare relaunch only shows the stop")
        self.assertEqual(2, self.main("resume", "--no-chat", role=provider))
        self.assertEqual(["astra_review"], calls, "resume continues it as --resume-paused does")

    # A plain relaunch only shows these and --resume-paused continues them, so the word does too (#412).
    def test_resume_continues_a_resolver_wait(self):
        self.held_then_resumed("RESOLVER_PENDING")

    def test_resume_continues_a_blocked_run(self):
        self.held_then_resumed("BLOCKED_HUMAN")

    def test_resume_continues_a_rework_limit(self):
        self.held_then_resumed("PLAN_REWORK_REQUIRED")

    def test_resume_only_shows_a_design_conflict(self):
        # Nothing guards a design conflict on relaunch; the user edits the design first (autocode_args).
        self.paused("PAUSED_DESIGN_CONFLICT")
        calls, provider = self.provider()
        for argv in (["--no-chat"], ["resume", "--no-chat"]):
            with self.subTest(argv=argv):
                self.assertEqual(2, self.main(*argv, role=provider))
                self.assertEqual(([], "PAUSED_DESIGN_CONFLICT"), (calls, self.state["status"]))


if __name__ == "__main__":
    unittest.main()
