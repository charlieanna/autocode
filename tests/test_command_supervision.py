"""Public verification-command execution under normal and interrupted ownership."""

import builtins
import hashlib
import importlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import autocode_command_receipt as receipts
import autocode_command_supervision as commands
import autocode_verification_schedule as schedule
import autocode_verify as verify


class CommandSupervisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="command-supervision-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def command(self, source):
        # exec preserves the actual shell child's native PID when Python starts.
        return "exec " + shlex.join([sys.executable, "-u", "-c", source])

    def test_real_nonzero_exit_preserves_identity_environment_and_failure_evidence(self):
        command = self.command(
            "import json,os,sys; print(json.dumps({'pid':os.getpid(),"
            "'parent':os.getppid(),'session':os.getsid(0),'cwd':os.getcwd(),"
            "'secret':os.getenv('OPENAI_API_KEY'),'ci':os.getenv('CI')})); "
            "print('stderr reached',file=sys.stderr); sys.exit(7)"
        )
        result = verify.run_command(
            command, self.root, self.root / "normal.log", env={**os.environ, "OPENAI_API_KEY": "fixture-secret"}
        )
        actual = json.loads(Path(result["output"]).read_text().splitlines()[0])
        self.assertEqual(7, result["exit_code"])
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["supervision"]["provider"]["pid"], actual["pid"])
        self.assertEqual(os.getpid(), actual["parent"])
        self.assertEqual(actual["pid"], actual["session"])
        self.assertEqual(str(self.root.resolve()), actual["cwd"])
        self.assertIsNone(actual["secret"])
        self.assertEqual("1", actual["ci"])
        self.assertIn("stderr reached", result["tail"])
        self.assertTrue(receipts.completed(result, root=self.root))
        self.assertTrue(schedule.intact(result, root=self.root))
        self.assertEqual("failing", verify.suite_health(result))
        self.assertFalse(schedule.reusable(result, root=self.root))
        commands.reconcile(result["supervision"])
        admission = json.loads((Path(result["supervision"]["receipt"]).parent / "admission.json").read_text())
        self.assertEqual(command, admission["command"])
        self.assertEqual(str(Path(result["output"]).resolve()), admission["output"])
        self.assertEqual(result["supervision"], admission["supervision"])
        self.assertFalse((self.root / "normal.log").exists())

    def test_a_failing_stage_is_not_hidden_by_an_output_filter(self):
        for filtered in ("exit 3 | tail -1", "(echo out; exit 3) 2>&1 | cat"):
            with self.subTest(command=filtered):
                result = verify.run_command(filtered, self.root, self.root / "pipe.log")
                self.assertEqual(3, result["exit_code"])
        passing = verify.run_command("echo ok | tail -1", self.root, self.root / "ok.log")
        self.assertEqual(0, passing["exit_code"])

    def test_the_sh_fallback_sets_pipefail_only_where_supported(self):
        with patch.object(commands.os, "access", return_value=False):
            argv = commands.shell_argv("echo ran")
        self.assertEqual(["/bin/sh", "-c"], argv[:2])
        for shell in ("/bin/sh", "/bin/bash"):
            ran = subprocess.run([shell, "-c", argv[2]], capture_output=True, text=True)
            self.assertEqual((0, "ran\n"), (ran.returncode, ran.stdout), shell)

    def test_repeated_log_path_retains_each_real_commands_output_and_receipt(self):
        latest = self.root / "scratch-command.log"
        legacy = b"retained legacy command evidence\n"
        latest.write_bytes(legacy)
        admitted = []

        def checkpoint(command, output, metadata):
            admitted.append((command, Path(output), metadata))

        first = verify.run_command(
            self.command("import sys; print('first stdout'); print('first stderr',file=sys.stderr); sys.exit(7)"),
            self.root,
            latest,
            checkpoint=checkpoint,
        )
        self.assertEqual(legacy, latest.read_bytes())
        second = verify.run_command(self.command("pass"), self.root, latest, checkpoint=checkpoint)
        self.assertEqual(7, first["exit_code"])
        self.assertEqual(0, second["exit_code"])
        self.assertNotEqual(first["output"], second["output"])
        self.assertEqual(legacy, latest.read_bytes())
        latest.write_bytes(b"changed latest view\n")
        for result, expected in ((first, b"first stdout\nfirst stderr\n"), (second, b"")):
            output = Path(result["output"])
            self.assertEqual(self.root, output.parent)
            self.assertNotEqual(latest, output)
            self.assertEqual(expected, output.read_bytes())
            self.assertTrue(schedule.intact(result, root=self.root))
            directory = Path(result["supervision"]["receipt"]).parent
            admission = json.loads((directory / "admission.json").read_text())
            self.assertEqual(str(output.resolve()), admission["output"])
            self.assertEqual(result, json.loads((directory / "result.json").read_text()))
        self.assertEqual([first["command"], second["command"]], [row[0] for row in admitted])
        self.assertEqual([Path(first["output"]), Path(second["output"])], [row[1] for row in admitted])
        self.assertEqual([first["supervision"], second["supervision"]], [row[2] for row in admitted])

    def test_original_deadline_overrides_collected_zero_and_complete_passing_output(self):
        command = self.command(
            "print('test_claim (__main__.Claim.test_claim) ... ok\\n\\nRan 1 test in 0.001s\\n\\nOK',flush=True)"
        )
        # Drive the caller's original budget past its deadline only after the
        # actual child reports its zero exit. The independent keeper's clock
        # and launch/wait bounds stay real and unchanged.
        clock = iter((0, 0, 0, 901, 902))
        with patch.object(commands, "time", SimpleNamespace(monotonic=lambda: next(clock))):
            result = verify.run_suite(
                verify.Framework("unittest", command), command, self.root, self.root, "late-zero", timeout=900
            )
        final = receipts.load(result["supervision"])
        self.assertIn(final["cause"], ("provider_stopped", "controller_finished"))
        self.assertEqual("stopped", final["phase"])
        self.assertTrue(result["results"]["complete"])
        self.assertEqual(1, result["results"]["total"])
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        self.assertEqual("timeout", verify.suite_health(result))
        self.assertFalse(receipts.completed(result))
        self.assertFalse(schedule.intact(result, root=self.root))
        self.assertFalse(schedule.reusable(result, root=self.root))
        commands.reconcile(result["supervision"])

    def test_failed_before_exec_checkpoint_never_runs_command_and_allows_clean_retry(self):
        marker = self.root / "must-not-run"
        metadata = []
        command = self.command(f"from pathlib import Path; Path({str(marker)!r}).touch()")

        def checkpoint(_command, _output, value):
            metadata.append(value)
            raise OSError("admission persistence failed")

        with self.assertRaisesRegex(OSError, "admission persistence failed"):
            verify.run_command(command, self.root, self.root / "admission.log", checkpoint=checkpoint)
        self.assertFalse(marker.exists())
        commands.reconcile(metadata[0])
        result = verify.run_command(command, self.root, self.root / "retry.log")
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(marker.is_file())
        self.assertTrue(receipts.completed(result))
        self.assertNotEqual(metadata[0]["nonce"], result["supervision"]["nonce"])


@patch.dict(os.environ, {"AUTOCODE_RUN_PROCESS": "run_process_handler"})
class InProcessCommandSeamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="command-seam-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.provider = self.bin / "fixture-provider"
        self.provider.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "print(json.dumps({'cwd': os.getcwd(), 'argument': sys.argv[1], 'probe': os.environ['PROBE']}))\n"
            "sys.stderr.write('stderr marker \\u03bb\\n')\n"
            "raise SystemExit(7)\n"
        )
        self.provider.chmod(0o755)
        self.env = {"PATH": str(self.bin), "PROBE": "child"}

    def test_relative_path_lookup_cwd_and_unicode_capture_have_a_text_tail(self):
        env = {**self.env, "PATH": "bin"}
        with patch.object(commands.time, "monotonic", return_value=100):
            result = commands.run("fixture-provider argument", self.root, self.root / "seam.log", timeout=30, env=env)
        expected = json.dumps({"cwd": str(self.root.resolve()), "argument": "argument", "probe": "child"})
        expected += "\nstderr marker \u03bb\n"
        data = expected.encode("utf-8")
        self.assertEqual(7, result["exit_code"])
        self.assertFalse(result["timed_out"])
        self.assertTrue(result["in_process"])
        self.assertEqual(data, Path(result["output"]).read_bytes())
        self.assertEqual(hashlib.sha256(data).hexdigest(), result["output_sha256"])
        self.assertIsInstance(result["tail"], str)
        self.assertEqual(expected, result["tail"])
        self.assertNotIn("supervision", result)

    def test_package_relative_command_path_does_not_import_bare_autocode_util(self):
        # Source package mode exercises the same relative imports as an installed
        # autocode_cli package, without constructing or claiming an installed wheel.
        package_commands = importlib.import_module("tools.autocode_command_supervision")
        original_import = builtins.__import__

        def package_only(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "autocode_util" and level == 0:
                raise AssertionError("package path requested a bare autocode_util import")
            return original_import(name, globals, locals, fromlist, level)

        with patch.object(builtins, "__import__", side_effect=package_only), patch.object(
            package_commands.time, "monotonic", return_value=100
        ):
            result = package_commands.run(
                shlex.join([str(self.provider), "argument"]), self.root, self.root / "package.log", timeout=30, env=self.env
            )
        self.assertEqual(7, result["exit_code"])
        self.assertTrue(result["in_process"])
        self.assertIn("stderr marker \u03bb", result["tail"])

    def test_shell_syntax_and_non_python_commands_keep_supervision(self):
        native = self.bin / "native-fixture"
        native.write_text("#!/bin/sh\nexit 0\n")
        native.chmod(0o755)
        command = shlex.join([str(self.provider), "argument"])
        requests = [
            command + "; echo second",
            command + " | cat",
            command + " > redirected",
            command + " $(echo expansion)",
            shlex.join([str(self.provider), "literal;argument"]),
            shlex.join([str(self.provider), "literal*argument"]),
            str(self.provider) + " 'unterminated",
            str(native),
        ]
        for index, request in enumerate(requests):
            with self.subTest(command=request), patch.object(
                commands.util, "run_process", side_effect=AssertionError("unsafe seam bypass")
            ) as seam, patch.object(
                commands.supervision, "launch", side_effect=RuntimeError("supervisor selected")
            ) as launch, patch.object(commands.time, "monotonic", return_value=100):
                with self.assertRaisesRegex(RuntimeError, "supervisor selected"):
                    commands.run(request, self.root, self.root / f"shell-{index}.log", timeout=30, env=self.env)
                seam.assert_not_called()
                self.assertEqual(["/bin/sh", "-c", request], launch.call_args.args[0])
                self.assertEqual(self.root, launch.call_args.kwargs["cwd"])

    def test_native_deadline_activity_and_admission_requests_never_use_the_seam(self):
        callback = lambda *_: None
        command = shlex.join([str(self.provider), "argument"])
        requests = [
            ("checkpoint", {"checkpoint": callback}, None),
            ("context_checkpoint", {}, callback),
            ("activity", {"activity": object()}, None),
            ("activity_checkpoint", {"activity_checkpoint": callback}, None),
            ("deadline", {"deadline_monotonic": 120}, None),
        ]
        for name, extra, context_checkpoint in requests:
            token = commands.CHECKPOINT.set(context_checkpoint)
            try:
                with self.subTest(request=name), patch.object(
                    commands.util, "run_process", side_effect=AssertionError("native seam bypass")
                ) as seam, patch.object(
                    commands.supervision, "launch", side_effect=RuntimeError("supervisor selected")
                ) as launch, patch.object(commands.time, "monotonic", return_value=100):
                    with self.assertRaisesRegex(RuntimeError, "supervisor selected"):
                        commands.run(command, self.root, self.root / f"native-{name}.log", timeout=30, env=self.env, **extra)
                    seam.assert_not_called()
                    launch.assert_called_once()
            finally:
                commands.CHECKPOINT.reset(token)
        with patch.object(commands.util, "run_process", side_effect=AssertionError("held native seam bypass")) as seam, patch.object(
            commands.supervision, "launch", side_effect=AssertionError("held request lost its launcher")
        ) as ordinary, patch.object(commands.time, "monotonic", return_value=100):
            held = mock.Mock(side_effect=RuntimeError("held supervisor selected"))
            with self.assertRaisesRegex(RuntimeError, "held supervisor selected"):
                commands.run(command, self.root, self.root / "held.log", timeout=30, env=self.env, held_launch=held)
            seam.assert_not_called()
            ordinary.assert_not_called()
            held.assert_called_once()

class InProcessSeamTests(unittest.TestCase):
    def test_a_python_command_run_in_process_returns_its_text_output(self):
        # #931: the #704 seam built its output as text and then called .decode on it.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            provider = root / "fixture-provider"
            provider.write_text(
                "#!/usr/bin/env python3\nimport sys\nprint('hello from the fixture', sys.argv[1:])\n"
                "print('warned', file=sys.stderr)\nsys.exit(3)\n"
            )
            provider.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}{os.pathsep}{os.environ['PATH']}"}
            handler = Path(commands.__file__).with_name("run_process_handler.py")
            with patch.dict(os.environ, {"AUTOCODE_RUN_PROCESS": str(handler)}):
                result = commands.run(
                    "fixture-provider --flag", root, root / "logs" / "command.log", timeout=5, env=env
                )
            self.assertTrue(result["in_process"])
            self.assertEqual(3, result["exit_code"])
            self.assertEqual("hello from the fixture ['--flag']\nwarned\n", result["tail"])
            self.assertEqual(result["tail"], Path(result["output"]).read_text())


if __name__ == "__main__":
    unittest.main()
