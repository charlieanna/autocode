"""A retained acceptance report after the Investigator, through the public CLI (#3927); no model call.

The in-process TaskRun seam keeps real parsing, locks, state commits and status. The run is saved at
``PAUSED_INVALID_OUTPUT`` after the Tester's retained report spent its one local re-check. The provider
launch is a recording stub: it saves the request it was given and stops the run, so a test sees whether
the resume reached a Tester attempt and with which guidance.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as core
import autocode_acceptance_tests as acceptance
import autocode_acceptance_verification_retry as retry
import autocode_args as arguments
import autocode_contract_identity as identity
import autocode_planning_advice as recovery_binding
import autocode_source_scope as source_scope
import autocode_support as support
import autocode_taskrun as taskrun
import autocode_util as util
import goal_fixtures
import inprocess_cli

IDENTITY = "write_acceptance_tests:PAUSED_INVALID_OUTPUT"
GUIDANCE = "Keep the public import and move it into the test body"
FAILURE = "Acceptance verification retry could not finish: original-reference execution missing"


class ReachedTesterLaunch(Exception):
    """The recording provider stub: the resume reached a model launch."""


class RetainedRetryResumeCliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.run_dir = self.workspace / ".autocode/runs/retained-retry"
        self.run_dir.mkdir(parents=True)
        self.env = {
            "HOME": str(self.root / "home"),
            "PATH": os.environ["PATH"],
            "AUTOCODE_HOME": str(self.root / "registry"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "CODEX_HOME": str(self.root / "codex-home"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.enterContext(patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}))
        git = ["git", "-C", str(self.workspace), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test"]
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        (self.workspace / ".gitignore").write_text(".autocode/\n")
        (self.workspace / "greet.py").write_text("def greet(name):\n    return f'Hello, {name}'\n")
        (self.workspace / "test_greeting.py").write_text("# independently written test\n")
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "base"], check=True)
        self.state = self.saved_state(git)
        util.atomic_json(self.run_dir / "state.json", self.state)
        self.run = taskrun.TaskRun(self.workspace, self.run_dir, env=self.env, options=())
        self.enterContext(inprocess_cli.patched())
        self.launches = []
        self.enterContext(patch.object(core, "run_role", side_effect=self.launch))

    def launch(self, **request):
        self.launches.append({"prompt": request.get("prompt"), "stage": request["state"].get("next_stage")})
        raise ReachedTesterLaunch

    def saved_state(self, git):
        body = goal_fixtures.body()
        body["acceptance_criteria"][0]["verification_method"] = "test: test_greeting"
        contract = {"task_id": "task", "revision": 1, "body": copy.deepcopy(body)}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
        contract.update(approval_status="approved", approval_event=approval, origin="finalize_plan")
        parsed = arguments.build_parser(None, arguments.DEFAULT_ROLE_MODELS).parse_args(
            ["--engine", "codex", "--joint-planning", "--spec-first", "--max-cost-usd", "0"]
        )
        settings = core.configure(parsed, {"workspace": str(self.workspace), "iteration": 1})
        state = {
            "version": 3,
            "target": "code",
            "task": "Provide a deterministic greeting CLI",
            "workspace": str(self.workspace),
            "created_at": "2026-11-20T02:00:00+00:00",
            "iteration": 1,
            "sessions": {},
            "history": [],
            "stages": [],
            "settings": settings,
            "goal_contract": contract,
            "user_events": [approval],
            "current_task": {"id": "T1", "milestone_id": "M1", "objective": "Deliver and verify the greeting flow"},
            "next_stage": "builder",
            "no_progress_batches": 0,
            "base_commit": subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip(),
        }
        acceptance.route(state)
        value = {
            "summary": "Test the greeting",
            "tests": [{"case_id": "C1", "file": "test_greeting.py", "test_name": "test_greeting"}],
            "already_passing": [],
            "cannot_write": [],
            "tests_run": [],
        }
        iteration = self.run_dir / "iterations" / "001"
        iteration.mkdir(parents=True)
        record = {
            "stage": acceptance.STAGE,
            "role": "tester",
            "contract_hash": contract["hash"],
            "task_id": "T1",
            "accounted": True,
            "exit_code": 0,
            "started_at": "2026-11-20T02:10:00+00:00",
            "finished_at": "2026-11-20T02:26:03+00:00",
            "source_revision": source_scope.snapshot(self.workspace, state)["revision"],
            "changed_files": ["test_greeting.py"],
            "engine": "opencode",
            "output_mode": "opencode_events",
            "iteration": 1,
        }
        for key in retry.ARTIFACTS:
            path = iteration / f"acceptance-tests-01.{key}.json"
            util.atomic_json(path, value if key == "output" else {})
            record[key] = str(path)
        rows = [
            ("text", {"text": json.dumps(value)}),
            ("step_finish", {"reason": "stop", "tokens": {"input": 1, "output": 1, "cache": {"read": 0, "write": 0}}}),
        ]
        Path(record["events"]).write_text(
            "\n".join(
                json.dumps({"type": kind, "sessionID": "fixture", "part": {"id": kind, "messageID": "reply", **part}})
                for kind, part in rows
            )
        )
        record["metrics"] = {
            "request_context": {
                "accounting": {"event_file": {"path": record["events"], "sha256": util.file_hash(record["events"])}}
            }
        }
        state["stages"] = [record]
        state[acceptance.KEY]["verification_retry"] = {
            "record": copy.deepcopy(record),
            "value": copy.deepcopy(value),
            "pins": {record[key]: util.file_hash(record[key]) for key in retry.ARTIFACTS},
            "attempts": 1,
            "failures": [
                {"at": "2026-11-20T02:26:04+00:00", "reason": "original-reference execution missing"},
                {"at": "2026-11-20T02:26:20+00:00", "reason": "original-reference execution missing"},
            ],
        }
        state.update(
            status="PAUSED_INVALID_OUTPUT",
            phase="PAUSED_OR_BLOCKED",
            next_stage=acceptance.STAGE,
            stop_reason=FAILURE,
            paused_at="2026-11-20T02:26:21+00:00",
        )
        return state

    def advise(self, cause="stage_output"):
        """The Investigator's retry as autocode_stuck_job.apply records it: the cause and requested_at on
        the history entry, the in-force record with exactly the keys apply writes."""
        binding = recovery_binding.binding(self.state, acceptance.STAGE)
        self.state["stuck_investigations"] = [
            {
                "identity": IDENTITY,
                "stage": acceptance.STAGE,
                "status": "PAUSED_INVALID_OUTPUT",
                "reason": FAILURE,
                "requested_at": "2026-11-20T02:26:26+00:00",
                "finished_at": "2026-11-20T02:32:35+00:00",
                "outcome": "retried",
                "trigger": "rejected_output",
                "cause": cause,
                "diagnosis": "The saved test imports the public module at collection time.",
                "guidance": GUIDANCE,
                **binding,
            }
        ]
        self.state["stuck_investigation"] = {
            "identity": IDENTITY,
            "stage": acceptance.STAGE,
            "status": "PAUSED_INVALID_OUTPUT",
            **binding,
            "guidance": GUIDANCE,
            "diagnosis": "The saved test imports the public module at collection time.",
            "example": "Given the saved test; when it is collected on the original; then the import fails",
            "in_force": True,
        }
        util.atomic_json(self.run_dir / "state.json", self.state)

    def test_ac4_resume_after_test_cause_advice_launches_tester(self):
        self.advise()
        before = self.run.status()
        self.assertIn("--resume-paused", before["next_command"])
        with self.assertRaises(ReachedTesterLaunch):
            self.run.resume_paused()
        # The resume fell through to the ordinary model dispatch: one Tester attempt, with the guidance.
        self.assertEqual([acceptance.STAGE], [row["stage"] for row in self.launches])
        self.assertIn(GUIDANCE, self.launches[0]["prompt"])
        saved = util.read(self.run_dir / "state.json")
        self.assertNotIn(retry.KEY, saved[acceptance.KEY])
        archived = saved[acceptance.KEY]["verification_retries"][-1]
        self.assertEqual(1, archived["attempts"])
        self.assertEqual(2, len(archived["failures"]))
        self.assertEqual({"identity": IDENTITY, "requested_at": "2026-11-20T02:26:26+00:00"}, archived["retired_by"])
        self.assertFalse(acceptance.receipts(saved))

    def test_resume_after_exhausted_recheck_ends_the_run_with_a_report(self):
        # #4037: no re-check is left and the Investigator has looked; the run used to sit at a pause that
        # named no command, took no chat message and offered only inspection.
        before = self.run.status()
        self.assertEqual({"next": "exhausted", "rechecks": 1, "allowed": 1}, before["acceptance_verification_retry"])
        self.assertIn("--resume-paused", before["next_command"])
        self.run.resume_paused()
        self.assertEqual([], self.launches, "ending the run launches no model")
        saved = util.read(self.run_dir / "state.json")
        self.assertEqual("PAUSED_INTERVENTION", saved["status"])
        after = self.run.status()
        self.assertIsNone(after["next_command"])
        outcome = after["recovery"]["outcome"]
        self.assertEqual("could_not_finish", outcome["result"])
        self.assertIn("1 of 1 allowed times", outcome["reason"])
        self.assertIn("original-reference execution missing", outcome["reason"])
        self.assertEqual(2, len(outcome["evidence"]["failures"]))

    def test_ac9_status_progress_reads_retained_check(self):
        shown = self.run.status()
        self.assertEqual("PAUSED_INVALID_OUTPUT", shown["status"])
        # No re-check is left: the next resume ends the run with a report (#4037), never "Next: Tester".
        self.assertEqual("Ending with a report", shown["progress"]["headline"])
        self.assertEqual("nothing needed from you", shown["progress"]["needs_you"])
        # A retained report whose one re-check is still pending: the resume re-checks the saved tests.
        self.state[acceptance.KEY]["verification_retry"]["attempts"] = 0
        util.atomic_json(self.run_dir / "state.json", self.state)
        self.assertEqual("resume to re-check the saved tests", self.run.status()["progress"]["needs_you"])


if __name__ == "__main__":
    unittest.main()
