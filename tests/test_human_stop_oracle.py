"""The unreproduced-bug oracle accepts only an honest unanswered Requirements stop."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from harness import catalog
from harness.project import git, materialize, without_maintenance


class HumanStopOracleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="human-stop-oracle-")
        self.addCleanup(temporary.cleanup)
        self.scenario = catalog.load("bugfix-not-reproducible")
        self.project = without_maintenance(materialize(self.scenario.seed, Path(temporary.name) / "project"))
        self.oracle = self.scenario.oracle()
        self.note = json.loads((self.scenario.reference / "docs/bugs/none-cells.json").read_text())

    def stopped(self):
        return {
            "status": "WAITING_FOR_USER",
            "view": {
                "status": "WAITING_FOR_USER",
                "done": False,
                "workflow": None,
                "needs": {
                    "kind": "answer",
                    "questions": [
                        {"id": f"REPORTER_{number}", "question": question, "options": [], "proposed_default": ""}
                        for number, question in enumerate(self.note["questions"], start=1)
                    ],
                },
            },
            "stages": ["requirements_gather"],
            "model_stages": ["requirements_gather"],
            "answers": [],
            "approvals": [],
            "cli_calls": ["start"],
        }

    def checks(self, run):
        return {row.name: row for row in self.oracle(self.project, self.scenario, run)}

    def test_exact_unanswered_requirements_stop_passes_before_a_workflow_is_dispatched(self):
        checks = self.checks(self.stopped())
        self.assertTrue(checks)
        self.assertTrue(all(row.ok for row in checks.values()), checks)

    def test_an_arbitrary_or_answered_question_is_not_reporter_evidence(self):
        initial = self.stopped()
        variants = []
        for questions in (
            [],
            initial["view"]["needs"]["questions"][:1],
            [{"id": "OTHER", "question": "May I change the model route?", "options": []}],
            [initial["view"]["needs"]["questions"][0]] * 2,
        ):
            run = copy.deepcopy(initial)
            run["view"]["needs"]["questions"] = questions
            variants.append(run)
        for field, value in (("proposed_default", "Assume the latest version"), ("options", ["Assume latest"])):
            run = copy.deepcopy(initial)
            run["view"]["needs"]["questions"][0][field] = value
            variants.append(run)
        run = copy.deepcopy(initial)
        run["view"]["needs"]["questions"][1]["id"] = "REPORTER_1"
        variants.append(run)
        run = copy.deepcopy(initial)
        run["answers"] = [{"id": "REPORTER_1", "text": "Invented input"}]
        variants.append(run)
        for run in variants:
            with self.subTest(run=run):
                self.assertFalse(self.checks(run)["reports_not_reproduced_or_asks_the_reporter"].ok)

    def test_build_dispatch_and_plan_approval_are_still_rejected(self):
        for stage in ("builder", "orchestrator", "plan_task"):
            run = self.stopped()
            run["stages"].append(stage)
            with self.subTest(stage=stage):
                self.assertFalse(self.checks(run)["no_builder_dispatched"].ok)
        run = self.stopped()
        run["cli_calls"].append("approve-plan")
        self.assertFalse(self.checks(run)["no_build_plan_approval_requested"].ok)
        run = self.stopped()
        run["approvals"] = [{"gate": "approve_plan"}]
        checks = self.checks(run)
        self.assertTrue(any(not row.ok for row in checks.values()), checks)

    def test_nonwaiting_or_plan_offering_views_cannot_use_the_early_stop(self):
        for key, value in (
            ("done", True),
            ("status", "RUNNING"),
            ("workflow", "discuss"),
            ("displayed_plan", {"token": "plan"}),
            ("next_command", ["autocode", "--approve-goal", "plan"]),
        ):
            run = self.stopped()
            run["view"][key] = value
            with self.subTest(key=key):
                checks = self.checks(run)
                self.assertTrue(any(not row.ok for row in checks.values()), checks)
        run = self.stopped()
        run["view"]["needs"] = {"kind": "approve_plan", "token": "plan"}
        self.assertFalse(self.checks(run)["no_build_plan_offered"].ok)

    def test_unanswered_questions_cannot_complete_even_with_a_negative_note(self):
        (self.project / "docs/bugs/none-cells.json").write_text(json.dumps(self.note))
        run = self.stopped()
        run.update(status="TASK_COMPLETE", stages=["requirements_gather", "investigate_bug"])
        run["model_stages"] = run["stages"][:]
        run["view"].update(status="TASK_COMPLETE", done=True, workflow="bugfix")
        checks = self.checks(run)
        self.assertTrue(any(not row.ok for row in checks.values()), checks)

    def test_a_committed_phantom_source_change_is_still_rejected(self):
        source = self.project / "csvx/__init__.py"
        source.write_text(source.read_text() + "\n# An unauthorized phantom fix.\n")
        git(self.project, "add", "csvx/__init__.py")
        git(self.project, "commit", "-qm", "Phantom change")
        self.assertFalse(self.checks(self.stopped())["workspace_unchanged_except_report"].ok)

    def test_a_requirements_stop_cannot_add_a_report_or_extra_model_stage(self):
        run = self.stopped()
        run["model_stages"].append("answer_question")
        checks = self.checks(run)
        self.assertTrue(any(not row.ok for row in checks.values()), checks)
        (self.project / "docs/bugs/none-cells.json").write_text(json.dumps(self.note))
        checks = self.checks(self.stopped())
        self.assertTrue(any(not row.ok for row in checks.values()), checks)

    def test_historical_negative_diagnosis_without_pending_questions_still_passes(self):
        (self.project / "docs/bugs/none-cells.json").write_text(json.dumps(self.note))
        run = self.stopped()
        run.update(status="TASK_COMPLETE", stages=["requirements_gather", "investigate_bug"])
        run["model_stages"] = run["stages"][:]
        run["view"].update(status="TASK_COMPLETE", done=True, workflow="bugfix", needs=None)
        checks = self.checks(run)
        self.assertTrue(all(row.ok for row in checks.values()), checks)


if __name__ == "__main__":
    unittest.main()
