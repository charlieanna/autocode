"""A project's runs reuse a tool check that already passed, and only that (providers/env_prep.reuse_checks)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_connections as connections
from providers import command, env_prep

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

    def test_a_new_chat_in_the_same_project_reuses_the_checks(self):
        runs = self.root / "project" / ".autocode" / "runs"
        for run in ("first-chat", "second-chat"):
            (runs / run).mkdir(parents=True)
        (self.root / "elsewhere" / ".autocode" / "runs" / "chat").mkdir(parents=True)
        with env_prep.reuse_checks(runs / "first-chat"):
            self.check()
        with env_prep.reuse_checks(runs / "second-chat"):  # a new run of the same project
            self.check()
        self.assertEqual(1, self.calls())
        self.assertTrue((self.root / "project" / ".autocode" / env_prep.REUSE_FOLDER).is_dir())
        with env_prep.reuse_checks(self.root / "elsewhere" / ".autocode" / "runs" / "chat"):  # another project
            self.check()
        self.assertEqual(2, self.calls())

    def test_another_terminal_window_asks_the_same_question(self):
        with env_prep.reuse_checks(self.run_dir):
            self.check(env={**self.env, "TERM_SESSION_ID": "w0t0p0", "SHLVL": "1", "OLDPWD": "/a"})
            self.check(env={**self.env, "TERM_SESSION_ID": "w1t0p0", "SHLVL": "2", "OLDPWD": "/b"})
        self.assertEqual(1, self.calls())

    def test_controller_call_markers_do_not_repeat_successful_preflights(self):
        with mock.patch.object(env_prep.subprocess, "run", wraps=env_prep.subprocess.run) as spawn:
            for argument in ("--version", "models", "auth"):
                with self.subTest(preflight=argument):
                    before = self.calls()
                    first_env = {**self.env, "CODEX_TOOL_CALL_ID": "call-one", "KILO_CONFIG": "same-profile"}
                    second_env = {**first_env, "CODEX_TOOL_CALL_ID": "call-two"}
                    with env_prep.reuse_checks(self.run_dir):
                        first = self.check(argument, first_env)
                    with env_prep.reuse_checks(self.run_dir):
                        second = self.check(argument, second_env)
                    self.assertEqual("call-two", second_env["CODEX_TOOL_CALL_ID"], "Keep the caller's mapping")
                    self.assertEqual(first.stdout, second.stdout)
                    self.assertEqual(before + 1, self.calls(), "A new controller call asks the same question")
                    self.assertEqual(first_env, spawn.call_args.kwargs["env"], "Preserve the actual tool environment")

    def test_call_marker_reuse_preserves_profile_changes_and_explicit_fresh_reads(self):
        first_env = {**self.env, "CODEX_TOOL_CALL_ID": "call-one", "CODEX_HOME": "first-profile"}
        changed_env = {**first_env, "CODEX_TOOL_CALL_ID": "call-two", "CODEX_HOME": "second-profile"}
        with env_prep.reuse_checks(self.run_dir):
            self.check(env=first_env)
            self.check(env=changed_env)
            self.assertEqual(2, self.calls(), "A real profile change must invalidate the answer")
            self.check(env=changed_env)
            self.assertEqual(2, self.calls())
            env_prep.preflight_run(
                ["tool", "--version"],
                changed_env,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=15,
                fresh=True,
            )
            self.assertEqual(3, self.calls(), "Explicit fresh checks still execute")
        with env_prep.reuse_checks(self.run_dir):
            self.check(env={**changed_env, "CODEX_TOOL_CALL_ID": "call-three"})
        self.assertEqual(3, self.calls(), "Only the invocation marker changed")

    def test_a_new_login_or_a_changed_setting_runs_the_check_again(self):
        home = self.root / "home"
        login = home / ".local" / "share" / "tool" / "auth.json"
        settings = home / ".config" / "tool" / "tool.json"
        for path in (login, settings):
            path.parent.mkdir(parents=True)
            path.write_text("{}")
        env = {**self.env, "HOME": str(home)}
        with env_prep.reuse_checks(self.run_dir):
            self.check(env=env)
            login.write_text('{"zai-coding-plan": {"type": "api"}}')  # `tool auth login`
            self.check(env=env)
            settings.write_text('{"model": "other"}')
            self.check(env=env)
            self.check(env=env)
        self.assertEqual(3, self.calls())

    def test_kept_answers_are_private_to_the_person(self):
        with env_prep.reuse_checks(self.run_dir):
            self.check()
        [kept] = (self.run_dir / env_prep.REUSE_FOLDER).iterdir()
        self.assertEqual(0o600, kept.stat().st_mode & 0o777)
        self.assertEqual(0o700, (self.run_dir / env_prep.REUSE_FOLDER).stat().st_mode & 0o777 & ~_umask_bits())

    @unittest.skipIf(os.name == "nt", "POSIX O_NONBLOCK fd 1 pipe truncation test")
    def test_nonblocking_large_stdout_is_not_truncated_by_pipe(self):
        # A single O_NONBLOCK write larger than the kernel pipe buffer (64 KB default, 1 MB Linux max)
        # deterministically short-writes on a pipe regardless of parent reader scheduling (#3078).
        single_write = (
            "import fcntl, os, sys\n"
            "flags = fcntl.fcntl(1, fcntl.F_GETFL)\n"
            "fcntl.fcntl(1, fcntl.F_SETFL, flags | os.O_NONBLOCK)\n"
            "data = b'x' * 2_000_000\n"
            "written = os.write(1, data)\n"
            "sys.stderr.write(f'wrote={written}\\n')\n"
        )
        piped = subprocess.run([sys.executable, "-c", single_write], capture_output=True, text=True, timeout=15)
        self.assertLess(len(piped.stdout), 2_000_000, "POSIX pipe short-writes single O_NONBLOCK 2 MB write")
        captured = env_prep.preflight_run(
            [sys.executable, "-c", single_write],
            env_prep.snapshot_environment(),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(0, captured.returncode)
        self.assertEqual(2_000_000, len(captured.stdout))
        self.assertEqual("x" * 2_000_000, captured.stdout)
        self.assertEqual("wrote=2000000\n", captured.stderr)

        loop_write = (
            "import fcntl, os, sys\n"
            "flags = fcntl.fcntl(1, fcntl.F_GETFL)\n"
            "fcntl.fcntl(1, fcntl.F_SETFL, flags | os.O_NONBLOCK)\n"
            "view = memoryview(b'y' * 500_000)\n"
            "written = 0\n"
            "while written < len(view):\n"
            "    written += os.write(1, view[written:])\n"
            "sys.stderr.write(f'wrote={written}\\n')\n"
        )
        loop_captured = env_prep.preflight_run(
            [sys.executable, "-c", loop_write],
            env_prep.snapshot_environment(),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(0, loop_captured.returncode)
        self.assertEqual(500_000, len(loop_captured.stdout))
        self.assertEqual("wrote=500000\n", loop_captured.stderr)

    def test_captured_timeout_decodes_partial_output_and_leaves_mocks_untouched(self):
        script = "import os, time\nos.write(1, b'part1\\r\\n\\xc3')\nos.write(2, b'err1\\r\\n')\ntime.sleep(30)\n"
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            env_prep.preflight_run(
                [sys.executable, "-S", "-c", script],
                env_prep.snapshot_environment(),
                capture_output=True,
                encoding="utf-8",
                timeout=2.0,
            )
        self.assertEqual("part1\n\ufffd", caught.exception.stdout)
        self.assertEqual("err1\n", caught.exception.stderr)

        binary = env_prep.preflight_run(
            [sys.executable, "-S", "-c", "import os; os.write(1, b'raw\\r\\n\\xff')"],
            env_prep.snapshot_environment(),
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(b"raw\r\n\xff", binary.stdout)
        self.assertEqual(b"", binary.stderr)

        with self.assertRaises(UnicodeDecodeError):
            env_prep.preflight_run(
                [sys.executable, "-S", "-c", "import os; os.write(1, b'\\xff')"],
                env_prep.snapshot_environment(),
                capture_output=True,
                encoding="utf-8",
                timeout=15,
            )

        with self.assertRaises(subprocess.CalledProcessError) as failed:
            env_prep.preflight_run(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    "import os; os.write(1, b'out\\r\\n'); os.write(2, b'err\\r\\n'); raise SystemExit(3)",
                ],
                env_prep.snapshot_environment(),
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
        self.assertEqual(3, failed.exception.returncode)
        self.assertEqual("out\n", failed.exception.output)
        self.assertEqual("err\n", failed.exception.stderr)

        with mock.patch.object(
            env_prep.subprocess, "run", return_value=subprocess.CompletedProcess(["tool"], 0, "ok", "")
        ) as mocked:
            result = env_prep.preflight_run(["tool"], self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual("ok", result.stdout)
        self.assertTrue(mocked.call_args.kwargs["capture_output"])
        self.assertNotIn("stdout", mocked.call_args.kwargs)
        self.assertNotIn("stderr", mocked.call_args.kwargs)

    def test_reuse_workspace_checks_reuses_only_in_initialized_workspace_without_creating_dot_autocode(self):
        uninit = self.root / "uninitialized"
        uninit.mkdir()
        with env_prep.reuse_workspace_checks(uninit):
            self.check()
            self.check()
        self.assertEqual(2, self.calls())
        self.assertFalse((uninit / ".autocode").exists())

        project = self.root / "initialized"
        (project / ".autocode").mkdir(parents=True)
        with env_prep.reuse_workspace_checks(project):
            self.check()
            self.check()
        self.assertEqual(3, self.calls())
        self.assertTrue((project / ".autocode" / env_prep.REUSE_FOLDER).is_dir())
        self.assertFalse((project / ".autocode" / "runs").exists())

        # An explicit run_dir still routes reuse there, and an outer reuse scope is preserved.
        explicit = self.root / "explicit-run"
        explicit.mkdir()
        with env_prep.reuse_workspace_checks(uninit, run_dir=explicit):
            self.check()
            self.check()
        self.assertEqual(4, self.calls())
        self.assertTrue((explicit / env_prep.REUSE_FOLDER).is_dir())
        with env_prep.reuse_checks(explicit):
            with env_prep.reuse_workspace_checks(project):
                self.check()
        self.assertEqual(4, self.calls())

    def test_connections_routes_reuses_kept_preflights_when_autocode_exists(self):
        project = self.root / "conn-project"
        (project / ".autocode").mkdir(parents=True)
        xdg = self.root / "xdg"
        xdg.mkdir()
        fake_bin = self.root / "kbin"
        fake_bin.mkdir()
        calls_file = fake_bin / "calls"
        kilo = fake_bin / "kilo"
        kilo.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> "{calls_file}"\n'
            'case "$*" in\n'
            '  "--version") echo "7.8.12" ;;\n'
            '  "auth list") printf "%s\\n" "●  Zhipu AI Coding Plan api" "●  OpenAI oauth" ;;\n'
            '  "models") printf "%s\\n" "zhipuai-coding-plan/glm-5.3" "openai/gpt-6-sol" ;;\n'
            '  "models --verbose") printf "%s\\n" "zhipuai-coding-plan/glm-5.3" \'{"family":"glm","capabilities":{"toolcall":true},"variants":{"high":{}}}\' "openai/gpt-6-sol" \'{"family":"gpt","capabilities":{"toolcall":true},"variants":{"high":{}}}\' ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n"
        )
        kilo.chmod(0o755)
        old_cwd = Path.cwd()
        path = f"{fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin"
        try:
            os.chdir(project)
            with mock.patch.dict(os.environ, {"PATH": path, "XDG_CONFIG_HOME": str(xdg)}):
                with mock.patch.object(command, "load", side_effect=command.load):
                    first = connections.routes("kilocode")
                    second = connections.routes("kilocode")
        finally:
            os.chdir(old_cwd)
        self.assertEqual("kilocode", first["tool"])
        self.assertEqual(first["routes"], second["routes"])
        self.assertTrue((project / ".autocode" / env_prep.REUSE_FOLDER).is_dir())
        self.assertEqual(
            ["--version", "auth list", "models", "models --verbose"],
            sorted(calls_file.read_text().splitlines()),
        )


def _umask_bits():
    current = os.umask(0)
    os.umask(current)
    return current


if __name__ == "__main__":
    unittest.main()
