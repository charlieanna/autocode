"""Real local held roots, controlled seals; no provider/native qualification."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_checkout_lock as checkout_lock
import autocode_native_launch as native
import autocode_process as processes
import autocode_supervision as supervision
import autocode_util as util
import autocode_visual_protection as protection  # noqa: F401 - preserve existing import behavior
import autocode_visual_writer_admission as admissions
import autocode_writer_barrier as barriers


class HeldEnvelopeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-held-envelope-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.marker, self.seal = self.root / "target.json", self.root / "seal.json"
        self.environment = {"PATH": "/usr/bin:/bin", "CONTROLLED_SECRET": "never-in-receipts"}
        self.command = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import json,os,pathlib,sys; s=json.loads(pathlib.Path(sys.argv[2]).read_text()); "
            'pathlib.Path(sys.argv[1]).write_text(json.dumps({"sealed_pid":s["root"]["pid"],'
            '"actual_pid":os.getpid(),"argument":sys.argv[3]}))',
            str(self.marker),
            str(self.seal),
            "literal target argument",
        ]

    def options(self, **changes):
        return {
            "cwd": self.root,
            "env": dict(self.environment),
            "start_new_session": True,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            **changes,
        }

    def seal_target(self, child, tree, facts):
        self.assertFalse(self.marker.exists())
        self.assertEqual(child.pid, tree.capture_root()["pid"])
        util.atomic_json(self.seal, {"root": tree.capture_root(), "transport": facts})

    def run_held_target(self, *, clock_shift=False):
        with barriers.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            inner, options = gate.launch(self.options())
            with supervision.launch(inner, receipt_path=self.root / "supervision.json", timeout=5, **options) as child:
                outer = tuple(child.args)
                self.assertNotEqual(list(outer), inner)
                self.assertNotEqual(inner, self.command)
                gate.spawned(child)
                with ExitStack() as changes:
                    if clock_shift:
                        original_identity = supervision._identity
                        original_root = processes.ProcessTree.capture_root

                        def shifted(row):
                            return {**row, "birth_time": row["birth_time"] + 3600, "started": "shifted display"}

                        changes.enter_context(
                            patch.object(
                                supervision,
                                "_identity",
                                side_effect=lambda pid: (
                                    shifted(original_identity(pid)) if pid == child.pid else original_identity(pid)
                                ),
                            )
                        )
                        changes.enter_context(
                            patch.object(
                                processes.ProcessTree,
                                "capture_root",
                                lambda tree: (
                                    shifted(original_root(tree)) if tree.pid == child.pid else original_root(tree)
                                ),
                            )
                        )
                    code, expired = processes.wait_for_stage(
                        child,
                        5,
                        lambda _: None,
                        on_root=lambda tree, release: gate.release_after_seal(child, tree, self.seal_target, release),
                    )
                self.assertEqual((0, False), (code, expired))
                self.assertEqual(outer, tuple(child.args))
                facts = json.loads(self.seal.read_text())["transport"]
                envelope = facts["supervision_envelope"]
                self.assertEqual(list(outer), envelope["outer_argv"])
                self.assertEqual(inner, envelope["inner_argv"])
                self.assertEqual(util.digest(self.command), facts["target_command_sha256"])
                self.assertEqual(util.digest(self.environment), facts["target_environment_sha256"])
                self.assertEqual(child.pid, envelope["supervision"]["provider"]["pid"])
                self.assertNotIn("never-in-receipts", self.seal.read_text())
                result = json.loads(self.marker.read_text())
                self.assertEqual(child.pid, result["actual_pid"])
                self.assertEqual(child.pid, result["sealed_pid"])
            self.assertEqual("stopped", child.supervision_result["phase"])

    def test_real_outer_barrier_target_use_one_child_and_seal_before_execution(self):
        self.run_held_target()

    def test_wall_clock_birth_drift_preserves_original_kernel_owner_and_target(self):
        self.run_held_target(clock_shift=True)

    def refused_root(self, mutate, *, reason=None):
        with barriers.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            inner, options = gate.launch(self.options())
            child = None
            expected = (supervision.SupervisionError, barriers.BarrierError)
            with self.assertRaisesRegex(expected, reason) if reason else self.assertRaises(expected):
                with supervision.launch(
                    inner, receipt_path=self.root / "supervision.json", timeout=5, **options
                ) as child:
                    gate.spawned(child)

                    def on_root(tree, release):
                        with ExitStack() as changes:
                            mutate(gate, child, tree, changes)
                            gate.release_after_seal(child, tree, self.seal_target, release)

                    processes.wait_for_stage(child, 5, lambda _: None, on_root=on_root)
            self.assertIsNotNone(child.returncode)
            self.assertFalse(processes.live_processes([child.supervision["provider"]]))
            self.assertFalse(self.marker.exists())
            self.assertFalse(self.seal.exists())

    def test_missing_original_envelope_cannot_be_replaced_by_saved_facts(self):
        self.refused_root(
            lambda gate, child, tree, changes: setattr(child, "_autocode_launch_envelope", deepcopy(child.supervision))
        )

    def test_current_root_birth_mismatch_cleans_the_same_blocked_child(self):
        def mutate(gate, child, tree, changes):
            original = processes.live_processes
            changes.enter_context(
                patch.object(
                    processes,
                    "live_processes",
                    side_effect=lambda rows: [] if rows == [child.supervision["provider"]] else original(rows),
                )
            )

        self.refused_root(mutate)

    def test_current_session_mismatch_refuses_before_seal_and_cleans_same_child(self):
        def mutate(gate, child, tree, changes):
            original = supervision._identity

            def identity(pid):
                actual = original(pid)
                return {**actual, "session": actual["session"] + 1} if pid == child.pid else actual

            changes.enter_context(patch.object(supervision, "_identity", side_effect=identity))

        self.refused_root(mutate, reason="actual supervisor root birth or session differs")

    def reject_current_identity_change(self, field):
        def mutate(gate, child, tree, changes):
            original = supervision._identity

            def identity(pid):
                actual = original(pid)
                return {**actual, field: actual[field] + 1} if pid == child.pid else actual

            changes.enter_context(patch.object(supervision, "_identity", side_effect=identity))

        self.refused_root(mutate, reason="actual supervisor root birth or session differs")

    def test_current_kernel_birth_change_still_refuses_same_child(self):
        self.reject_current_identity_change("birth_identity")

    def test_current_group_change_still_refuses_same_child(self):
        self.reject_current_identity_change("group")

    def test_keeper_admission_drift_cleans_the_same_blocked_child(self):
        self.refused_root(lambda gate, child, tree, changes: child.supervision.update(nonce="foreign-keeper"))

    def test_final_environment_drift_cleans_the_same_blocked_child(self):
        self.refused_root(
            lambda gate, child, tree, changes: setattr(
                gate, "environment_sha256", util.digest({"changed": "environment"})
            )
        )

    def test_outer_source_drift_cleans_the_same_blocked_child(self):
        def mutate(gate, child, tree, changes):
            original = util.file_hash
            outer = str(Path(supervision.__file__).with_name("autocode_supervision_bootstrap.py").resolve())
            changes.enter_context(
                patch.object(
                    util, "file_hash", side_effect=lambda name: "0" * 64 if str(name) == outer else original(name)
                )
            )

        self.refused_root(mutate)

    def test_outer_argv_drift_refuses_without_rewriting_child_args(self):
        def mutate(gate, child, tree, changes):
            envelope = supervision.launch_envelope(child)
            envelope._bootstrap = (*envelope._bootstrap[:-1], "foreign-inner-command")

        self.refused_root(mutate)

    def test_inherited_descriptor_drift_refuses_before_target_and_cleans_same_child(self):
        with barriers.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            inner, options = gate.launch(self.options())
            with (self.root / "extra-descriptor").open("w") as unexpected:
                options["pass_fds"] = (*options["pass_fds"], unexpected.fileno())
                with self.assertRaisesRegex(supervision.SupervisionError, "descriptors changed"):
                    with supervision.launch(
                        inner, receipt_path=self.root / "supervision.json", timeout=5, **options
                    ) as child:
                        gate.spawned(child)
                self.assertIsNotNone(child.returncode)
                self.assertFalse(self.marker.exists())

    def test_release_needs_successful_seal_even_with_authentic_outer_admission(self):
        with barriers.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            inner, options = gate.launch(self.options())
            with self.assertRaisesRegex(OSError, "controlled durable sink failure"):
                with supervision.launch(
                    inner, receipt_path=self.root / "supervision.json", timeout=5, **options
                ) as child:
                    gate.spawned(child)

                    def failed(*_):
                        raise OSError("controlled durable sink failure")

                    processes.wait_for_stage(
                        child,
                        5,
                        lambda _: None,
                        on_root=lambda tree, release: gate.release_after_seal(child, tree, failed, release),
                    )
            self.assertIsNotNone(child.returncode)
            self.assertFalse(self.marker.exists())

    def test_writer_admission_seals_target_and_outer_from_the_same_genuine_child(self):
        run = self.root / ".autocode" / "runs" / "controlled-admission"
        run.mkdir(parents=True)
        ledger = admissions.WriterAdmissions(self.root, run, authenticate=lambda *_: None)
        # Explicit controlled preparation/recheck seam. It supplies no native
        # approval or dispatch proof; this control exercises the real sealing
        # transport and durable ledger append, not historical eligibility.
        body = {
            "binding": {"contract": {"hash": "controlled"}, "task": {"milestone_id": "M1"}},
            "lineage": None,
            "command_sha256": util.digest(self.command),
            "environment_sha256": util.digest(self.environment),
        }
        with ledger._operation() as operation:
            original = ledger._append("prepared", body, operation=operation)
        prepared = admissions.Prepared(original["id"], admissions._json(body))
        ledger._pending[prepared.id] = prepared
        ledger._ready[prepared.id] = admissions._json({"controlled": "not-native-dispatch"})
        rows = []
        with barriers.WriterBarrier(self.command, self.environment, payload_directory=self.root) as gate:
            inner, options = gate.launch(self.options())
            with supervision.launch(inner, receipt_path=self.root / "supervision.json", timeout=5, **options) as child:
                gate.spawned(child)

                def seal(actual, tree, facts):
                    self.assertFalse(self.marker.exists())
                    rows.append(
                        ledger.seal_launch(
                            prepared,
                            actual,
                            tree,
                            command=self.command,
                            environment=self.environment,
                            launched_at=util.now(),
                            barrier=gate,
                        )
                    )
                    self.seal_target(actual, tree, facts)

                self.assertEqual(
                    (0, False),
                    processes.wait_for_stage(
                        child,
                        5,
                        lambda _: None,
                        on_root=lambda tree, release: gate.release_after_seal(child, tree, seal, release),
                    ),
                )
        sealed = rows[0]["body"]["launch"]
        self.assertEqual(util.digest(self.command), sealed["command_sha256"])
        self.assertEqual(child.pid, sealed["root"]["pid"])
        self.assertEqual(child.args, sealed["transport"]["supervision_envelope"]["outer_argv"])
        self.assertTrue(self.marker.exists())
        self.assertNotIn(prepared.id, ledger._pending)


class NativeContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-run-context-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / ".gitignore").write_text(".autocode/\n")
        (self.root / "source.txt").write_text("original\n")
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"],
        ):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)
        self.run = self.root / ".autocode" / "runs" / "fresh"
        self.run.mkdir(parents=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.run_handle = self.stack.enter_context(util.run_lock(self.run))
        self.checkout_handle = self.stack.enter_context(checkout_lock.exclusive(self.root, self.run))
        self.state = {"settings": {}, "goal_contract": None, "stages": []}
        self.snapshot = util.snapshot(self.root)

    def context(self):
        return native.NativeRunContext(
            self.root,
            self.run,
            run_handle=self.run_handle,
            checkout_handle=self.checkout_handle,
            start_state=self.state,
            snapshot=self.snapshot,
        )

    def test_original_held_handles_create_one_origin_and_reopen_without_rebinding(self):
        context = self.context()
        original = {p.name: p.read_bytes() for p in context.factory.protection.root.iterdir()}
        reopened = self.context()
        self.assertEqual(original, {p.name: p.read_bytes() for p in reopened.factory.protection.root.iterdir()})
        self.assertIs(self.run_handle, context.factory.protection.handles[0][0])
        self.assertIs(self.checkout_handle, context.factory.protection.handles[1][0])
        with self.assertRaises(util.Paused):
            with context.held_launch(
                None, command=[], environment={}, options={}, receipt_path=self.run / "forbidden.json", timeout=5
            ):
                self.fail("Missing production preparation must not launch")
        self.assertFalse((self.run / "forbidden.json").exists())

    def test_old_recorded_run_cannot_acquire_a_fresh_native_origin(self):
        self.state["stages"] = [{"stage": "requirements_gather"}]
        with self.assertRaisesRegex(util.Paused, "must precede every source-capable route"):
            self.context()
        root = self.run / "visual-native-protection"
        self.assertFalse(root.exists() and any(root.iterdir()))

    def test_old_artifact_without_state_summary_cannot_acquire_origin(self):
        old = self.run / "iterations" / "0001"
        old.mkdir(parents=True)
        (old / "requirements-01.jsonl").write_text("retained earlier execution\n")
        with self.assertRaisesRegex(util.Paused, "prior execution artifacts"):
            self.context()
        self.assertFalse((self.run / "visual-native-protection").exists())

    def test_replaced_checkout_lock_inode_refuses_context(self):
        lock = self.root / ".autocode" / "writer.lock"
        lock.rename(lock.with_suffix(".retained"))
        lock.write_text("replacement")
        with self.assertRaisesRegex(util.Paused, "original lock inode"):
            self.context()

    def test_lost_original_lock_after_popen_cleans_same_held_root_without_target(self):
        context = self.context()
        _owner = context.factory.protection
        marker = self.run / "forbidden-target"
        command = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("executed")',
            str(marker),
        ]
        environment = {"PATH": "/usr/bin:/bin"}
        launch = self.controlled_launch(context, command)
        prepared, environment, options = launch["prepared"], launch["environment"], launch["options"]
        with self.assertRaisesRegex(util.Paused, "original lock inode"):
            with context.held_launch(
                prepared,
                command=command,
                environment=environment,
                options=options,
                receipt_path=launch["receipt_path"],
                timeout=5,
            ) as (child, on_root):
                outer = tuple(child.args)
                lock = self.root / ".autocode" / "writer.lock"
                lock.rename(lock.with_suffix(".retained"))
                lock.write_text("replacement")
                processes.wait_for_stage(child, 5, lambda _: None, on_root=on_root)
        self.assertEqual(outer, tuple(child.args))
        self.assertIsNotNone(child.returncode)
        self.assertFalse(processes.live_processes([child.supervision["provider"]]))
        self.assertFalse(marker.exists())

    def controlled_launch(self, context, command=None):
        """Real preparation/source guard; controlled transport, no native credit."""
        owner = context.factory.protection
        marker = self.run / "forbidden-target"
        command = command or [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("executed")',
            str(marker),
        ]
        environment = {"PATH": "/usr/bin:/bin"}
        # Descriptor-negative controls reuse the same unlaunched preparation.
        # They never create a second row or adopt an unfinished earlier launch.
        cached = getattr(self, "_controlled_preparation", None)
        if cached is not None and cached[0] is owner and cached[1] == command:
            prepared = cached[2]
        else:
            self.state.update(
                workspace=str(self.root),
                settings={
                    "roles": {
                        "glm": {
                            "engine": "opencode",
                            "provider": "opencode",
                            "model": "controlled/model",
                            "reasoning_effort": "high",
                        }
                    }
                },
            )
            before = self.run / "controlled.before.json"
            util.atomic_json(before, self.snapshot)
            record = {
                "stage": "plan",
                "role": "glm",
                "engine": "opencode",
                "command": command,
                "launch_route": dict(self.state["settings"]["roles"]["glm"]),
                "events": str(self.run / "controlled.jsonl"),
                "output": str(self.run / "controlled.json"),
                "before_ref": str(before),
            }
            manifest = self.run / "controlled-isolation.json"
            manifest.write_text("{}\n")
            binary = str(Path(command[0]).resolve())
            descriptor = {
                "version": 1,
                "files": {},
                "manifest": str(manifest),
                "manifest_sha256": util.file_hash(manifest),
                "child_identity": {"executable": binary, "executable_sha256": util.file_hash(binary)},
            }
            # Only transport conformance is controlled. Ownership, runtime,
            # journal, source and evidence pins use the real preparation path.
            with patch.object(
                owner, "_native_config", return_value=({str(manifest): util.file_hash(manifest)}, descriptor)
            ):
                prepared = context.factory.prepare_launch(
                    self.state,
                    self.snapshot,
                    record,
                    boundary={"allow_write": False, "write_paths": []},
                    command=command,
                    environment=environment,
                    native_role_isolation=None,
                )
            self._controlled_preparation = (owner, list(command), prepared)
        return dict(
            prepared=prepared,
            command=command,
            environment=environment,
            options={
                "cwd": self.root,
                "env": environment,
                "start_new_session": True,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            },
            # Held planning cancellation binds the receipt beside the stage output.
            receipt_path=self.run / "controlled.supervision.json",
            timeout=5,
        )

    def contender_exit(self):
        # A separate process opens a distinct OFD; it must not acquire the
        # original run lock. All stdio is explicit so no lock alias is passed.
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                "import fcntl,sys\n"
                'with open(sys.argv[1], "a+") as handle:\n'
                " try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                " except BlockingIOError: sys.exit(73)\n"
                " sys.exit(0)\n",
                str(self.run / "writer.lock"),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return result.returncode

    def test_run_lock_original_and_duplicate_stdio_and_pass_fds_refuse_before_child(self):
        context = self.context()
        alias = os.dup(self.run_handle.fileno())
        try:
            changes = [{"pass_fds": (self.run_handle.fileno(),)}, {"pass_fds": (alias,)}]
            changes += [
                {key: value}
                for key in ("stdin", "stdout", "stderr")
                for value in (self.run_handle, self.run_handle.fileno(), alias)
            ]
            for change in changes:
                with self.subTest(option=tuple(change), value_type=type(next(iter(change.values()))).__name__):
                    launch = self.controlled_launch(context)
                    launch["options"].update(change)
                    with patch.object(
                        supervision, "launch", side_effect=AssertionError("target child started")
                    ) as start:
                        with self.assertRaisesRegex(util.Paused, "run-lock authority"):
                            with context.held_launch(**launch):
                                self.fail("Run-lock alias reached target launch")
                        start.assert_not_called()
                    self.assertEqual(73, self.contender_exit())
                    self.assertFalse(launch["receipt_path"].exists())
                    self.assertFalse((self.run / "forbidden-target").exists())
        finally:
            os.close(alias)
        # Prove the independent contender can observe a real release, rather
        # than merely failing to open the fixture or run its interpreter.
        import fcntl

        fcntl.flock(self.run_handle, fcntl.LOCK_UN)
        try:
            self.assertEqual(0, self.contender_exit())
        finally:
            fcntl.flock(self.run_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_default_inherited_standard_slot_run_lock_alias_refuses_before_child(self):
        context = self.context()
        for index, key in enumerate(("stdin", "stdout", "stderr")):
            launch = self.controlled_launch(context)
            launch["options"][key] = None
            original = os.dup(index)
            caught, started = None, None
            try:
                os.dup2(self.run_handle.fileno(), index)
                with patch.object(supervision, "launch", side_effect=AssertionError("target child started")) as start:
                    try:
                        with context.held_launch(**launch):
                            raise AssertionError("Inherited run-lock alias reached target launch")
                    except Exception as error:
                        caught = error
                    started = start.call_count
            finally:
                os.dup2(original, index)
                os.close(original)
            with self.subTest(inherited=key):
                self.assertIsInstance(caught, util.Paused)
                self.assertIn("run-lock authority", str(caught))
                self.assertEqual(0, started)
                self.assertEqual(73, self.contender_exit())
                self.assertFalse(launch["receipt_path"].exists())
                self.assertFalse((self.run / "forbidden-target").exists())

    def test_context_preserves_checkout_descriptor_through_both_real_bootstraps(self):
        context = self.context()
        marker = self.run / "checkout-inherited.json"
        command = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            "import json,os,pathlib,sys; s=os.fstat(int(sys.argv[2])); "
            "pathlib.Path(sys.argv[1]).write_text(json.dumps([s.st_dev,s.st_ino]))",
            str(marker),
            str(self.checkout_handle.fileno()),
        ]
        launch = self.controlled_launch(context, command)

        def controlled_release(prepared, gate, child, tree, token):
            def seal(actual, owned, facts):
                self.assertFalse(marker.exists())
                context.factory.protection._ownership()
                util.atomic_json(self.run / "controlled-seal.json", {"root": owned.capture_root(), "transport": facts})

            gate.release_after_seal(child, tree, seal, token)

        # Controlled fsync seal only; production native preparation/publication
        # remains disabled and is not fabricated by this transport control.
        with patch.object(context.factory, "release_launch", side_effect=controlled_release):
            with context.held_launch(**launch) as (child, on_root):
                self.assertEqual((0, False), processes.wait_for_stage(child, 5, lambda _: None, on_root=on_root))
        original = os.fstat(self.checkout_handle.fileno())
        self.assertEqual([original.st_dev, original.st_ino], json.loads(marker.read_text()))
        self.assertEqual(73, self.contender_exit())
