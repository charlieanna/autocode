"""Public resume reconsiders a legacy no-change pause before any provider call."""

import copy
import json
import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_builder_failure as failure
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_source_scope as source_scope
import autocode_util as util

from . import test_json_output as json_fixture


class RetainedWriterCLI(unittest.TestCase):
    setUp = json_fixture.JsonActionCliTests.setUp
    cli = json_fixture.JsonActionCliTests.cli
    invoke = json_fixture.JsonActionCliTests.invoke

    def test_named_resume_retains_writer_changes_and_requires_fresh_verification(self):
        body = copy.deepcopy(self.state["goal_contract"]["body"])
        body["acceptance_criteria"][0]["verification_method"] = "test: test_value"
        lifecycle.install_draft(self.state, body, origin="test")
        util.atomic_json(self.run_dir / "state.json", self.state)
        self.invoke("--show-goal")
        self.assertEqual(0, self.invoke("--approve-goal", goals.token(self.state["goal_contract"]))[0])
        state = self.state
        start = source_scope.snapshot(self.root, state)
        test = self.root / "test_value.py"
        test.write_text(
            "import unittest\nclass Value(unittest.TestCase):\n def test_value(self): self.assertEqual(1, 1)\n"
        )
        end = source_scope.snapshot(self.root, state)
        before, after = self.run_dir / "writer.before.json", self.run_dir / "writer.after.json"
        util.atomic_json(before, start)
        util.atomic_json(after, end)
        archive = self.run_dir / "test-value.archive"
        archive.write_bytes(test.read_bytes())
        contract = state["goal_contract"]
        state["current_task"] = {
            "id": "writer-task",
            "kind": "implement",
            "contract_hash": contract["hash"],
            "milestone_id": "M1",
            "source_revision": start["revision"],
            "affected_paths": ["test_value.py"],
            "acceptance_criteria": ["C1"],
        }
        writer = {
            "stage": "write_acceptance_tests",
            "task_id": "writer-task",
            "iteration": 1,
            "contract_hash": contract["hash"],
            "contract_revision": contract["revision"],
            "source_revision": end["revision"],
            "before_ref": str(before),
            "after_ref": str(after),
            "output": str(self.run_dir / "writer.json"),
            "exit_code": 0,
            "accounted": True,
            "changed_files": ["test_value.py"],
        }
        Path(writer["output"]).write_text("{}")
        report = {"changed_files": [], "summary": "Existing implementation already passes"}
        output = self.run_dir / "builder.json"
        util.atomic_json(output, report)
        builder = {**writer, "stage": "builder", "before_ref": str(after), "output": str(output), "changed_files": []}
        state["stages"] = [writer, builder]
        state["acceptance_tests"] = {
            "receipts": {
                "writer": {
                    "contract_hash": contract["hash"],
                    "source_revision": end["revision"],
                    "output": writer["output"],
                    "case_ids": ["C1"],
                    "results": {"C1": "passes candidate; fails original reference"},
                    "files": {"test_value.py": {"sha256": util.file_hash(test), "archive": str(archive)}},
                }
            }
        }
        state.update(no_progress_reports=[report], no_progress_batches=3)
        failure.queue(
            state,
            failure.evidence(state, builder),
            "Builder completed an implementation attempt without source changes",
            enabled=True,
        )
        state.update(status="PAUSED_REQUESTED", stop_reason="Legacy checkpoint before Investigator")
        # Synthetic pre-upgrade checkpoint. All subsequent actions use the CLI.
        util.atomic_json(self.run_dir / "state.json", state)
        before_view = json.loads(self.invoke("--status", "--json")[1])["view"]
        observed = []

        def at_verification(current, *_args):
            observed.append(current["next_stage"])
            raise util.Paused("PAUSED_REQUESTED", "Fixture stops before fresh proof; no PASS is granted")

        with patch.object(runner.build_loop.regression, "before_review", side_effect=at_verification):
            code, stdout, stderr = self.invoke(*shlex.split(before_view["next_command"])[1:], "--json")
        self.assertEqual(2, code, stderr)
        after_view = json.loads(stdout)["view"]
        self.assertEqual(["tester"], observed)
        self.assertEqual("tester", after_view["next_stage"])
        self.assertFalse(after_view["done"])
        self.assertIsNone(self.state.get("validation"))
        self.assertIsNone(self.state.get("regression_proof"))
        self.assertNotIn("stuck_investigation", self.state)
        self.assertEqual([writer, builder], self.state["stages"], "resume must not repeat a paid stage")
        self.assertEqual(["test_value.py"], self.state["changed_files"])
        self.assertEqual("candidate_retained", after_view["recoveries"][-1]["kind"])
