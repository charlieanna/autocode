"""A CLI the subprocess fixture kills at its deadline still shows what it printed (#1005)."""

import os
import subprocess
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

from tests import test_subprocess

PROGRESS = (
    "orchestrator: saved; next=terra; status=RUNNING\n"
    "Builder: started; model=xiaomi-token-plan-sgp/mimo-v2\n"
    "Tester: provider_active; model=openai/gpt-6-luna; elapsed=212.4s\n"
)


class TimedOutLaunchTests(unittest.TestCase):
    def fixture(self):
        fixture = test_subprocess.SubprocessFlow()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def timed_out(self, fixture, stdout, stderr):
        # On POSIX, subprocess.run kills the child and raises with the partial streams as bytes, even with text=True.
        error = subprocess.TimeoutExpired(["autocode"], 240, output=stdout, stderr=stderr)
        with mock.patch.object(test_subprocess.subprocess, "run", side_effect=error) as run:
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                fixture.launch(["--run-dir", str(fixture.root / "run"), "--resume-paused", "--no-chat"], 0)
        self.assertIs(error, raised.exception)
        self.assertEqual(240, run.call_args.kwargs["timeout"])
        return "".join(traceback.format_exception(raised.exception))

    def test_the_failure_output_shows_the_streams_printed_before_the_deadline(self):
        for stdout, stderr in (
            (PROGRESS.encode(), b"Tester started: Build M3 [openai/gpt-6-luna]\n\xff"),
            (PROGRESS, "Tester started: Build M3 [openai/gpt-6-luna]\n"),
        ):
            with self.subTest(kind=type(stdout).__name__):
                shown = self.timed_out(self.fixture(), stdout, stderr)
                self.assertIn("timed out after 240 seconds", shown)
                self.assertIn(f"--- stdout before the timeout ({len(PROGRESS)} characters) ---\n{PROGRESS}", shown)
                self.assertIn("--- stderr before the timeout", shown)
                self.assertIn("Tester started: Build M3 [openai/gpt-6-luna]", shown)

    def test_a_cli_killed_before_printing_says_its_streams_were_empty(self):
        shown = self.timed_out(self.fixture(), None, None)
        self.assertIn("--- stdout before the timeout (0 characters) ---", shown)
        self.assertIn("--- stderr before the timeout (0 characters) ---", shown)

    def test_only_the_end_of_a_long_stream_is_shown(self):
        tail = test_subprocess.TIMEOUT_STREAM_TAIL
        stdout = ("EARLY-LINE\n" + "x" * (2 * tail) + "\n" + PROGRESS).encode()
        shown = self.timed_out(self.fixture(), stdout, b"")
        self.assertIn(f"--- stdout before the timeout (last {tail} of {len(stdout)} characters) ---", shown)
        self.assertIn(PROGRESS, shown)
        self.assertNotIn("EARLY-LINE", shown)

    def test_a_retained_fixture_keeps_the_whole_streams(self):
        stdout = ("EARLY-LINE\n" + "x" * (2 * test_subprocess.TIMEOUT_STREAM_TAIL) + "\n" + PROGRESS).encode()
        stderr = b"Tester started\n\xff"
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"BUILD_AUDIT_ARTIFACTS": directory}):
                fixture = self.fixture()
                self.assertTrue(fixture.root.is_relative_to(Path(directory).resolve()))
                self.timed_out(fixture, stdout, stderr)
            self.assertEqual(stdout, (fixture.root / "command-timeout.stdout.log").read_bytes())
            self.assertEqual(stderr, (fixture.root / "command-timeout.stderr.log").read_bytes())

    def test_an_unretained_fixture_writes_no_stream_files(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("BUILD_AUDIT_ARTIFACTS", None)
            fixture = self.fixture()
            self.timed_out(fixture, PROGRESS.encode(), b"")
        self.assertEqual([], sorted(fixture.root.glob("command-timeout.*")))


if __name__ == "__main__":
    unittest.main()
