"""Portable identity is not portable execution authority; no provider launches.

These characterize the current boundary, not an implementation of native replay.
Commands in the fixtures are data. Only fixed Git setup in the existing source
fixture/copy helpers executes; no archived command is dispatched.
"""

import copy
import io
import json
import shlex
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_check_replay as clean_checks
import autocode_native_check_execution as native_checks
import autocode_replay_bindings as identities
import autocode_replay_checks as archival
import autocode_replay_cli as replay_cli
import autocode_replay_recording as recording
import autocode_support as support
import autocode_util as util
import autocode_verification_copy as copies

from tests.test_replay_recording import make_export_projection, make_recording


class CaptureIdentityContractTests(unittest.TestCase):
    def setUp(self):
        self.old = {
            "attempt": "/original/.autocode/runs/one/tester.json",
            "nonce": "original-nonce",
            "source_revision": "a" * 64,
        }
        self.fresh = {
            "output": "/fresh/.autocode/runs/two/tester.json",
            "capture_nonce": "fresh-nonce",
            "source_revision": "b" * 64,
        }

    def bindings(self, payload):
        return identities.bindings(
            payload,
            {"capture_context": self.old},
            {},
            "/original",
            "/original/.autocode/runs/one",
            [],
        )

    def test_exact_context_rebinds_but_copy_digest_and_embedded_command_output_do_not(self):
        receipt = {
            "capture_context": self.old,
            "verification_copy": {"source_revision": "a" * 64, "manifest_sha256": "c" * 64},
            "command": ["python3", "/original/check.py"],
        }
        payload = {"report": receipt, "events": [{"aggregated_output": json.dumps(receipt)}]}
        before = copy.deepcopy(payload)
        rebound = recording.rebind(payload, self.bindings(payload), self.fresh)
        self.assertEqual(
            {"attempt": self.fresh["output"], "nonce": "fresh-nonce", "source_revision": "b" * 64},
            rebound["report"]["capture_context"],
        )
        self.assertEqual(receipt["verification_copy"], rebound["report"]["verification_copy"])
        self.assertEqual(receipt["command"], rebound["report"]["command"])
        self.assertEqual(payload["events"], rebound["events"])
        self.assertEqual(before, payload)

    def test_foreign_attempt_or_nonce_cannot_be_rebound_from_matching_prose(self):
        for field in ("attempt", "nonce"):
            payload = {"report": {"capture_context": {**self.old, field: "foreign"}, "summary": json.dumps(self.old)}}
            with self.subTest(field=field):
                bindings = self.bindings(payload)
                self.assertEqual([], bindings)
                self.assertEqual(payload, recording.rebind(payload, bindings, self.fresh))

    def test_copy_authority_has_no_v1_binding_and_missing_current_nonce_refuses(self):
        for symbol in ("verification_copy", "manifest_sha256", "native_check"):
            with self.subTest(symbol=symbol), self.assertRaisesRegex(ValueError, "binding is invalid"):
                recording.rebind(
                    {"receipt": {}}, [{"pointer": "/receipt", "symbol": symbol, "original_valid": True}], {symbol: {}}
                )
        payload = {"report": {"capture_context": self.old}}
        with self.assertRaisesRegex(ValueError, "symbol is unavailable"):
            recording.rebind(payload, self.bindings(payload), {"output": self.fresh["output"]})


class VerificationCopyPortabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        projection = make_export_projection(self.root)
        self.workspace, self.run = Path(projection["workspace"]), Path(projection["run_dir"])
        scratch = self.run / ("tool-containment-" + "1" * 32) / "scratch"
        scratch.mkdir(parents=True)
        self.descriptor = copies.create(self.workspace, scratch, run_dir=self.run)
        self.manifest = Path(self.descriptor["manifest"])

    def execute_identity(self, *, manifest=None, workspace=None):
        return copies.execution(manifest or self.manifest, self.descriptor["sha256"], workspace or self.workspace)

    def test_original_copy_validates_but_identical_tree_transplant_has_no_fresh_authority(self):
        tree, identity = self.execute_identity()
        self.assertEqual((self.workspace / "greet.py").read_bytes(), (tree / "greet.py").read_bytes())
        self.assertEqual(self.descriptor["sha256"], identity["manifest_sha256"])
        relocated = self.root / "relocated"
        shutil.copytree(self.workspace, relocated, symlinks=True)
        manifest = relocated / self.manifest.relative_to(self.workspace)
        self.assertEqual(self.manifest.read_bytes(), manifest.read_bytes())
        with self.assertRaisesRegex(ValueError, "Unexpected verification copy layout"):
            self.execute_identity(manifest=manifest, workspace=relocated)

    def test_source_bytes_and_executable_mode_must_still_match(self):
        self.execute_identity()
        source = self.workspace / "greet.py"
        original, mode = source.read_bytes(), source.stat().st_mode
        for change in (lambda: source.write_bytes(b"print('stale')\n"), lambda: source.chmod(0o755)):
            change()
            with self.assertRaisesRegex(ValueError, "Verification source changed"):
                self.execute_identity()
            source.write_bytes(original)
            source.chmod(mode)

    def test_manifest_alias_and_replaced_copy_input_are_refused(self):
        tree, _ = self.execute_identity()
        raw = self.manifest.read_bytes()
        retained = self.manifest.with_name("retained.json")
        self.manifest.rename(retained)
        self.manifest.symlink_to(retained)
        with self.assertRaisesRegex(ValueError, "authority changed"):
            self.execute_identity()
        self.manifest.unlink()
        self.manifest.write_bytes(raw)
        (tree / "greet.py").write_bytes(b"print('forged copy')\n")
        with self.assertRaisesRegex(ValueError, "Verification input changed"):
            self.execute_identity()


class CapturedCheckCustodyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        evidence = self.root / ".autocode" / "evidence"
        evidence.mkdir(parents=True)
        self.path, self.raw, self.events = evidence / "check.json", evidence / "check.log", evidence / "events.jsonl"
        self.raw.write_bytes(b"one test passed\n")
        self.context = {"attempt": "current-attempt", "nonce": "current-nonce", "source_revision": "a" * 64}
        self.receipt = {
            "command": ["python3", "-m", "unittest", "test_example.py"],
            "exit_code": 0,
            "full_output": str(self.raw),
            "full_output_sha256": util.file_hash(self.raw),
            "capture_context": self.context,
        }
        util.atomic_json(self.path, self.receipt)
        self.event_command = shlex.join(
            ["autocode", "capture", "--output", str(self.path), "--", *self.receipt["command"]]
        )
        self.write_event(self.receipt)

    def write_event(self, displayed):
        self.events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "capture-1",
                        "type": "tool_output",
                        "command": self.event_command,
                        "exit_code": 0,
                        "aggregated_output": json.dumps(displayed),
                    },
                }
            )
            + "\n"
        )

    def check(self):
        return [{"command": shlex.join(self.receipt["command"]), "exit_code": 0, "evidence_ref": str(self.path)}]

    def test_receipt_only_accepts_exact_context_and_refuses_old_run_or_nonce(self):
        checks = self.check()
        support.verify_checks(checks, self.root, self.events, receipt_only=True, capture_context=self.context)
        self.assertEqual(self.check(), checks)
        for field in ("attempt", "nonce", "source_revision"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "does not belong"):
                support.verify_checks(
                    self.check(),
                    self.root,
                    self.events,
                    receipt_only=True,
                    capture_context={**self.context, field: "fresh-other-value"},
                )

    def test_native_event_must_attest_exact_receipt_and_its_command(self):
        support.verify_checks(self.check(), self.root, self.events)
        changed = {**self.receipt, "capture_context": {**self.context, "nonce": "forged"}}
        util.atomic_json(self.path, changed)
        with self.assertRaisesRegex(ValueError, "No independently executed"):
            support.verify_checks(self.check(), self.root, self.events)
        util.atomic_json(self.path, self.receipt)
        checks = self.check()
        checks[0]["command"] += " --different-test"
        with self.assertRaisesRegex(ValueError, "command/result differs"):
            support.verify_checks(checks, self.root, self.events)

    def test_matching_receipt_cannot_hide_changed_or_external_output(self):
        self.raw.write_bytes(b"different result\n")
        with self.assertRaisesRegex(ValueError, "Full check output missing or changed"):
            support.verify_checks(self.check(), self.root, self.events)
        outside = self.root / "external.log"
        outside.write_bytes(b"one test passed\n")
        changed = {**self.receipt, "full_output": str(outside)}
        util.atomic_json(self.path, changed)
        self.write_event(changed)
        with self.assertRaisesRegex(ValueError, "Full check output missing or changed"):
            support.verify_checks(self.check(), self.root, self.events)


class PortableArchiveAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_archived_capture_fields_remain_historical_bytes_with_no_completion_pins(self):
        projection = make_export_projection(self.root)
        run = Path(projection["run_dir"])
        output, receipt = run / "check.log", run / "check.json"
        output.write_bytes(b"historical output\n")
        original = {
            "command": "python3 archived_check.py",
            "exit_code": 0,
            "output": str(output),
            "output_sha256": util.file_hash(output),
            "capture_context": {"attempt": "old-attempt", "nonce": "old-nonce", "source_revision": "a" * 64},
            "verification_copy": {"source_revision": "a" * 64, "manifest_sha256": "b" * 64},
        }
        util.atomic_json(receipt, original)
        projection["checks"] = [
            {
                "id": "check-0",
                "dispatch": "dispatch-0",
                "purpose": "independent_clean_replay",
                "command": original["command"],
                "repetition": 1,
                "snapshot": "source-0",
                "receipt": str(receipt),
                "output": str(output),
                "authentication": "intact",
                "bindings": [],
            }
        ]
        raw = receipt.read_bytes()
        tape = recording.load(recording.export(projection, self.root / "bundle"))
        receipt.unlink()
        output.unlink()
        with patch("subprocess.Popen", side_effect=AssertionError("archived command must not run")):
            result = archival.replay_receipt(tape, "check-0", self.root / "archive")
        self.assertEqual(raw, Path(result["receipt"]).read_bytes())
        self.assertEqual(original, result["recorded"])
        self.assertTrue(result["simulated"])
        self.assertFalse(result["current_proof"])
        self.assertEqual({}, clean_checks.evidence_pins({"simulated": True, "checks": [original]}))

    def test_native_clean_replay_refuses_simulation_and_a_duck_typed_owner_before_execution(self):
        runner = Mock(side_effect=AssertionError("must not execute"))
        owner = Mock()
        for settings, message in (
            ({}, "Original concrete native check owner required"),
            ({"replay_recording": {"checks": "recorded"}}, "requires fresh executed checks"),
        ):
            with self.subTest(settings=settings), self.assertRaisesRegex(util.Paused, message):
                clean_checks.replay(
                    [],
                    self.root,
                    self.root / "run",
                    {},
                    runner,
                    approved_state={"settings": settings},
                    native_execution=owner,
                )
        runner.assert_not_called()
        owner.require_replay.assert_not_called()
        with self.assertRaisesRegex(util.Paused, "original context required"):
            native_checks.NativeCheckExecution(owner, {}, persist=Mock())

    def test_public_import_refuses_traversal_or_symlink_before_starting_a_run(self):
        manifest, document = make_recording(self.root / "bundle")
        digest = next(iter(document["blobs"]))
        blob = manifest.parent / "blobs" / digest
        retained = self.root / "retained"
        retained.write_bytes(blob.read_bytes())
        for attack in ("traversal", "symlink"):
            candidate = copy.deepcopy(document)
            if attack == "traversal":
                candidate["snapshots"]["initial"]["files"] = {"../outside.py": digest}
            else:
                blob.unlink()
                blob.symlink_to(retained)
            manifest.write_text(json.dumps(candidate))
            destination = self.root / attack / ".autocode" / "runs" / "replay"
            errors = io.StringIO()
            with self.subTest(attack=attack), patch.object(replay_cli.TaskRun, "start") as start:
                with redirect_stderr(errors):
                    status = replay_cli.cli([str(manifest), "--into", str(destination), "--json"])
                self.assertEqual(3, status)
                self.assertEqual("REPLAY_REFUSED", json.loads(errors.getvalue())["code"])
                self.assertFalse(destination.parent.parent.parent.exists())
                start.assert_not_called()
        self.assertEqual(b"print('hello')\n", retained.read_bytes())
