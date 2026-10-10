"""The #704 process seam: one CLI-process launch in autocode_taskrun.

TaskRun's driver routes every CLI call through taskrun.run_process, so a test
can replace interpreter-per-call spawning with an in-process runner. These
tests pin the seam; the supervision it triggers still runs for real.
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import autocode_taskrun as taskrun

TOOLS = pathlib.Path(taskrun.__file__).resolve().parent


class ProcessSeamTests(unittest.TestCase):
    def test_the_seam_runs_a_real_command(self):
        result = taskrun.run_process([sys.executable, "-c", "print('seam')"])
        self.assertEqual(0, result.returncode)
        self.assertEqual("seam\n", result.stdout)

    def test_the_cli_driver_launches_only_through_the_seam(self):
        source = (TOOLS / "autocode_taskrun.py").read_text(encoding="utf-8")
        invoke = source[source.index("    def _invoke(") :]
        body = invoke[: invoke.index("\n    def ") if "\n    def " in invoke else len(invoke)]
        self.assertIn("run_process(cmd", body)
        self.assertNotIn("subprocess.run(", body, "_invoke must not bypass the #704 process seam")
        self.assertNotIn("run_captured(", body, "_invoke must not bypass the #704 process seam")

    def test_an_in_process_runner_replaces_the_child(self):
        seen = []

        def scripted_runner(argv, *, env=None, cwd=None, timeout=None, advancing=False):
            seen.append((argv, advancing))
            return subprocess.CompletedProcess(argv, 0, "scripted out", "")

        with tempfile.TemporaryDirectory() as tmp:
            run = taskrun.TaskRun(workspace=pathlib.Path(tmp), run_dir=pathlib.Path(tmp) / "run")
            with mock.patch.object(taskrun, "run_process", scripted_runner):
                result = run._invoke("probe", "--status")
        self.assertEqual("scripted out", result.stdout)
        self.assertIs(run.last_advance, None)  # a non-advancing call never sets it
        self.assertEqual(1, len(seen))
        argv, advancing = seen[0]
        self.assertFalse(advancing)
        self.assertIn("--status", argv)
        self.assertIn("--workspace", argv)


if __name__ == "__main__":
    unittest.main()
