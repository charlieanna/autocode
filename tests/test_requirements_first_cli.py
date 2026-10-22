"""Requirements is the first stage of every run and follow-up turn, and names the job (#1860).

Owner decision 2026-10-22: Requirements is AutoCode's front door, on a plain CLI run as in a chat, and
the separate job-recognizer call goes. These tests drive catalog scenarios through the CLI with the
scripted provider, as ``scenarios/run.py run --fake`` does (no --spec-first, so nothing asks the person
to confirm what Requirements understood), and judge them by their saved stages, the status view's
``workflow`` and the scenario's own oracle. Before #1860 every such run began with ``recognize_workflow``;
a bug fix, a review and a question never met Requirements at all, and "Fix them." after a review went to
the Planner without it.
"""

import argparse
import tempfile
import unittest
from pathlib import Path

from scenarios import run

RECOGNIZER = "recognize_workflow"
REQUIREMENTS = "requirements_gather"


class RequirementsFirstCliTests(unittest.TestCase):
    def run_fake(self, scenario):
        temporary = tempfile.TemporaryDirectory(prefix="requirements-first-")
        self.addCleanup(temporary.cleanup)
        args = argparse.Namespace(
            fake=True,
            profile=None,
            fake_solution="reference",
            out=Path(temporary.name),
            autocode=None,
            max_steps=None,
            timeout_minutes=10,
        )
        result = run.run_one(run.catalog.load(scenario), args)
        self.assertEqual("PASS", result["verdict"], result["summary"])
        return result

    def assertStartsAtRequirements(self, stages, first_job_stage):
        model = [stage for stage in stages if not stage.endswith("_report_repair")]
        self.assertNotIn(RECOGNIZER, stages, "Requirements names the job; no recognizer call")
        self.assertEqual([REQUIREMENTS, first_job_stage], model[:2], stages)
        self.assertEqual(1, model.count(REQUIREMENTS), stages)

    def test_a_plain_cli_question_goes_from_requirements_to_the_analyst(self):
        result = self.run_fake("discuss-cache-choice")
        self.assertEqual("discuss", result["workflow"])
        self.assertStartsAtRequirements(result["metrics"]["model_stage_names"], "answer_question")
        self.assertNotIn("astra_discovery", result["metrics"]["model_stage_names"], "Nothing is planned")

    def test_a_plain_cli_review_goes_from_requirements_to_the_code_reviewer(self):
        result = self.run_fake("review-clean-pr")
        self.assertEqual("review", result["workflow"])
        self.assertEqual([REQUIREMENTS, "review_change"], result["metrics"]["model_stage_names"])

    def test_a_plain_cli_bug_fix_goes_from_requirements_to_the_investigator(self):
        result = self.run_fake("bugfix-trivial")
        self.assertEqual("bugfix", result["workflow"])
        self.assertStartsAtRequirements(result["metrics"]["model_stage_names"], "investigate_bug")

    def test_fix_the_findings_after_a_review_is_named_a_build_by_requirements(self):
        result = self.run_fake("review-then-fix")
        review, fix = result["turns"]
        self.assertEqual(("review", "build"), (review["workflow"], fix["workflow"]))
        self.assertEqual([REQUIREMENTS, "review_change"], review["model_stage_names"])
        # The follow-up begins at Requirements too, which reads the earlier review as context and names a
        # build; the Planner then plans the fix from the review's findings.
        self.assertStartsAtRequirements(fix["model_stage_names"], "astra_discovery")


if __name__ == "__main__":
    unittest.main()
