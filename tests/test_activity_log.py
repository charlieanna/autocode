"""Isolated tests for AutoCode's always-on activity log: no model calls."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_activity_log as activity_log
import autocode_status

SECRET = "SECRET-CONTENT-7f3a"


def state(**extra):
    base = {"task": f"Build {SECRET}", "workspace": "/w", "status": "RUNNING", "phase": "EXECUTING",
            "iteration": 1, "next_stage": "terra", "stages": [],
            "current_task": {"id": "task-1", "objective": SECRET},
            "goal_contract": {"revision": 2, "approval_status": "approved", "body": {"intended_outcome": SECRET}},
            "findings_ledger": [], "stop_reason": None, "user_events": [{"answer": SECRET}]}
    base.update(extra)
    return base


def stage(name, **extra):
    return {"stage": name, "role": name, "iteration": 1, "engine": "codex", "exit_code": 0,
            "command": ["codex", "exec", "--model", "gpt-x", SECRET], "duration_seconds": 1.5,
            "changed_files": [f"{SECRET}.py"], "output": f"/runs/{SECRET}.json",
            "metrics": {"provider_tokens": {"input_tokens": 10, "output_tokens": 5}}, **extra}


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name)
        self.path = self.run_dir / "state.json"
        activity_log._announced.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def entries(self):
        return [json.loads(line) for line in (self.run_dir / "activity.jsonl").read_text().splitlines()]

    def test_persist_logs_invocation_transitions_and_stages_without_content(self):
        argv = ["autocode", "--run-dir", "r", f"--answer=Q1={SECRET}", "--feedback", SECRET]
        with patch.object(sys, "argv", argv), patch.dict(os.environ, {"AUTOCODE_CALLER": "unattended"}):
            autocode_status.persist(self.path, state())
            autocode_status.persist(self.path, state(active_stage={"stage": "terra", "role": "terra",
                                                                     "started_at": "t1", "command": ["--model", "gpt-x"]}))
            ledger = [{"status": "open", "severity": "high", "finding": SECRET}]
            autocode_status.persist(self.path, state(next_stage="sol", stages=[stage("terra")], findings_ledger=ledger))
            autocode_status.persist(self.path, state(status="PAUSED_INVALID_OUTPUT", stages=[stage("terra")],
                                                     findings_ledger=ledger, stop_reason="Inspect the saved output"))
        entries = self.entries()
        self.assertEqual(["invocation", "transition", "plan_revision", "stage_started", "transition",
                          "stage_finished", "findings", "transition"], [e["event"] for e in entries])
        self.assertEqual({"event": "invocation", "program": "autocode", "caller": "unattended",
                          "flags": ["--run-dir", "--answer", "--feedback"]},
                         {k: entries[0][k] for k in ("event", "program", "caller", "flags")})
        finished = entries[5]
        self.assertEqual(("gpt-x", 0, 1, 10, 5), (finished["model"], finished["exit_code"], finished["changed_files"],
                                                 finished["tokens"]["input_tokens"], finished["tokens"]["output_tokens"]))
        self.assertEqual({"open/high": 1}, entries[6]["counts"])
        self.assertEqual(("PAUSED_INVALID_OUTPUT", "Inspect the saved output"),
                         (entries[7]["status"], entries[7]["stop_reason"]))
        self.assertNotIn(SECRET, (self.run_dir / "activity.jsonl").read_text())
        self.assertIn(SECRET, self.path.read_text())  # the checkpoint itself is unchanged

    def test_unchanged_saves_append_nothing_and_a_new_process_resumes_from_the_log(self):
        autocode_status.persist(self.path, state())
        size = (self.run_dir / "activity.jsonl").stat().st_size
        autocode_status.persist(self.path, state())
        self.assertEqual(size, (self.run_dir / "activity.jsonl").stat().st_size)
        activity_log._announced.clear()  # as if a later command started
        autocode_status.persist(self.path, state(status="TASK_COMPLETE", phase="COMPLETE"))
        self.assertEqual(["invocation", "transition"], [e["event"] for e in self.entries()[-2:]])
        self.assertEqual(["phase", "status"], self.entries()[-1]["changed"])

    def test_approvals_and_human_reviews_are_logged(self):
        autocode_status.persist(self.path, state(goal_contract={"revision": 3, "approval_status": "draft"}))
        autocode_status.persist(self.path, state(goal_contract={"revision": 3, "approval_status": "approved"},
                                                 human_reviews={"C1": {"actor": "user_cli"}}))
        events = [(e["event"], e.get("status") or e.get("criterion")) for e in self.entries()[-2:]]
        self.assertEqual([("plan_approval", "approved"), ("human_review", "C1")], events)

    def test_logging_failure_never_fails_the_checkpoint(self):
        (self.run_dir / "activity.jsonl").mkdir()  # appending to a directory raises
        autocode_status.persist(self.path, state())
        self.assertEqual("RUNNING", json.loads(self.path.read_text())["status"])


if __name__ == "__main__":
    unittest.main()
