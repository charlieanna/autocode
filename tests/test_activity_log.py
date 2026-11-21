"""Isolated tests for AutoCode's always-on activity log: no model calls."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_activity_log as activity_log
import autocode_preflight_attempts as preflight_attempts
import autocode_status

SECRET = "SECRET-CONTENT-7f3a"
DRIFT = "Repository changed during read-only review; preserve result and revalidate"


def state(**extra):
    base = {
        "task": f"Build {SECRET}",
        "workspace": "/w",
        "status": "RUNNING",
        "phase": "EXECUTING",
        "iteration": 1,
        "next_stage": "builder",
        "stages": [],
        "current_task": {"id": "task-1", "objective": SECRET},
        "goal_contract": {"revision": 2, "approval_status": "approved", "body": {"intended_outcome": SECRET}},
        "findings_ledger": [],
        "stop_reason": None,
        "user_events": [{"answer": SECRET}],
    }
    base.update(extra)
    return base


def stage(name, **extra):
    return {
        "stage": name,
        "role": name,
        "iteration": 1,
        "engine": "codex",
        "exit_code": 0,
        "command": ["codex", "exec", "--model", "gpt-x", SECRET],
        "duration_seconds": 1.5,
        "changed_files": [f"{SECRET}.py"],
        "output": f"/runs/{SECRET}.json",
        "metrics": {"provider_tokens": {"input_tokens": 10, "output_tokens": 5}},
        **extra,
    }


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

    def finished(self, stage_name):
        return [e for e in self.entries() if e["event"] == "stage_finished" and e["stage"] == stage_name]

    def probe(self, run, clock):
        def save():
            autocode_status.persist(self.path, run)

        adapter = SimpleNamespace(NAME="opencode")
        return preflight_attempts.Attempt(SimpleNamespace(), run, self.run_dir, adapter, "zai/glm-test", save, clock)

    def retained_review(self, **extra):
        base = self.run_dir / "iterations" / "001" / "review-completion-01"
        return {
            "stage": "review_completion",
            "role": "review_completion",
            "iteration": 1,
            "engine": "codex",
            "command": ["codex", "exec", "--model", "gpt-x"],
            "events": str(base.with_suffix(".jsonl")),
            "output": str(base.with_suffix(".json")),
            "started_at": "2026-11-20T10:00:00+00:00",
            "finished_at": "2026-11-20T10:01:00+00:00",
            "duration_seconds": 60.0,
            "exit_code": 0,
            "after_ref": str(base.with_suffix(".after.json")),
            **extra,
        }

    def assert_no_attempt_paths(self, record):
        text = (self.run_dir / "activity.jsonl").read_text()
        self.assertNotIn(record["events"], text)
        self.assertNotIn(record["output"], text)

    def test_persist_logs_invocation_transitions_and_stages_without_content(self):
        argv = ["autocode", "--run-dir", "r", f"--answer=Q1={SECRET}", "--feedback", SECRET]
        with patch.object(sys, "argv", argv), patch.dict(os.environ, {"AUTOCODE_CALLER": "unattended"}):
            autocode_status.persist(self.path, state())
            autocode_status.persist(
                self.path,
                state(
                    active_stage={
                        "stage": "builder",
                        "role": "builder",
                        "started_at": "t1",
                        "command": ["--model", "gpt-x"],
                    }
                ),
            )
            ledger = [{"status": "open", "severity": "high", "finding": SECRET}]
            autocode_status.persist(
                self.path, state(next_stage="tester", stages=[stage("builder")], findings_ledger=ledger)
            )
            autocode_status.persist(
                self.path,
                state(
                    status="PAUSED_INVALID_OUTPUT",
                    stages=[stage("builder")],
                    findings_ledger=ledger,
                    stop_reason="Inspect the saved output",
                ),
            )
        entries = self.entries()
        self.assertEqual(
            [
                "invocation",
                "transition",
                "plan_revision",
                "stage_started",
                "transition",
                "stage_finished",
                "findings",
                "transition",
            ],
            [e["event"] for e in entries],
        )
        self.assertEqual(
            {
                "event": "invocation",
                "program": "autocode",
                "caller": "unattended",
                "flags": ["--run-dir", "--answer", "--feedback"],
            },
            {k: entries[0][k] for k in ("event", "program", "caller", "flags")},
        )
        finished = entries[5]
        self.assertEqual(
            ("gpt-x", 0, 1, 10, 5),
            (
                finished["model"],
                finished["exit_code"],
                finished["changed_files"],
                finished["tokens"]["input_tokens"],
                finished["tokens"]["output_tokens"],
            ),
        )
        self.assertEqual({"open/high": 1}, entries[6]["counts"])
        self.assertEqual(
            ("PAUSED_INVALID_OUTPUT", "Inspect the saved output"), (entries[7]["status"], entries[7]["stop_reason"])
        )
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
        autocode_status.persist(
            self.path,
            state(
                goal_contract={"revision": 3, "approval_status": "approved"},
                human_reviews={"C1": {"actor": "user_cli"}},
            ),
        )
        events = [(e["event"], e.get("status") or e.get("criterion")) for e in self.entries()[-2:]]
        self.assertEqual([("plan_approval", "approved"), ("human_review", "C1")], events)

    def test_ac1_preflight_probe_finishes_once_with_timing(self):
        run = state()
        autocode_status.persist(self.path, run)
        ticks = iter([100.0, 102.5])
        attempt = self.probe(run, lambda: next(ticks))
        attempt.admit({"schema": 1})  # _publish appends the admitted record and saves the checkpoint
        self.assertEqual([], self.finished("model_preflight"))
        attempt.finish({"exit_code": 0, "timed_out": False, "metrics": {}})
        finished = self.finished("model_preflight")
        self.assertEqual(1, len(finished))
        self.assertEqual(
            ("zai/glm-test", 0, 2.5), tuple(finished[0][k] for k in ("model", "exit_code", "duration_seconds"))
        )
        self.assertIsNotNone(finished[0]["finished_at"])
        autocode_status.persist(self.path, run)
        autocode_status.persist(self.path, run)
        activity_log._announced.clear()  # re-projection from a new process
        autocode_status.persist(self.path, run)
        self.assertEqual(1, len(self.finished("model_preflight")))
        self.assert_no_attempt_paths(attempt.record)

    def test_ac2_drift_stopped_review_has_one_unaccepted_outcome(self):
        review = self.retained_review(unusable_reason=DRIFT)
        autocode_status.persist(self.path, state(next_stage="review_completion", active_stage=review))
        self.assertEqual([], self.finished("review_completion"))
        paused = state(status="PAUSED_STALE_VALIDATION", stop_reason=DRIFT, active_stage=review)
        autocode_status.persist(self.path, paused)
        finished = self.finished("review_completion")
        self.assertEqual(1, len(finished))
        self.assertEqual(
            ("unaccepted", DRIFT, "2026-11-20T10:01:00+00:00", 60.0),
            tuple(finished[0][k] for k in ("outcome", "outcome_reason", "finished_at", "duration_seconds")),
        )
        autocode_status.persist(self.path, paused)
        activity_log._announced.clear()  # re-projection from a new process
        autocode_status.persist(self.path, paused)
        autocode_status.persist(self.path, state(stages=[review]))  # recovery moves the record into stages
        self.assertEqual(1, len(self.finished("review_completion")))
        self.assert_no_attempt_paths(review)

    def test_drift_rejected_review_set_aside_with_archived_artifacts_is_not_announced_again(self):
        review = self.retained_review(unusable_reason=DRIFT)
        autocode_status.persist(
            self.path, state(status="PAUSED_STALE_VALIDATION", stop_reason=DRIFT, active_stage=review)
        )
        archive = self.run_dir / "iterations" / "001" / "rejected-1"
        set_aside = {
            **review,
            "events": str(archive / Path(review["events"]).name),
            "output": str(archive / Path(review["output"]).name),
            "archived_paths": {
                review["events"]: str(archive / Path(review["events"]).name),
                review["output"]: str(archive / Path(review["output"]).name),
            },
            "rejected": True,
        }
        autocode_status.persist(self.path, state(stages=[set_aside]))  # --abandon-stage archives the attempt
        autocode_status.persist(self.path, state(stages=[set_aside, stage("builder")]))
        self.assertEqual(["unaccepted"], [e["outcome"] for e in self.finished("review_completion")])
        self.assertEqual(1, len(self.finished("builder")))
        self.assertNotIn(str(archive), (self.run_dir / "activity.jsonl").read_text())

    def test_ac3_uncertain_probe_waits_until_settled(self):
        run = state()
        ticks = iter([10.0, 12.0])
        attempt = self.probe(run, lambda: next(ticks))
        unverifiable = {
            "schema": 1,
            "receipt": str(self.run_dir / "missing" / "supervision.json"),  # no keeper receipt to verify
            "owner": {"pid": 101, "birth_identity": 1},
            "keeper": {"pid": 102, "birth_identity": 2},
            "provider": {"pid": 103, "birth_identity": 3},
        }
        attempt.admit(unverifiable)
        attempt.finish(error=RuntimeError("transport lost"))
        self.assertIsNotNone(attempt.record["finished_at"])
        self.assertIs(False, attempt.record["preflight_complete"])
        self.assertEqual([], self.finished("model_preflight"))
        attempt.record["preflight_complete"] = True  # as reconciliation records once cleanup is verified
        attempt.record["preflight_outcome"]["status"] = "PAUSED_PROVIDER_UNCERTAIN"
        autocode_status.persist(self.path, run)
        autocode_status.persist(self.path, run)
        finished = self.finished("model_preflight")
        self.assertEqual(1, len(finished))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", finished[0].get("outcome"))

    def test_ac19_unmarked_retained_attempt_waits_for_its_saved_row(self):
        review = self.retained_review()
        uncertain = state(status="PAUSED_UNCERTAIN_STAGE", stop_reason="Inspect the stage", active_stage=review)
        autocode_status.persist(self.path, uncertain)
        self.assertEqual([], self.finished("review_completion"))
        autocode_status.persist(self.path, state(stages=[review]))
        autocode_status.persist(self.path, state(stages=[review]))
        autocode_status.persist(self.path, state(stages=[review]))
        finished = self.finished("review_completion")
        self.assertEqual(1, len(finished))
        self.assertEqual(
            ("completed", "2026-11-20T10:01:00+00:00"), (finished[0].get("outcome"), finished[0]["finished_at"])
        )
        self.assert_no_attempt_paths(review)

    def test_logging_failure_never_fails_the_checkpoint(self):
        (self.run_dir / "activity.jsonl").mkdir()  # appending to a directory raises
        autocode_status.persist(self.path, state())
        self.assertEqual("RUNNING", json.loads(self.path.read_text())["status"])


if __name__ == "__main__":
    unittest.main()
