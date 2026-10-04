"""Recovered original sessions remain usable as read-only planning witnesses."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_repair_provenance as provenance
import autocode_util as util


class RecoverySessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        self.run_dir = self.workspace / ".autocode" / "runs" / "recovery"
        self.run_dir.mkdir(parents=True)
        self.state = {"workspace": str(self.workspace), "run_dir": str(self.run_dir),
            "iteration": 1, "settings": {}, "sessions": {"astra": "original-reviewer"},
            "stages": [], "history": [], "next_stage": "astra_finalize"}

    def attempt(self, session, *, report_only=False):
        stage = "astra_finalize" + ("_report_repair" if report_only else "")
        base = self.run_dir / stage
        util.atomic_json(base.with_suffix(".before.json"), util.snapshot(self.workspace))
        util.atomic_json(base.with_suffix(".json"), {"summary": "Recovered review"})
        schema = self.run_dir / "schema.json"
        util.atomic_json(schema, {"type": "object", "properties": {"summary": {"type": "string"}}})
        base.with_suffix(".jsonl").write_text(
            json.dumps({"type": "thread.started", "thread_id": session}) + "\n"
            + json.dumps({"type": "turn.completed"}) + "\n")
        record = {"stage": stage, "role": "astra", "iteration": 1, "exit_code": 0,
            "duration_seconds": 1, "processes": [], "supports_sessions": True,
            "expected_session": None, "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")), "schema": str(schema),
            "before_ref": str(base.with_suffix(".before.json"))}
        if report_only:
            record.update(report_only=True, original_stage="astra_finalize")
        self.state["active_stage"] = record
        return record

    def test_recovered_original_session_is_a_progressive_witness_before_dispatch(self):
        record = self.attempt("original-reviewer")
        self.assertNotIn("thread_id", record)

        def dispatch(state, stage, value, recovered, workspace, run_dir):
            self.assertEqual("original-reviewer", recovered.get("thread_id"))
            runner.save_record(state, recovered)

        with patch.object(runner, "commit_stage_result", side_effect=dispatch):
            runner.reconcile_active(self.state, self.run_dir, self.workspace)
        self.assertEqual("original-reviewer", self.state["stages"][-1]["thread_id"])
        original, session = provenance.witness(self.state, "astra_finalize", record["output"])
        self.assertEqual("original-reviewer", session)
        self.assertEqual(record["events"], original["events"])

    def test_expected_session_mismatch_preserves_the_boundary(self):
        record = self.attempt("unexpected-reviewer")
        record["expected_session"] = "expected-reviewer"
        before = copy.deepcopy(self.state)
        with patch.object(runner, "account_stage") as account, patch.object(runner, "commit_stage_result") as dispatch:
            with self.assertRaises(runner.support.Paused) as caught:
                runner.reconcile_active(self.state, self.run_dir, self.workspace)
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", caught.exception.status)
        self.assertEqual(before, self.state)
        account.assert_not_called()
        dispatch.assert_not_called()

    def test_recovered_repair_transport_does_not_replace_the_original_session(self):
        original = self.attempt("original-reviewer")
        with patch.object(runner, "commit_stage_result", side_effect=lambda state, stage, value, record, workspace, run_dir:
                          runner.save_record(state, record)):
            runner.reconcile_active(self.state, self.run_dir, self.workspace)
        original["rejected"] = True
        saved_original = copy.deepcopy(original)
        repair = self.attempt("repair-transport", report_only=True)
        self.state.update(status="RUNNING", pending_report_repair={
            "original": copy.deepcopy(original), "contract_hash": None, "attempts": 1,
            "pins": {original[key]: util.file_hash(original[key]) for key in ("output", "events")}})

        with patch.object(runner, "apply_result", side_effect=lambda state, stage, value, record, workspace, run_dir:
                          runner.save_record(state, record)):
            runner.reconcile_active(self.state, self.run_dir, self.workspace)
        self.assertEqual(saved_original, original)
        self.assertEqual("original-reviewer", self.state["sessions"]["astra"])
        self.assertNotIn("thread_id", repair)
        self.assertEqual("original-reviewer", original.get("thread_id"))
        witness, session = provenance.witness(self.state, "astra_finalize", repair["output"])
        self.assertEqual(original, witness)
        self.assertEqual("original-reviewer", session)
        self.assertEqual(util.file_hash(repair["output"]), self.state["report_repair_history"][-1]["output_hash"])
