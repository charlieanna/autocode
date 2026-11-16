"""Bridge worker batch answer submission (#3650)."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

import autocode_bridge_worker as worker
import autocode_taskrun as taskrun


class BridgeWorkerAnswerAllTests(unittest.TestCase):
    def test_answer_all_submits_multiple_open_answers_in_a_single_cli_invocation(self):
        run = taskrun.TaskRun(Path("/work/project"), Path("/work/project/.autocode/runs/r1"))
        initial_view = {
            "status": "WAITING_FOR_USER",
            "needs": {
                "kind": "answer",
                "resolver_request_id": "req-1",
                "resolver_token": "tok-batch-1",
                "questions": [
                    {"id": "q-1", "question": "Which DB?", "options": ["sqlite", "postgres"], "allow_custom": True},
                    {"id": "q-2", "question": "Seed data?", "options": [], "allow_custom": True},
                    {"id": "q-3", "question": "Output format?", "options": ["json", "text"], "allow_custom": True},
                ],
            },
        }
        after_view = {"status": "RUNNING", "done": False, "needs": {"kind": "continue"}}
        calls: list[tuple[list[str], dict | None]] = []

        def fake_run_process(argv, *, env=None, **_kwargs):
            calls.append((list(argv), env))
            import json

            view = initial_view if "--status" in argv else after_view
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"view": view}), stderr="")

        with mock.patch.object(taskrun, "run_process", side_effect=fake_run_process):
            note = worker.answer_all(run, {"q-1": "duckdb", "q-2": "fixtures", "q-3": "json"})

        self.assertEqual("Answered q-1, q-2, q-3.", note)
        action_calls = [(argv, env) for argv, env in calls if "--answer" in argv]
        self.assertEqual(1, len(action_calls), "expected a single batched --answer CLI invocation")
        self.assertEqual(3, len(calls), "expected 1 status read + 1 batched --answer CLI call + 1 post-act status read")
        action_argv, action_env = action_calls[0]
        self.assertIn("--answer", action_argv)
        self.assertIn("q-1=duckdb", action_argv)
        self.assertIn("q-2=fixtures", action_argv)
        self.assertIn("q-3=json", action_argv)
        self.assertEqual("tok-batch-1", (action_env or {}).get("AUTOCODE_RESOLVER_TOKEN"))


if __name__ == "__main__":
    unittest.main()
