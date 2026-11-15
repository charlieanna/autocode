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
        "first_reply_budget_active": {"max_seconds": 600, "max_tool_calls": 12},
        "processes": [],
    }
    state["active_stage"] = record
    state["stages"] = [record]
    return record


class FirstReplyBudgetTests(unittest.TestCase):
    def test_limits_defaults_and_clamping(self):
        self.assertEqual((600, 12), first_reply_budget.limits({"settings": {}}))
        self.assertEqual(
            (900, 12),
            first_reply_budget.limits(
                {"settings": {"roles": {"requirements": {"model": "kilo/xiaomi/mimo-v2-pro:free"}}}}
            ),
        )
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
            {"settings": {"limits": {"stage_timeout_seconds": 900}}},
            "requirements_gather",
            900,
            report_only=False,
        )
        self.assertEqual(600, timeout)
        self.assertEqual({"max_seconds": 600, "max_tool_calls": 12}, meta)
        self.assertIsNotNone(progress)
        mimo_timeout, mimo_meta, _ = role_launch.stage_deadline_and_progress(
            {
                "settings": {
                    "roles": {"requirements": {"model": "kilo/xiaomi/mimo-v2-pro:free"}},
                    "limits": {"stage_timeout_seconds": 900},
                }
            },
            "requirements_gather",
            900,
            report_only=False,
        )
        self.assertEqual(900, mimo_timeout)
        self.assertEqual({"max_seconds": 900, "max_tool_calls": 12}, mimo_meta)
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

    def test_partial_evidence_names_what_native_tools_looked_at(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            rows = [
                {
                    "type": "item.completed",
                    "item": {
                        "id": "c1",
                        "type": "tool_call",
                        "name": "Read",
                        "status": "completed",
                        "display_input": {"file_path": "/work/src/app.py"},
                    },
                },
                {
                    "type": "tool_use",
                    "part": {
                        "id": "o1",
                        "tool": "grep",
                        "state": {"status": "completed", "input": {"pattern": "def run", "path": "src"}, "output": ""},
                    },
                },
            ]
            events.write_text("".join(json.dumps(row) + "\n" for row in rows))
            evidence = first_reply_budget.extract_partial_evidence(events, {})
        self.assertEqual(
            ["Read /work/src/app.py", "grep src def run"], [row["command"] for row in evidence["inspected_commands"]]
        )

    def test_partial_evidence_retains_2000_char_excerpts_reasoning_parts_and_latest_notes(self):
        long_source = "line = 1\n" * 180  # 1,620 chars (> old 400-char trim, <= 2,000-char cap)
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.jsonl"
            rows = [
                {
                    "type": "tool_use",
                    "part": {
                        "id": "r1",
                        "tool": "read",
                        "state": {
                            "status": "completed",
                            "input": {"filePath": "/work/src/app.py"},
                            "output": long_source,
                        },
                    },
                },
                *[{"type": "reasoning", "part": {"id": f"th_{idx}", "text": f"Thought {idx}"}} for idx in range(9)],
                {
                    "type": "autocode_progress",
                    "progress": {"id": "prt_live", "kind": "reasoning", "preview": "Early draft"},
                },
                {
                    "type": "autocode_progress",
                    "progress": {"id": "prt_live", "kind": "reasoning", "preview": "Weighing Q1 vs Q3 fork"},
                },
            ]
            events.write_text("".join(json.dumps(row) + "\n" for row in rows))
            evidence = first_reply_budget.extract_partial_evidence(events, {})
        self.assertEqual(long_source.strip(), evidence["inspected_commands"][0]["output_excerpt"])
        self.assertEqual(first_reply_budget.MAX_NOTES, len(evidence["assistant_notes"]))
        self.assertNotIn("Thought 0", evidence["assistant_notes"])
        self.assertNotIn("Thought 1", evidence["assistant_notes"])
        self.assertIn("Thought 8", evidence["assistant_notes"])
        self.assertEqual("Weighing Q1 vs Q3 fork", evidence["assistant_notes"][-1])

    def test_the_budget_points_requirements_at_the_code_the_change_touches(self):
        instruction = first_reply_budget.handoff_spec({"settings": {}})["instruction"]
        self.assertIn("the target module, its callers and its tests", instruction)
        self.assertIn("user-visible forks to ask about together", instruction)
        self.assertNotIn("at most a few targeted file reads", instruction)

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

    def _stopped_budget_repair(self, root: Path, run_dir: Path, state: dict, *, ordinal: int, events_text: str) -> dict:
        original = state["pending_report_repair"]["original"]
        events_path = run_dir / f"repair-{ordinal:02d}.jsonl"
        events_path.write_text(events_text)
        before_ref = run_dir / f"repair-{ordinal:02d}.before.json"
        before = util.snapshot(root)
        before_ref.write_text(json.dumps(before))
        record = {
            "stage": "requirements_gather_report_repair",
            "original_stage": "requirements_gather",
            "role": original["role"],
            "iteration": original["iteration"],
            "report_only": True,
            "events": str(events_path),
            "output": str(run_dir / f"repair-{ordinal:02d}.json"),
            "schema": original["schema"],
            "before_ref": str(before_ref),
            "source_revision": before["revision"],
            "contract_hash": original.get("contract_hash"),
            "task_id": original.get("task_id"),
            "criteria_revision": original.get("criteria_revision"),
            "finished_at": "2026-11-15T00:00:00Z",
            "timed_out": True,
            "timeout_kind": "idle" if not events_text else "stage",
            "timeout_reason": "90s cold start" if not events_text else "repair stage deadline",
            "exit_code": -15,
            "processes": [{"pid": 99999999, "birth_time": 1}],
        }
        state["active_stage"] = record
        return record

    def test_budget_repair_silent_start_timeout_refunds_once_and_disables_startup_budget(self):
        from unittest import mock

        import autocode_resolve_repair_job as resolve_repair_job

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _init_git_repo(root)
            run_dir = root / ".autocode/runs/r1"
            run_dir.mkdir(parents=True)
            state = _base_state(root, run_dir)
            state["settings"]["budget_origins"] = {"idle_timeout_seconds": "runner_default"}
            _write_stopped_attempt(root, run_dir, state)
            self.assertEqual(
                "timeout",
                recovery_dispatch.recover(
                    autocode, state, run_dir, root, autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "budget reached")
                ),
            )
            pending = state["pending_report_repair"]

            watch_before = role_launch.launch_limits(
                state, "mimo-v2-pro", "requirements_gather", "requirements_gather_report_repair", report_only=True
            )[3]
            self.assertEqual(90, watch_before["first_event_limit"])
            self.assertEqual(900, watch_before["idle_limit"])

            pending["attempts"] = 1
            self._stopped_budget_repair(root, run_dir, state, ordinal=1, events_text="")
            with mock.patch.object(stage_recovery.processes, "live_processes", return_value=[]):
                recovered = recovery_dispatch.recover(
                    autocode,
                    state,
                    run_dir,
                    root,
                    autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "90s cold start"),
                )
            self.assertEqual("timeout", recovered)
            self.assertNotIn("active_stage", state)
            self.assertEqual(("RUNNING", "REPORT_REPAIR"), (state["status"], state["phase"]))
            self.assertEqual(0, pending["attempts"])
            self.assertTrue(pending["silent_start_retried"])
            self.assertTrue(report_repair_queue.pending_repair_dispatchable(state))
            receipt = next(row for row in reversed(state["stages"]) if row["stage"] == "resolver")["receipt"]
            self.assertEqual(0, receipt["evidence"]["repair_attempts_used"])
            self.assertTrue(receipt["evidence"]["silent_start_retried"])

            watch_mimo = role_launch.launch_limits(
                state, "mimo-v2-pro", "requirements_gather", "requirements_gather_report_repair", report_only=True
            )[3]
            self.assertIsNone(watch_mimo["first_event_limit"])
            self.assertEqual(900, watch_mimo["idle_limit"])
            watch_glm = role_launch.launch_limits(
                state, "glm-5", "requirements_gather", "requirements_gather_report_repair", report_only=True
            )[3]
            self.assertIsNone(watch_glm["first_event_limit"])
            self.assertEqual(600, watch_glm["idle_limit"])

            # A second 0-event timeout does not refund again; it archives cleanly and escalates to resolve_repair.
            pending["attempts"] = 1
            self._stopped_budget_repair(root, run_dir, state, ordinal=2, events_text="")
            with mock.patch.object(stage_recovery.processes, "live_processes", return_value=[]):
                recovered_again = recovery_dispatch.recover(
                    autocode,
                    state,
                    run_dir,
                    root,
                    autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "wedged transport"),
                )
            self.assertEqual("timeout", recovered_again)
            self.assertEqual(1, pending["attempts"])
            self.assertFalse(report_repair_queue.pending_repair_dispatchable(state))
            with self.assertRaises(autocode.support.Paused) as limit_stop:
                autocode.execute_report_repair(state, run_dir, root)
            self.assertEqual("PAUSED_REPORT_REPAIR_LIMIT", limit_stop.exception.status)
            self.assertTrue(resolve_repair_job.eligible_report_repair(state))
            self.assertTrue(resolve_repair_job.queue_from_pending(state, run_dir))
            self.assertEqual("resolve_repair", state["next_stage"])
            self.assertEqual("report_repair", state["resolve_repair_request"]["kind"])

    def test_budget_repair_post_event_timeout_escalates_to_resolve_repair_without_operational_pause(self):
        from unittest import mock

        import autocode_resolve_repair_job as resolve_repair_job

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _init_git_repo(root)
            run_dir = root / ".autocode/runs/r1"
            run_dir.mkdir(parents=True)
            state = _base_state(root, run_dir)
            _write_stopped_attempt(root, run_dir, state)
            recovery_dispatch.recover(
                autocode, state, run_dir, root, autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "budget reached")
            )
            pending = state["pending_report_repair"]
            pending["attempts"] = 1
            self._stopped_budget_repair(
                root,
                run_dir,
                state,
                ordinal=1,
                events_text=json.dumps({"type": "thread.started", "thread_id": "r-1"}) + "\n",
            )
            with mock.patch.object(stage_recovery.processes, "live_processes", return_value=[]):
                recovered = recovery_dispatch.recover(
                    autocode,
                    state,
                    run_dir,
                    root,
                    autocode.support.Paused("PAUSED_PROVIDER_TIMEOUT", "repair stage deadline"),
                )
            self.assertEqual("timeout", recovered)
            self.assertEqual(1, pending["attempts"])
            self.assertNotIn("silent_start_retried", pending)
            with self.assertRaises(autocode.support.Paused) as limit_stop:
                autocode.execute_report_repair(state, run_dir, root)
            self.assertEqual("PAUSED_REPORT_REPAIR_LIMIT", limit_stop.exception.status)
            self.assertTrue(resolve_repair_job.queue_from_pending(state, run_dir, force=True))
            self.assertEqual("resolve_repair", state["next_stage"])


if __name__ == "__main__":
    unittest.main()
