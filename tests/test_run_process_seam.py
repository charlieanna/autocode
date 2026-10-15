"""The #704 process seam: one spawn in autocode_util, one in-process handler.

AUTOCODE_RUN_PROCESS names a handler module; `util.run_process` then executes
commands in-process (runpy) instead of spawning a grand-child interpreter, so a
battery of provider calls costs one import. Without the variable it delegates
to subprocess.run unchanged.
"""

import contextlib
import json
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

    def test_optional_cwd_relative_script_input_and_globals_are_restored(self):
        root = Path(self.directory.name)
        (root / "input.txt").write_text("relative fixture")
        self.script.write_text(
            "import json, os, pathlib, sys\n"
            "print(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv, "
            "'probe': os.environ['PROBE'], 'relative': pathlib.Path('input.txt').read_text()}))\n"
            "sys.stdout.write(sys.stdin.read())\n"
            "sys.stderr.write('stderr marker\\n')\n"
            "sys.path.append('fixture-child-added-path')\n"
            "raise SystemExit(3)\n"
        )
        before = (sys.argv, sys.stdin, sys.stdout, sys.stderr, os.getcwd(), dict(os.environ), sys.path, list(sys.path))
        result = util.run_process(
            [self.script.name, "argument"], input=b"payload", env={"PROBE": "child"}, cwd=str(root)
        )
        first_line, payload = result.stdout.split("\n", 1)
        actual = json.loads(first_line)
        self.assertEqual(str(root.resolve()), actual["cwd"])
        self.assertEqual(self.script.name, Path(actual["argv"][0]).name)
        self.assertEqual(["argument"], actual["argv"][1:])
        self.assertEqual("child", actual["probe"])
        self.assertEqual("relative fixture", actual["relative"])
        self.assertEqual("payload", payload)
        self.assertEqual("stderr marker\n", result.stderr)
        self.assertEqual(3, result.returncode)
        for actual_stream, original in zip((sys.argv, sys.stdin, sys.stdout, sys.stderr), before[:4], strict=False):
            self.assertIs(original, actual_stream)
        self.assertEqual(before[4], os.getcwd())
        self.assertEqual(before[5], dict(os.environ))
        self.assertIs(before[6], sys.path)
        self.assertEqual(before[7], sys.path)

    def test_error_restores_cwd_environment_streams_argv_and_import_path(self):
        root = Path(self.directory.name)
        (root / "nested").mkdir()
        self.script.write_text(
            "import os, sys\n"
            "os.chdir('nested')\n"
            "os.environ['PROBE'] = 'mutated'\n"
            "sys.argv.append('mutated')\n"
            "sys.path.append('fixture-child-added-path')\n"
            "raise RuntimeError('fixture failed')\n"
        )
        before = (sys.argv, sys.stdin, sys.stdout, sys.stderr, os.getcwd(), dict(os.environ), sys.path, list(sys.path))
        with self.assertRaisesRegex(RuntimeError, "fixture failed"):
            util.run_process([self.script.name], env={"PROBE": "child"}, cwd=str(root))
        for actual_stream, original in zip((sys.argv, sys.stdin, sys.stdout, sys.stderr), before[:4], strict=False):
            self.assertIs(original, actual_stream)
        self.assertEqual(before[4], os.getcwd())
        self.assertEqual(before[5], dict(os.environ))
        self.assertIs(before[6], sys.path)
        self.assertEqual(before[7], sys.path)

    def test_omitting_cwd_preserves_the_legacy_handler_signature(self):
        handler = Path(self.directory.name) / "legacy_handler.py"
        handler.write_text(
            "from types import SimpleNamespace\n"
            "def run_process(cmd, *, input=None, env=None):\n"
            "    return SimpleNamespace(returncode=9, stdout=input.decode() + ':' + env['PROBE'], stderr='')\n"
        )
        with mock.patch.dict(os.environ, {"AUTOCODE_RUN_PROCESS": str(handler)}):
            result = util.run_process(["fixture"], input=b"payload", env={"PROBE": "legacy"})
        self.assertEqual(9, result.returncode)
        self.assertEqual("payload:legacy", result.stdout)

    def test_no_handler_preserves_spawn_arguments_and_forwards_only_explicit_cwd(self):
        argv = ["fixture", "argument"]
        env = {"PROBE": "child"}
        with mock.patch.dict(os.environ):
            os.environ.pop("AUTOCODE_RUN_PROCESS", None)
            with mock.patch.object(util.subprocess, "run", return_value=mock.sentinel.result) as spawn:
                self.assertIs(mock.sentinel.result, util.run_process(argv, input=b"payload", env=env))
                spawn.assert_called_once_with(argv, input=b"payload", env=env)
                spawn.reset_mock()
                root = self.directory.name
                self.assertIs(mock.sentinel.result, util.run_process(argv, input=b"payload", env=env, cwd=root))
                spawn.assert_called_once_with(argv, input=b"payload", env=env, cwd=root)


if __name__ == "__main__":
    unittest.main()
