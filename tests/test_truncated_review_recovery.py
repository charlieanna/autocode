"""Bounded recovery for token-truncated read-only review reports."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_stage_recovery as recovery
import autocode_support as support

from tests import test_subprocess as subprocess_test_support


class TruncatedReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.snapshot = {"revision": "source-revision", "head": "head", "files": {}}
        self.schema = self.run / "schemas/review.json"
        self.schema.parent.mkdir(parents=True)
        self.schema.write_text("{}")

    def attempt(self, stage="sol", ordinal=1, *, report_only=False):
        actual_stage = f"{stage}_report_repair" if report_only else stage
        base = self.run / "iterations/001" / f"{actual_stage}-{ordinal:02d}"
        base.parent.mkdir(parents=True, exist_ok=True)
        before = Path(str(base) + ".before.json")
        before.write_text(json.dumps(self.snapshot))
        events = Path(str(base) + ".jsonl")
        session, message = "ses_limit", f"msg_{ordinal}"
        events.write_text(
            "\n".join(
                json.dumps(row)
                for row in [
                    {"type": "step_start", "sessionID": session, "part": {"id": "p_start", "sessionID": session}},
                    {
                        "type": "text",
                        "sessionID": session,
                        "part": {
                            "id": "p_text",
                            "sessionID": session,
                            "messageID": message,
                            "text": '{"verdict":"PASS","checks":[...',
                        },
                    },
                    {
                        "type": "step_finish",
                        "sessionID": session,
                        "part": {
                            "id": "p_finish",
                            "sessionID": session,
                            "messageID": message,
                            "reason": "length",
                            "tokens": {"input": 12, "output": 7, "reasoning": 15, "cache": {"read": 2, "write": 0}},
                        },
                    },
                ]
            )
            + "\n"
        )
        record = {
            "role": "sol" if stage == "sol" else "completion",
            "stage": actual_stage,
            "iteration": 1,
            "started_at": "2026-10-01T00:00:00+00:00",
            "finished_at": "2026-10-01T00:00:02+00:00",
            "duration_seconds": 2,
            "exit_code": 0,
            "events": str(events),
            "output": str(base) + ".json",
            "schema": str(self.schema),
            "before_ref": str(before),
            "engine": "opencode",
            "processes": [],
            "supports_sessions": True,
            "source_revision": "source-revision",
            "contract_hash": "contract-hash",
        }
        if report_only:
            record.update(report_only=True, original_stage=stage)
        return record

    def state(self, record):
        return {
            "status": "RUNNING",
            "workspace": str(self.root),
            "next_stage": record.get("original_stage", record["stage"]),
            "iteration": 1,
            "active_seconds": 0,
            "active_stage": record,
            "settings": {"report_repair": {"max_attempts": 2}, "roles": {}},
            "goal_contract": {"hash": "contract-hash"},
            "stages": [],
            "history": [],
            "sessions": {},
        }

    def invoke_recovery(self, state, error=None):
        error = error or support.Paused("PAUSED_PROVIDER_UNCERTAIN", "Output token limit")

        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))

        with (
            patch.object(support, "snapshot", return_value=self.snapshot),
            patch.object(recovery.interventions, "pending", return_value=[]),
            patch.object(recovery.records, "write_json", side_effect=save),
        ):
            return recovery.automatically_recover_truncated_review(state, self.run, self.root, error)

    def test_recovery_queues_report_only_repair_and_archives_usage_and_events(self):
        state = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(state))
        self.assertNotIn("active_stage", state)
        self.assertEqual(("RUNNING", "REPORT_REPAIR", "sol"), (state["status"], state["phase"], state["next_stage"]))
        pending = state["pending_report_repair"]
        original = pending["original"]
        self.assertTrue(original["truncated_output"])
        self.assertTrue(Path(original["events"]).is_file())
        self.assertEqual(22, original["metrics"]["provider_tokens"]["output_tokens"])
        self.assertEqual(0, original["metrics"]["completed_turns"])
        source = runner.repair_report_source(original)
        self.assertFalse(source["content"]["complete_report"])
        self.assertEqual('{"verdict":"PASS","checks":[...', source["content"]["partial_text"])

    def test_report_only_retry_keeps_original_and_consumes_existing_bound(self):
        first = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(first))
        first["pending_report_repair"]["attempts"] = 1
        retry = self.attempt("sol", 1, report_only=True)
        first["active_stage"] = retry
        self.assertTrue(self.invoke_recovery(first))
        self.assertTrue(first["pending_report_repair"]["original"]["stage"] == "sol")
        self.assertTrue(first["pending_report_repair"]["latest_rejected"]["truncated_output"])
        self.assertEqual(1, first["pending_report_repair"]["attempts"])
        latest = runner.repair_report_source(first["pending_report_repair"]["latest_rejected"])
        self.assertEqual("truncated_provider_response", latest["format"])

    def test_exhausted_repair_limit_keeps_active_truncated_attempt_and_original(self):
        state = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(state))
        original_path = state["pending_report_repair"]["original"]["events"]
        state["pending_report_repair"]["attempts"] = 2
        state["active_stage"] = self.attempt("sol", 2, report_only=True)
        with self.assertRaisesRegex(support.Paused, "attempts exhausted"):
            self.invoke_recovery(state)
        self.assertEqual(original_path, state["pending_report_repair"]["original"]["events"])
        self.assertTrue(Path(original_path).is_file())
        self.assertEqual("sol_report_repair", state["active_stage"]["stage"])

    def test_changed_source_wrong_stage_and_non_truncation_do_not_recover(self):
        record = self.attempt("terra")
        state = self.state(record)
        self.assertFalse(self.invoke_recovery(state))
        self.assertIn("active_stage", state)
        record = self.attempt("sol", ordinal=2)
        state = self.state(record)
        changed = {**self.snapshot, "revision": "changed"}
        with patch.object(support, "snapshot", return_value=changed):
            with patch.object(recovery.interventions, "pending", return_value=[]):
                self.assertFalse(
                    recovery.automatically_recover_truncated_review(
                        state, self.run, self.root, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "Output token limit")
                    )
                )
        record = self.attempt("sol", ordinal=3)
        Path(record["events"]).write_text("{}\n")
        state = self.state(record)
        self.assertFalse(self.invoke_recovery(state))


class TruncatedReviewCliTests(unittest.TestCase):
    new_run_engine_args = ("--engine", "opencode")

    def setUp(self):
        subprocess_test_support.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / "tools" / "fake_opencode.py"
        provider = self.root / "fixture-bin" / "opencode"
        shutil.copy2(source, provider)
        provider.chmod(0o755)
        from .opencode_fixture_cli import entrypoint

        self.entry = entrypoint(self.entry)
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"

    launch = subprocess_test_support.SubprocessFlow.launch
    saved = subprocess_test_support.SubprocessFlow.saved

    def test_cli_repairs_truncated_validator_report_without_replaying_checks(self):
        self.env["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] = "sol"
        self.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(1, sum(row["stage"] == "sol" for row in state["stages"]))
        self.assertEqual(1, sum(row["stage"] == "sol_report_repair" for row in state["stages"]))
        original = next(row for row in state["stages"] if row["stage"] == "sol")
        repair = next(row for row in state["stages"] if row["stage"] == "sol_report_repair")
        self.assertTrue(original["truncated_output"])
        self.assertEqual(0, original["metrics"]["completed_turns"])
        self.assertGreater(original["metrics"]["provider_tokens"]["output_tokens"], 0)
        self.assertEqual(original["events"], repair["applied_original_events"])
        self.assertFalse(any(event.get("type") == "tool_use" for event in support.events(repair["events"])))
        self.assertEqual("accepted", state["report_repair_history"][-1]["result"])


if __name__ == "__main__":
    unittest.main()
