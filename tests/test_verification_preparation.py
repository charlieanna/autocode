"""Exact preparation custody and a read-only public current-obligation frontier."""

import json
import os
import re
import shlex
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
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_verification_preparation as preparation
import autocode_verification_prepare_worker as prepare_worker
import autocode_verification_recovery as recovery
import autocode_verification_schedule as schedule
import autocode_verify as verify

from .test_verification_recovery import AdmissionFixture


class PreparationFixture(AdmissionFixture):
    def __init__(self, root):
        super().__init__(root)
        self.worker = root.parent / "autocode_verification_prepare_worker.py"
        self.worker.write_text("# Immutable owned fixture worker\n")
        self.prep_path = self.out / "preparation-admission.json"
        self.prep = {
            "schema": 1,
            "attempt_id": self.active["attempt_id"],
            "identity": util.digest(self.active["identity"]),
            "started_at": self.active["started_at"],
            "nonce": "d" * 32,
            "out": str(self.out),
            "owner": self.metadata["owner"],
            "worker": str(self.worker),
            "python": sys.executable,
            "worker_sha256": util.file_hash(self.worker),
        }
        util.atomic_json(self.prep_path, self.prep)
        reference = {"path": str(self.prep_path), "sha256": util.file_hash(self.prep_path)}
        self.request_path = self.out / ("preparation-" + "e" * 32 + ".json")
        self.request = {
            "schema": 1,
            "operation": "prepare",
            "workspace": str(root.parent),
            "out": str(self.out),
            "parameters": {},
            "result": str(self.out / "preparation-result.json"),
            "preparation_admission": reference,
            "nonce": self.prep["nonce"],
        }
        util.atomic_json(self.request_path, self.request)
        request_sha = util.file_hash(self.request_path)
        self.check.update(
            phase="preparing",
            request=str(self.request_path),
            request_sha256=request_sha,
            command=shlex.join(
                [sys.executable, str(self.worker), "--request", str(self.request_path), "--request-sha256", request_sha]
            ),
        )
        self.admission.update(command=self.check["command"], cwd=self.request["workspace"])
        self.active.update(preparation_admission=reference, phase="preparing", current_check=deepcopy(self.check))
        self.write()


class PublicationFixture(PreparationFixture):
    """A collected inner command beneath an authenticated outer worker."""

    def __init__(self, root):
        super().__init__(root)
        self.request["operation"] = "execute"
        util.atomic_json(self.request_path, self.request)
        request_sha = util.file_hash(self.request_path)
        self.check.update(
            phase="executing",
            request_sha256=request_sha,
            command=shlex.join(
                [sys.executable, str(self.worker), "--request", str(self.request_path), "--request-sha256", request_sha]
            ),
        )
        self.admission["command"] = self.check["command"]
        self.inner_path = self.out / "command-supervision" / ("f" * 32) / "supervision.json"
        self.inner_path.parent.mkdir(parents=True)
        self.inner_metadata = {
            **self.metadata,
            "receipt": str(self.inner_path),
            "nonce": "1" * 32,
            "owner": self.terminal["processes"][-1],
            "keeper": {"pid": 2147480005, "birth_identity": 5},
            "provider": {"pid": 2147480006, "birth_identity": 6},
        }
        self.inner_terminal = {
            **self.inner_metadata,
            "phase": "stopped",
            "cause": "provider_stopped",
            "cleanup_error": None,
            "observed_at": "2026-10-08T00:54:01Z",
            "processes": [self.inner_metadata["provider"]],
        }
        util.atomic_json(self.inner_path, self.inner_terminal)
        self.inner_output = self.out / "inner-test.log"
        self.inner_output.write_text("actual inner capture\n")
        self.result = {
            "command": "node --test inner.cjs",
            "output": str(self.inner_output),
            "output_sha256": util.file_hash(self.inner_output),
            "exit_code": 0,
            "timed_out": False,
            "error": "",
            "supervision": self.inner_metadata,
            "supervision_sha256": util.file_hash(self.inner_path),
            "supervision_errors": [],
        }
        util.atomic_json(
            self.inner_path.with_name("admission.json"),
            {
                "schema": 1,
                "command": self.result["command"],
                "cwd": str(self.out / "scratch/tree"),
                "output": str(self.inner_output),
                "supervision": self.inner_metadata,
            },
        )
        self.answer = {
            "operation": "execute",
            "request_sha256": request_sha,
            "nonce": self.prep["nonce"],
            "receipt": self.result,
        }
        util.atomic_json(self.request["result"], self.answer)
        self.saved = {
            "runner_owned": True,
            "attempt_id": self.active["attempt_id"],
            "identity": self.active["identity"],
            "started_at": self.active["started_at"],
            "finished_at": "2026-10-08T00:55:01Z",
            "reason": "mandatory_approved_execution",
            "result": self.result,
        }
        util.atomic_json(self.out / "receipt.json", self.saved)
        self.active.update(phase="post_execution_identity", current_check=None, completed_check=deepcopy(self.check))
        self.write()


class PreparationCustodyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = PreparationFixture(Path(temporary.name).resolve() / "obligations")
        native = patch.object(processes, "live_processes", return_value=[])
        self.observed = native.start()
        self.addCleanup(native.stop)

    def held(self):
        before = self.fixture.pending.read_bytes()
        with self.assertRaises(receipts.OwnershipUncertain):
            self.fixture.recover()
        self.assertEqual(before, self.fixture.pending.read_bytes())
        self.assertFalse((self.fixture.out / "receipt.json").exists())

    def test_exact_current_preparation_recovery_ignores_an_unrelated_saved_check(self):
        stale = deepcopy(self.fixture.check)
        stale["output"] = str(self.fixture.root / "unrelated.log")
        fixed = schedule.recover_interrupted(self.fixture.root, stale)
        saved = util.read(fixed["receipt"])
        self.assertEqual("interrupted_receipt", fixed["disposition"])
        self.assertEqual(self.fixture.active, saved["pending_admission"])
        self.assertIsNone(saved["result"]["exit_code"])
        self.assertIsNone(saved["result"]["duration_seconds"])
        self.assertFalse(schedule.reusable(saved["result"]))
        self.assertFalse(self.fixture.pending.exists())
        identities = self.observed.call_args.args[0]
        for required in ("owner", "keeper", "provider"):
            self.assertIn(self.fixture.metadata[required], identities)
        self.assertIn(self.fixture.terminal["processes"][-1], identities)

    def test_frontier_authenticates_request_above_receipt_limit(self):
        import autocode_launch_inputs as launch_inputs

        inputs = launch_inputs.Supply(
            self.fixture.root.parent, self.fixture.root.parent / "store", {}, {}, [], ["x" * (5 * 1024 * 1024)]
        )
        self.fixture.request["parameters"]["ignored_inputs"] = inputs.to_transport()
        preparation._publish_request(self.fixture.request_path, self.fixture.request)
        self.assertGreater(self.fixture.request_path.stat().st_size, receipts.MAX_RECEIPT_BYTES)
        request_sha = util.file_hash(self.fixture.request_path)
        command = shlex.join(
            [
                sys.executable,
                str(self.fixture.worker),
                "--request",
                str(self.fixture.request_path),
                "--request-sha256",
                request_sha,
            ]
        )
        self.fixture.check.update(request_sha256=request_sha, command=command)
        self.fixture.admission["command"] = command
        self.fixture.active["current_check"] = deepcopy(self.fixture.check)
        self.fixture.write()
        with patch.object(preparation.supervision, "observe", return_value={"kind": "stopped"}):
            current = preparation.frontier(self.fixture.root)
        self.assertEqual("preparing", current["phase"])
        self.assertEqual(request_sha, current["current_check"]["request_sha256"])
        self.assertFalse(current["recovery_authorized"])
        self.fixture.recover()
        self.assertFalse(self.fixture.pending.exists())

    def test_oversized_envelope_rejects_before_request_publication_or_native_launch(self):
        before = {path.name: util.file_hash(path) for path in self.fixture.out.iterdir() if path.is_file()}
        reference = self.fixture.active["preparation_admission"]
        with preparation.admitted(reference), patch.object(preparation.commands, "run") as run:
            with self.assertRaisesRegex(ValueError, "12 MiB envelope bound"):
                preparation.launch(
                    "execute",
                    self.fixture.root.parent,
                    self.fixture.out,
                    {"oversized": "x" * recovery.MAX_PREPARATION_REQUEST_BYTES},
                    timeout=30,
                    env={},
                )
        self.assertFalse(run.called)
        self.assertEqual(
            before, {path.name: util.file_hash(path) for path in self.fixture.out.iterdir() if path.is_file()}
        )

    def test_phase_without_exact_keeper_custody_stays_held(self):
        for phase in ("admitted", "post_execution_identity", "unknown"):
            with self.subTest(phase=phase):
                self.fixture.active["phase"] = phase
                self.fixture.write()
                self.held()

    def test_owner_request_worker_output_and_nonce_changes_stay_held(self):
        original = self.fixture.pending.read_bytes()
        for field, value in (
            ("owner", {"pid": 12, "birth_identity": 1}),
            ("nonce", "bad"),
            ("identity", "other"),
            ("out", str(self.fixture.root)),
        ):
            with self.subTest(field=field):
                util.atomic_json(self.fixture.prep_path, {**self.fixture.prep, field: value})
                self.held()
                util.atomic_json(self.fixture.prep_path, self.fixture.prep)
        self.fixture.worker.write_text("# changed worker\n")
        self.held()
        self.assertEqual(original, self.fixture.pending.read_bytes())

    def test_current_request_pin_or_pending_command_mismatch_stays_held(self):
        util.atomic_json(self.fixture.request_path, {**self.fixture.request, "operation": "remove"})
        self.held()
        util.atomic_json(self.fixture.request_path, self.fixture.request)
        self.fixture.active["current_check"]["command"] = "another command"
        self.fixture.write()
        self.held()

    def test_live_native_identity_or_unfinished_keeper_stays_held(self):
        self.observed.return_value = [self.fixture.metadata["provider"]]
        self.held()
        self.observed.return_value = []
        self.fixture.terminal["phase"] = "armed"
        self.fixture.write()
        self.held()

    def test_unknown_native_inspection_stays_held(self):
        self.observed.side_effect = processes.ProcessError("denied")
        self.held()

    def test_symlink_and_fifo_request_fail_before_native_inspection(self):
        p = self.fixture.request_path
        retained = p.with_suffix(".retained")
        p.rename(retained)
        p.symlink_to(retained)
        self.held()
        self.assertFalse(self.observed.called)
        p.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(p)
            self.held()
            self.assertFalse(self.observed.called)
            p.unlink()
        retained.rename(p)

    def test_crash_after_receipt_publication_recovers_without_rewriting(self):
        real = util.atomic_json

        def crash(path, value):
            if Path(path).name == "completed.json":
                raise RuntimeError("fake publication barrier")
            return real(path, value)

        with patch.object(util, "atomic_json", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.fixture.recover()
        path = self.fixture.out / "receipt.json"
        before = path.read_bytes()
        self.fixture.recover()
        self.assertEqual(before, path.read_bytes())
        self.assertFalse(self.fixture.pending.exists())

    def test_public_frontier_names_current_obligation_and_never_reconciles(self):
        before = self.fixture.pending.read_bytes()
        with patch.object(preparation.supervision, "observe", return_value={"kind": "stopped"}):
            current = preparation.frontier(self.fixture.root, {"output": "older obligation"})
        view = run_view.view({"status": "PAUSED_VERIFICATION_UNCERTAIN"}, verification_obligation=current)
        self.assertEqual(self.fixture.active["attempt_id"], view["verification_obligation"]["attempt_id"])
        self.assertEqual("preparing", current["phase"])
        self.assertFalse(current["runner_check_matches_current"])
        self.assertFalse(current["recovery_authorized"])
        self.assertEqual(before, self.fixture.pending.read_bytes())

    def test_legacy_missing_directory_is_exposed_and_stays_held(self):
        legacy = {"attempt_id": "f" * 32, "identity": {"different": "obligation"}, "started_at": "earlier"}
        util.atomic_json(self.fixture.pending, legacy)
        current = preparation.frontier(self.fixture.root, self.fixture.check)
        self.assertEqual("legacy_unbound", current["phase"])
        self.assertEqual(legacy["attempt_id"], current["attempt_id"])
        self.held()

    def test_corrupt_frontier_is_unavailable_without_mutation(self):
        self.fixture.pending.write_text("not JSON")
        self.assertEqual("unavailable", preparation.frontier(self.fixture.root)["phase"])
        self.assertEqual("not JSON", self.fixture.pending.read_text())

    def test_initial_admission_cannot_use_a_stale_saved_command(self):
        self.fixture.active.pop("current_check")
        self.fixture.active["phase"] = "admitted"
        self.fixture.write()
        self.held()

    def test_frontier_rejects_path_components_before_any_referenced_read(self):
        for attempt in ("../foreign", "/tmp/foreign", "a" * 31, 1):
            with self.subTest(attempt=attempt):
                self.fixture.active["attempt_id"] = attempt
                self.fixture.write()
                with (
                    patch.object(recovery, "read_regular", wraps=recovery.read_regular) as reads,
                    patch.object(preparation.supervision, "observe") as observe,
                ):
                    self.assertEqual("unavailable", preparation.frontier(self.fixture.root)["phase"])
                self.assertEqual(1, reads.call_count)
                self.assertFalse(observe.called)

    def test_frontier_authenticates_reference_schema_time_nonce_and_owner(self):
        original = deepcopy(self.fixture.active)
        for field, value in (
            ("schema", True),
            ("started_at", "foreign time"),
            ("nonce", 7),
            ("owner", {"pid": True, "birth_identity": 1}),
        ):
            with self.subTest(field=field):
                altered = {**self.fixture.prep, field: value}
                util.atomic_json(self.fixture.prep_path, altered)
                self.fixture.active = deepcopy(original)
                self.fixture.active["preparation_admission"]["sha256"] = util.file_hash(self.fixture.prep_path)
                self.fixture.write()
                with patch.object(preparation.supervision, "observe") as observe:
                    self.assertEqual("unavailable", preparation.frontier(self.fixture.root)["phase"])
                self.assertFalse(observe.called)
        self.fixture.active = deepcopy(original)
        util.atomic_json(self.fixture.prep_path, self.fixture.prep)
        self.fixture.active["preparation_admission"]["path"] = str(self.fixture.root / "foreign-admission.json")
        self.fixture.write()
        with patch.object(recovery, "read_regular", wraps=recovery.read_regular) as reads:
            self.assertEqual("unavailable", preparation.frontier(self.fixture.root)["phase"])
        self.assertEqual(1, reads.call_count)

    def test_frontier_rejects_copied_or_mismatched_current_custody_before_liveness(self):
        original = deepcopy(self.fixture.active)
        changes = (
            ("phase", "removing"),
            ("supervision", {**self.fixture.metadata, "owner": {"pid": 9, "birth_identity": 9}}),
            ("output", str(self.fixture.root / "foreign.log")),
            ("supervision", {**self.fixture.metadata, "receipt": str(self.fixture.root / "foreign.json")}),
        )
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.fixture.active = deepcopy(original)
                self.fixture.active["current_check"][field] = value
                self.fixture.write()
                with patch.object(preparation.supervision, "observe") as observe:
                    self.assertEqual("unavailable", preparation.frontier(self.fixture.root)["phase"])
                self.assertFalse(observe.called)

    def test_frontier_can_observe_a_genuinely_live_admitted_worker(self):
        self.fixture.terminal.update(phase="armed", cause=None)
        self.fixture.write()
        with patch.object(preparation.supervision, "observe", return_value={"kind": "supervised"}) as observe:
            current = preparation.frontier(self.fixture.root)
        self.assertEqual("supervised", current["liveness"]["kind"])
        observe.assert_called_once_with(self.fixture.metadata)
        self.assertFalse(self.observed.called)

    def test_idle_phase_does_not_borrow_completed_worker_liveness(self):
        self.fixture.active.update(
            phase="post_execution_identity", current_check=None, completed_check=deepcopy(self.fixture.check)
        )
        self.fixture.write()
        with patch.object(preparation.supervision, "observe") as observe:
            current = preparation.frontier(self.fixture.root, self.fixture.check)
        self.assertEqual("post_execution_identity", current["phase"])
        self.assertIsNone(current["current_check"])
        self.assertNotIn("liveness", current)
        self.assertFalse(observe.called)
        self.held()


class PreparationReaderBoundsTests(unittest.TestCase):
    def test_default_receipt_and_explicit_request_limits_remain_separate_and_bounded(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        path = root / "request.json"
        value = {"notes": "x" * (5 * 1024 * 1024)}
        preparation._publish_request(path, value)
        with self.assertRaisesRegex(receipts.OwnershipUncertain, "reader limit"):
            recovery.read_regular(path, root)
        loaded, pin = recovery.read_regular(path, root, max_bytes=recovery.MAX_PREPARATION_REQUEST_BYTES)
        self.assertEqual(value, loaded)
        self.assertEqual(util.file_hash(path), pin)
        for limit in (True, False, 0, -1, 1.5, recovery.MAX_PREPARATION_REQUEST_BYTES + 1):
            with self.subTest(limit=limit), patch.object(recovery, "owned_path") as read:
                with self.assertRaisesRegex(receipts.OwnershipUncertain, "reader limit is invalid"):
                    recovery.read_regular(path, root, max_bytes=limit)
                self.assertFalse(read.called)
        path.write_bytes(b" " * (recovery.MAX_PREPARATION_REQUEST_BYTES + 1))
        with self.assertRaisesRegex(receipts.OwnershipUncertain, "reader limit"):
            recovery.read_regular(path, root, max_bytes=recovery.MAX_PREPARATION_REQUEST_BYTES)


class OwnedPreparationExecutionTests(unittest.TestCase):
    def _scheduled_supply(self, notes):
        import hashlib

        import autocode_launch_inputs as launch_inputs

        from .test_verify import Project

        project = Project(
            {
                ".gitignore": "_version.py\nvendor/\n",
                "app.py": "from _version import VALUE\nfrom vendor.library import OFFSET\ndef answer(): return VALUE + OFFSET\n",
                "test_app.py": "import unittest\nimport app\nclass T(unittest.TestCase):\n"
                "    def test_answer(self): self.assertEqual(app.answer(),42)\n",
            }
        )
        self.addCleanup(project.close)
        generated, vendored = b"VALUE = 41\n", b"OFFSET = 1\n"
        (project.root / "_version.py").write_bytes(generated)
        (project.root / "vendor").mkdir()
        (project.root / "vendor/library.py").write_bytes(vendored)
        for path in (project.root / "_version.py", project.root / "vendor/library.py"):
            path.chmod(0o644)
        store = project.evidence / "launch-sources"
        store.mkdir(parents=True)
        generated_hash = hashlib.sha256(generated).hexdigest()
        (store / generated_hash).write_bytes(generated)
        inputs = launch_inputs.Supply(
            project.root,
            store,
            {"_version.py": [generated_hash, 0o644]},
            {"vendor/library.py": [hashlib.sha256(vendored).hexdigest(), 0o644]},
            [],
            notes,
            recorded=True,
        )
        identity = {"fixture": "transported Supply", "inputs": inputs.identity}
        result = schedule.run(
            project.evidence / "obligations",
            identity,
            lambda out: verify.scratch_run(
                project.root,
                out,
                command=shlex.join([sys.executable, "-m", "unittest", "-v", "test_app"]),
                timeout=30,
                ignored_inputs=inputs,
            ),
            reuse_allowed=False,
            reason="fresh",
            current_identity=lambda: identity,
            owned_preparation=True,
        )
        self.assertEqual(0, result["exit_code"], result)
        self.assertEqual(1, result["results"]["total"])
        out = Path(result["scheduling"]["receipt"]).parent
        requests = [
            util.read(path)
            for path in out.glob("preparation-*.json")
            if re.fullmatch(r"preparation-[0-9a-f]{32}\.json", path.name)
        ]
        self.assertEqual(1, len(requests))
        self.assertEqual(inputs.to_transport(), requests[0]["parameters"]["ignored_inputs"])
        self.assertFalse((out / "scratch/tree").exists())
        return inputs, requests[0], out

    def test_scheduled_worker_restores_non_none_supply_with_ignored_source_and_vendor(self):
        self._scheduled_supply(["Unlisted added input omitted"])

    def test_scheduled_worker_restores_valid_supply_larger_than_receipt_reader_limit(self):
        inputs, request, out = self._scheduled_supply(["x" * (5 * 1024 * 1024)])
        wire = json.dumps(inputs.to_transport(), sort_keys=True).encode()
        self.assertGreater(len(wire), receipts.MAX_RECEIPT_BYTES)
        self.assertLessEqual(len(wire), 8 * 1024 * 1024)
        paths = [
            path
            for path in out.glob("preparation-*.json")
            if re.fullmatch(r"preparation-[0-9a-f]{32}\.json", path.name)
        ]
        self.assertEqual(1, len(paths))
        raw = paths[0].read_bytes()
        self.assertGreater(len(raw), receipts.MAX_RECEIPT_BYTES)
        self.assertLessEqual(len(raw), recovery.MAX_PREPARATION_REQUEST_BYTES)
        self.assertEqual((json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode(), raw)

    def test_fake_barrier_after_admission_before_pending_launches_no_child(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / "obligations"
        calls = []
        original = util.atomic_json

        def barrier(path, value):
            if Path(path).name == "pending.json":
                raise receipts.OwnershipUncertain("fake before-pending barrier")
            return original(path, value)

        with (
            patch.object(processes, "process_table", return_value={os.getpid(): {"pid": 7, "birth_identity": 1}}),
            patch.object(processes, "identity", return_value={"pid": 7, "birth_identity": 1}),
            patch.object(util, "atomic_json", side_effect=barrier),
        ):
            with self.assertRaises(receipts.OwnershipUncertain):
                schedule.run(
                    root,
                    {"fixture": "one"},
                    lambda out: calls.append(out),
                    reuse_allowed=False,
                    reason="fresh",
                    current_identity=lambda: {"fixture": "one"},
                    owned_preparation=True,
                )
        self.assertEqual([], calls)
        self.assertFalse((root / "pending.json").exists())
        self.assertEqual(1, len(list(root.glob("*/*/preparation-admission.json"))))

    def test_fake_barrier_after_pending_without_keeper_remains_held(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / "obligations"

        def barrier(out):
            raise receipts.OwnershipUncertain("fake before-custody barrier")

        with (
            patch.object(preparation, "allocate", wraps=preparation.allocate),
            patch.object(processes, "process_table", return_value={os.getpid(): {}}),
            patch.object(processes, "identity", return_value={"pid": 7, "birth_identity": 1}),
        ):
            with self.assertRaises(receipts.OwnershipUncertain):
                schedule.run(
                    root,
                    {"fixture": "two"},
                    barrier,
                    reuse_allowed=False,
                    reason="fresh",
                    current_identity=lambda: {"fixture": "two"},
                    owned_preparation=True,
                )
        active = util.read(root / "pending.json")
        before = (root / "pending.json").read_bytes()
        self.assertEqual("admitted", active["phase"])
        self.assertTrue((root / util.digest(active["identity"]) / active["attempt_id"]).is_dir())
        with self.assertRaises(receipts.OwnershipUncertain):
            schedule.recover_interrupted(root, {"old": "unrelated"})
        self.assertEqual(before, (root / "pending.json").read_bytes())

    def test_unsupervised_execute_cannot_supply_a_successful_scheduled_receipt(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / "obligations"
        with (
            patch.object(processes, "process_table", return_value={os.getpid(): {}}),
            patch.object(processes, "identity", return_value={"pid": 7, "birth_identity": 1}),
        ):
            with self.assertRaisesRegex(receipts.OwnershipUncertain, "never admitted"):
                schedule.run(
                    root,
                    {"fixture": "three"},
                    lambda out: {"exit_code": 0},
                    reuse_allowed=False,
                    reason="fresh",
                    current_identity=lambda: {"fixture": "three"},
                    owned_preparation=True,
                )
        self.assertFalse(list(root.glob("*/*/receipt.json")))
        self.assertTrue((root / "pending.json").exists())

    def test_scheduled_worker_prepares_tests_and_removes_a_real_scratch_tree(self):
        from .test_verify import Project

        project = Project(
            {
                "app.py": "def answer():\n    return 42\n",
                "test_app.py": "import unittest\nimport app\nclass T(unittest.TestCase):\n"
                "    def test_answer(self): self.assertEqual(app.answer(),42)\n",
            }
        )
        self.addCleanup(project.close)
        identity = {"command": "unittest", "source": "immutable fixture"}
        result = schedule.run(
            project.evidence / "obligations",
            identity,
            lambda out: verify.scratch_run(
                project.root, out, command=shlex.join([sys.executable, "-m", "unittest", "-v", "test_app"]), timeout=30
            ),
            reuse_allowed=False,
            reason="fresh",
            current_identity=lambda: identity,
            owned_preparation=True,
        )
        self.assertEqual(0, result["exit_code"], result)
        self.assertEqual(1, result["results"]["total"])
        out = Path(result["scheduling"]["receipt"]).parent
        self.assertFalse((out / "scratch/tree").exists())
        admission = util.read(out / "preparation-admission.json")
        self.assertEqual(util.digest(identity), admission["identity"])
        requests = [
            util.read(p)
            for p in out.glob("preparation-*.json")
            if re.fullmatch(r"preparation-[0-9a-f]{32}\.json", p.name)
        ]
        self.assertEqual(["execute"], [r["operation"] for r in requests])
        command_admissions = [util.read(p) for p in out.glob("command-supervision/*/admission.json")]
        worker_commands = [r for r in command_admissions if "autocode_verification_prepare_worker.py" in r["command"]]
        self.assertEqual(1, len(worker_commands))
        for r in worker_commands:
            self.assertTrue(r["supervision"]["keeper"])
            self.assertEqual(admission["owner"], r["supervision"]["owner"])

    def test_uncertain_test_preserves_scratch_and_never_starts_cleanup(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = PublicationFixture(Path(temporary.name).resolve() / "obligations")
        fixture.active.update(phase="executing", current_check=fixture.active.pop("completed_check"))
        fixture.write()
        (fixture.out / "receipt.json").unlink()
        tree = fixture.out / "scratch/tree"
        source = tree / "retained.py"
        source.write_text("retained source\n")
        before = fixture.pending.read_bytes()
        with (
            patch.object(preparation, "active", return_value=False),
            patch.object(verify, "prepare_scratch_tree", return_value=tree),
            patch.object(verify, "run_suite", side_effect=receipts.OwnershipUncertain("uncertain native test")),
            patch.object(verify, "remove_tree") as remove,
            patch.object(preparation, "launch") as launch,
        ):
            with self.assertRaises(receipts.OwnershipUncertain):
                verify.scratch_run(
                    fixture.root.parent,
                    fixture.out,
                    command=shlex.join([sys.executable, "-m", "unittest", "-v", "test_app"]),
                )
        self.assertFalse(remove.called)
        self.assertFalse(launch.called)
        self.assertEqual(before, fixture.pending.read_bytes())
        self.assertEqual("retained source\n", source.read_text())
        with patch.object(processes, "live_processes", return_value=[fixture.metadata["provider"]]):
            with self.assertRaisesRegex(receipts.OwnershipUncertain, "still alive"):
                fixture.recover()
        self.assertEqual(before, fixture.pending.read_bytes())

    def test_untrusted_symlinked_scheduler_parent_is_not_canonicalized_away(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        real = base / "real"
        real.mkdir()
        linked = base / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with patch.object(preparation, "allocate") as allocate:
            with self.assertRaises(util.Paused):
                schedule.run(
                    linked / "obligations",
                    {"fixture": "linked"},
                    lambda out: {"exit_code": 0},
                    reuse_allowed=False,
                    reason="fresh",
                    current_identity=lambda: {"fixture": "linked"},
                    owned_preparation=True,
                )
        self.assertFalse(allocate.called)
        self.assertFalse((real / "obligations").exists())

    def test_parent_construction_does_not_discover_environment_before_worker(self):
        with (
            patch.object(preparation, "active", return_value=True),
            patch.object(
                preparation, "launch", side_effect=receipts.OwnershipUncertain("owned worker stopped")
            ) as launch,
            patch.object(verify, "test_environment", side_effect=AssertionError("unowned discovery")),
        ):
            with self.assertRaises(receipts.OwnershipUncertain):
                verify.scratch_run("/tmp/workspace", "/tmp/evidence", command="node --test test.cjs")
        self.assertEqual(["execute"], [call.args[0] for call in launch.call_args_list])

    def test_slow_setup_does_not_reduce_the_configured_inner_timeout(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = PublicationFixture(Path(temporary.name).resolve() / "obligations")
        clock = {"seconds": 0}
        bounds = []

        def tree(*args, **kwargs):
            clock["seconds"] += 600
            return fixture.out / "scratch/tree"

        def inner(*args, **kwargs):
            bounds.append(("inner", kwargs["timeout"], clock["seconds"]))
            clock["seconds"] += 29
            return deepcopy(fixture.result)

        def outer(command, cwd, output, *, timeout, env):
            bounds.append(("outer", timeout, clock["seconds"]))
            words = shlex.split(command)
            with patch.object(sys, "argv", words[1:]), preparation.admitted(None):
                prepare_worker.main()
            return {"exit_code": 0, "timed_out": False, "error": ""}

        out = fixture.out
        fixture.prep.update(
            worker=str(Path(prepare_worker.__file__).resolve()), worker_sha256=util.file_hash(prepare_worker.__file__)
        )
        util.atomic_json(fixture.prep_path, fixture.prep)
        reference = {"path": str(fixture.prep_path), "sha256": util.file_hash(fixture.prep_path)}
        # Use the existing native inner fixture as the simulated collected result;
        # this clock test makes no process or proof claim.
        with (
            preparation.admitted(reference),
            patch.object(preparation.commands, "run", side_effect=outer),
            patch.object(verify, "prepare_scratch_tree", side_effect=tree),
            patch.object(verify, "run_command", side_effect=inner),
            patch.object(verify, "remove_tree"),
        ):
            result = preparation.launch(
                "execute", fixture.root.parent, out, {"command": "printf fixture", "timeout": 30}, timeout=30, env={}
            )
        self.assertEqual([("outer", 930, 0), ("inner", 30, 600)], bounds)
        self.assertEqual(629, clock["seconds"])
        self.assertEqual(fixture.result["exit_code"], result["exit_code"])

    def test_zero_outer_exit_cannot_authorize_an_unknown_inner_receipt(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = PreparationFixture(Path(temporary.name).resolve() / "obligations")

        def outer(command, cwd, output, *, timeout, env):
            request = util.read(shlex.split(command)[3])
            util.atomic_json(
                request["result"],
                {
                    "operation": "execute",
                    "nonce": fixture.prep["nonce"],
                    "request_sha256": util.file_hash(shlex.split(command)[3]),
                    "receipt": {"exit_code": 0},
                },
            )
            return {"exit_code": 0, "timed_out": False, "error": ""}

        reference = fixture.active["preparation_admission"]
        with preparation.admitted(reference), patch.object(preparation.commands, "run", side_effect=outer):
            with self.assertRaisesRegex(receipts.OwnershipUncertain, "unknown or uncollected"):
                preparation.launch("execute", fixture.root.parent, fixture.out, {}, timeout=30, env={})

    def test_first_and_later_git_children_require_published_worker_custody(self):
        from .test_verify import Project

        project = Project(
            {
                "app.py": "answer=42\n",
                "test_app.py": "import unittest,app\nclass T(unittest.TestCase):\n"
                "    def test_answer(self): self.assertEqual(app.answer,42)\n",
            }
        )
        self.addCleanup(project.close)
        root = (project.evidence / "obligations").resolve()
        hook = project.root / "hook"
        hook.mkdir()
        observations = project.root / "git-observations.jsonl"
        hook.joinpath("sitecustomize.py").write_text(
            "import os,json,subprocess,pathlib\n"
            "original=subprocess.run\n"
            "def guarded(argv,*args,**kwargs):\n"
            '    if isinstance(argv,(list,tuple)) and argv and argv[0]=="git":\n'
            "        active=json.loads(pathlib.Path(" + repr(str(root / "pending.json")) + ").read_text())\n"
            '        check=active.get("current_check") or {}\n'
            '        meta=check.get("supervision") or {}\n'
            '        path=pathlib.Path(meta.get("receipt","/missing"))\n'
            "        receipt=json.loads(path.read_text())\n"
            '        admission=json.loads(path.with_name("admission.json").read_text())\n'
            '        good=(active["phase"]==check.get("phase")=="executing" and\n'
            '              receipt.get("phase")=="armed" and receipt.get("keeper")==meta.get("keeper") and\n'
            '              admission.get("supervision")==meta and admission.get("command")==check.get("command"))\n'
            "        with open("
            + repr(str(observations))
            + ',"a") as f:f.write(json.dumps({"bound":good,"phase":active["phase"]})+"\\n")\n'
            "        if not good:os._exit(91)\n"
            "    return original(argv,*args,**kwargs)\n"
            "subprocess.run=guarded\n"
        )
        identity = {"fixture": "all discovery owned"}
        with patch.dict(os.environ, {"PYTHONPATH": str(hook)}):
            result = schedule.run(
                root,
                identity,
                lambda out: verify.scratch_run(
                    project.root,
                    out,
                    command=shlex.join([sys.executable, "-m", "unittest", "-v", "test_app"]),
                    timeout=30,
                ),
                reuse_allowed=False,
                reason="fresh",
                current_identity=lambda: identity,
                owned_preparation=True,
            )
        self.assertEqual(0, result["exit_code"], result)
        rows = [json.loads(line) for line in observations.read_text().splitlines()]
        self.assertGreater(len(rows), 1)
        self.assertTrue(all(row["bound"] for row in rows), rows)

    def test_normal_owned_receipt_before_pointer_recovers_without_rewriting(self):
        self.check_normal_publication(pointer_published=False)

    def test_normal_owned_pointer_before_retirement_recovers_without_rewriting(self):
        self.check_normal_publication(pointer_published=True)

    def test_normal_failed_post_context_receipt_before_pointer_recovers(self):
        self.check_normal_publication(pointer_published=False, context_exception=True)

    def test_normal_failed_post_context_pointer_before_retirement_recovers(self):
        self.check_normal_publication(pointer_published=True, context_exception=True)

    def check_normal_publication(self, *, pointer_published, context_exception=False):
        from .test_verify import Project

        project = Project(
            {
                "test_app.py": "import unittest\nclass T(unittest.TestCase):\n"
                "    def test_answer(self): self.assertEqual(21*2,42)\n"
            }
        )
        self.addCleanup(project.close)
        root = (project.evidence / "obligations").resolve()
        identity = {"fixture": "publication"}
        original = util.atomic_json

        def barrier(path, value):
            if Path(path).name == "completed.json":
                if pointer_published:
                    original(path, value)
                raise receipts.OwnershipUncertain("fake normal publication barrier")
            return original(path, value)

        def after():
            if context_exception:
                raise ValueError("Synthetic post-context failure")
            return identity

        with patch.object(util, "atomic_json", side_effect=barrier):
            with self.assertRaises(receipts.OwnershipUncertain):
                schedule.run(
                    root,
                    identity,
                    lambda out: verify.scratch_run(
                        project.root,
                        out,
                        command=shlex.join([sys.executable, "-m", "unittest", "-v", "test_app"]),
                        timeout=30,
                    ),
                    reuse_allowed=False,
                    reason="mandatory_approved_execution",
                    current_identity=after,
                    owned_preparation=True,
                )
        active = util.read(root / "pending.json")
        out = root / util.digest(identity) / active["attempt_id"]
        path = out / "receipt.json"
        before = path.read_bytes()
        saved = util.read(path)
        self.assertEqual("post_execution_identity", active["phase"])
        self.assertIsNone(active["current_check"])
        self.assertEqual(not context_exception, receipts.completed(saved["result"], root=out))
        self.assertTrue(recovery.collected_command(saved["result"], out))
        self.assertEqual(0, saved["result"]["exit_code"])
        self.assertEqual(1, saved["result"]["results"]["total"])
        if context_exception:
            self.assertEqual(
                "Verification attempt did not complete (ValueError): Synthetic post-context failure",
                saved["result"]["error"],
            )
            self.assertFalse(schedule.reusable(saved["result"]))
        # Fake the old controller's death, without sleeps or killing this test.
        with patch.object(processes, "live_processes", return_value=[]) as native:
            recovered = schedule.recover_interrupted(root, {"old": "unrelated"})
        self.assertTrue(native.called)
        self.assertEqual(
            "existing_completed_receipt" if pointer_published else "published_existing_completed_receipt",
            recovered["disposition"],
        )
        self.assertEqual(before, path.read_bytes())
        self.assertFalse((root / "pending.json").exists())


class CompletedWorkerRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = PublicationFixture(Path(temporary.name).resolve() / "obligations")
        native = patch.object(processes, "live_processes", return_value=[])
        self.native = native.start()
        self.addCleanup(native.stop)

    def held(self):
        before = self.fixture.pending.read_bytes()
        receipt = (self.fixture.out / "receipt.json").read_bytes()
        with self.assertRaises(receipts.OwnershipUncertain):
            self.fixture.recover()
        self.assertEqual(before, self.fixture.pending.read_bytes())
        self.assertEqual(receipt, (self.fixture.out / "receipt.json").read_bytes())
        self.assertFalse((self.fixture.out.parent / "completed.json").exists())

    def test_normal_completed_result_requires_closed_outer_and_inner_custody(self):
        original = (self.fixture.out / "receipt.json").read_bytes()
        fixed = self.fixture.recover()
        self.assertEqual("published_existing_completed_receipt", fixed["disposition"])
        self.assertEqual(original, (self.fixture.out / "receipt.json").read_bytes())
        rows = [row for call in self.native.call_args_list for row in call.args[0]]
        self.assertIn(self.fixture.metadata["owner"], rows)
        self.assertIn(self.fixture.inner_metadata["owner"], rows)
        self.assertIn(self.fixture.inner_metadata["keeper"], rows)

    def test_inner_owner_not_in_outer_inventory_stays_held(self):
        self.fixture.terminal["processes"] = [self.fixture.metadata["provider"]]
        self.fixture.write()
        self.held()

    def test_live_outer_or_inner_and_unknown_inspection_stays_held(self):
        for row in (self.fixture.metadata["provider"], self.fixture.inner_metadata["keeper"]):
            self.native.return_value = [row]
            self.held()
        self.native.side_effect = processes.ProcessError("unknown")
        self.held()

    def test_incomplete_inner_capture_or_changed_worker_stays_held(self):
        self.fixture.inner_terminal.update(phase="armed", cause=None)
        util.atomic_json(self.fixture.inner_path, self.fixture.inner_terminal)
        self.held()
        self.fixture.worker.write_text("# changed worker\n")
        self.held()

    def test_changed_answer_or_output_never_publishes_an_old_result(self):
        util.atomic_json(self.fixture.request["result"], {**self.fixture.answer, "nonce": "changed"})
        self.held()
        util.atomic_json(self.fixture.request["result"], self.fixture.answer)
        self.fixture.inner_output.write_text("changed output\n")
        self.held()

    def test_publication_crash_repeats_preserving_original_result(self):
        original = util.atomic_json
        before = (self.fixture.out / "receipt.json").read_bytes()

        def barrier(path, value):
            if Path(path).name == "completed.json":
                raise RuntimeError("fake publication barrier")
            return original(path, value)

        with patch.object(util, "atomic_json", side_effect=barrier):
            with self.assertRaises(RuntimeError):
                self.fixture.recover()
        fixed = self.fixture.recover()
        self.assertEqual("published_existing_completed_receipt", fixed["disposition"])
        self.assertEqual(before, (self.fixture.out / "receipt.json").read_bytes())

    def test_collected_nonzero_and_error_results_are_preserved_as_failed(self):
        for exit_code, error in ((1, ""), (0, "Test command reported zero tests or incomplete per-test results")):
            with self.subTest(exit_code=exit_code, error=error):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                f = PublicationFixture(Path(temporary.name).resolve() / "obligations")
                f.result.update(exit_code=exit_code, error=error)
                util.atomic_json(f.request["result"], f.answer)
                util.atomic_json(f.out / "receipt.json", f.saved)
                before = (f.out / "receipt.json").read_bytes()
                recovered = f.recover()
                self.assertEqual("published_existing_completed_receipt", recovered["disposition"])
                self.assertEqual(before, (f.out / "receipt.json").read_bytes())
                self.assertFalse(schedule.reusable(util.read(f.out / "receipt.json")["result"]))

    def test_exact_context_changed_error_can_retire_failed_receipt_only(self):
        returned = deepcopy(self.fixture.result)
        self.fixture.saved["result"] = {
            **returned,
            "error": recovery.CONTEXT_CHANGED_ERROR,
            "observed_after_identity": {"different": "context"},
        }
        util.atomic_json(self.fixture.out / "receipt.json", self.fixture.saved)
        before = (self.fixture.out / "receipt.json").read_bytes()
        self.fixture.recover()
        self.assertEqual(before, (self.fixture.out / "receipt.json").read_bytes())
        saved = util.read(self.fixture.out / "receipt.json")["result"]
        self.assertFalse(receipts.completed(saved))
        self.assertFalse(schedule.reusable(saved))

    def test_other_context_extension_or_uncollected_result_stays_held(self):
        self.fixture.saved["result"] = {
            **self.fixture.result,
            "error": recovery.CONTEXT_CHANGED_ERROR,
            "observed_after_identity": {"different": "context"},
            "extra": "not admitted",
        }
        util.atomic_json(self.fixture.out / "receipt.json", self.fixture.saved)
        self.held()
        self.fixture.result["exit_code"] = None
        util.atomic_json(self.fixture.request["result"], self.fixture.answer)
        self.fixture.saved["result"] = self.fixture.result
        util.atomic_json(self.fixture.out / "receipt.json", self.fixture.saved)
        self.held()


class PublicPreparationFrontierTests(unittest.TestCase):
    def test_public_taskrun_resume_uses_current_preparation_instead_of_stale_check(self):
        self.check_public_resume(legacy=False)

    def test_public_taskrun_legacy_missing_directory_remains_held(self):
        self.check_public_resume(legacy=True)

    def check_public_resume(self, *, legacy):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        workspace = root / "project"
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
        run_dir = workspace / ".autocode/runs/owned"
        run_dir.mkdir(parents=True)
        fixture = PreparationFixture(run_dir / "check-replay/obligations")
        stale = deepcopy(fixture.check)
        if legacy:
            fixture.active = {"attempt_id": "f" * 32, "identity": {"different": "legacy"}, "started_at": "earlier"}
            util.atomic_json(fixture.pending, fixture.active)
        else:
            stale["output"] = str(run_dir / "old-check.log")
        state = {
            "version": 3,
            "task": "Retain owned task",
            "task_id": "owned-task",
            "workspace": str(workspace),
            "project_workspace": str(workspace),
            "run_dir": str(run_dir),
            "status": "PAUSED_VERIFICATION_UNCERTAIN",
            "next_stage": "sol",
            "iteration": 2,
            "sessions": {},
            "stages": [],
            "active_runner_check": stale,
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
        wrapper = root / "fixture_cli.py"
        wrapper.write_text(
            "import sys\nsys.path.insert(0," + repr(str(tools)) + ")\n"
            "import autocode,autocode_build_loop,autocode_regression\n"
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
            timeout=60,
            env={"AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        before = fixture.pending.read_bytes()
        view = run.status()
        self.assertEqual("legacy_unbound" if legacy else "preparing", view["verification_obligation"]["phase"])
        self.assertEqual(before, fixture.pending.read_bytes())
        result = run.resume_paused()
        self.assertEqual(2, run.last_advance.returncode)
        if legacy:
            self.assertEqual("PAUSED_VERIFICATION_UNCERTAIN", result["status"])
            self.assertEqual(before, fixture.pending.read_bytes())
            self.assertFalse((fixture.out / "receipt.json").exists())
        else:
            self.assertEqual("PAUSED_REQUESTED", result["status"], result)
            self.assertIsNone(result["verification_obligation"])
            self.assertIsNone(result["runner_check"])
            failed = util.read(fixture.out / "receipt.json")
            self.assertIsNone(failed["result"]["exit_code"])
            self.assertTrue(failed["result"]["interrupted"])
