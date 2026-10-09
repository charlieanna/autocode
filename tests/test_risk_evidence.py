"""Actual scratch executions and completion revalidation of lifecycle evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_risk_evidence as evidence
import autocode_risk_obligations as obligations
import autocode_risk_protocols as protocols
import autocode_util as util
import autocode_verify as verify

from tests.test_risk_acceptance import OUTBOX
from tests.test_risk_obligations import CATALOG, SCENARIOS, RiskFixture


def candidate(workspace, module, variant="reference"):
    source = CATALOG / SCENARIOS[module] / variant / module
    shutil.copytree(source, Path(workspace) / module, dirs_exist_ok=True)


def commit_seed(workspace):
    for args in [
        ("init", "-q"),
        ("add", "."),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Original imported public packages",
        ),
    ]:
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class RiskEvidenceTests(RiskFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="risk-real-scratch-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        fixture = RiskFixture()
        cls.original_state = fixture.fixture(cls.root, both=True)
        fixture.install(cls.original_state)
        cls.workspace = Path(cls.original_state["workspace"])
        commit_seed(cls.workspace)
        for module in SCENARIOS:
            candidate(cls.workspace, module)
        cls.revision = util.snapshot(cls.workspace)["revision"]
        cls.actual = evidence.replay(
            cls.original_state,
            cls.workspace,
            cls.root / "replay",
            verify.scratch_run,
            timeout=30,
            source_revision=cls.revision,
            all_observations=True,
        )
        cls.original_state["validation"] = {"check_replay": {"risk_acceptance": cls.actual}}

    def state(self):
        return copy.deepcopy(self.original_state)

    def save_result(self, state, result):
        """Retain changed copies separately; never overwrite the shared actual execution."""
        directory = tempfile.TemporaryDirectory(dir=self.root, prefix="changed-receipt-")
        self.addCleanup(directory.cleanup)
        summary = Path(directory.name) / "summary.json"
        util.atomic_json(
            summary, {key: value for key, value in result.items() if key not in ("summary", "summary_sha256")}
        )
        result.update(summary=str(summary), summary_sha256=util.file_hash(summary))
        state["validation"]["check_replay"]["risk_acceptance"] = result
        return result

    def test_actual_queue_and_outbox_pass_through_public_scratch_runner_with_full_death_receipts(self):
        self.assertEqual("PASS", self.actual["verdict"])
        self.assertEqual(self.revision, util.snapshot(self.workspace)["revision"])
        self.assertTrue(evidence.ready(self.state(), self.revision))
        self.assertEqual(2, len(self.actual["checks"]))
        for row, case in zip(
            self.actual["checks"], obligations.observations(self.original_state, all_observations=True), strict=False
        ):
            with self.subTest(protocol=case["protocol"]):
                raw = Path(row["output"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row["output_sha256"])
                actual = protocols.validate_transcript(raw, case)
                self.assertEqual(actual, row["observation"])
                self.assertEqual(3, len(actual["phases"]))
                crash = 0 if case["protocol"].startswith("lease_") else 1
                worker = actual["phases"][crash]["worker"]
                self.assertEqual(-signal.SIGKILL, worker["exit_code"])
                self.assertLess(worker["received"], worker["terminated"])
                self.assertLess(worker["terminated"], worker["exited"])
                self.assertTrue(all(item["reaped"] for item in actual["owned_workers"]))
                for item in actual["owned_workers"]:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(item["pid"], 0)
                if case["protocol"].startswith("transactional_"):
                    deliveries = actual["sink"]["deliveries"]
                    self.assertEqual(4, len(deliveries))
                    self.assertEqual(deliveries[1]["event"], deliveries[2]["event"])
        self.assertEqual(5, len(evidence.evidence_pins(self.actual)))
        for row in self.actual["checks"]:
            self.assertEqual(
                row["supervision_sha256"], evidence.evidence_pins(self.actual)[row["supervision"]["receipt"]]
            )
        wrapper = self.original_state["goal_contract"]["body"]["risk_acceptance"]
        artifact = util.read_object(wrapper["targets"]["artifact"])
        for row in artifact["targets"]:
            self.assertNotEqual(row["sha256"], util.file_hash(self.workspace / row["path"]))

    def test_lifecycle_readiness_rejects_changed_missing_or_partial_command_ownership(self):
        state = self.state()
        row = state["validation"]["check_replay"]["risk_acceptance"]["checks"][0]
        ownership = Path(row["supervision"]["receipt"])
        original = ownership.read_bytes()
        try:
            self.assertTrue(evidence.ready(state, self.revision))
            ownership.write_bytes(original + b"changed")
            self.assertFalse(evidence.ready(state, self.revision))
            ownership.unlink()
            self.assertFalse(evidence.ready(state, self.revision))
        finally:
            ownership.write_bytes(original)
        self.assertTrue(evidence.ready(state, self.revision))
        del row["supervision_errors"]
        self.save_result(state, state["validation"]["check_replay"]["risk_acceptance"])
        self.assertFalse(evidence.ready(state, self.revision))

    def test_known_queue_and_outbox_mutants_fail_with_retained_full_actual_transcripts(self):
        mutants = [
            ("leasequeue", "broken/process-local-tokens", 0, "lease token"),
            ("outbox", "broken/ack-with-exception-rollback", 1, "unacknowledged event"),
        ]
        for module, variant, crash, reason in mutants:
            with self.subTest(module=module), tempfile.TemporaryDirectory(prefix="risk-mutant-") as directory:
                state = self.fixture(
                    Path(directory), both=module == "outbox", task=OUTBOX if module == "outbox" else None
                )
                self.install(state)
                workspace = Path(state["workspace"])
                commit_seed(workspace)
                for name in ["leasequeue", "outbox"] if module == "outbox" else ["leasequeue"]:
                    candidate(workspace, name, variant if name == module else "reference")
                state["current_task"] = {"acceptance_criteria": ["AC-queue" if module == "leasequeue" else "AC-outbox"]}
                out = Path(directory) / "replay"
                with self.assertRaisesRegex(ValueError, "mandatory risk lifecycle observation"):
                    evidence.replay(
                        state,
                        workspace,
                        out,
                        verify.scratch_run,
                        timeout=30,
                        source_revision=util.snapshot(workspace)["revision"],
                    )
                summaries = list(out.rglob("summary.json"))
                self.assertEqual(1, len(summaries))
                result = util.read_object(summaries[0])
                self.assertEqual("FAIL", result["verdict"])
                row = result["checks"][0]
                raw = Path(row["output"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row["output_sha256"])
                transcript = json.loads(raw)
                self.assertIn(reason, transcript["error"])
                self.assertEqual(-signal.SIGKILL, transcript["phases"][crash]["worker"]["exit_code"])
                self.assertTrue(all(item["reaped"] for item in transcript["owned_workers"]))
                with self.assertRaises(ValueError):
                    protocols.validate_transcript(raw, obligations.observations(state)[0])

    def test_current_revision_contract_manifest_and_target_pins_are_required(self):
        self.assertFalse(evidence.ready(self.state(), "changed-source"))
        changes = [
            lambda s: s["goal_contract"].update(hash="new-contract"),
            lambda s: s.update(task=s["task"] + " Changed original brief."),
            lambda s: s["goal_contract"]["body"]["risk_acceptance"]["manifest"].update(hash="0" * 64),
            lambda s: s["goal_contract"]["body"]["risk_acceptance"]["targets"].update(sha256="0" * 64),
            lambda s: s["goal_contract"]["body"]["risk_acceptance"]["review"].update(output_sha256="0" * 64),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                state = self.state()
                change(state)
                self.assertFalse(evidence.ready(state, self.revision))

    def test_missing_changed_or_linked_summary_and_output_are_invalid(self):
        result = self.actual
        paths = [result["summary"], result["checks"][0]["output"]]
        for field in paths:
            path = Path(field)
            raw = path.read_bytes()
            for fault in ("changed", "missing", "symlink"):
                with self.subTest(path=path.name, fault=fault):
                    if fault == "changed":
                        path.write_bytes(raw + b"changed")
                    elif fault == "missing":
                        path.unlink()
                    else:
                        copy_path = path.with_suffix(".saved")
                        copy_path.write_bytes(raw)
                        path.unlink()
                        path.symlink_to(copy_path)
                    try:
                        self.assertFalse(evidence.ready(self.state(), self.revision))
                    finally:
                        if path.is_symlink():
                            path.unlink()
                        path.write_bytes(raw)
                        if fault == "symlink":
                            copy_path.unlink()

    def test_resealed_transcript_cannot_turn_changed_lifecycle_values_or_a_soft_exit_into_proof(self):
        for fault in ("stale-ack", "kill-exit"):
            with self.subTest(fault=fault):
                state = self.state()
                result = state["validation"]["check_replay"]["risk_acceptance"]
                row = next(item for item in result["checks"] if item["observation"]["protocol"].startswith("lease_"))
                transcript = copy.deepcopy(row["observation"])
                if fault == "stale-ack":
                    transcript["phases"][1]["calls"]["stale_ack"] = True
                else:
                    transcript["phases"][0]["worker"]["exit_code"] = 0
                directory = tempfile.TemporaryDirectory(dir=self.root, prefix="changed-transcript-")
                self.addCleanup(directory.cleanup)
                output = Path(directory.name) / "transcript.json"
                output.write_bytes(encode(transcript))
                row.update(output=str(output), output_sha256=util.file_hash(output), observation=transcript)
                self.save_result(state, result)
                self.assertFalse(evidence.ready(state, self.revision))

    def test_summary_cannot_omit_or_disagree_with_a_required_case(self):
        mutations = [
            lambda r: r["checks"].pop(),
            lambda r: r["checks"][0].update(command='python3 -c "pass"'),
            lambda r: r["checks"][0].update(exit_code=True),
            lambda r: r["checks"][0].update(
                output=r["checks"][1]["output"], output_sha256=r["checks"][1]["output_sha256"]
            ),
            lambda r: r["checks"][0].update(observation=None),
            lambda r: r.update(timeout_seconds=31),
            lambda r: r.update(verdict="FAIL"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(change=index):
                state = self.state()
                result = state["validation"]["check_replay"]["risk_acceptance"]
                mutate(result)
                self.save_result(state, result)
                self.assertFalse(evidence.ready(state, self.revision))
        state = self.state()
        state["validation"]["check_replay"]["risk_acceptance"]["checks"][0]["error"] = "changed after summary"
        self.assertFalse(evidence.ready(state, self.revision))

    def test_selected_task_replay_cannot_satisfy_whole_product_completion(self):
        state = self.state()
        state["current_task"] = {"acceptance_criteria": ["AC-queue"]}
        result = evidence.replay(
            state, self.workspace, self.root / "selected", verify.scratch_run, timeout=30, source_revision=self.revision
        )
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(1, len(result["checks"]))
        state["validation"]["check_replay"]["risk_acceptance"] = result
        self.assertFalse(evidence.ready(state, self.revision))
        context = {"required_checks": [{"relation": "contributes_to", "criterion_ids": ["AC-queue"]}]}
        calls = []
        self.assertIsNone(
            evidence.replay(
                state,
                self.workspace,
                self.root / "contribution",
                lambda *args, **kwargs: calls.append(kwargs),
                timeout=30,
                source_revision=self.revision,
                progressive_context=context,
            )
        )
        self.assertEqual([], calls)

    def test_one_shared_deadline_never_grants_a_second_case_more_time(self):
        state = self.state()
        calls = []

        def existing_actual_receipt(workspace, out, *, command, timeout):
            calls.append(timeout)
            row = next(row for row in self.actual["checks"] if row["command"] == command)
            return {key: row[key] for key in ("exit_code", "timed_out", "error", "output", "output_sha256")}

        # Replay consumes the first case's actual receipt, then exhausts the same deadline.
        with patch.object(evidence.time, "monotonic", side_effect=[100, 100, 131]):
            with self.assertRaisesRegex(ValueError, "shared lifecycle observation deadline"):
                evidence.replay(
                    state,
                    self.workspace,
                    self.root / "budget",
                    existing_actual_receipt,
                    timeout=900,
                    source_revision=self.revision,
                    all_observations=True,
                )
        self.assertEqual([30], calls)
        result = util.read_object(next((self.root / "budget").rglob("summary.json")))
        self.assertEqual(30, result["timeout_seconds"])
        self.assertEqual("PASS", result["checks"][0]["observation"]["verdict"])
        self.assertTrue(result["checks"][1]["timed_out"])
        self.assertIsNone(result["checks"][1]["output"])
        for timeout in (True, 0, -1, 901):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                evidence.replay(
                    state,
                    self.workspace,
                    self.root / "invalid",
                    existing_actual_receipt,
                    timeout=timeout,
                    source_revision=self.revision,
                )
        self.assertEqual([30], calls)

    def test_supplied_short_allowance_is_used_and_ordinary_tasks_launch_nothing(self):
        state = self.state()
        calls = []

        def unavailable(workspace, out, *, command, timeout):
            calls.append(timeout)
            return {"error": "Control did not launch a process"}

        with patch.object(evidence.time, "monotonic", side_effect=[100, 100.25, 102]):
            with self.assertRaises(ValueError):
                evidence.replay(
                    state,
                    self.workspace,
                    self.root / "short-budget",
                    unavailable,
                    timeout=2,
                    source_revision=self.revision,
                    all_observations=True,
                )
        self.assertEqual([1.75], calls)
        ordinary = {
            "task": "Build a helpful CLI",
            "workspace": str(self.workspace),
            "goal_contract": {"body": {"acceptance_criteria": []}},
        }
        self.assertIsNone(
            evidence.replay(
                ordinary, self.workspace, self.root / "ordinary", unavailable, timeout=2, source_revision=self.revision
            )
        )
        self.assertTrue(evidence.ready(ordinary, self.revision))
        self.assertEqual([1.75], calls)


if __name__ == "__main__":
    unittest.main()
