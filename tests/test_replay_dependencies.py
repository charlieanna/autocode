"""Refusal diagnostics identify capture claims without inspecting or executing them."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import autocode_replay_cli as cli
import autocode_replay_recording as recording
import autocode_util as util

from .test_replay_recording import make_export_projection, sha


def event(receipt):
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": "copied shell command must never run",
            "aggregated_output": json.dumps(receipt),
            "exit_code": 0,
        },
    }


class DependencyInventoryTests(unittest.TestCase):
    def setUp(self):
        import autocode_replay_dependencies as dependencies

        self.dependencies = dependencies
        self.snapshot = {"head": "a" * 40, "files": {"test.py": "b" * 64}, "source_paths": ["test.py"]}
        self.revision = util.digest({key: self.snapshot[key] for key in ("head", "files")})
        self.context = {
            "attempt": "/project/.autocode/runs/one/report.json",
            "nonce": "old",
            "source_revision": self.revision,
        }
        self.receipt = {
            "command": ["sh", "-c", "touch MUST_NOT_EXECUTE"],
            "exit_code": 0,
            "full_output": "/project/.autocode/runs/one/check.log",
            "full_output_sha256": "c" * 64,
            "verification_copy": {"manifest_sha256": "d" * 64, "source_revision": self.revision},
            "capture_context": self.context,
        }
        self.document = {
            "dispatches": [
                {
                    "id": "dispatch-0",
                    "events": "events-0",
                    "before": "source-0",
                    "after": "source-1",
                    "task_id": "task",
                    "contract_hash": "e" * 64,
                }
            ],
            "snapshots": {"source-0": self.snapshot},
            "artifacts": {"events-0": {"original_sha256": "f" * 64}},
        }

    def describe(self, *, receipt=None, rows=None):
        receipt = self.receipt if receipt is None else receipt
        return self.dependencies.inventory(
            document=self.document,
            originals={"events-0": [event(receipt)] if rows is None else rows},
            records=[{"capture_context": self.context}],
            references=recording._native_capture_references(
                [{"full_output": receipt["full_output"]}], Path("/project")
            ),
            workspace=Path("/project"),
            run_dir=Path("/project/.autocode/runs/one"),
            contexts={"dispatch-0": {"case_ids": ["regression-case"], "task": {"id": "task"}}},
        )

    def test_consistent_self_forged_claims_remain_unverified_and_bound_to_occurrence(self):
        before = copy.deepcopy(self.receipt)
        result = self.describe()
        self.assertEqual(before, self.receipt)
        self.assertEqual(result, self.describe())
        self.assertEqual(
            (1, False, "unverified", False),
            (result["version"], result["execution_authority"], result["closure_status"], result["truncated"]),
        )
        [entry] = result["dependencies"]
        self.assertEqual(
            ("dispatch-0", 0, 0, "f" * 64),
            (entry["dispatch"], entry["event_index"], entry["line_index"], entry["events_sha256"]),
        )
        self.assertEqual(sha(json.dumps(self.receipt).encode()), entry["receipt_claim_sha256"])
        self.assertEqual(util.digest(self.receipt["command"]), entry["command_claim_sha256"])
        self.assertEqual(self.revision, entry["before_source_revision"])
        self.assertEqual(util.digest(["test.py"]), entry["source_selection_sha256"])
        self.assertEqual("match", entry["source_revision_match"])
        self.assertEqual("match", entry["capture_context_match"])
        self.assertEqual(
            ["receipt_not_authenticated", "raw_output_not_inspected", "copy_manifest_not_supplied"], entry["issues"]
        )
        encoded = json.dumps(result)
        for private in ("MUST_NOT_EXECUTE", "/project", '"nonce"', '"old"', "regression-case"):
            self.assertNotIn(private, encoded)

    def test_stale_file_mode_source_and_cross_attempt_context_are_not_matches(self):
        initial = self.describe()["dependencies"][0]
        self.snapshot["files"]["test.py"] = "executable:" + "b" * 64
        changed = self.describe()["dependencies"][0]
        self.assertNotEqual(initial["before_source_revision"], changed["before_source_revision"])
        self.assertEqual("mismatch", changed["source_revision_match"])
        for field in ("nonce", "attempt", "source_revision"):
            foreign = copy.deepcopy(self.receipt)
            foreign["capture_context"][field] = "foreign"
            with self.subTest(field=field):
                self.assertIn("capture_context_mismatch", self.describe(receipt=foreign)["dependencies"][0]["issues"])

    def test_missing_and_malformed_fields_are_reported_without_inventing_pins(self):
        for command in (None, [], "shell command", [1]):
            value = {
                "command": command,
                "exit_code": True,
                "verification_copy": [],
                "full_output": self.receipt["full_output"],
            }
            with self.subTest(command=command):
                entry = self.describe(receipt=value)["dependencies"][0]
                for issue in (
                    "malformed_receipt",
                    "missing_or_invalid_copy_pin",
                    "missing_or_invalid_output_pin",
                    "source_revision_unavailable",
                    "capture_context_mismatch",
                ):
                    self.assertIn(issue, entry["issues"])
                for field in (
                    "copy_manifest_sha256_claim",
                    "raw_output_sha256_claim",
                    "source_revision_claim",
                    "command_claim_sha256",
                ):
                    self.assertIsNone(entry[field])
        self.context = None
        self.assertEqual("unavailable", self.describe()["dependencies"][0]["capture_context_match"])

    def test_paths_are_classified_lexically_without_opening_resolving_or_executing(self):
        variants = {
            "/project/.autocode/runs/other/check.log": "other_run",
            "/private/foreign/check.log": "outside_evidence_roots",
            "/project/test.log": "outside_evidence_roots",
            "/project/.autocode/../escape.log": "noncanonical",
            "./.autocode/check.log": "noncanonical",
            ".autocode/link/check.log": "project_evidence",
            "/project/.autocode/runs/one/check.log": "run",
            ".autocode/bad\x00.log": "noncanonical",
            ".autocode/\\escape.log": "noncanonical",
        }
        with (
            patch("builtins.open", side_effect=AssertionError("must not open")),
            patch.object(Path, "resolve", side_effect=AssertionError("must not resolve")),
            patch("subprocess.Popen", side_effect=AssertionError("must not execute")),
        ):
            for path, location in variants.items():
                value = {**self.receipt, "full_output": path}
                with self.subTest(path=path):
                    entry = self.describe(receipt=value)["dependencies"][0]
                    self.assertEqual(location, entry["output_location"])
                    self.assertIn("raw_output_not_inspected", entry["issues"])

    def test_scope_changes_and_duplicate_claims_have_distinct_inventory_evidence(self):
        initial = self.describe()["dependencies"][0]
        self.document["dispatches"][0]["contract_hash"] = "0" * 64
        changed = self.describe()["dependencies"][0]
        self.assertNotEqual(initial["scope_sha256"], changed["scope_sha256"])
        rows = [event(self.receipt), event({**self.receipt, "full_output_sha256": "1" * 64})]
        entries = self.describe(rows=rows)["dependencies"]
        self.assertEqual([0, 1], [entry["event_index"] for entry in entries])
        self.assertNotEqual(entries[0]["receipt_claim_sha256"], entries[1]["receipt_claim_sha256"])
        self.assertTrue(all("repeated_output_claim" in entry["issues"] for entry in entries))

    def test_unrelated_output_and_model_prose_do_not_become_dependencies(self):
        rows = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(self.receipt)}},
            event({**self.receipt, "full_output": "/different.log"}),
            event({"verification_copy": {}}),
            event([self.receipt]),
            {"type": "item.completed", "item": {"type": "tool_output", "aggregated_output": "not json\nnull\n{}"}},
        ]
        self.assertEqual([], self.describe(rows=rows)["dependencies"])

    def test_opencode_events_and_hard_bounds_preserve_refusal_diagnostics(self):
        row = {
            "type": "tool_use",
            "sessionID": "session",
            "part": {
                "id": "tool-1",
                "tool": "bash",
                "state": {
                    "status": "completed",
                    "input": {"command": "never run"},
                    "output": json.dumps(self.receipt),
                    "metadata": {"exit": 0},
                },
            },
        }
        self.assertEqual(1, len(self.describe(rows=[row])["dependencies"]))
        with patch.object(self.dependencies, "MAX_DEPENDENCIES", 1):
            result = self.describe(rows=[event(self.receipt)] * 2)
            self.assertTrue(result["truncated"])
            self.assertEqual(1, len(result["dependencies"]))
        with patch.object(self.dependencies, "MAX_LINES", 1):
            result = self.describe(rows=[event({}), event(self.receipt)])
            self.assertTrue(result["truncated"])
            self.assertEqual([], result["dependencies"])


class DependencyInventoryCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projection = make_export_projection(self.root)
        self.record = self.projection["records"][0]
        self.receipt = {
            "command": ["touch", str(self.root / "MUST_NOT_EXECUTE")],
            "exit_code": 0,
            "verification_copy": {"manifest_sha256": "0" * 64},
            "full_output": str(self.root / "outside" / "captured.log"),
        }
        Path(self.record["events"]).write_text(json.dumps(event(self.receipt)) + "\n")
        Path(self.record["output"]).write_text(json.dumps({"checks": [{"full_output": self.receipt["full_output"]}]}))

    def test_public_cli_keeps_refusing_before_writes_and_returns_non_authoritative_inventory(self):
        original = {key: Path(self.record[key]).read_bytes() for key in ("events", "output", "before_ref", "after_ref")}
        for option, destination in (
            ("--export", self.root / "bundle"),
            ("--into", self.root / "fresh/.autocode/runs/replay"),
        ):
            error = io.StringIO()
            with (
                self.subTest(option=option),
                patch.object(cli.TaskRun, "status", return_value={"replay_source": self.projection}),
                patch.object(cli.TaskRun, "start", side_effect=AssertionError("must not start")),
                patch("subprocess.Popen", side_effect=AssertionError("must not execute")),
                redirect_stderr(error),
            ):
                self.assertEqual(3, cli.cli([self.projection["run_dir"], option, str(destination), "--json"]))
            refusal = json.loads(error.getvalue())
            self.assertEqual("REPLAY_REFUSED", refusal["code"])
            self.assertIn("native capture receipt dependencies are unsupported in v1", refusal["message"])
            self.assertIn("native_capture_dependencies", refusal)
            inventory = refusal["native_capture_dependencies"]
            self.assertFalse(inventory["execution_authority"])
            self.assertEqual("unverified", inventory["closure_status"])
            [entry] = inventory["dependencies"]
            self.assertEqual("dispatch-0", entry["dispatch"])
            self.assertEqual(sha(original["events"]), entry["events_sha256"])
            self.assertIn("output_path_outside_evidence_roots", entry["issues"])
            self.assertFalse(destination.exists())
            self.assertFalse((self.root / "MUST_NOT_EXECUTE").exists())
        for key, data in original.items():
            self.assertEqual(data, Path(self.record[key]).read_bytes())

    def test_missing_and_symlinked_claimed_files_are_never_inspected_or_given_custody(self):
        outside = Path(self.receipt["full_output"]).parent
        outside.symlink_to(self.root)
        with self.assertRaises(recording.RecordingError) as caught:
            recording.export(self.projection, self.root / "bundle")
        entry = caught.exception.native_capture_dependencies["dependencies"][0]
        self.assertIn("raw_output_not_inspected", entry["issues"])
        self.assertIn("copy_manifest_not_supplied", entry["issues"])
        self.assertTrue(outside.is_symlink())
        self.assertFalse((self.root / "bundle").exists())

    def test_other_refusals_do_not_acquire_native_capture_diagnostics(self):
        error = io.StringIO()
        with patch.object(cli.TaskRun, "status", return_value={"replay_source": None}), redirect_stderr(error):
            self.assertEqual(3, cli.cli([self.projection["run_dir"], "--export", str(self.root / "bundle"), "--json"]))
        self.assertEqual({"code", "message"}, set(json.loads(error.getvalue())))
