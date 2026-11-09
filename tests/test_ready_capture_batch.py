"""Ready check batches preserve literal argv and the individual capture fallback."""

import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_ready_capture_batch as ready
import autocode_stage_context as context
import autocode_support as support


class ReadyCaptureBatchTests(unittest.TestCase):
    def packet(self, commands, stage="sol"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = {
                "version": 3,
                "task": "Verify the approved greeting",
                "workspace": str(root),
                "settings": {"roles": {stage: {"engine": "codex", "model": "fixture"}}},
                "current_task": {"id": "task", "validation_plan": commands},
                "goal_contract": {"hash": "approved", "body": {}},
            }
            with patch.object(context.support, "snapshot", return_value={"revision": "source", "head": "head"}):
                prompt, _ = context.context_packet(state, stage, root / ".autocode/runs/run/state.json")
            return json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

    def test_tester_receives_one_executable_batch_for_all_exact_approved_commands(self):
        commands = ["python3 -m unittest tests.test_report -v", "python3 -m unittest discover -v"]
        packet = self.packet(commands)
        batch = packet["ready_capture_batch"]
        self.assertEqual(commands, packet["verification_obligations"]["required_commands"])
        outer = shlex.split(batch["command"])
        payload = json.loads(base64.urlsafe_b64decode(outer[-1]))
        self.assertEqual([shlex.split(command) for command in commands], payload["commands"])
        self.assertEqual({"mode", "commands", "directory"}, set(payload))
        self.assertEqual(shlex.split(packet["capture_command"])[-1], payload["mode"])
        self.assertNotIn(sys.executable, base64.urlsafe_b64decode(outer[-1]).decode())
        self.assertNotIn("autocode.py", base64.urlsafe_b64decode(outer[-1]).decode())
        self.assertIn("sequential", batch["instruction"])

    def test_new_modes_and_valid_legacy_launchers_preserve_capture_semantics(self):
        canonical = [sys.executable, str(Path(support.__file__).with_name("autocode.py")), "capture"]
        commands = [[sys.executable, "-V"], [sys.executable, "-c", "print('second')"]]
        for mode in ("raw", "conservative"):
            legacy = {"capture": canonical + ["--mode", mode], "commands": commands, "directory": "/evidence"}
            batch = ready.context(shlex.join(legacy["capture"]), [shlex.join(row) for row in commands], "/evidence")
            encoded = shlex.split(batch["command"])[-1]
            expected = {"mode": mode, "commands": commands, "directory": "/evidence"}
            self.assertEqual(expected, ready._plan(encoded))
            self.assertEqual(expected, ready._plan(base64.urlsafe_b64encode(json.dumps(legacy).encode()).decode()))
            with patch.object(ready.subprocess, "run") as run, patch("builtins.print"):
                run.return_value.returncode = 0
                self.assertEqual(0, ready.run(encoded))
            self.assertEqual(2, run.call_count)
            for call, command in zip(run.call_args_list, commands, strict=True):
                self.assertEqual(canonical + ["--mode", mode], call.args[0][:5])
                self.assertEqual(command, call.args[0][call.args[0].index("--") + 1 :])
        bare = {"capture": canonical, "commands": commands, "directory": "/evidence"}
        encoded = base64.urlsafe_b64encode(json.dumps(bare).encode()).decode()
        for environment, expected in (({}, "conservative"), ({"AUTOCODE_OUTPUT_MODE": "raw"}, "raw")):
            with patch.dict(os.environ, environment, clear=True), self.subTest(environment=environment):
                self.assertEqual(expected, ready._plan(encoded)["mode"])
                with patch.object(ready.subprocess, "run") as run, patch("builtins.print"):
                    run.return_value.returncode = 0
                    self.assertEqual(0, ready.run(encoded))
                self.assertEqual(canonical + ["--mode", expected], run.call_args_list[0].args[0][:5])
        with patch.dict(os.environ, {"AUTOCODE_OUTPUT_MODE": "invalid"}), patch.object(ready.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "Invalid prepared capture batch plan"):
                ready.run(encoded)
            run.assert_not_called()

    def test_custom_and_altered_legacy_launchers_refuse_before_any_spawn(self):
        canonical = [sys.executable, str(Path(support.__file__).with_name("autocode.py")), "capture"]
        for launcher in (
            None,
            [],
            [1],
            [""],
            [canonical[0] + "-copy-error", *canonical[1:]],
            [canonical[0], "/model-supplied/capture.py", "capture"],
            ["env", *canonical],
            [*canonical, "--mode", "invalid"],
            [*canonical, "--mode", "raw", "--no-compress"],
        ):
            payload = {"capture": launcher, "commands": [["python3", "-V"]] * 2, "directory": "/evidence"}
            encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
            with self.subTest(launcher=launcher), patch.object(ready.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "Invalid prepared capture batch plan"):
                    ready.run(encoded)
                run.assert_not_called()
            if isinstance(launcher, list) and all(isinstance(word, str) for word in launcher):
                self.assertIsNone(ready.context(shlex.join(launcher), ["python3 -V"] * 2, "/evidence"))
        for capture in (None, 1, "'unfinished"):
            with self.subTest(capture=capture):
                self.assertIsNone(ready.context(capture, ["python3 -V"] * 2, "/evidence"))

    @staticmethod
    def observed_quote_alteration(command):
        # ISO-week trial: an extra single quote was inserted before these
        # shell-escaped Python literals. Apply only that observed alteration to
        # this test's fresh offer, never execute the archived trial's command.
        for literal in (
            "directory",
            "AUTOCODE_TOOL_CONTAINMENT_ID",
            "TMPDIR",
            "AUTOCODE_VERIFICATION_COPY",
            "AUTOCODE_VERIFICATION_COPY_SHA256",
        ):
            escaped = "'\"'\"'" + literal + "'\"'\"'"
            command = command.replace(escaped, "'" + escaped)
        return command

    def test_plain_quoted_arguments_stay_literal_and_shell_keywords_fall_back(self):
        command = "python3 -c 'print(\"hello; world\")'"
        self.assertEqual(shlex.split(command), ready.argv(command))
        for unsupported in ("time python3 -m unittest", "command python3 -m unittest", "wait"):
            with self.subTest(command=unsupported):
                self.assertIsNone(ready.argv(unsupported))

    def test_unsupported_shell_or_single_checks_keep_the_individual_capture_command(self):
        for unsupported in (
            "python3 -m unittest && python3 -m pytest",
            "python3 -m unittest | tail -5",
            "python3 -m unittest > tests.log",
            "PYTHONPATH=src python3 -m unittest",
            "python3 -m unittest $TESTS",
            "python3 -m unittest test_*.py",
            "cd src",
        ):
            with self.subTest(command=unsupported):
                packet = self.packet(["python3 -m unittest", unsupported])
                self.assertNotIn("ready_capture_batch", packet)
                self.assertIn("capture_command", packet)
        for stage in ("terra", "astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage):
                self.assertNotIn(
                    "ready_capture_batch", self.packet(["python3 -m unittest", "python3 -m pytest"], stage)
                )
        self.assertNotIn("ready_capture_batch", self.packet(["python3 -m unittest"]))

    def test_plan_bounds_keep_the_individual_fallback(self):
        capture = shlex.join([sys.executable, str(Path(support.__file__).with_name("autocode.py")), "capture"])
        for count in (0, 1, 17):
            with self.subTest(count=count):
                self.assertIsNone(ready.context(capture, ["python3 -V"] * count, "/evidence"))
        for count in (2, 16):
            with self.subTest(count=count):
                self.assertIsNotNone(ready.context(capture, ["python3 -V"] * count, "/evidence"))
        self.assertIsNone(ready.context(capture, ["python3 " + "x" * ready.MAX_PLAN_BYTES] * 2, "/evidence"))
        with patch.object(ready.os, "name", "nt"):
            self.assertIsNone(ready.context(capture, ["python3 -V"] * 2, "/evidence"))

    def test_malformed_encoded_plan_refuses_before_any_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            marker = root / "captured"
            spy = root / "capture-spy.py"
            spy.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
            payload = {
                "mode": "raw",
                "commands": [[sys.executable, str(spy)]] * 2,
                "directory": str(root),
            }

            def encode(value):
                return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()

            raw = json.dumps(payload).encode()
            oversized = raw + b" " * (ready.MAX_PLAN_BYTES + 1 - len(raw))
            for encoded in (
                "A" * (ready.MAX_ENCODED_BYTES + 1),
                base64.urlsafe_b64encode(oversized).decode(),
            ):
                with self.subTest(size=len(encoded)):
                    result = subprocess.run(
                        [sys.executable, ready.__file__, encoded], capture_output=True, text=True, timeout=10
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("plan is too large", result.stderr)
                    self.assertFalse(marker.exists())
            malformed = [
                "!not-base64!",
                base64.urlsafe_b64encode(b"\xff").decode(),
                base64.urlsafe_b64encode(b"{").decode(),
                encode([]),
                encode({**payload, "invented_authority": "yes"}),
            ]
            for key, values in (
                ("directory", [None, "bad\0path"]),
                ("mode", [None, "", "RAW", "raw\0", [], {}, True]),
                ("commands", [None, [], [["python3"]], [["python3"]] * 17, [[], []], [[1], [1]], [["bad\0"], ["x"]]]),
            ):
                malformed.extend(encode({**payload, key: value}) for value in values)
            for encoded in malformed:
                with self.subTest(encoded=encoded[:80]):
                    result = subprocess.run(
                        [sys.executable, ready.__file__, encoded], capture_output=True, text=True, timeout=10
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("prepared capture batch plan", result.stderr.lower())
                    self.assertFalse(marker.exists())
            result = subprocess.run([sys.executable, ready.__file__], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("one encoded plan", result.stderr)

    @unittest.skipUnless(sys.platform == "linux", "real bubblewrap requires supported Linux")
    def test_native_shell_uses_current_writable_scratch_and_keeps_run_store_readonly(self):
        import autocode_tool_containment as containment

        if problem := containment.sandbox_problem():
            self.skipTest(problem)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "source.py").write_text("VALUE = 42\n")
            git = ["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test"]
            for args in (["init", "-q"], ["add", "source.py"], ["commit", "-qm", "fixture"]):
                subprocess.run([*git, *args], check=True, capture_output=True)
            run = root / ".autocode/runs/run"
            (run / "evidence").mkdir(parents=True)
            runtime = Path(support.__file__).resolve().parent
            env = {**os.environ, "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}
            boundary = containment.prepare(
                root,
                read_roots=[runtime, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()],
                protected_paths=[run, runtime],
                environment=env,
                verification_copy=True,
            )
            commands = [shlex.join([sys.executable, "-c", f"print({i})"]) for i in range(4)]
            capture = shlex.join([sys.executable, str(runtime / "autocode.py"), "capture"])
            batch = ready.context(capture, commands, run / "evidence")
            result = subprocess.run(
                [boundary["shell"], "-c", self.observed_quote_alteration(batch["command"])],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            checks = [json.loads(line) for line in result.stdout.splitlines() if '"evidence_ref"' in line]
            self.assertEqual(4, len(checks))
            self.assertEqual([shlex.split(command) for command in commands], [row["command"] for row in checks])
            for index, check in enumerate(checks):
                path = Path(check["evidence_ref"])
                self.assertEqual(Path(boundary["scratch"]), path.parent)
                receipt = json.loads(path.read_text())
                self.assertEqual(boundary["verification_copy_sha256"], receipt["verification_copy"]["manifest_sha256"])
                self.assertIn(str(index) + "\n", Path(receipt["full_output"]).read_text())
                check["command"] = shlex.join(check["command"])
            events = root / "observed-events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "id": "real-native-batch",
                            "command": batch["command"],
                            "exit_code": result.returncode,
                            "aggregated_output": result.stdout + result.stderr,
                        },
                    }
                )
                + "\n"
            )
            support.verify_checks(checks, root, events)
            # Destination metadata grants nothing: canonical capture still refuses a forged copy pin.
            marker = Path(boundary["scratch"]) / "forged-copy-executed"
            probe = "from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('bad')"
            forged = ready.context(capture, [shlex.join([sys.executable, "-c", probe])] * 4, run / "evidence")
            refused = subprocess.run(
                [
                    boundary["shell"],
                    "-c",
                    "env AUTOCODE_VERIFICATION_COPY_SHA256=" + "0" * 64 + " " + forged["command"],
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("Verification copy authority changed", refused.stderr)
            self.assertFalse(marker.exists(), "forged environment executed a check")
            refused_refs = [
                json.loads(line)["evidence_ref"] for line in refused.stdout.splitlines() if '"evidence_ref"' in line
            ]
            self.assertEqual(4, len(refused_refs))
            self.assertTrue(all(not Path(path).exists() for path in refused_refs))
            denied = subprocess.run(
                [boundary["shell"], "-c", "touch " + shlex.quote(str(run / "evidence/forbidden"))],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(0, denied.returncode, "the fix must not grant writes in the private run store")
            self.assertFalse((run / "evidence/forbidden").exists())
            self.assertEqual("VALUE = 42\n", (root / "source.py").read_text())

    def test_invalid_native_destination_fails_before_any_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = "tool-containment-" + "a" * 32
            scratch = root / ".autocode" / control / "scratch"
            scratch.mkdir(parents=True)
            manifest = scratch.parent / "verification-copy.json"
            manifest.write_text("{}")
            spy = root / "capture-spy.py"
            marker = root / "captured"
            spy.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
            batch = ready.context(
                shlex.join([sys.executable, str(Path(support.__file__).with_name("autocode.py")), "capture"]),
                [shlex.join([sys.executable, str(spy)])] * 2,
                root / ".autocode/evidence",
            )
            base = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            base.update(
                AUTOCODE_TOOL_CONTAINMENT_ID=control,
                TMPDIR=str(scratch),
                AUTOCODE_VERIFICATION_COPY=str(manifest),
                AUTOCODE_VERIFICATION_COPY_SHA256="0" * 64,
            )
            outside = root / "outside"
            outside.mkdir()
            alias = scratch.parent / "alias"
            alias.symlink_to(scratch, target_is_directory=True)
            variants = [
                {"AUTOCODE_TOOL_CONTAINMENT_ID": ""},
                {"AUTOCODE_VERIFICATION_COPY_SHA256": None},
                {"TMPDIR": None},
                {"TMPDIR": "relative"},
                {"TMPDIR": str(outside)},
                {"TMPDIR": str(scratch / "missing")},
                {"TMPDIR": str(alias)},
                {"AUTOCODE_TOOL_CONTAINMENT_ID": "tool-containment-" + "b" * 32},
                {"AUTOCODE_VERIFICATION_COPY": str(root / "unrelated.json")},
            ]
            for change in variants:
                env = {**base, **change}
                env = {key: value for key, value in env.items() if value is not None}
                with self.subTest(change=change):
                    result = subprocess.run(
                        shlex.split(batch["command"]), cwd=root, env=env, capture_output=True, text=True, timeout=10
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("current native scratch directory", result.stderr)
                    self.assertFalse(marker.exists(), "invalid destination reached capture")

    def test_large_check_output_keeps_all_receipt_attestations_in_bounded_tool_display(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".autocode").mkdir()
            (root / "large.py").write_text("import sys\nprint('x' * 400000)\nraise SystemExit(int(sys.argv[1]))\n")
            commands = [shlex.join([sys.executable, "large.py", str(code)]) for code in (0, 1, 0)]
            capture = shlex.join(
                [sys.executable, str(Path(support.__file__).with_name("autocode.py")), "capture", "--mode", "raw"]
            )
            batch = ready.context(capture, commands, root / ".autocode/evidence")
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            environment["AUTOCODE_OUTPUT_STORE"] = str(root / "store")
            ran = subprocess.run(
                shlex.split(batch["command"]),
                cwd=root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
            self.assertEqual(1, ran.returncode)
            self.assertLess(len(ran.stdout.encode()), 8192, "tool display repeats the captured check output")
            checks = [json.loads(line) for line in ran.stdout.splitlines() if '"evidence_ref"' in line]
            self.assertEqual([0, 1, 0], [check["exit_code"] for check in checks])
            for check in checks:
                check["command"] = shlex.join(check["command"])
                receipt = json.loads(Path(check["evidence_ref"]).read_text())
                self.assertEqual("x" * 400000 + "\n", Path(receipt["full_output"]).read_text())
                self.assertEqual("x" * 400000 + "\n", receipt["summary"]["content"])
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "type": "tool_use",
                        "sessionID": "bounded-batch",
                        "part": {
                            "id": "batch",
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": batch["command"]},
                                "metadata": {"exit": 1},
                                "output": ran.stdout[:8192],
                            },
                        },
                    }
                )
                + "\n"
            )
            support.verify_checks(checks, root, events)

    def test_batch_keeps_order_all_failures_fresh_receipts_and_existing_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".autocode").mkdir()
            (root / "check.py").write_text(
                "import sys\nfrom pathlib import Path\n"
                "number=int(sys.argv[1])\n"
                "with Path('order.txt').open('a') as stream: stream.write(str(number)+'\\n')\n"
                "print('checked',number)\nraise SystemExit(1 if number==2 else 0)\n"
            )
            commands = [shlex.join([sys.executable, "check.py", str(i)]) for i in range(1, 5)]
            capture = shlex.join([sys.executable, str(Path(support.__file__).with_name("autocode.py")), "capture"])
            batch = ready.context(capture, commands, root / ".autocode/evidence")
            identity = {"attempt": "current", "nonce": "nonce", "source_revision": "source"}
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            environment["TMPDIR"] = str(root / "outside")
            environment["AUTOCODE_CAPTURE_CONTEXT"] = json.dumps(identity)
            environment["AUTOCODE_OUTPUT_STORE"] = str(root / "store")
            ran = subprocess.run(
                shlex.split(batch["command"]), cwd=root, env=environment, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(1, ran.returncode, ran.stderr)
            self.assertEqual("1\n2\n3\n4\n", (root / "order.txt").read_text())
            checks = [json.loads(line) for line in ran.stdout.splitlines() if '"evidence_ref"' in line]
            self.assertEqual([0, 1, 0, 0], [row["exit_code"] for row in checks])
            self.assertEqual(4, len({row["evidence_ref"] for row in checks}))
            for check in checks:
                check["command"] = shlex.join(check["command"])
            events = root / "events.jsonl"
            item = {
                "id": "batch",
                "type": "command_execution",
                "command": batch["command"],
                "exit_code": 1,
                "aggregated_output": ran.stdout + ran.stderr,
            }
            events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
            support.verify_checks(checks, root, events)
            native = {
                "type": "tool_use",
                "sessionID": "batch-session",
                "part": {
                    "id": "batch",
                    "sessionID": "batch-session",
                    "messageID": "batch-message",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": batch["command"]},
                        "metadata": {"exit": 1},
                        "output": ran.stdout + ran.stderr,
                    },
                },
            }
            events.write_text(json.dumps(native) + "\n")
            support.verify_checks(checks, root, events)
            support.verify_checks(checks, root, events, receipt_only=True, capture_context=identity)
            for changed in ({"nonce": "stale"}, {"source_revision": "different"}, {"attempt": "earlier"}):
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "validation attempt"):
                    support.verify_checks(
                        checks, root, events, receipt_only=True, capture_context={**identity, **changed}
                    )
            item.update(type="tool_output")
            item.pop("exit_code")
            events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
            with self.assertRaisesRegex(ValueError, "independently executed"):
                support.verify_checks(checks, root, events)
            Path(checks[0]["evidence_ref"]).unlink()
            with self.assertRaises(ValueError):
                support.verify_checks(checks, root, events, receipt_only=True, capture_context=identity)
