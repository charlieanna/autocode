"""A clean-review qualification must have valid input before judging its report."""

import dataclasses
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scenarios"))

from harness import catalog, verdict
from harness.oracle import apply_patch, python_tests, tail
from harness.project import materialize


class ReviewCleanFixtureTests(unittest.TestCase):
    def test_patch_applies_strictly_and_its_candidate_passes(self):
        scenario = catalog.load("review-clean-pr")
        with tempfile.TemporaryDirectory() as directory:
            project = materialize(scenario.seed, Path(directory) / "project")
            applied = apply_patch(project, project / "pr-191.patch")
            self.assertEqual(0, applied.returncode, tail(applied))
            checked = python_tests(project)
            self.assertEqual(0, checked.returncode, tail(checked))

    def test_reference_and_broken_review_controls_still_discriminate(self):
        scenario = catalog.load("review-clean-pr")
        controls = [("seed", None, False), ("reference", scenario.reference, True)]
        controls += [(path.name, path, False) for path in scenario.broken]
        with tempfile.TemporaryDirectory() as directory:
            for name, overlay, accepted in controls:
                with self.subTest(control=name):
                    project = materialize(scenario.seed, Path(directory) / name, *([overlay] if overlay else []))
                    result = verdict.evaluate(scenario, project)
                    self.assertFalse(result.error, result.summary)
                    self.assertEqual(accepted, result.passed, result.summary)

    def test_an_approving_report_on_invalid_input_is_an_error_not_a_model_verdict(self):
        scenario = catalog.load("review-clean-pr")
        with tempfile.TemporaryDirectory() as directory:
            owned = Path(directory)
            damaged = owned / "catalog"
            shutil.copytree(scenario.dir, damaged)
            (damaged / "seed" / "pr-191.patch").write_text("not a patch\n")
            invalid = dataclasses.replace(scenario, dir=damaged)
            project = materialize(invalid.seed, owned / "project", invalid.reference)
            result = verdict.evaluate(invalid, project)
            self.assertTrue(result.error, result.summary)
            self.assertIn("invalid review fixture", result.error.lower())
            outcome, _ = verdict.judge("TASK_COMPLETE", result)
            self.assertEqual(verdict.ERROR, outcome)

    def test_a_patch_whose_candidate_tests_fail_is_also_a_fixture_error(self):
        scenario = catalog.load("review-clean-pr")
        with tempfile.TemporaryDirectory() as directory:
            owned = Path(directory)
            damaged = owned / "catalog"
            shutil.copytree(scenario.dir, damaged)
            patch = damaged / "seed" / "pr-191.patch"
            source = patch.read_text()
            self.assertIn("+        return code in self.retryable_errors", source)
            patch.write_text(source.replace("+        return code in self.retryable_errors", "+        return False"))
            invalid = dataclasses.replace(scenario, dir=damaged)
            project = materialize(invalid.seed, owned / "project", invalid.reference)
            result = verdict.evaluate(invalid, project)
            self.assertIn("candidate tests fail", result.error.lower())
            self.assertEqual(verdict.ERROR, verdict.judge("TASK_COMPLETE", result)[0])
