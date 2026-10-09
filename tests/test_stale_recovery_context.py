"""An abandoned attempt's recovery note does not outlive later work (#563)."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_recovery_context as recovery_context
import autocode_recovery_limits as limits
import autocode_stage_context as stage_context
import autocode_support as support
from goal_fixtures import body, seed_greeting_workspace

ABANDONED = "001/completion-review-03"
ABANDON_INSTRUCTION = (
    "This response was abandoned. Inspect partial work before assigning a task or "
    "validation; its report is not evidence of success."
)


class StaleRecoveryContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        self.registry.start()
        self.addCleanup(self.registry.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        seed_greeting_workspace(self.root)
        subprocess.run(["git", "-C", str(self.root), "add", "greet.py", "test_greeting.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.local = {"auth_mode": "fixture"}
        self.state = {
            "version": 3,
            "task_id": "task-fixture",
            "workspace": str(self.root),
            "task": "Make a greeting tool",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {"astra": "completion-session"},
            "stages": [],
            "history": [],
            "answers": {},
            "user_events": [],
            "deferred_backlog": [],
            "next_stage": "astra_review",
            "acceptance_criteria": [],
            "settings": {
                "roles": {
                    role: {"model": role, "reasoning_effort": "high"} for role in ("astra", "terra", "sol", "glm")
                },
                "transport_identity": self.local,
                "headroom": {"enabled": False},
                "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3},
            },
        }

    def abandon(self):
        base = self.run / "iterations/001/completion-review-03"
        base.parent.mkdir(parents=True)
        support.atomic_json(base.with_suffix(".before.json"), support.snapshot(self.root))
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"completion-session"}\n')
        self.state["active_stage"] = {
            "role": "astra",
            "stage": "astra_review",
            "iteration": 1,
            "duration_seconds": 4,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
            "exit_code": -15,
            "processes": [],
        }
        runner.abandon_stage(self.state, self.run, self.root, ABANDONED)
        self.assertEqual(ABANDONED, self.state["recovery_context"]["attempt_id"])
        self.assertEqual(ABANDON_INSTRUCTION, self.state["recovery_context"]["instruction"])

    def handoff(self, stage):
        prompt, _ = stage_context.context_packet(self.state, stage, self.run / "state.json")
        return prompt, json.loads(prompt.rsplit("CURRENT HANDOFF DATA\n", 1)[1])

    def invoke(self, *args):
        support.atomic_json(self.run / "state.json", self.state)
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), *args]
        with (
            patch.object(sys, "argv", argv),
            patch.object(support, "assert_no_legacy_process"),
            patch.object(support, "local_settings", return_value=self.local),
            patch.object(runner, "run_role", side_effect=AssertionError("No agent may launch")),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = runner.main()
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        self.state = support.read(self.run / "state.json")
        return code

    def test_a_later_saved_stage_drops_the_abandon_from_the_next_prompt(self):
        self.abandon()
        prompt, packet = self.handoff("terra")
        self.assertEqual(ABANDONED, packet["recovery_context"]["attempt_id"])
        self.assertIn(ABANDON_INSTRUCTION, prompt)
        runner.save_record(
            self.state,
            {
                "role": "terra",
                "stage": "terra",
                "iteration": 4,
                "output": str(self.run / "iterations/004/builder-04.json"),
            },
        )
        prompt, packet = self.handoff("sol")
        self.assertFalse(packet.get("recovery_context"))
        self.assertNotIn(ABANDONED, prompt)
        self.assertNotIn("This response was abandoned", prompt)
        archived = self.state["recovery_context_archive"][-1]
        self.assertEqual(ABANDONED, archived["recovery_context"]["attempt_id"])
        self.assertEqual("A later stage saved", archived["reason"])

    def test_saving_the_named_attempt_keeps_the_note(self):
        state = {"recovery_context": {"attempt_id": "004/builder-04", "instruction": "inspect"}}
        recovery_context.stage_saved(state, {"iteration": 4, "output": "iterations/004/builder-04.json"})
        self.assertEqual("004/builder-04", state["recovery_context"]["attempt_id"])

    def test_a_note_that_names_no_attempt_survives_a_saved_stage(self):
        state = {"recovery_context": {"timeout_kind": "tool", "task_id": "task-1"}}
        recovery_context.stage_saved(state, {"iteration": 4, "output": "iterations/004/builder-04.json"})
        self.assertEqual("tool", state["recovery_context"]["timeout_kind"])

    def test_edit_goal_and_approve_leave_recovery_context_empty(self):
        self.abandon()
        revised = body()
        revised["constraints"].append("Keep the greeting on one line")
        path = self.root / "edited-goal.json"
        path.write_text(json.dumps(revised))
        self.assertEqual(0, self.invoke("--edit-goal", str(path), "--no-chat"), self.stderr)
        self.assertTrue(self.state.get("recovery_context"), self.state.get("status"))
        self.assertEqual(0, self.invoke("--show-goal", "--no-chat"), self.stderr)
        token = self.state["displayed_goal"]
        self.assertEqual(0, self.invoke("--approve-goal", token, "--no-chat"), self.stderr)
        self.assertTrue(goals.approved(self.state))
        self.assertFalse(self.state.get("recovery_context"))
        prompt, packet = self.handoff("terra")
        self.assertFalse(packet.get("recovery_context"))
        self.assertNotIn(ABANDONED, prompt)
        self.assertNotIn("This response was abandoned", prompt)

    def test_budget_pause_names_the_timeout_not_an_earlier_abandon(self):
        state = {
            "recovery_context": {"attempt_id": ABANDONED, "instruction": ABANDON_INSTRUCTION},
            "automatic_timeout_recoveries": [
                {"attempt_id": "001/builder-02", "timeout_reason": "No meaningful activity for 5 seconds"}
            ],
            "user_events": [
                {"kind": "automatic_timeout_recovery", "attempt_id": "001/builder-02"},
                {"kind": "stage_abandoned", "attempt_id": ABANDONED},
            ],
        }
        status, text = limits.stop_reason(state, 3, 3)
        self.assertEqual("PAUSED_TIMEOUT_RECOVERY", status)
        self.assertIn("Last cause: No meaningful activity for 5 seconds", text)
        self.assertNotIn("abandoned", text)
        self.assertNotIn("Inspect partial work", text)

    def test_budget_pause_says_the_cause_is_unknown_without_a_counted_recovery(self):
        state = {"recovery_context": {"attempt_id": ABANDONED, "instruction": ABANDON_INSTRUCTION}}
        status, text = limits.stop_reason(state, 3, 3)
        self.assertEqual("PAUSED_TIMEOUT_RECOVERY", status)
        self.assertIn("Last cause: the cause is unknown", text)
        self.assertNotIn("abandoned", text)
        self.assertNotIn(ABANDON_INSTRUCTION, text)

    def test_approve_archives_a_note_that_names_no_attempt(self):
        self.state["recovery_context"] = {"timeout_kind": "idle", "instruction": "inspect the stall"}
        lifecycle.install_draft(self.state, body(), origin="user_cli_edit")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertFalse(self.state.get("recovery_context"))
        self.assertEqual("idle", self.state["recovery_context_archive"][-1]["recovery_context"]["timeout_kind"])
