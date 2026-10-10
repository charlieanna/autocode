"""Explicit interrupted-verification recovery never supplies passing evidence."""

import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_command_receipt as receipts
import autocode_configure as configure
import autocode_process as processes
import autocode_runner_check as runner_check
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_verification_recovery as recovery
import autocode_verification_schedule as schedule


class AdmissionFixture:
    def __init__(self, root):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.active = {
            "attempt_id": "a" * 32,
            "identity": {
                "command": "node --test test.cjs",
                "source_revision": "source",
                "obligation": {"task": "accepted", "contract": "unchanged"},
            },
            "started_at": "2026-10-08T00:53:47Z",
        }
        self.pending = self.root / "pending.json"
        self.out = self.root / util.digest(self.active["identity"]) / self.active["attempt_id"]
        (self.out / "scratch" / "tree").mkdir(parents=True)
        self.output = self.out / "scratch-command.log"
        self.output.write_text("retained native output; not a scheduler PASS\n")
        self.path = self.out / "command-supervision" / ("b" * 32) / "supervision.json"
        self.path.parent.mkdir(parents=True)
        self.metadata = {
            "schema": 1,
            "nonce": "c" * 32,
            "receipt": str(self.path),
            "owner": {"pid": 2147480001, "birth_identity": 1},
            "keeper": {"pid": 2147480002, "birth_identity": 2},
            "provider": {"pid": 2147480003, "birth_identity": 3},
        }
        self.terminal = {
            **self.metadata,
            "phase": "stopped",
            "cause": "provider_stopped",
            "cleanup_error": None,
            "observed_at": "2026-10-08T00:54:01Z",
            "processes": [self.metadata["provider"], {"pid": 2147480004, "birth_identity": 4}],
        }
        self.check = {
            "stage": "sol",
            "summary": "Checking completed report",
            "command": "node --test-reporter=spec --test test.cjs",
            "output": str(self.output),
            "supervision": self.metadata,
            "processes": [self.metadata["owner"]],
        }
        self.admission_path = self.path.parent / "admission.json"
        self.admission = {
            "schema": 1,
            "command": self.check["command"],
            "cwd": str(self.out / "scratch" / "tree"),
            "output": str(self.output),
            "supervision": self.metadata,
            "timeout_seconds": 900,
        }
        self.write()

    def write(self):
        util.atomic_json(self.pending, self.active)
        util.atomic_json(self.path, self.terminal)
        util.atomic_json(self.admission_path, self.admission)

    def recover(self):
        return schedule.recover_interrupted(self.root, self.check)

    def original_receipt(self, *, exit_code=0):
        saved = {
            "runner_owned": True,
            "attempt_id": self.active["attempt_id"],
            "identity": self.active["identity"],
            "started_at": self.active["started_at"],
            "finished_at": "2026-10-08T00:55:22Z",
            "reason": "mandatory_approved_execution",
            "result": {
                "command": self.check["command"],
                "output": str(self.output),
                "output_sha256": util.file_hash(self.output),
                "exit_code": exit_code,
                "error": "",
                "timed_out": False,
                "duration_seconds": 33.21,
                "supervision": self.metadata,
                "supervision_sha256": util.file_hash(self.path),
                "supervision_errors": [],
                "results": {
                    "complete": True,
                    "total": 1,
                    "passed": ["original_test"] if exit_code == 0 else [],
                    "failed": [] if exit_code == 0 else ["original_test"],
                    "skipped": [],
                    "uncollected": [],
                },
            },
        }
        util.atomic_json(self.out / "receipt.json", saved)
        return saved


class VerificationRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = AdmissionFixture(Path(temporary.name) / "obligations")
        self.native = patch.object(processes, "live_processes", return_value=[])
        self.observe = self.native.start()
        self.addCleanup(self.native.stop)

    def assert_held(self):
        before = self.fixture.pending.read_bytes()
        with self.assertRaises(receipts.OwnershipUncertain):
            self.fixture.recover()
        self.assertEqual(before, self.fixture.pending.read_bytes())
        self.assertFalse((self.fixture.out / "receipt.json").exists())

    def test_recovery_retains_failed_admission_and_runs_the_next_obligation_fresh(self):
        fixed = self.fixture.recover()
        path = Path(fixed["receipt"])
        saved = util.read(path)
        self.assertEqual(self.fixture.active, saved["pending_admission"])
        self.assertEqual(self.fixture.active["identity"], saved["identity"])
        self.assertIsNone(saved["result"]["exit_code"])
        self.assertIsNone(saved["result"]["duration_seconds"])
        self.assertEqual("interrupted_receipt", fixed["disposition"])
        self.assertTrue(saved["result"]["interrupted"])
        self.assertFalse(receipts.completed(saved["result"]))
        self.assertFalse(schedule.reusable(saved["result"]))
        self.assertFalse(self.fixture.pending.exists())
        retained = path.read_bytes()
        schedule.guard(self.fixture.root)
        calls = []

        def execute(out):
            calls.append(out)
            out.mkdir()
            log = out / "fresh.log"
            log.write_text("fresh execution")
            return {
                "exit_code": 0,
                "output": str(log),
                "output_sha256": util.file_hash(log),
                "results": {
                    "complete": True,
                    "total": 1,
                    "passed": ["test_fresh"],
                    "failed": [],
                    "skipped": [],
                    "uncollected": [],
                },
            }

        result = schedule.run(
            self.fixture.root,
            self.fixture.active["identity"],
            execute,
            reuse_allowed=True,
            reason="new",
            current_identity=lambda: self.fixture.active["identity"],
        )
        self.assertEqual("execute", result["scheduling"]["action"])
        self.assertEqual(1, len(calls))
        self.assertEqual(retained, path.read_bytes())

    def test_repeat_without_pending_does_not_rewrite_evidence(self):
        first = self.fixture.recover()
        path = Path(first["receipt"])
        before = path.read_bytes()
        self.assertIsNone(self.fixture.recover())
        self.assertEqual(before, path.read_bytes())

    def test_existing_valid_completed_publication_is_preserved_without_an_interrupted_result(self):
        path = self.fixture.out / "receipt.json"
        saved = {
            "runner_owned": True,
            "attempt_id": self.fixture.active["attempt_id"],
            "identity": self.fixture.active["identity"],
            "started_at": "original",
            "finished_at": "collected",
            "result": {
                "exit_code": 0,
                "output": str(self.fixture.output),
                "output_sha256": util.file_hash(self.fixture.output),
            },
        }
        util.atomic_json(path, saved)
        pointer = self.fixture.out.parent / "completed.json"
        util.atomic_json(pointer, {"name": str(path.relative_to(pointer.parent)), "sha256": util.file_hash(path)})
        before, pointer_before = path.read_bytes(), pointer.read_bytes()
        recovered = self.fixture.recover()
        self.assertEqual("existing_completed_receipt", recovered["disposition"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(pointer_before, pointer.read_bytes())
        self.assertFalse(self.fixture.pending.exists())

    def test_original_completed_receipt_before_pointer_is_preserved_and_fresh_execution_runs(self):
        self.fixture.original_receipt()
        path = self.fixture.out / "receipt.json"
        before = path.read_bytes()
        recovered = self.fixture.recover()
        self.assertEqual("published_existing_completed_receipt", recovered["disposition"])
        self.assertEqual(before, path.read_bytes())
        self.assertFalse(self.fixture.pending.exists())
        self.assertEqual(util.file_hash(path), util.read(path.parent.parent / "completed.json")["sha256"])
        calls = []
        result = schedule.run(
            self.fixture.root,
            self.fixture.active["identity"],
            lambda out: calls.append(out) or {"exit_code": 0},
            reuse_allowed=False,
            reason="fresh_independent_phase",
            current_identity=lambda: self.fixture.active["identity"],
        )
        self.assertEqual("execute", result["scheduling"]["action"])
        self.assertEqual(1, len(calls))
        self.assertEqual(before, path.read_bytes())

    def test_original_nonzero_collected_exit_is_preserved_without_claiming_pass(self):
        self.fixture.original_receipt(exit_code=1)
        path = self.fixture.out / "receipt.json"
        before = path.read_bytes()
        self.assertEqual("published_existing_completed_receipt", self.fixture.recover()["disposition"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(1, util.read(path)["result"]["exit_code"])
        self.assertFalse(schedule.reusable(util.read(path)["result"]))

    def test_unpublished_original_receipt_conflicts_and_incomplete_results_remain_held(self):
        saved = self.fixture.original_receipt()
        path = self.fixture.out / "receipt.json"
        changes = [
            ("attempt_id", "d" * 32),
            ("identity", {}),
            ("started_at", "other"),
            ("finished_at", None),
            ("reason", None),
            ("runner_owned", False),
        ]
        result_changes = [
            ("command", "other"),
            ("output", str(self.fixture.root / "other.log")),
            ("output_sha256", "f" * 64),
            ("exit_code", None),
            ("exit_code", True),
            ("error", "unfinished"),
            ("timed_out", True),
            ("interrupted", True),
            ("supervision", {}),
            ("supervision_sha256", "f" * 64),
            ("supervision_errors", ["unknown"]),
        ]
        for container, key, value in [(None, *row) for row in changes] + [("result", *row) for row in result_changes]:
            with self.subTest(container=container, key=key, value=value):
                changed = deepcopy(saved)
                (changed[container] if container else changed)[key] = value
                util.atomic_json(path, changed)
                before, pending = path.read_bytes(), self.fixture.pending.read_bytes()
                with self.assertRaises(receipts.OwnershipUncertain):
                    self.fixture.recover()
                self.assertEqual(before, path.read_bytes())
                self.assertEqual(pending, self.fixture.pending.read_bytes())
                self.assertFalse((path.parent.parent / "completed.json").exists())

    def test_original_receipt_and_ownership_pins_changed_before_publication_keep_hold(self):
        for target_name in ("receipt", "ownership", "output"):
            with self.subTest(target=target_name):
                self.fixture.write()
                self.fixture.original_receipt()
                path = self.fixture.out / "receipt.json"
                target = {"receipt": path, "ownership": self.fixture.path, "output": self.fixture.output}[target_name]
                actual = recovery.read_regular

                def change(filename, root):
                    result = actual(filename, root)
                    if Path(filename) == path:
                        target.write_text("{}")
                    return result

                pending = self.fixture.pending.read_bytes()
                with (
                    patch.object(recovery, "read_regular", side_effect=change),
                    self.assertRaises(receipts.OwnershipUncertain),
                ):
                    self.fixture.recover()
                self.assertEqual(pending, self.fixture.pending.read_bytes())
                self.assertFalse((path.parent.parent / "completed.json").exists())

    def test_crash_after_original_pointer_before_unlink_preserves_original_receipt(self):
        self.fixture.original_receipt()
        path = self.fixture.out / "receipt.json"
        before = path.read_bytes()
        unlink = Path.unlink

        def crash(path, *args, **kwargs):
            if path == self.fixture.pending:
                raise OSError("retirement interrupted")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", crash), self.assertRaises(receipts.OwnershipUncertain):
            self.fixture.recover()
        pointer = path.parent.parent / "completed.json"
        pointer_before = pointer.read_bytes()
        self.assertEqual("existing_completed_receipt", self.fixture.recover()["disposition"])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(pointer_before, pointer.read_bytes())
        self.assertFalse(self.fixture.pending.exists())

    def test_completed_pointer_cannot_claim_current_attempt_from_a_different_directory(self):
        other = self.fixture.out.parent / ("e" * 32) / "receipt.json"
        util.atomic_json(
            other,
            {
                "runner_owned": True,
                "attempt_id": self.fixture.active["attempt_id"],
                "identity": self.fixture.active["identity"],
                "started_at": "original",
                "finished_at": "collected",
                "result": {"exit_code": 0},
            },
        )
        pointer = self.fixture.out.parent / "completed.json"
        util.atomic_json(pointer, {"name": str(other.relative_to(pointer.parent)), "sha256": util.file_hash(other)})
        self.assert_held()

    def test_wrapped_actual_command_is_authenticated_without_matching_the_configured_command(self):
        self.fixture.check["command"] = '/bin/sh -c "node --test --test-reporter=spec scratch/test.cjs"'
        self.fixture.admission["command"] = self.fixture.check["command"]
        self.fixture.write()
        recovered = self.fixture.recover()
        self.assertEqual(self.fixture.check["command"], util.read(recovered["receipt"])["result"]["command"])

    def test_crash_after_failed_receipt_before_pointer_preserves_immutable_receipt(self):
        actual = util.atomic_json

        def crash(path, data):
            if Path(path).name == "completed.json":
                raise OSError("publication interrupted")
            actual(path, data)

        with patch.object(util, "atomic_json", side_effect=crash), self.assertRaises(receipts.OwnershipUncertain):
            self.fixture.recover()
        path = self.fixture.out / "receipt.json"
        retained = path.read_bytes()
        self.assertTrue(self.fixture.pending.exists())
        self.fixture.recover()
        self.assertEqual(retained, path.read_bytes())
        self.assertFalse(self.fixture.pending.exists())

    def test_crash_after_pointer_before_unlink_reconciles_exact_failed_publication(self):
        unlink = Path.unlink

        def crash(path, *args, **kwargs):
            if path == self.fixture.pending:
                raise OSError("retirement interrupted")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", crash), self.assertRaises(receipts.OwnershipUncertain):
            self.fixture.recover()
        path = self.fixture.out / "receipt.json"
        retained = path.read_bytes()
        self.assertTrue(self.fixture.pending.exists())
        self.fixture.recover()
        self.assertEqual(retained, path.read_bytes())
        self.assertFalse(self.fixture.pending.exists())

    def test_unfinished_uncertain_failed_or_mismatched_terminal_ownership_stays_held(self):
        for change in (
            {"phase": "armed"},
            {"phase": "stopping"},
            {"phase": "uncertain"},
            {"cleanup_error": "keeper failed"},
            {"nonce": "d" * 32},
            {"processes": "unknown"},
        ):
            with self.subTest(change=change):
                self.fixture.terminal = {**self.fixture.terminal, **change}
                util.atomic_json(self.fixture.path, self.fixture.terminal)
                self.assert_held()
                self.fixture.terminal = {
                    **self.fixture.metadata,
                    "phase": "stopped",
                    "cause": "provider_stopped",
                    "cleanup_error": None,
                    "observed_at": "fixture",
                    "processes": [self.fixture.metadata["provider"]],
                }

    def test_every_owned_identity_must_be_absent_and_denied_inspection_keeps_hold(self):
        for pid in (2147480001, 2147480002, 2147480003, 2147480004):
            with self.subTest(pid=pid):
                self.observe.return_value = [{"pid": pid}]
                self.assert_held()
                rows = self.observe.call_args.args[0]
                self.assertIn(pid, [r["pid"] for r in rows])
        for error in (processes.ProcessError("denied"), SystemError("native unknown"), OverflowError("invalid PID")):
            with self.subTest(error=type(error).__name__):
                self.observe.side_effect = error
                self.assert_held()

    def test_unknown_runner_inventory_keeps_hold_even_when_supervision_is_terminal(self):
        for rows in (
            None,
            "unknown",
            [{}],
            [{"pid": -1, "birth_identity": 1}],
            [{"pid": 2147480001, "birth_identity": float("nan")}],
            [{"pid": 2147480001}],
        ):
            with self.subTest(rows=rows):
                self.fixture.check["processes"] = rows
                self.assert_held()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX named pipes")
    def test_nonregular_completed_pointer_target_is_rejected_before_reading(self):
        other = self.fixture.out.parent / ("e" * 32) / "receipt.json"
        other.parent.mkdir()
        os.mkfifo(other)
        util.atomic_json(
            self.fixture.out.parent / "completed.json",
            {"name": str(other.relative_to(self.fixture.out.parent)), "sha256": "f" * 64},
        )
        # No reader of the FIFO may run: rejection must happen at the type guard.
        with patch.object(util, "file_hash", wraps=util.file_hash) as hashing:
            self.assert_held()
        self.assertNotIn(other, [Path(call.args[0]) for call in hashing.call_args_list])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX named pipes")
    def test_nonregular_existing_writer_lock_is_rejected_before_opening(self):
        lock = self.fixture.root / "writer.lock"
        os.mkfifo(lock)
        with patch.object(util, "run_lock", wraps=util.run_lock) as locking:
            self.assert_held()
        locking.assert_not_called()

    def test_symlinked_completed_reference_and_parent_are_rejected(self):
        other = self.fixture.out.parent / ("e" * 32) / "receipt.json"
        other.parent.mkdir()
        other.symlink_to(self.fixture.output)
        pointer = self.fixture.out.parent / "completed.json"
        util.atomic_json(
            pointer, {"name": str(other.relative_to(pointer.parent)), "sha256": util.file_hash(self.fixture.output)}
        )
        self.assert_held()
        other.unlink()
        parent = other.parent
        parent.rmdir()
        parent.symlink_to(self.fixture.out, target_is_directory=True)
        self.assert_held()

    def test_missing_or_corrupt_ownership_and_runner_check_never_retire_pending(self):
        for payload in (b"not json", b"{}"):
            with self.subTest(payload=payload):
                self.fixture.path.write_bytes(payload)
                self.assert_held()
        self.fixture.write()
        self.fixture.path.unlink()
        self.assert_held()
        self.fixture.write()
        self.fixture.check = None
        self.assert_held()

    def test_malformed_pending_context_and_attempt_stay_held(self):
        for change in ({"identity": None}, {"attempt_id": "../other"}, {"attempt_id": "unknown"}, {"started_at": None}):
            with self.subTest(change=change):
                util.atomic_json(self.fixture.pending, {**self.fixture.active, **change})
                self.assert_held()
        self.fixture.pending.write_text("not JSON")
        self.assert_held()

    def test_admission_must_bind_exact_output_command_cwd_and_supervision(self):
        for change in (
            {"command": "another command"},
            {"output": str(self.fixture.root / "other.log")},
            {"cwd": str(self.fixture.root)},
            {"supervision": {}},
            {"schema": 2},
        ):
            with self.subTest(change=change):
                util.atomic_json(self.fixture.admission_path, {**self.fixture.admission, **change})
                self.assert_held()

    def test_other_attempt_output_does_not_bind_the_pending_execution(self):
        other = self.fixture.root / "other.log"
        other.write_text("other")
        self.fixture.check["output"] = str(other)
        self.assert_held()

    def test_symlinked_owned_files_remain_held(self):
        for name in ("pending", "path", "admission_path", "output"):
            with self.subTest(path=name):
                path = getattr(self.fixture, name)
                target = path.with_name(path.name + ".retained")
                path.rename(target)
                path.symlink_to(target)
                self.assert_held()
                path.unlink()
                target.rename(path)

    def test_corrupt_existing_pointer_or_conflicting_attempt_receipt_remains_held(self):
        pointer = self.fixture.out.parent / "completed.json"
        pointer.write_text("{}")
        self.assert_held()
        pointer.unlink()
        (self.fixture.out / "receipt.json").write_text("{}")
        before = self.fixture.pending.read_bytes()
        with self.assertRaises(receipts.OwnershipUncertain):
            self.fixture.recover()
        self.assertEqual(before, self.fixture.pending.read_bytes())
        self.assertEqual("{}", (self.fixture.out / "receipt.json").read_text())

    def test_ownership_changed_during_native_check_is_rejected(self):
        def change(_rows):
            self.fixture.path.write_text("{}")
            return []

        self.observe.side_effect = change
        self.assert_held()

    def test_model_attempt_must_be_set_aside_before_runner_recovery(self):
        state = {"active_stage": {"stage": "sol"}, "active_runner_check": self.fixture.check}
        with self.assertRaisesRegex(receipts.OwnershipUncertain, "Abandon the exact"):
            runner_check.recover_interrupted(state, self.fixture.root.parent, lambda *_: None)
        self.assertTrue(self.fixture.pending.exists())


class PublicResumeRegression(unittest.TestCase):
    def test_public_taskrun_resume_after_model_abandonment_retires_failed_check(self):
        self.assert_public_resume("PAUSED_VERIFICATION_UNCERTAIN")

    def test_public_taskrun_first_resume_from_stage_abandonment_retires_failed_check(self):
        self.assert_public_resume("PAUSED_STAGE_ABANDONED")

    def test_public_taskrun_resume_publishes_original_receipt_without_rewriting_result(self):
        self.assert_public_resume("PAUSED_VERIFICATION_UNCERTAIN", original_completed=True)

    def assert_public_resume(self, initial_status, *, original_completed=False):
        # The fixture starts after legitimate model abandonment. Only the final
        # build loop is stopped to avoid a provider; real CLI recovery and its
        # pre-review verification guard run through the public TaskRun API.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve() / "project"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@test.invalid",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "fixture",
            ],
            check=True,
        )
        run_dir = workspace / ".autocode/runs/recovered"
        run_dir.mkdir(parents=True)
        fixture = AdmissionFixture(run_dir / "check-replay/obligations")
        original = None
        if original_completed:
            fixture.original_receipt()
            original = (fixture.out / "receipt.json").read_bytes()
        state = {
            "version": 3,
            "task": "Retain the saved task",
            "task_id": "owned-task",
            "workspace": str(workspace),
            "project_workspace": str(workspace),
            "run_dir": str(run_dir),
            "status": initial_status,
            "next_stage": "sol",
            "iteration": 2,
            "sessions": {},
            "stages": [],
            "active_runner_check": fixture.check,
            "settings": {
                "engine": "codex",
                "roles": {
                    role: {"model": model, "reasoning_effort": "high"}
                    for role, model in configure.DEFAULT_ROLE_MODELS.items()
                },
            },
        }
        util.atomic_json(run_dir / "state.json", state)
        tools = Path(__file__).resolve().parents[1] / "tools"
        wrapper = Path(temporary.name).resolve() / "fixture_cli.py"
        wrapper.write_text(
            "import sys\nfrom pathlib import Path\nsys.path.insert(0, " + repr(str(tools)) + ")\n"
            "import autocode, autocode_build_loop, autocode_regression\n"
            "def stopped(runner,args,state,state_path,run_dir,workspace):\n"
            "    autocode_regression.before_review(state,None,workspace,run_dir)\n"
            '    state.update(status="PAUSED_REQUESTED",stop_reason="Fixture boundary before provider")\n'
            "    runner.write_json(state_path,state)\n    return 2\n"
            "autocode_build_loop.run=stopped\nraise SystemExit(autocode.main())\n"
        )
        run = taskrun.TaskRun(
            workspace,
            run_dir,
            command=(sys.executable, str(wrapper)),
            timeout=30,
            env={"AUTOCODE_HOME": str(Path(temporary.name).resolve() / "registry")},
        )
        before = fixture.pending.read_bytes()
        self.assertEqual(initial_status, run.status()["status"])
        self.assertEqual(before, fixture.pending.read_bytes(), "Status cannot reconcile a pending check")
        view = run.resume_paused()
        self.assertEqual(2, run.last_advance.returncode)
        self.assertEqual("PAUSED_REQUESTED", view["status"], view)
        self.assertIsNone(view["runner_check"])
        self.assertIsNone(view["evidence"]["regression_proof"])
        self.assertFalse(fixture.pending.exists())
        pointer = util.read(fixture.out.parent / "completed.json")
        failed = util.read(fixture.out.parent / pointer["name"])
        if original_completed:
            self.assertEqual(original, (fixture.out / "receipt.json").read_bytes())
            self.assertEqual(0, failed["result"]["exit_code"])
            self.assertNotIn("interrupted", failed["result"])
            self.assertTrue(receipts.completed(failed["result"]))
        else:
            self.assertTrue(failed["result"]["interrupted"])
            self.assertIsNone(failed["result"]["exit_code"])
            self.assertFalse(receipts.completed(failed["result"]))
