"""Black-box recovery attacks, with independent positive and negative controls.

Run with the repository venv: python -m unittest scenarios.test_adversarial_recovery
Failures are product invariant violations, never expectedFailure/xfail markers.
Every test proves its injected fault reached the intended provider boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.adversarial import AdversarialCase
from harness.project import git


class RecoveryAttacks(AdversarialCase):
    def setUp(self):
        super().setUp()
        # An existing repository requires valid code citations. An entirely
        # empty greenfield seed intentionally does not enforce that contract.
        (self.project / "README.md").write_text("Build the greeting CLI described in the task.\n")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "Seed a repository citation target")

    def run_fault(self, case):
        self.set_fault("recovery", case)
        return self.driver.drive(self.scenario.brief)

    def assert_citation_reached_investigator(self):
        self.assertTrue(self.trace("planner_citation_injected"), self.root)
        probes = self.trace("investigator_probe")
        self.assertEqual(1, len(probes), f"One bounded investigation expected: {self.root}")
        self.assertTrue(self.trace("stage_enter", "investigate_stuck"), self.root)
        return probes[0]

    def test_iteration_evidence_allows_a_verified_investigator_retry(self):
        view = self.run_fault("investigator_iteration")
        probe = self.assert_citation_reached_investigator()
        self.assertTrue(all(row["exists"] for row in probe["cited_files"]))
        self.assertTrue(self.trace("guided_planner_retry"), self.root)
        self.assertEqual("TASK_COMPLETE", view["status"], f"Valid iteration evidence must recover: {self.root}")

    def test_run_root_evidence_allows_a_verified_investigator_retry(self):
        view = self.run_fault("investigator_run_root")
        probe = self.assert_citation_reached_investigator()
        self.assertEqual(2, len(probe["cited_files"]))
        self.assertTrue(all(row["exists"] for row in probe["cited_files"]))
        self.assertTrue(any(Path(ref).name == "state.json" for ref in probe["evidence_refs"]))
        self.assertEqual(
            "TASK_COMPLETE",
            view["status"],
            f"A cited existing run-root artifact must be staged like iteration evidence: {self.root}",
        )

    def test_missing_investigator_citation_cannot_authorize_a_retry(self):
        view = self.run_fault("investigator_missing_citation")
        probe = self.assert_citation_reached_investigator()
        self.assertEqual([True, False], [row["exists"] for row in probe["cited_files"]])
        self.assertFalse(self.trace("guided_planner_retry"), self.root)
        self.assertFalse(self.trace("stage_enter", "terra"), self.root)
        self.assertNotEqual("TASK_COMPLETE", view["status"], view)
        self.assertLessEqual(len(self.trace("stage_enter", "investigate_stuck")), 3)

    def test_uncited_investigator_input_cannot_authorize_a_retry(self):
        view = self.run_fault("investigator_uncited_input")
        probe = self.assert_citation_reached_investigator()
        self.assertEqual(1, len(probe["cited_files"]))
        self.assertTrue(probe["cited_files"][0]["exists"])
        self.assertIn("run/state.json", probe["probe"])
        self.assertFalse(self.trace("guided_planner_retry"), self.root)
        self.assertFalse(self.trace("stage_enter", "terra"), self.root)
        self.assertNotEqual("TASK_COMPLETE", view["status"], view)
        self.assertLessEqual(len(self.trace("stage_enter", "investigate_stuck")), 3)

    def test_one_truncated_report_is_repaired_without_losing_the_run(self):
        view = self.run_fault("truncated_once")
        self.assertEqual(1, len(self.trace("truncated_report_emitted")), self.root)
        self.assertTrue(
            any(row["repair"] for row in self.trace("stage_enter", "astra_discovery")),
            f"Fault must reach report-only repair: {self.root}",
        )
        self.assertEqual("TASK_COMPLETE", view["status"], view)

    def test_persistent_truncation_stops_before_builder_with_bounded_recovery(self):
        view = self.run_fault("truncated_repeated")
        faults = self.trace("truncated_report_emitted")
        self.assertGreaterEqual(len(faults), 2, self.root)
        self.assertTrue(any(row["repair"] for row in self.trace("stage_enter", "astra_discovery")), self.root)
        self.assertLessEqual(
            len(faults), 4, f"Persistent report failure must stop with a bounded repair count: {self.root}"
        )
        self.assertFalse(self.trace("stage_enter", "terra"), self.root)
        self.assertNotEqual("TASK_COMPLETE", view["status"], view)
        self.assertIn(view["needs"]["kind"], ("resume", "answer"), view)

    def test_completion_permission_recovery_keeps_or_rebuilds_validation(self):
        view = self.run_fault("completion_denial")
        faults = self.trace("completion_permission_denied")
        self.assertEqual(1, len(faults), self.root)
        self.assertEqual("PASS", faults[0]["validation"]["verdict"])
        handoffs = [row for row in self.trace("completion_handoff") if not row["repair"]]
        self.assertGreaterEqual(len(handoffs), 2, f"Fault must reach automatic completion recovery: {self.root}")
        self.assertEqual(
            handoffs[0]["source_revision"],
            handoffs[1]["source_revision"],
            "The injected denial did not change the source",
        )
        self.assertEqual(
            "PASS",
            handoffs[1]["validation_verdict"],
            f"Retried completion needs retained valid evidence or fresh validation: {self.root}",
        )
        self.assertEqual("TASK_COMPLETE", view["status"], view)


if __name__ == "__main__":
    import unittest

    unittest.main()
