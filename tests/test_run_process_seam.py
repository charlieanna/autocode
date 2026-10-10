"""The #704 process seam: one spawn in autocode_util, one in-process handler.

AUTOCODE_RUN_PROCESS names a handler module; `util.run_process` then executes
commands in-process (runpy) instead of spawning a grand-child interpreter, so a
battery of provider calls costs one import. Without the variable it delegates
to subprocess.run unchanged.
"""

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_util as util

TOOLS = Path(__file__).resolve().parents[1] / "tools"


@unittest.mock.patch.dict(os.environ, {"AUTOCODE_RUN_PROCESS": "run_process_handler"})
class InProcessHandlerTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(TOOLS))
        self.addCleanup(sys.path.remove, str(TOOLS))
        self.directory = tempfile.TemporaryDirectory(prefix="run-process-seam-")
        self.addCleanup(self.directory.cleanup)
        self.script = Path(self.directory.name) / "greet.py"
        self.script.write_text(
            "import os, sys\n"
            "print('hi ' + sys.argv[1])\n"
            "sys.stderr.write('note=' + os.environ.get('PROBE', 'missing') + '\\n')\n"
            "sys.stdout.write(sys.stdin.read())\n"
            "raise SystemExit(3)\n"
        )

    def test_the_command_runs_in_process_with_captured_streams_and_exit_code(self):
        result = util.run_process(
            [str(self.script), "there"],
            input=b"piped",
            env={**os.environ, "PROBE": "set"},
        )
        self.assertEqual(3, result.returncode)
        self.assertEqual("hi there\npiped", result.stdout)
        self.assertIn("note=set", result.stderr)

    def test_the_handler_path_never_spawns_a_subprocess(self):
        with mock.patch.object(util, "subprocess") as spawn_guard:
            result = util.run_process([str(self.script), "there"], env=dict(os.environ))
        spawn_guard.run.assert_not_called()
        self.assertEqual(3, result.returncode)
        self.assertEqual("hi there\n", result.stdout)

    def test_the_environment_is_restored_after_the_handler(self):
        before = dict(os.environ)
        with contextlib.suppress(SystemExit):
            util.run_process([str(self.script), "there"], env={**os.environ, "PROBE": "temporary"})
        self.assertEqual(before, os.environ)

    def test_stdin_and_env_reach_a_real_spawn_when_no_handler_is_set(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("AUTOCODE_RUN_PROCESS", None)
            env = {**os.environ, "PROBE": "probe="}
            result = util.run_process(
                [sys.executable, "-c", "import os,sys; sys.stdout.write(os.environ['PROBE'] + sys.stdin.read())"],
                input=b"payload",
                env=env,
            )
        self.assertEqual(0, result.returncode)  # output flows through uncaptured, as subprocess.run does


if __name__ == "__main__":
    unittest.main()
