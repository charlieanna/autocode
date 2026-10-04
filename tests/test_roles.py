"""Issue #19: one list of job names, and names that follow the job not the AI."""
import unittest

import autocode_roles as roles
import autocode_status as status
import autocode_workflow as workflow


class OneListOfNames(unittest.TestCase):
    def test_every_stage_and_its_report_repair_has_a_screen_name(self):
        for stage in roles.STAGE_JOB:
            with self.subTest(stage=stage):
                name = roles.screen_name(stage)
                self.assertTrue(name and name[0].isupper(), name)
                self.assertEqual(name, roles.screen_name(stage + "_report_repair"))

    def test_no_unit_or_model_code_names_on_screen(self):
        banned = ("autoplanner", "autocode", "autoreview", "autoresolver",
                  "astra", "terra", "glm")
        for stage, job in roles.STAGE_JOB.items():
            name = roles.SCREEN[job]
            tokens = name.lower().replace("-", " ").split()
            for word in banned:
                self.assertNotIn(word, tokens, f"{stage} screen name {name!r} leaks {word!r}")
            # 'sol' as a whole token only — 'Resolver' is not a leak.
            self.assertNotIn("sol", tokens, name)
            self.assertNotIn("validator", tokens, name)

    def test_unknown_stages_title_case_but_are_not_in_the_table(self):
        self.assertEqual("Unlisted Stage", status.role_name("unlisted_stage"))
        self.assertNotIn("unlisted_stage", roles.STAGE_JOB)

    def test_stages_the_runner_queues_have_job_names(self):
        # Written as next_stage by AutoResolver diagnosis and the --planning-v2 flow (#29 shows them).
        for stage, name in {"astra_diagnose": "Resolver", "requirements": "Requirements", "plan": "Planner",
                            "plan_revise": "Planner", "plan_review": "Plan Reviewer",
                            "plan_finalize": "Plan Reviewer"}.items():
            self.assertEqual(name, status.role_name(stage), stage)

    def test_status_reexports_the_same_table(self):
        for stage in roles.STAGE_JOB:
            self.assertEqual(roles.screen_name(stage), status.ROLES[stage])
            self.assertEqual(roles.screen_name(stage), status.role_name(stage))


class NamesFollowTheJobNotTheAi(unittest.TestCase):
    def test_testing_is_tester_even_when_the_plan_reviewer_runs_it(self):
        # sol is the Tester job on the Validator AI.
        self.assertEqual("Tester", status.role_name("sol"))
        # Reviewer routing hands the same testing job to the Plan Reviewer
        # (autocode_workflow.MODE → astra_checkpoint). The screen name is the job.
        state = {"settings": {"workflow": {"mode": workflow.MODE}}}
        self.assertEqual("Tester", status.role_name("astra_checkpoint", state))
        self.assertEqual("Tester", status.role_name("astra_checkpoint_report_repair", state))
        # Without that routing, astra_checkpoint is the completion job.
        self.assertEqual("Completion Reviewer", status.role_name("astra_checkpoint"))
        self.assertEqual("Completion Reviewer", status.role_name("astra_checkpoint", {"settings": {}}))
        # Final-audit routing is the completion job again, not Tester.
        final = {"settings": {"workflow": {"mode": workflow.FINAL_MODE}}}
        self.assertEqual("Completion Reviewer", status.role_name("astra_checkpoint", final))

    def test_validation_routing_modes_match_the_workflow_module(self):
        self.assertEqual((workflow.MODE,), roles.VALIDATION_ROUTING_MODES)

    def test_plan_jobs_stay_plan_reviewer(self):
        for stage in ("astra_challenge", "astra_finalize", "plan_reviewer", "plan_finalizer"):
            self.assertEqual("Plan Reviewer", status.role_name(stage))

    def test_completion_is_completion_reviewer_not_bare_reviewer(self):
        for stage in ("astra_review", "decision_owner"):
            self.assertEqual("Completion Reviewer", status.role_name(stage))
        self.assertEqual("Code Reviewer", status.role_name("review_change"))

    def test_requirements_and_resolver_job_names(self):
        for stage in ("astra_discovery", "glm_revise", "requirements_planner", "requirements_revision"):
            self.assertEqual("Requirements", status.role_name(stage))
        self.assertEqual("Resolver", status.role_name("astra_resolve"))


if __name__ == "__main__":
    unittest.main()
