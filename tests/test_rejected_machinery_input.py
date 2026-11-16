"""Rejected answers cannot migrate an old machinery checkpoint or schedule work."""

import unittest
from unittest.mock import patch

import autocode as runner
import autocode_operational_disposition as disposition
import autocode_resolver_human as human
import autocode_util as util

from . import test_human_publication


class RejectedMachineryInputTests(unittest.TestCase):
    setUp = test_human_publication.HumanResponseCLITests.setUp
    launch = test_human_publication.HumanResponseCLITests.launch
    saved = test_human_publication.HumanResponseCLITests.saved
    new_run_engine_args = ("--engine", "codex")
    stopped_operational_checkpoint = test_human_publication.HumanResponseCLITests.stopped_operational_checkpoint

    def test_rejected_decisions_preserve_the_original_checkpoint(self):
        # Only fixture construction writes historical bytes without today's
        # migration. All three real CLI invocations use the production writer.
        with patch.object(runner, "write_json", side_effect=util.atomic_json):
            run, stopped = self.stopped_operational_checkpoint()
        public = human.retained(stopped)
        question = public["questions"][0]["id"]
        before = (run / "state.json").read_bytes()
        probe = self.root / "rejected-input-launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        for answer, message in (
            (["--answer", question + "=Try again"], disposition.MESSAGE),
            (["--delegate", question], disposition.MESSAGE),
            (["--delegate-all", "--review-token", runner.goals.token(stopped["goal_contract"])], disposition.MESSAGE),
            (["--show-goal", "--expected-goal-token", "r0:stale"], "Input rejected:"),
            (["--resume-paused", "--retry-failed-stage", "--expected-goal-token", "r0:stale"], "Input rejected:"),
        ):
            with self.subTest(answer=answer[0]):
                (run / "state.json").write_bytes(before)
                result = self.launch(
                    ["--run-dir", str(run), "--resolver-token", public["request_token"], *answer, "--no-chat"], 2
                )
                self.assertIn(message, result.stderr)
                self.assertEqual(before, (run / "state.json").read_bytes())
                self.assertFalse(probe.exists(), "a rejected answer must not launch work")
