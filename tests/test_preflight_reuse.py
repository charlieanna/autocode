"""A run reuses a tool check that already passed, and only that (providers/env_prep.reuse_checks)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from providers import env_prep

TOOL = """#!/bin/sh
echo run >> "{bin}/calls"
[ -f "{bin}/fail" ] && {{ echo broken; exit 1; }}
echo "tool 1.0 $1"
"""


class PreflightReuseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.tool = self.bin / "tool"
        self.tool.write_text(TOOL.format(bin=self.bin))
        self.tool.chmod(0o755)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.env = {"PATH": str(self.bin)}

    def check(self, argument="--version", env=None):
        return env_prep.preflight_run(
            ["tool", argument], env or self.env, cwd=self.root, capture_output=True, text=True, timeout=15
        )

    def calls(self):
        path = self.bin / "calls"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def test_a_passed_check_is_answered_again_within_the_run(self):
        with env_prep.reuse_checks(self.run_dir):
            first, second = self.check(), self.check()
        self.assertEqual(1, self.calls())
        self.assertEqual((0, "tool 1.0 --version\n"), (second.returncode, second.stdout))
        self.assertEqual(first.stdout, second.stdout)
        with env_prep.reuse_checks(self.run_dir):  # the next chat turn: a new controller process, same run
            self.check()
        self.assertEqual(1, self.calls())

    def test_outside_a_run_every_check_runs(self):
        self.check()
        self.check()
        self.assertEqual(2, self.calls())

    def test_a_failed_check_is_never_kept(self):
        (self.bin / "fail").write_text("")
        with env_prep.reuse_checks(self.run_dir):
            self.assertEqual(1, self.check().returncode)
            (self.bin / "fail").unlink()
            self.assertEqual(0, self.check().returncode)
            self.check()
        self.assertEqual(2, self.calls())

    def test_anything_that_could_change_the_answer_runs_the_check_again(self):
        with env_prep.reuse_checks(self.run_dir):
            self.check()
            self.check("models")  # another command
            self.check(env={**self.env, "KILO_CONFIG": "other"})  # another environment
            self.tool.write_text(TOOL.format(bin=self.bin) + "# upgraded\n")  # the tool itself changed
            self.check()
            with mock.patch.object(env_prep, "REUSE_SECONDS", 0):  # an old answer expires
                self.check()
        self.assertEqual(5, self.calls())
        with env_prep.reuse_checks(self.root / "another-run"):
            self.check()
        self.assertEqual(6, self.calls())

    def test_kept_answers_are_private_to_the_person(self):
        with env_prep.reuse_checks(self.run_dir):
            self.check()
        [kept] = (self.run_dir / env_prep.REUSE_FOLDER).iterdir()
        self.assertEqual(0o600, kept.stat().st_mode & 0o777)
        self.assertEqual(0o700, (self.run_dir / env_prep.REUSE_FOLDER).stat().st_mode & 0o777 & ~_umask_bits())


def _umask_bits():
    current = os.umask(0)
    os.umask(current)
    return current


if __name__ == "__main__":
    unittest.main()
