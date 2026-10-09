"""A missing test prerequisite is repaired before approval or stops before a writer."""
import json
import shutil

from .harness.adversarial import AdversarialCase


class PlanningAttacks(AdversarialCase):
    def setUp(self):
        super().setUp()
        reference = self.root / "reference"
        shutil.copytree(self.scenario.reference, reference)
        (reference / "tests").mkdir()
        (reference / "test_greet.py").rename(reference / "tests/test_greet.py")
        (reference / "tests/__init__.py").write_text("")
        config = json.loads(self.config_path.read_text())
        config.update(reference=str(reference), check="python3 -m unittest discover -s tests -t .",
                      paths=["greet.py", "tests/test_greet.py", "README.md"])
        self.config_path.write_text(json.dumps(config))

    def test_missing_package_is_added_to_the_plan_before_approval_and_completes(self):
        self.set_fault("planning", "repair_scaffolding")
        self.start_to_approval()
        self.assertTrue(self.trace("scaffolding_repaired"), self.root)
        plans = self.trace("scaffolding_plan")
        self.assertNotIn("tests/__init__.py", plans[0]["assigned"])
        self.assertIn("tests/__init__.py", plans[-1]["assigned"])
        self.assertFalse(self.trace("stage_enter", "terra"), "No Builder before approval")
        self.approve()
        self.assertEqual("TASK_COMPLETE", self.finish()["status"])
        self.assertTrue((self.project / "tests/__init__.py").is_file())

    def test_persistent_impossible_scope_stops_before_approval_or_builder(self):
        self.set_fault("planning", "persist_missing_scaffolding")
        view = self.driver.drive(self.scenario.brief)
        plans = self.trace("scaffolding_plan")
        self.assertGreaterEqual(len(plans), 2, self.root)
        self.assertLessEqual(len(plans), 4, "Plan repair must remain bounded")
        self.assertTrue(all("tests/__init__.py" not in row["assigned"] for row in plans))
        self.assertNotEqual("approve_plan", (view.get("needs") or {}).get("kind"))
        self.assertFalse(self.trace("stage_enter", "terra"))
        self.assertFalse(view["done"])


class PlanningClarificationRecovery(AdversarialCase):
    def test_verified_citation_correction_survives_clarification_and_finishes(self):
        from .harness.project import git
        (self.project / "README.md").write_text("Implement the requested greeting CLI.\n")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "A source file makes citation checks applicable")
        self.set_fault("planning", "remember_citation_after_clarification")
        view = self.driver.drive(self.scenario.brief)
        cycles = self.trace("citation_cycle")
        self.assertTrue(any(not row["guided"] for row in cycles), self.root)
        self.assertTrue(any(row["guided"] and not row["answered"] for row in cycles), self.root)
        self.assertTrue(any(row["guided"] and row["answered"] for row in cycles), self.root)
        self.assertEqual("TASK_COMPLETE", view["status"], self.root)
        self.assertEqual(1, len(self.trace("stage_enter", "investigate_stuck")),
                         "Remembering a report correction must not buy another investigation")
        self.assertEqual(["Q_AFTER"], [row["id"] for row in self.driver.answers])


class PlanningMetadataRecovery(AdversarialCase):
    def setUp(self):
        super().setUp()
        # These faults target metadata returned by the full revise/finalize pipeline.
        self.driver.flags += ["--requirements-model", "gpt-6-luna", "--no-adaptive-planning"]

    def test_metadata_omission_and_mislabeled_addition_reach_approval_without_model_repair(self):
        self.set_fault("planning", "recover_planning_metadata")
        self.start_to_approval()
        self.assertTrue(self.trace("omitted_trace_id"), self.root)
        self.assertTrue(self.trace("mislabeled_addition", "glm_revise"), self.root)
        self.assertEqual([], self.trace("metadata_rejection"), "Mechanical metadata needs no model repair")
        self.assertFalse(self.trace("stage_enter", "terra"), "Approval is still required")
        displayed = self.driver.call("show-goal", "--show-goal", action=True)
        self.assertIn("C1", displayed.stdout)
        self.assertIn("C2", displayed.stdout)
        self.approve()
        self.assertEqual("TASK_COMPLETE", self.finish()["status"], self.root)

    def test_redundant_metadata_does_not_allow_a_proof_downgrade_or_builder(self):
        self.set_fault("planning", "metadata_with_proof_downgrade")
        view = self.driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], self.root)
        self.assertFalse(self.trace("stage_enter", "terra"))
        self.assertFalse(any(step["kind"] == "approve-plan" for step in self.driver.steps))
        self.assertTrue(self.trace("mislabeled_addition", "glm_revise"), self.root)
        rejected = self.trace("metadata_rejection", "glm_revise")
        self.assertTrue(any("drops or changes 'C1'" in row["error"] for row in rejected), self.root)


class DraftProofPlanning(AdversarialCase):
    def test_draft_command_is_corrected_before_approval_without_a_question(self):
        from .harness.project import git
        (self.project / "README.md").write_text("Implement the requested greeting CLI.\n")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "Source for the review's verification correction")
        self.set_fault("planning", "repair_draft_suite_proof")
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual("TASK_COMPLETE", view["status"], self.root)
        self.assertEqual([], self.driver.answers, "A proof repair is not a product decision")
        plans = self.trace("draft_proof")
        self.assertEqual("python3 -m unittest nonexistent_test.py", plans[0]["method"])
        self.assertEqual("python3 -m unittest test_greet.py", plans[-1]["method"])
        self.assertEqual(1, sum(step["kind"] == "approve-plan" for step in self.driver.steps))
        self.assertFalse(self.trace("stage_enter", "investigate_stuck"), self.root)

    def test_draft_test_proof_cannot_be_downgraded_to_a_suite_command(self):
        self.set_fault("planning", "reject_draft_proof_downgrade")
        view = self.driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], self.root)
        self.assertFalse(self.trace("stage_enter", "terra"))
        self.assertFalse(any(step["kind"] == "approve-plan" for step in self.driver.steps))
        self.assertTrue(self.trace("stage_enter", "glm_revise"), self.root)
        rejected = self.trace("proof_rejection_observed", "glm_revise")
        self.assertTrue(any("without a user-backed" in row["error"] for row in rejected), self.root)


class DraftExamplePlanning(AdversarialCase):
    scenario_id = "ladder-03-csv-validation-cli"

    def test_wrong_model_written_count_is_corrected_before_approval_and_finishes_without_questions(self):
        self.set_fault("planning", "repair_draft_example")
        view = self.start_to_approval()
        self.assertFalse(self.trace("stage_enter", "terra"), "No Builder before approving the corrected plan")
        displayed = self.driver.call("show-goal", "--show-goal", action=True)
        self.assertIn("Reviewed draft example corrections:", displayed.stdout)
        self.assertIn("C_COUNT", displayed.stdout)
        self.assertIn('"rows": 2', displayed.stdout)
        self.assertEqual([], self.driver.answers, "An arithmetic correction is not a new user decision")
        self.approve(view)
        self.assertEqual("TASK_COMPLETE", self.finish()["status"], self.root)
        self.assertEqual(1, sum(step["kind"] == "approve-plan" for step in self.driver.steps))
        self.assertEqual(1, len(self.trace("stage_enter", "terra")))
        self.assertFalse(self.trace("stage_enter", "investigate_stuck"), self.root)

    def test_example_receipt_cannot_also_change_an_input_and_dispatch_a_builder(self):
        self.set_fault("planning", "reject_draft_example_input_change")
        view = self.driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], self.root)
        self.assertFalse(self.trace("stage_enter", "terra"))
        self.assertFalse(any(step["kind"] == "approve-plan" for step in self.driver.steps))
        rejected = self.trace("example_rejection_observed", "glm_revise")
        self.assertTrue(any("saved user answer" in row["error"] for row in rejected), self.root)
