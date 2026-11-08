"""Builder batches preserve the approved commands, phases and native boundary."""

import base64
import copy
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
import autocode_util as util

from .test_review_source import approved_state


class BuilderCaptureContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.commands = ["python3 -m unittest -v test_week_key", "python3 -m unittest -v test_cli"]
        self.state = approved_state(self.root, ["week_key.py", "test_week_key.py", "test_cli.py"], task_kind="bugfix")
        self.state["goal_contract"]["body"]["initial_task"].update(
            kind="implement", milestone_id="M1", validation_plan=self.commands
        )
        self.seal()

    def seal(self):
        contract = self.state["goal_contract"]
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        contract["approval_event"]["token"] = "r1:" + contract["hash"]
        self.state["current_task"] = {
            **copy.deepcopy(contract["body"]["initial_task"]),
            "contract_hash": contract["hash"],
            "contract_revision": contract["revision"],
        }

    def packet(self):
        with patch.object(
            context.source_scope, "snapshot", return_value={"revision": "source", "head": "head", "files": {}}
        ):
            prompt, _ = context.context_packet(self.state, "terra", self.root / ".autocode/runs/run/state.json")
        return json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

    def test_builder_receives_exact_approved_commands_with_fresh_phase_guidance(self):
        before = copy.deepcopy(self.state)
        packet = self.packet()
        batch = packet["ready_capture_batch"]
        plan = json.loads(base64.urlsafe_b64decode(shlex.split(batch["command"])[-1]))
        self.assertEqual([shlex.split(command) for command in self.commands], plan["commands"])
        self.assertEqual(packet["builder_artifact_policy"]["evidence_directory"], plan["directory"])
        self.assertIn("before the product fix", batch["instruction"])
        self.assertIn("fresh", batch["instruction"])
        self.assertIn("expected failure", batch["instruction"])
        self.assertIn("does not advance", batch["instruction"])
        self.assertIn("individual capture_command remain available", batch["instruction"])
        self.assertEqual(before, self.state, "presentation must not change phases, routes or authority")

    def test_missing_complex_single_or_unapproved_plans_keep_individual_capture(self):
        initial = copy.deepcopy(self.state)
        for commands in (None, [], [self.commands[0]], [self.commands[0], "python3 -m unittest && false"]):
            with self.subTest(commands=commands):
                self.state = copy.deepcopy(initial)
                self.state["goal_contract"]["body"]["initial_task"]["validation_plan"] = commands
                self.seal()
                packet = self.packet()
                self.assertNotIn("ready_capture_batch", packet)
                self.assertIn("capture_command", packet)
        changes = (
            lambda state: state["goal_contract"].update(approval_status="draft"),
            lambda state: state["current_task"].update(contract_hash="old"),
            lambda state: state["current_task"].update(contract_revision=0),
            lambda state: state["current_task"].update(kind="validate"),
            lambda state: state["current_task"].update(validation_plan=list(reversed(self.commands))),
        )
        for change in changes:
            self.state = copy.deepcopy(initial)
            change(self.state)
            packet = self.packet()
            self.assertNotIn("ready_capture_batch", packet)
            self.assertIn("capture_command", packet)


class BuilderNativeBatchTests(unittest.TestCase):
    def test_invalid_native_scratch_and_unsafe_marker_are_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = "tool-containment-" + "a" * 32
            scratch = root / ".autocode" / control / "scratch"
            scratch.mkdir(parents=True)
            marker = scratch.parent / "writer-capture.json"
            batch = ready.context(shlex.join(ready._capture()), ["python3 -V"] * 2, root / ".autocode/evidence")
            encoded = shlex.split(batch["command"])[-1]
            env = {"AUTOCODE_TOOL_CONTAINMENT_ID": control, "TMPDIR": str(scratch)}
            with patch.dict(os.environ, env, clear=True), patch.object(ready.Path, "cwd", return_value=root):
                for tmp in (str(root), "relative", str(scratch / "missing")):
                    with patch.dict(os.environ, {"TMPDIR": tmp}), patch.object(ready.Path, "read_text") as read:
                        with self.assertRaisesRegex(ValueError, "current native scratch"):
                            ready.run(encoded)
                        read.assert_not_called()
                for kind in ("missing", "directory", "writable", "symlink"):
                    if kind == "directory":
                        marker.mkdir()
                    elif kind == "writable":
                        marker.write_text("{}")
                    elif kind == "symlink":
                        marker.symlink_to(root / "outside")
                    with self.subTest(kind=kind), patch.object(ready.Path, "read_text") as read:
                        with self.assertRaisesRegex(ValueError, "current native scratch"):
                            ready.run(encoded)
                        read.assert_not_called()
                    if marker.is_dir():
                        marker.rmdir()
                    elif marker.exists() or marker.is_symlink():
                        marker.unlink()

    def test_writer_requires_immutable_bound_marker_and_never_downgrades_readonly_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = "tool-containment-" + "a" * 32
            scratch = root / ".autocode" / control / "scratch"
            scratch.mkdir(parents=True)
            marker = scratch.parent / "writer-capture.json"
            binding = {"version": 1, "workspace": str(root), "scratch": str(scratch), "control": control}
            marker.write_text(json.dumps(binding))
            marker.chmod(0o400)
            batch = ready.context(shlex.join(ready._capture()), ["python3 -V"] * 2, root / ".autocode/evidence")
            encoded = shlex.split(batch["command"])[-1]
            env = {"AUTOCODE_TOOL_CONTAINMENT_ID": control, "TMPDIR": str(scratch)}
            with patch.dict(os.environ, env, clear=True), patch.object(ready.Path, "cwd", return_value=root):
                with patch.object(ready.subprocess, "run") as run, patch("builtins.print"):
                    run.return_value.returncode = 0
                    self.assertEqual(0, ready.run(encoded))
                    self.assertEqual(2, run.call_count)
                    self.assertTrue(
                        all(
                            Path(call.args[0][call.args[0].index("--output") + 1]).parent == scratch
                            for call in run.call_args_list
                        )
                    )
                for value in (
                    {**binding, "workspace": str(root / "other")},
                    {**binding, "control": "old"},
                    {**binding, "version": True},
                    {},
                ):
                    marker.chmod(0o600)
                    marker.write_text(json.dumps(value))
                    marker.chmod(0o400)
                    with (
                        patch.object(ready.subprocess, "run") as run,
                        self.assertRaisesRegex(ValueError, "current native scratch"),
                    ):
                        ready.run(encoded)
                    run.assert_not_called()
                marker.chmod(0o600)
                marker.write_text(json.dumps(binding))
                with (
                    patch.object(ready.subprocess, "run") as run,
                    self.assertRaisesRegex(ValueError, "current native scratch"),
                ):
                    ready.run(encoded)
                run.assert_not_called()
                marker.chmod(0o400)
                original = marker.with_suffix(".original")
                marker.rename(original)
                marker.symlink_to(original)
                with (
                    patch.object(ready.subprocess, "run") as run,
                    self.assertRaisesRegex(ValueError, "current native scratch"),
                ):
                    ready.run(encoded)
                run.assert_not_called()
                marker.unlink()
                with (
                    patch.object(ready.subprocess, "run") as run,
                    self.assertRaisesRegex(ValueError, "current native scratch"),
                ):
                    ready.run(encoded)
                run.assert_not_called()
                original.rename(marker)
                # Neither one missing copy variable nor both removed variables may
                # turn an existing readonly-copy launch into a writable launch.
                copy_path = scratch.parent / "verification-copy.json"
                copy_path.write_text("{}")
                for copy_env in (
                    {},
                    {"AUTOCODE_VERIFICATION_COPY": str(copy_path)},
                    {"AUTOCODE_VERIFICATION_COPY_SHA256": "pin"},
                ):
                    with patch.dict(os.environ, copy_env), patch.object(ready.subprocess, "run") as run:
                        with self.assertRaisesRegex(ValueError, "current native scratch"):
                            ready.run(encoded)
                        run.assert_not_called()

    @unittest.skipUnless(sys.platform == "linux", "real bubblewrap requires supported Linux")
    def test_native_builder_runs_both_phases_with_distinct_authenticated_receipts(self):
        import autocode_tool_containment as containment

        if problem := containment.sandbox_problem():
            self.skipTest(problem)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "source.py").write_text("VALUE = 0\n")
            for name in ("test_api.py", "test_cli.py"):
                (root / name).write_text(
                    "import unittest\nfrom source import VALUE\n"
                    "class Check(unittest.TestCase):\n"
                    "    def test_value(self): self.assertEqual(42, VALUE)\n"
                )
            runtime = Path(support.__file__).resolve().parent
            run = root / ".autocode/runs/run"
            (run / "evidence").mkdir(parents=True)
            boundary = containment.prepare(
                root,
                allow_write=True,
                write_paths=["source.py"],
                read_roots=[runtime, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()],
                protected_paths=[run, runtime],
                environment={"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"},
            )
            capture = shlex.join([sys.executable, str(runtime / "autocode.py"), "capture"])
            commands = [shlex.join([sys.executable, "-m", "unittest", "-v", name]) for name in ("test_api", "test_cli")]
            batch = ready.context(capture, commands, run / "evidence")

            def execute(command):
                return subprocess.run(
                    [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=30
                )

            marker = Path(boundary["scratch"]).parent / "writer-capture.json"
            denied = execute(
                shlex.join(
                    [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('forged')"]
                )
            )
            self.assertNotEqual(
                0, denied.returncode, "the kernel must protect the affirmative writer marker before batch use"
            )
            before = execute(batch["command"])
            self.assertEqual(1, before.returncode, before.stderr)
            edited = execute(
                shlex.join(
                    [sys.executable, "-c", "from pathlib import Path; Path('source.py').write_text('VALUE = 42\\n')"]
                )
            )
            self.assertEqual(0, edited.returncode, edited.stderr)
            after = execute(batch["command"])
            self.assertEqual(0, after.returncode, after.stderr)
            all_refs = []
            for result, expected in ((before, [1, 1]), (after, [0, 0])):
                checks = [json.loads(line) for line in result.stdout.splitlines() if '"evidence_ref"' in line]
                self.assertEqual(expected, [row["exit_code"] for row in checks])
                self.assertEqual([shlex.split(command) for command in commands], [row["command"] for row in checks])
                for check in checks:
                    path = Path(check["evidence_ref"])
                    self.assertEqual(Path(boundary["scratch"]), path.parent)
                    receipt = json.loads(path.read_text())
                    self.assertNotIn("verification_copy", receipt, "Builder checks execute in the original workspace")
                    self.assertIn("Ran 1 test", Path(receipt["full_output"]).read_text())
                    check["command"] = shlex.join(check["command"])
                    all_refs.append(str(path))
                events = root / "events.jsonl"
                events.write_text(
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "batch",
                                "type": "command_execution",
                                "command": batch["command"],
                                "exit_code": result.returncode,
                                "aggregated_output": result.stdout + result.stderr,
                            },
                        }
                    )
                    + "\n"
                )
                support.verify_checks(checks, root, events)
            self.assertEqual(4, len(set(all_refs)), "post-fix checks must never reuse before-fix receipts")
            for target in (marker, run / "evidence/forbidden", root / "test_api.py"):
                denied = execute(
                    shlex.join(
                        [sys.executable, "-c", f"from pathlib import Path; Path({str(target)!r}).write_text('forged')"]
                    )
                )
                self.assertNotEqual(0, denied.returncode, str(target))
            self.assertEqual("VALUE = 42\n", (root / "source.py").read_text())
