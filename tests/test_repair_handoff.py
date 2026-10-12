"""The verified-repair handoff (#969): export a dispatched bounded repair, adopt an external implementation.

The fixture state mirrors the #961 wedge reproduction (run-dir state.json.bak-classification-wedge
from the Rails gRPC run): PAUSED_BUILDER_CLASSIFICATION with resolution_request + current_task, the
dispatched repair reachable only externally. The wedge is the export trigger.
"""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_repair_handoff as handoff

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], env=GIT_ENV, check=True, capture_output=True, text=True)


def write(root, name, text):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def wedge_state(source_revision="base-revision"):
    """The #961 wedge shape: a dispatched bounded repair stranded at a classification pause."""
    return {
        "status": "PAUSED_BUILDER_CLASSIFICATION",
        "phase": "PAUSED_OR_BLOCKED",
        "stop_reason": "Root cause: the runner's regression gate is pytest-based",
        "resolution_request": {
            "contract_hash": "contract-hash",
            "task_id": "task-1",
            "source_stage": "astra_review",
            "provenance": "completion_review_decision",
            "source_revision": source_revision,
        },
        "current_task": {
            "id": "task-1",
            "kind": "implement",
            "milestone_id": "M1",
            "milestone_ids": ["M1"],
            "decision": "CONTINUE",
            "objective": "Repair the shadow decision seam",
            "requirements": ["Wrap the seam in its own rescue", "One executable example per outcome"],
            "acceptance_criteria": ["AC1", "AC2"],
            "affected_paths": ["src/seam.py", "src/new.py", "src/gone.py"],
            "validation_plan": ["Run `python3 -c \"assert open('src/seam.py').read().startswith('fixed')\"`"],
            "contract_hash": "contract-hash",
            "contract_revision": 3,
            "source_revision": source_revision,
        },
        "goal_contract": {
            "hash": "contract-hash",
            "revision": 3,
            "body": {
                "task_kind": "build",
                "acceptance_criteria": [
                    {
                        "id": "AC1",
                        "criterion": "The seam rescues its own failures",
                        "verification_method": "Run `python3 -c \"assert open('src/new.py').read().startswith('new')\"`",
                    },
                    {
                        "id": "AC2",
                        "criterion": "Each outcome has one executable example",
                        "verification_method": "Run `python3 -c \"print('ok')\"`",
                    },
                ],
                "milestones": [
                    {
                        "id": "M1",
                        "objective": "Repair the shadow decision seam",
                        "acceptance_criteria": ["AC1", "AC2"],
                        "affected_paths": ["src/seam.py"],
                    }
                ],
            },
        },
        "settings": {"regression": {}},
        "next_stage": "sol",
        "validation": {},
        "user_events": [],
        "task_archive": [],
    }


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "run"
        self.run_dir.mkdir()
        self.state = wedge_state()

    def test_export_writes_the_dispatched_repair_as_json_and_markdown(self):
        before = copy.deepcopy(self.state)
        written = handoff.export(self.state, self.run_dir)
        self.assertEqual(self.state, before)  # Export never touches run state.
        spec = handoff.spec(self.state)
        saved = json.loads(Path(written["spec"]).read_text())
        self.assertEqual(saved, spec)
        self.assertEqual(saved["task_id"], "task-1")
        self.assertEqual(saved["contract_hash"], "contract-hash")
        self.assertEqual(saved["source_revision"], "base-revision")
        self.assertEqual(
            saved["repair_instruction"]["requirements"],
            ["Wrap the seam in its own rescue", "One executable example per outcome"],
        )
        self.assertEqual(saved["affected_paths"], ["src/seam.py", "src/new.py", "src/gone.py"])
        self.assertEqual([row["id"] for row in saved["acceptance_criteria"]], ["AC1", "AC2"])
        self.assertIn("src/new.py", saved["acceptance_criteria"][0]["verification_method"])
        self.assertTrue(any("src/seam.py" in command for command in saved["verification_commands"]))
        self.assertIn("dispatched base", saved["delivery"])
        document = Path(written["readme"]).read_text()
        self.assertIn("Repair the shadow decision seam", document)
        self.assertIn("- src/gone.py", document)
        self.assertIn("## Verification commands", document)

    def test_export_refuses_without_a_dispatched_repair(self):
        for state in (
            {**self.state, "status": "RUNNING"},
            {**self.state, "resolution_request": None},
            {**self.state, "current_task": None},
            {**copy.deepcopy(self.state), "current_task": {**self.state["current_task"], "validation_plan": []}},
        ):
            with self.assertRaises(ValueError):
                handoff.export(state, self.run_dir)


class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], env=GIT_ENV, check=True, capture_output=True)
        write(self.workspace, "src/seam.py", "original\n")
        write(self.workspace, "src/gone.py", "gone\n")
        git(self.workspace, "add", "-A")
        git(self.workspace, "commit", "-qm", "base")
        self.delivery = root / "delivery"
        subprocess.run(
            ["git", "clone", "-q", str(self.workspace), str(self.delivery)],
            env=GIT_ENV,
            check=True,
            capture_output=True,
        )
        write(self.delivery, "src/seam.py", "fixed\n")
        write(self.delivery, "src/new.py", "new\n")
        (self.delivery / "src/gone.py").unlink()
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        self.state = wedge_state()
        self.state["current_task"]["source_revision"] = handoff.source_scope.snapshot(self.workspace, self.state)[
            "revision"
        ]
        self.state["resolution_request"]["source_revision"] = self.state["current_task"]["source_revision"]
        handoff.export(self.state, self.run_dir)

    def test_submit_requires_an_unmodified_exported_handoff(self):
        fresh_run = Path(self.tmp.name) / "run-without-handoff"
        fresh_run.mkdir()
        with self.assertRaises(ValueError) as caught:
            handoff.submit(self.state, fresh_run, self.workspace, self.delivery)
        self.assertIn("--export-repair first", str(caught.exception))
        state = copy.deepcopy(self.state)
        state["current_task"]["requirements"].append("changed after export")
        with self.assertRaises(ValueError) as caught:
            handoff.submit(state, self.run_dir, self.workspace, self.delivery)
        self.assertIn("no longer matches", str(caught.exception))

    def test_submit_refuses_a_workspace_off_the_dispatched_base(self):
        write(self.workspace, "src/seam.py", "drifted while paused\n")
        with self.assertRaises(ValueError) as caught:
            handoff.submit(self.state, self.run_dir, self.workspace, self.delivery)
        self.assertIn("no longer at the dispatched base", str(caught.exception))

    def test_submit_refuses_changes_outside_the_repair_scope(self):
        write(self.delivery, "outside.py", "not part of the repair\n")
        with self.assertRaises(ValueError) as caught:
            handoff.submit(self.state, self.run_dir, self.workspace, self.delivery)
        self.assertIn("outside the dispatched repair's affected paths: outside.py", str(caught.exception))

    def test_submit_refuses_a_delivery_that_is_the_workspace_itself(self):
        with self.assertRaises(ValueError) as caught:
            handoff.submit(self.state, self.run_dir, self.workspace, self.workspace)
        self.assertIn("separate checkout", str(caught.exception))

    def test_submit_accepts_the_external_repair_and_advances_the_milestone(self):
        verdict = handoff.submit(self.state, self.run_dir, self.workspace, self.delivery)
        self.assertTrue(verdict["accepted"], verdict["message"])
        self.assertIn("milestone advanced", verdict["message"])
        self.assertEqual((self.workspace / "src/seam.py").read_text(), "fixed\n")
        self.assertEqual((self.workspace / "src/new.py").read_text(), "new\n")
        self.assertFalse((self.workspace / "src/gone.py").exists())
        state = self.state
        self.assertIsNone(state.get("current_task"))
        self.assertIsNone(state.get("resolution_request"))
        self.assertEqual(state["status"], "RUNNING")
        self.assertNotIn("stop_reason", state)
        self.assertEqual(len(state["task_archive"]), 1)
        self.assertEqual(state["task_archive"][0]["completed_by"], "external_repair")
        self.assertEqual(state["task_archive"][0]["changed_paths"], ["src/gone.py", "src/new.py", "src/seam.py"])
        self.assertEqual(state["user_events"][-1]["kind"], "external_repair_accepted")
        self.assertEqual(state["user_events"][-1]["actor"], "user_cli")
        accepted = [row for row in state["milestone_progress"].values() if row.get("accepted")]
        self.assertTrue(accepted, state["milestone_progress"])
        self.assertEqual(
            accepted[0]["accepted_source_revision"], handoff.source_scope.snapshot(self.workspace, state)["revision"]
        )

    def test_submit_rejects_a_failing_static_gate_and_rolls_the_workspace_back(self):
        state = copy.deepcopy(self.state)
        state["current_task"]["validation_plan"] = ['Run `python3 -c "raise SystemExit(3)"`']
        handoff.export(state, self.run_dir)  # The handoff must match the state under test.
        verdict = handoff.submit(state, self.run_dir, self.workspace, self.delivery)
        self.assertFalse(verdict["accepted"])
        self.assertIn("failed", verdict["message"])
        self.assertIn("exited 3", verdict["message"])
        self.assertEqual((self.workspace / "src/seam.py").read_text(), "original\n")
        self.assertEqual((self.workspace / "src/gone.py").read_text(), "gone\n")
        self.assertFalse((self.workspace / "src/new.py").exists())
        self.assertIsNotNone(state.get("current_task"))
        self.assertEqual(state["status"], "PAUSED_BUILDER_CLASSIFICATION")
        self.assertEqual(state["user_events"][-1]["kind"], "external_repair_rejected")
        self.assertIn("exited 3", state["user_events"][-1]["criterion"])

    def test_submit_rejects_when_the_regression_proof_fails(self):
        with (
            mock.patch.object(handoff.regression, "required", return_value=True),
            mock.patch.object(handoff.regression, "prove", return_value={"verdict": "FAIL", "failures": ["case X"]}),
            mock.patch.object(handoff.regression, "complete", return_value=False),
            mock.patch.object(
                handoff.regression,
                "rejection",
                return_value="Completion rejected: no passing regression proof (case X)",
            ),
        ):
            verdict = handoff.submit(self.state, self.run_dir, self.workspace, self.delivery)
        self.assertFalse(verdict["accepted"])
        self.assertIn("no passing regression proof", verdict["message"])
        self.assertEqual((self.workspace / "src/seam.py").read_text(), "original\n")
        self.assertEqual(self.state["user_events"][-1]["kind"], "external_repair_rejected")

    def test_verification_commands_run_in_the_repaired_workspace(self):
        recorded = []
        real = handoff.verify.run_command

        def spy(command, cwd, log_path, **kwargs):
            recorded.append((command, (Path(cwd) / "src/seam.py").read_text()))
            return real(command, cwd, log_path, **kwargs)

        with mock.patch.object(handoff.verify, "run_command", side_effect=spy):
            verdict = handoff.submit(self.state, self.run_dir, self.workspace, self.delivery)
        self.assertTrue(verdict["accepted"], verdict["message"])
        self.assertTrue(recorded)
        self.assertTrue(all(text == "fixed\n" for _, text in recorded), recorded)


if __name__ == "__main__":
    unittest.main()
