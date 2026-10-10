"""Where each stage may write (tools/autocode_stage_access.py), and the code that enforces it, agree."""

import re
import unittest

import autocode_jobs as jobs
import autocode_stage_access as access
import autocode_stray_writes as stray_writes
from units import common


class JobPromptsKeepScratchInTheWorkspace(unittest.TestCase):
    def test_no_job_sends_the_model_outside_the_workspace(self):
        # OpenCode stops a stage that touches an external directory, and the attempt is lost
        # (#228, #301). The review and bug prompts were moved inside the workspace; the design,
        # design-check and discussion prompts still told the model to work OUTSIDE it.
        for job in jobs.JOBS:
            with self.subTest(stage=job.STAGE):
                self.assertNotIn("OUTSIDE the workspace", job.PROMPT)
                self.assertNotRegex(job.PROMPT, r"(?i)scratch copy (?!under|inside|in )(outside|elsewhere)")

    def test_a_job_that_lets_the_model_make_a_scratch_copy_names_one_under_autocode(self):
        # A scratch tree the runner builds (the stuck-run Investigator's probes) is not the model's.
        for job in jobs.JOBS:
            if re.search(r"(?i)\b(make|create)\b[^.]{0,20}scratch cop", job.PROMPT):
                with self.subTest(stage=job.STAGE):
                    self.assertIn(".autocode/", job.PROMPT)

    def test_the_scratch_rule_names_the_stage_directory_and_forbids_external_paths(self):
        rule = access.scratch_rule("review_design")
        self.assertIn(".autocode/scratch/review_design/", rule)
        self.assertIn("Never use /tmp", rule)


class GuardsAgreeWithTheRules(unittest.TestCase):
    # Each job's own after-stage check, called the way its unit calls it. The stray check runs before
    # the report is validated, so a minimal report is enough to tell a stray write from a bad report.
    CHECKS = {
        "collect_design": lambda changed: jobs.design_intake.apply({}, {}, {"changed_files": changed}, "/repo"),
        "review_change": lambda changed: jobs.review_job.apply({}, {}, {"changed_files": changed}, "/repo"),
        "investigate_bug": lambda changed: jobs.bug_job.check({"note_path": ""}, changed),
        "review_design": lambda changed: jobs.design_job.check({"mode": ""}, changed),
        "answer_question": lambda changed: jobs.discuss_job.check(
            {"note_path": "docs/answer.md", "answer": ""}, changed, "/repo"
        ),
        "check_design": lambda changed: jobs.design_check_job.check({}, {"design_document": ""}, changed, "/repo"),
        "investigate_stuck": lambda changed: jobs.stuck_job.check({"diagnosis": ""}, changed),
    }

    def outcome(self, stage, changed):
        try:
            self.CHECKS[stage](changed)
        except stray_writes.StrayWrites as error:
            return error.paths
        except (ValueError, KeyError, TypeError):
            return []  # past the stray check, rejected (or not) for its report
        return []

    def test_every_job_has_a_check_here(self):
        self.assertEqual(set(jobs.STAGES), set(self.CHECKS))

    def test_each_job_rejects_exactly_the_changes_outside_its_listed_prefixes(self):
        for stage in jobs.STAGES:
            allowed = [prefix + "new.json" for prefix in access.job_writes(stage)]
            with self.subTest(stage=stage):
                self.assertEqual(["src/app.py"], self.outcome(stage, ["src/app.py", *allowed]))
                self.assertEqual([], self.outcome(stage, allowed))

    def test_only_job_stages_are_listed(self):
        self.assertLessEqual(set(access.JOB_WRITES), set(jobs.STAGES))

    def test_opencode_additions_stay_within_what_the_job_may_change(self):
        for stage, prefixes in access.OPENCODE_ADDITIONS.items():
            with self.subTest(stage=stage):
                self.assertTrue(all(prefix.startswith(access.job_writes(stage)) for prefix in prefixes))

    def test_a_report_repair_keeps_its_stage_s_opencode_additions(self):
        self.assertEqual(("review/",), access.opencode_additions("review_change_report_repair"))
        self.assertEqual((), access.opencode_additions("sol"))

    def test_judging_stages_launch_able_to_write_evidence_and_planning_does_not(self):
        for stage in access.JUDGING_STAGES:
            self.assertEqual("workspace-write", common.launch_sandbox(stage, False), stage)
        for stage in ("astra_plan", "astra_challenge", "requirements_gather", "astra_resolve"):
            self.assertEqual("read-only", common.launch_sandbox(stage, False), stage)


if __name__ == "__main__":
    unittest.main()
