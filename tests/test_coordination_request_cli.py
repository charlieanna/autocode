"""Saved late members recover earlier clarification context through public actions."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_budget as budget
import autocode_coordination_store as store
import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "supervised provider requires supported Linux")
class LateMemberRequestCliTests(unittest.TestCase):
    def test_saved_late_member_receives_scoped_context_once_without_a_provider_call(self):
        workspace, env = fixture_workspace(self)
        late_workspace, _ = fixture_workspace(self)
        self.enterContext(patch.dict(os.environ, env))
        options = (*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning")
        source = taskrun.TaskRun.start(workspace, BRIEF, options=options, env=env, timeout=180)
        source_before = source.advance_until_input()
        late = taskrun.TaskRun.start(late_workspace, BRIEF, options=options, env=env, timeout=180)
        late_before = late.advance_until_input()
        clarification = "Correction: any non-EPP registry. Leave payloads blank; do not convert them."
        applied = {"request_id": "earlier-correction", "text": clarification}
        store.save(
            {
                "version": 1,
                "id": "late-context-fixture",
                "anchor": str(source.run_dir),
                "phase": "fixing",
                "task": BRIEF,
                "request_sources": source_before["request_sources"],
                "choices": {},
                "initial_scope": [],
                "joint_attempts": [],
                "members": [
                    {
                        "target": "api",
                        "workspace": str(source.workspace),
                        "run_dir": str(source.run_dir),
                        "base": "fixture",
                        "started": True,
                        "brief": BRIEF + "\n\n" + clarification,
                        "message_sources": [applied],
                    },
                    {
                        "target": "late",
                        "workspace": str(late.workspace),
                        "run_dir": str(late.run_dir),
                        "base": "fixture",
                        "started": True,
                        "brief": BRIEF,
                    },
                ],
                "conversation": {
                    "results": [
                        {
                            **applied,
                            "context": {"kind": "coordinated_task", "participants": [{"target": "api"}]},
                            "affected_targets": ["api"],
                            "reply": {
                                "text": "Untrusted paraphrase must not become a requirement",
                                "conversation": {"intent": "change"},
                            },
                        }
                    ]
                },
                "budget": {**budget.initial(source_before), "cap_usd": 0},
            }
        )

        def invoke(argv):
            response = subprocess.run(
                [*taskrun.AUTOCODE, *argv],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(0, response.returncode, response.stdout + response.stderr)
            return json.loads(response.stdout)

        status = ["coordinate", "status", "--run-dir", str(source.run_dir), "--json"]
        before = invoke(status)
        self.assertEqual("request_feedback", before["needs"]["kind"])
        self.assertEqual("late", before["needs"]["target"])
        after = invoke(before["next_command"][1:])
        self.assertNotEqual("request_feedback", after["needs"]["kind"])
        self.assertNotEqual("request_feedback", invoke(status)["needs"]["kind"])
        recovered = taskrun.TaskRun(Path(late.workspace), Path(late.run_dir), env=env).status()
        sources = [row["text"] for row in recovered["request_sources"] if clarification in row["text"]]
        self.assertEqual(1, len(sources), recovered["request_sources"])
        self.assertIn("applied to api before late joined", sources[0])
        self.assertIn("not permission to change another repository or approve a plan", sources[0])
        self.assertNotIn("Untrusted paraphrase", sources[0])
        self.assertEqual(late_before["usage"], recovered["usage"])
        self.assertEqual(source_before["usage"], source.status()["usage"])
        self.assertIsNone(recovered.get("approved_contract"))
        self.assertFalse((late_workspace / "greet.py").exists())
