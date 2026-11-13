"""Bounded per-pass reply budget for ``requirements_gather`` (#3345, Proposal 1)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode
import autocode_first_reply_budget as first_reply_budget
import autocode_interventions as interventions
import autocode_recovery_dispatch as recovery_dispatch
import autocode_report_repair_queue as report_repair_queue
import autocode_report_source as report_source
import autocode_requirements_handoff as requirements_handoff
import autocode_role_launch as role_launch
import autocode_stage_recovery as stage_recovery
import autocode_util as util
from units import autoplanner


def _init_git_repo(root: Path) -> None:
    import subprocess

    from tests import GIT_TEST_CONFIG

    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    for key, value in GIT_TEST_CONFIG.items():
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    (root / "app.py").write_text("def run():\n    return 1\n")
    subprocess.run(["git", "-C", str(root), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


def _base_state(root: Path, run_dir: Path, *, limits=None) -> dict:
    return {
        "task": "Make the app output clearer errors",
        "workspace": str(root),
        "run_dir": str(run_dir),
        "status": "RUNNING",
        "phase": "DISCOVERING",
        "next_stage": "requirements_gather",
        "iteration": 1,
        "stages": [],
        "history": [],
        "sessions": {},
        "answers": {},
        "user_events": [],
        "brief_feedback": [],
        "source_log": [{"id": "task:0", "kind": "task", "text": "Make the app output clearer errors"}],
        "planning": {"reports": {}, "history": []},
        "settings": {
            "engine": "codex",
            "roles": {"planner": {"model": "test-model", "engine": "codex"}},
            "limits": limits or {},
            "report_repair": {"max_attempts": 2},
        },
        "usage": {
            "total": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
            "by_stage": {},
            "by_model": {},
        },
    }


def _write_stopped_attempt(root: Path, run_dir: Path, state: dict, *, timeout_kind="progress") -> dict:
    events_path = run_dir / "stage-01.jsonl"
    output_path = run_dir / "stage-01.json"
    schema_path = run_dir / "stage-01.schema.json"
    before_ref = run_dir / "stage-01.before.json"
    events_rows = [
        {"type": "thread.started", "thread_id": "t-1"},
        {
            "type": "item.completed",
            "item": {
                "id": "cmd_1",
                "type": "command_execution",
                "command": "cat tools/app.py",
                "exit_code": 0,
                "aggregated_output": "def run(): return 1\n",
            },
        },
        {
            "type": "tool_use",
            "part": {
                "id": "call_2",
                "tool": "read",
                "state": {"status": "completed", "input": {"command": "read src/lib.rs"}, "output": "fn ok() {}"},
            },
        },
        {"type": "item.completed", "item": {"id": "msg_1", "type": "agent_message", "text": "Checked app.py."}},
    ]
    events_path.write_text("\n".join(json.dumps(row) for row in events_rows) + "\n")
    schema_path.write_text('{"type": "object"}')
    before = util.snapshot(root)
    before_ref.write_text(json.dumps(before))
    record = {
        "stage": "requirements_gather",
        "role": "planner",
        "iteration": 1,
        "events": str(events_path),
        "output": str(output_path),
        "schema": str(schema_path),
        "before_ref": str(before_ref),
        "source_revision": before["revision"],
        "timed_out": True,
        "timeout_kind": timeout_kind,
        "timeout_reason": "Requirements pass reached its 12-tool-call reply budget; finish from collected evidence",
        "exit_code": -15,
        "first_reply_budget_active": {"max_seconds": 360, "max_tool_calls": 12},
        "processes": [],
    }
    state["active_stage"] = record
    state["stages"] = [record]
    return record


class FirstReplyBudgetTests(unittest.TestCase):
    def test_limits_defaults_and_clamping(self):
        self.assertEqual((360, 12), first_reply_budget.limits({"settings": {}}))
        self.assertEqual(
            (180, 5),
            first_reply_budget.limits(
                {
                    "settings": {
                        "limits": {
                            "stage_timeout_seconds": 180,
                            "first_reply_timeout_seconds": 300,
                            "first_reply_max_tool_calls": 5,
                        }
                    }
                }
            ),
        )
        timeout, meta, progress = role_launch.stage_deadline_and_progress(
            {"settings": {"limits": {"stage_timeout_seconds": 600}}},
            "requirements_gather",
            600,
            report_only=False,
        )
        self.assertEqual(360, timeout)
        self.assertEqual({"max_seconds": 360, "max_tool_calls": 12}, meta)
        self.assertIsNotNone(progress)
        self.assertEqual(
            (600, None, None),
            role_launch.stage_deadline_and_progress(
                {"settings": {"limits": {"stage_timeout_seconds": 600}}},
                "requirements_gather",
                600,
                report_only=True,
            ),
        )

    def test_progress_budget_counts_unique_completed_tools(self):
        budget = first_reply_budget.ProgressBudget(3)
        # Non-completed and non-tool events do not consume budget.
        budget.observe({"type": "item.started", "item": {"id": "c1", "type": "command_execution"}})
        budget.observe({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "hi"}})
        budget.observe({"type": "tool_use", "part": {"id": "o1", "tool": "bash", "state": {"status": "running"}}})
        self.assertEqual(0, budget.completed_count)
        self.assertIsNone(budget.reason())

        # Duplicate completed IDs only count once across Codex & OpenCode shapes.
        budget.observe({"type": "item.completed", "item": {"id": "c1", "type": "command_execution"}})
        budget.observe(
            {"type": "item.updated", "item": {"id": "c1", "type": "command_execution", "status": "completed"}}
        )
        budget.observe({"type": "tool_use", "part": {"id": "o1", "tool": "bash", "state": {"status": "completed"}}})
        self.assertEqual(2, budget.completed_count)
        self.assertIsNone(budget.reason())

        budget.observe({"type": "item.updated", "item": {"id": "c2", "type": "web_search", "status": "failed"}})
        self.assertEqual(3, budget.completed_count)
        self.assertIn("3-tool-call reply budget", budget.reason() or "")

    def test_recovery_queues_one_shot_report_only_finish_with_partial_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _init_git_repo(root)
            run_dir = root / ".autocode/runs/r1"
            run_dir.mkdir(parents=True)
            state = _base_state(root, run_dir)
            _write_stopped_attempt(root, run_dir, state)
            error = autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "budget reached")

            recovered = recovery_dispatch.recover(autocode, state, run_dir, root, error)
            self.assertEqual("timeout", recovered)
            pending = state["pending_report_repair"]
            self.assertTrue(pending["first_reply_budget"])
            self.assertEqual(1, pending["max_attempts"])
            self.assertEqual(0, pending["attempts"])
            self.assertTrue(report_repair_queue.pending_repair_dispatchable(state))

            source_payload = report_source.repair_report_source(pending["original"])
            self.assertEqual("first_reply_budget_partial", source_payload["format"])
            content = source_payload["content"]
            self.assertEqual(2, content["completed_tool_calls"])
            self.assertIn("tools/app.py", content["inspected_paths"])
            self.assertIn("src/lib.rs", content["inspected_paths"])
            self.assertIn("Checked app.py.", content["assistant_notes"])
            self.assertIn("FIRST-REPLY BUDGET", report_source.repair_report_instruction(pending))

            # Once the 1-attempt finish is used, effective_repair_limit prevents another loop.
            pending["attempts"] = 1
            self.assertFalse(report_repair_queue.pending_repair_dispatchable(state))
            _write_stopped_attempt(root, run_dir, state)
            with self.assertRaisesRegex(autocode.support.Paused, "1-attempt allowance"):
                stage_recovery.automatically_recover_first_reply_budget(state, run_dir, root, error)

    def test_person_stop_or_pending_intervention_bypasses_budget_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _init_git_repo(root)
            run_dir = root / ".autocode/runs/r1"
            run_dir.mkdir(parents=True)
            error = autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "stopped by person")

            state = _base_state(root, run_dir)
            _write_stopped_attempt(root, run_dir, state, timeout_kind="person")
            self.assertFalse(stage_recovery.automatically_recover_first_reply_budget(state, run_dir, root, error))

            state = _base_state(root, run_dir)
            _write_stopped_attempt(root, run_dir, state, timeout_kind="progress")
            state["pause_intent"] = {"kind": "stop_now"}
            self.assertFalse(stage_recovery.automatically_recover_first_reply_budget(state, run_dir, root, error))

            state = _base_state(root, run_dir)
            _write_stopped_attempt(root, run_dir, state, timeout_kind="progress")
            (run_dir / "pause-requested").write_text("{}")
            self.assertFalse(stage_recovery.automatically_recover_first_reply_budget(state, run_dir, root, error))
            (run_dir / "pause-requested").unlink()

            (run_dir / "state.json").write_text(json.dumps(state))
            interventions.submit(root, run_dir, request_id="iv-1", kind="feedback", text="Use JSON.")
            self.assertFalse(stage_recovery.automatically_recover_first_reply_budget(state, run_dir, root, error))

    def test_handoff_and_draft_plan_preserve_budget_note(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _init_git_repo(root)
            run_dir = root / ".autocode/runs/r1"
            run_dir.mkdir(parents=True)
            state = _base_state(root, run_dir)
            _write_stopped_attempt(root, run_dir, state)
            error = autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "budget reached")
            self.assertTrue(stage_recovery.automatically_recover_first_reply_budget(state, run_dir, root, error))

            repaired_record = {**state["pending_report_repair"]["original"], "report_repaired": True}
            report = {
                "job": "bugfix",
                "summary": "Clarify app output errors.",
                "intended_outcome": "Clearer error output.",
                "required_behaviors": ["Emit clearer error messages."],
                "important_failure_cases": [],
                "scope_exclusions": [],
                "constraints": [],
                "permission_boundaries": [],
                "accepted_assumptions": [],
                "delegated_decisions": [],
                "acceptance_tests": ["Unit test verifies clearer error messages."],
                "open_questions": [],
                "ignored_statements": [],
                "ignored_requirements": [],
                "source_refs": ["task"],
                "requirements": [
                    {
                        "id": "R1",
                        "text": "Make app output clearer errors",
                        "source_quote": "Make the app output clearer errors",
                    }
                ],
            }
            requirements_handoff.accept(
                state,
                report,
                repaired_record,
                check_code_refs=lambda *_a, **_k: None,
                queue=lambda *_a, **_k: None,
            )
            self.assertIn("first_reply_budget", state["requirements_handoff"])
            state["next_stage"] = "draft_plan"
            state_path = run_dir / "state.json"
            state_path.write_text(json.dumps(state))
            req = autoplanner.prepare(state, "draft_plan", str(state_path), run_dir)
            self.assertIn("requirements_reply_budget", req.prompt)
            self.assertIn("tools/app.py", req.prompt)
            req_gather = SimpleNamespace(
                prompt=autoplanner.prepare(state, "requirements_gather", str(state_path), run_dir).prompt
            )
            self.assertIn("reply_budget", req_gather.prompt)


if __name__ == "__main__":
    unittest.main()
