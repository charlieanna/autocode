"""Regression tests for parallel Builder user_request deferral and boundary-save isolation."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_builder_policy as builder_policy
import autocode_builder_worker as worker
import autocode_dispatch as dispatch
import autocode_goals as goals
import autocode_operational_disposition as disposition
import autocode_resolver_human as human
import autocode_resolver_runtime as resolver_runtime
import autocode_run_records as run_records
import autocode_support as support
import goal_fixtures


class SharedCauseDParallelBuilderUserRequestTests(unittest.TestCase):
    """Parallel Builder user_request defers cleanly to serial on untouched worktree and retries cleanly when modified."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory(prefix="parallel-user-req-"))
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "coord.py").write_text("x = 1\n")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "add", "coord.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "commit", "-qm", "base"],
            check=True,
        )
        self.parent_run = self.root / ".autocode" / "runs" / "parent"
        self.member_run = self.root / ".autocode" / "runs" / "member"
        self.parent_run.mkdir(parents=True)
        self.member_run.mkdir(parents=True)

    def _seed_worker(self, *, modified: bool):
        base_commit = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        baseline = support.snapshot(self.root)
        state = {
            "version": 3,
            "workspace": str(self.root),
            "task": "Build mapreduce",
            "task_id": "mr-1",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "history": [],
            "parent_run": str(self.parent_run),
            "parent_batch": "batch-1",
            "settings": {
                "engine": "opencode",
                "provider": "opencode",
                "roles": {
                    name: {"model": f"fixture-{name}", "reasoning_effort": "high"}
                    for name in ("builder", "tester", "completion", "task_planner")
                },
                "builder_retry": dict(builder_policy.DEFAULTS),
                "report_repair": {"max_attempts": 2},
                "limits": {},
                "headroom": {"enabled": False},
            },
        }
        goal_fixtures.approve_fixture(state, goals)
        contract = state["goal_contract"]
        task = {
            "id": "task-m3",
            "kind": "implement",
            "milestone_id": "M3",
            "objective": "Implement workloads",
            "affected_paths": ["workloads.py"],
            "requirements": ["r1"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["python3 -m unittest"],
            "findings": [],
            "contract_hash": contract["hash"],
            "contract_revision": contract["revision"],
            "source_revision": baseline["revision"],
        }
        state["current_task"] = task
        batch = {
            "id": "batch-1",
            "status": "BUILDING",
            "contract_hash": contract["hash"],
            "base_commit": base_commit,
            "baseline": baseline,
            "directory": str(self.parent_run / "orch"),
            "workers": [
                {
                    "milestone_id": "M3",
                    "run_dir": str(self.member_run),
                    "workspace": str(self.root),
                    "status": "RUNNING",
                    "task": copy.deepcopy(task),
                }
            ],
        }
        parent_state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.parent_run),
            "task": "Build mapreduce",
            "task_id": "mr-1",
            "status": "RUNNING",
            "next_stage": "orchestrator",
            "iteration": 1,
            "goal_contract": copy.deepcopy(contract),
            "current_task": copy.deepcopy(task),
            "orchestration_batch": batch,
            "settings": copy.deepcopy(state["settings"]),
            "stages": [],
            "history": [],
        }
        runner.write_json(self.parent_run / "state.json", parent_state)
        if modified:
            (self.root / "workloads.py").write_text("# partial\n")
        current = support.snapshot(self.root)
        out = self.member_run / "builder-01.json"
        out.write_text("{}")
        rec = {
            "role": "builder",
            "stage": "builder",
            "iteration": 1,
            "output": str(out),
            "events": str(self.member_run / "builder-01.jsonl"),
            "exit_code": 0,
            "started_at": "2026-11-16T10:00:00+00:00",
            "finished_at": "2026-11-16T10:00:05+00:00",
            "duration_seconds": 5.0,
            "accounted": True,
            "source_revision": current["revision"],
            "changed_files": ["workloads.py"] if modified else [],
        }
        state["stages"].append(rec)
        state["next_stage"] = "review_completion"
        state["implementation"] = {
            **goal_fixtures.envelope(state),
            "source_revision": current["revision"],
            "workspace": str(self.root),
            "changed_files": ["workloads.py"] if modified else [],
            "user_request": {
                "kind": "blocker",
                "discovered": "Needs coord.py owned by sibling M2",
                "impact": "Cannot integrate in isolated worktree",
                "decision_needed": "Coordinate shared coordinator changes",
                "options": ["Defer after M2", "Adjust plan"],
                "proposed_delta": "Allow coord.py",
            },
        }
        runner.write_json(self.member_run / "state.json", state)
        return parent_state, state

    def test_untouched_parallel_user_request_defers_to_serial_and_collects_without_pausing(self):
        parent_state, _ = self._seed_worker(modified=False)
        with (
            patch.object(runner.autopilot.regression, "before_review", return_value=None),
            patch.object(runner.autocode_providers, "resolve", return_value=runner.opencode),
            patch.object(runner, "run_role", side_effect=AssertionError("Must not relaunch model")),
        ):
            self.assertEqual(0, worker.main(self.member_run, "start"))
        result = json.loads((self.member_run / "result.json").read_text())
        self.assertEqual(builder_policy.SERIAL_USER_REQUEST, result["status"])

        batch = parent_state["orchestration_batch"]
        batch["workers"][0]["status"] = builder_policy.SERIAL_USER_REQUEST
        dispatch.collect(parent_state, self.root, self.parent_run, batch)
        self.assertEqual("DEFERRING", batch["status"])
        self.assertEqual(["M3"], batch["deferred"])

    def test_modified_parallel_user_request_pauses_then_clears_stale_implementation_on_retry(self):
        _, _ = self._seed_worker(modified=True)
        with (
            patch.object(runner.autopilot.regression, "before_review", return_value=None),
            patch.object(runner.autocode_providers, "resolve", return_value=runner.opencode),
            patch.object(runner, "run_role", side_effect=AssertionError("No launch on start with existing impl")),
        ):
            self.assertEqual(2, worker.main(self.member_run, "start"))
        paused = json.loads((self.member_run / "result.json").read_text())
        self.assertEqual("PAUSED_ORCHESTRATOR_WORKER", paused["status"])

        launched = []

        def fresh_builder(**kwargs):
            launched.append(kwargs["role"])
            current = support.snapshot(self.root)
            out = self.member_run / "builder-02.json"
            val = {
                **goal_fixtures.envelope(kwargs["state"]),
                "evidence_refs": [str(out)],
                "commands_run": [],
                "results": ["Built workloads"],
                "changed_files": ["workloads.py"],
                "remaining_risks": [],
                "untested_behavior": [],
                "addressed_requirements": ["r1"],
            }
            out.write_text(json.dumps(val))
            ev = self.member_run / "builder-02.jsonl"
            ev.write_text(json.dumps({"type": "turn.completed"}) + "\n")
            bef = self.member_run / "builder-02.before.json"
            aft = self.member_run / "builder-02.after.json"
            sch = self.member_run / "schema.json"
            bef.write_text(json.dumps(current))
            aft.write_text(json.dumps(current))
            rec = {
                "role": "builder",
                "stage": "builder",
                "iteration": 1,
                "output": str(out),
                "events": str(ev),
                "before_ref": str(bef),
                "after_ref": str(aft),
                "schema": str(sch),
                "exit_code": 0,
                "started_at": "2026-11-16T10:01:00+00:00",
                "finished_at": "2026-11-16T10:01:02+00:00",
                "metrics": {},
                "task_id": kwargs["state"]["current_task"]["id"],
                "contract_hash": kwargs["state"]["goal_contract"]["hash"],
                "contract_revision": kwargs["state"]["goal_contract"]["revision"],
                "source_revision": current["revision"],
                "changed_files": ["workloads.py"],
                "diff_ref": None,
            }
            kwargs["state"]["active_stage"] = rec
            return val, rec

        with (
            patch.object(runner.autopilot.regression, "before_review", return_value=None),
            patch.object(runner.autocode_providers, "resolve", return_value=runner.opencode),
            patch.object(worker.stage_context, "context_packet", return_value=("prompt", {})),
            patch.object(runner, "run_role", side_effect=fresh_builder),
        ):
            self.assertEqual(0, worker.main(self.member_run, "retry"))
        self.assertEqual(["builder"], launched)
        rebuilt = json.loads((self.member_run / "result.json").read_text())
        self.assertEqual("BUILT", rebuilt["status"])

    def test_refused_historical_operational_publication_is_not_rewritten_to_running_by_boundary_save(self):
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.parent_run),
            "task": "Build mapreduce",
            "task_id": "mr-1",
            "status": "PAUSED_RESOLVER_OPERATIONAL",
            "phase": "PAUSED_OR_BLOCKED",
            "next_stage": "review_completion",
            "iteration": 1,
            "settings": {"limits": {}},
            "stages": [{"stage": "tester", "output": str(self.parent_run / "val.json"), "exit_code": 0}],
            "history": [],
            "user_events": [],
        }
        (self.parent_run / "val.json").write_text("{}")
        goal_fixtures.approve_fixture(state, goals)
        budget = disposition.budget(state, "PAUSED_RESOLVER_OPERATIONAL")
        receipt = resolver_runtime._operational_receipt(
            state,
            self.parent_run,
            "hold",
            "Retained recovery exhaustion",
            {"status": "PAUSED_RESOLVER_OPERATIONAL", "stage": "review_completion", "budget": budget},
        )
        human.queue(
            state,
            "operational_exhaustion",
            {"pause_status": "PAUSED_RESOLVER_OPERATIONAL", "stage": "review_completion", "budget": budget},
            request={
                "kind": "blocker",
                "discovered": "Retained fixture exhaustion",
                "impact": "No stage can start",
                "decision_needed": "Provide corrective information",
                "options": ["Provide corrective information", "Leave paused"],
            },
            evidence={"resolver_receipt_id": receipt},
            next_stage="review_completion",
        )
        human._publish(
            state,
            {
                "version": 1,
                "issuer": "resolver",
                "binding": human._binding(state),
                "proposal": copy.deepcopy(state[human.PRIVATE]),
                "action": "escalate",
                "reason": "Historical operational request",
            },
        )
        run_records.write_json(self.parent_run / "state.json", state)
        saved = json.loads((self.parent_run / "state.json").read_text())
        self.assertIn(human.PUBLIC, saved)
        self.assertNotEqual("RUNNING", saved["status"])
        self.assertIsNotNone(disposition.held(saved))


if __name__ == "__main__":
    unittest.main()
