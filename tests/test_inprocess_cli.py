"""The in-process CLI runner fixture (#704): same answers as a real spawn.

The equivalence tests are the contract: whatever ``_invoke`` inspects of a
result — returncode, stdout, and the "usage:" shape of an argparse rejection —
must be identical between the in-process runner and a real interpreter spawn,
or swapping them in would silently change what the driver sees.
"""

import pathlib
import subprocess
import sys
import tempfile
import unittest

import autocode_taskrun as taskrun
import inprocess_cli

TOOLS = pathlib.Path(taskrun.__file__).resolve().parent


class InProcessRunnerTests(unittest.TestCase):
    def test_entry_scripts_dispatch_by_filename(self):
        # The failure-routing driver invokes autocode_build and autoplanner, not
        # just autocode; the runner must resolve whichever script the argv names.
        for unit in ("autocode", "autocode_build", "autoplanner"):
            with self.subTest(unit=unit):
                result = inprocess_cli.run([sys.executable, str(TOOLS / f"{unit}.py"), "--help"])
                self.assertEqual(0, result.returncode)
                self.assertTrue(result.stdout.startswith("usage:"), f"{unit} must reach its own parser")

    def test_version_matches_a_real_spawn_exactly(self):
        argv = [sys.executable, str(TOOLS / "autocode.py"), "--version"]
        real = subprocess.run(argv, capture_output=True, text=True)
        inline = inprocess_cli.run(argv)
        self.assertEqual(real.returncode, inline.returncode)
        self.assertEqual(real.stdout, inline.stdout)
        self.assertEqual(real.stderr, inline.stderr)

    def test_usage_error_keeps_the_shape_the_driver_inspects(self):
        argv = [sys.executable, str(TOOLS / "autocode.py"), "--no-such-flag"]
        real = subprocess.run(argv, capture_output=True, text=True)
        inline = inprocess_cli.run(argv)
        self.assertEqual(real.returncode, inline.returncode)
        self.assertTrue(inline.stderr.startswith("usage:"), "the in-process rejection must look like argparse's")
        self.assertTrue(real.stderr.startswith("usage:"))

    def test_env_and_cwd_reach_the_cli(self):
        import os

        argv = [sys.executable, str(TOOLS / "autocode.py"), "--version"]
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "AUTOCODE_INPROCESS_PROBE": "set"}
            result = inprocess_cli.run(argv, env=env, cwd=tmp)
        self.assertEqual(0, result.returncode)
        self.assertIn("autocode", result.stdout)

    def test_the_runner_installs_on_the_seam(self):
        with inprocess_cli.patched():
            run = taskrun.TaskRun(
                workspace=pathlib.Path(tempfile.gettempdir()), run_dir=pathlib.Path(tempfile.gettempdir()) / "run"
            )
            proc = run._invoke("probe", "--version")
        self.assertEqual(0, proc.returncode)
        self.assertIn("autocode", proc.stdout)


if __name__ == "__main__":
    unittest.main()
