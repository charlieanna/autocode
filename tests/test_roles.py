"""Issue #19: one list of job names, and names that follow the job not the AI."""

import unittest

import autocode_roles as roles
import autocode_status as status
import autocode_workflow as workflow
from units import autoplanner


class OneListOfNames(unittest.TestCase):
    def test_every_stage_and_its_report_repair_has_a_screen_name(self):
        for stage in roles.STAGE_JOB:
            with self.subTest(stage=stage):
                name = roles.screen_name(stage)
                self.assertTrue(name and name[0].isupper(), name)
                self.assertEqual(name, roles.screen_name(stage + "_report_repair"))

    def test_no_unit_or_model_code_names_on_screen(self):
        banned = ("autoplanner", "autocode", "autoreview", "autoresolver", "astra", "terra", "glm")
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

    def test_a_legacy_stage_code_reads_as_its_jobs_name_not_its_old_code(self):
        # A run saved before the stage rename (#3010) still records astra/terra/sol codes;
        # the status view must read them through autocode_names, never title-case the code
        # back onto the screen ("Astra Discovery", "Terra", "Sol" in a live VS Code chat, 2026-11-12).
        import autocode_names as names

        expected = {
            "astra_discovery": "Planner",
            "astra_challenge": "Plan Reviewer",
            "glm_revise": "Planner",
            "astra_finalize": "Plan Reviewer",
            "terra": "Builder",
            "sol": "Tester",
            "astra_review": "Completion Reviewer",
        }
        for old, name in expected.items():
            with self.subTest(old=old):
                self.assertEqual(name, roles.screen_name(old))
                self.assertEqual(name, roles.screen_name(old + "_report_repair"))
                self.assertEqual(name, status.role_name(old))
        for old, new in names.STAGES.items():
            with self.subTest(old=old, new=new):
                self.assertIsNotNone(roles.job_for_stage(old), old)
                self.assertEqual(roles.screen_name(new), roles.screen_name(old))
                self.assertIn(roles.screen_name(old), roles.SCREEN.values())
                for word in ("astra", "terra", "glm", "sol"):
                    self.assertNotIn(word, roles.screen_name(old).lower().split(), old)

    def test_stages_the_runner_queues_have_job_names(self):
        # Written as next_stage by AutoResolver diagnosis and the --planning-v2 flow (#29 shows them).
        for stage, name in {
            "diagnose": "Resolver",
            "requirements": "Requirements",
            "plan": "Planner",
            "plan_revise": "Planner",
            "plan_review": "Plan Reviewer",
            "plan_finalize": "Plan Reviewer",
        }.items():
            self.assertEqual(name, status.role_name(stage), stage)

    def test_status_reexports_the_same_table(self):
        for stage in roles.STAGE_JOB:
            self.assertEqual(roles.screen_name(stage), status.ROLES[stage])
            self.assertEqual(roles.screen_name(stage), status.role_name(stage))


class NamesFollowTheJobNotTheAi(unittest.TestCase):
    def test_configured_slot_labels_follow_the_canonical_jobs_and_planning_flow(self):
        for role, expected in {
            "task_planner": "Resolver and per-task Planner",
            "planner": "Planner",
            "requirements": "Requirements",
            "plan_reviewer": "Plan Reviewer",
            "builder": "Builder",
            "tester": "Tester",
            "completion": "Completion Reviewer",
            "resolver": "Resolver",
            "architect": "Design Reviewer",
            "stuck_investigator": "Investigator",
            "acceptance_tester": "Tester (acceptance tests)",
        }.items():
            self.assertEqual(expected, roles.route_name(role, {}))
        self.assertEqual("Planner", roles.route_name("planner", {"settings": {"planning_flow": "v2"}}))
        self.assertEqual("Future Role", roles.route_name("future_role", {}))

    def test_no_two_configured_slots_share_a_screen_name(self):
        # A chat's model list showed "Planner" twice, on GLM and on MiMo, because the per-task Planner's
        # slot was named after its primary job like the Planner's own (live VS Code run, 2026-11-11).
        from autocode_role_route import records

        names = [roles.route_name(role, {}) for role in roles.ROUTE_JOB]
        self.assertEqual(len(names), len(set(names)), names)
        state = {"settings": {"roles": {role: {"model": "m"} for role in roles.ROUTE_JOB}}}
        jobs = [row["job"] for row in records(state).values()]
        self.assertEqual(len(jobs), len(set(jobs)), jobs)

    def test_the_glm_slot_label_follows_the_planner_work_it_runs(self):
        # role_for sends the Planner's draft (draft_plan) and revision (revise_plan)
        # to the glm slot in the legacy flow, and plan/plan_revise in v2: its primary job
        # is the Planner's in both (#2592).
        from autocode_role_route import record as route_record

        for flow in ({}, {"settings": {"planning_flow": "v2"}}):
            with self.subTest(flow=flow):
                self.assertEqual("Planner", roles.route_name("planner", flow))
        state = {"settings": {"roles": {"planner": {"model": "zai-coding-plan/glm-5.3"}}}}
        self.assertEqual("Planner", route_record(state, "planner")["job"])

    def test_v2_names_follow_the_actual_planning_stages(self):
        from units.autoplanner import V2_STAGE_ROLES, V2_STAGES

        expected = {"requirements": "Requirements", "planner": "Planner", "plan_reviewer": "Plan Reviewer"}
        for stage in V2_STAGES:
            with self.subTest(stage=stage):
                self.assertEqual(expected[V2_STAGE_ROLES[stage]], status.role_name(stage))
                self.assertEqual(expected[V2_STAGE_ROLES[stage]], status.role_name(stage + "_report_repair"))

    def test_testing_is_tester_even_when_the_plan_reviewer_runs_it(self):
        # sol is the Tester job on the Validator AI.
        self.assertEqual("Tester", status.role_name("tester"))
        # Reviewer routing hands the same testing job to the Plan Reviewer
        # (autocode_workflow.MODE → review_checkpoint). The screen name is the job.
        state = {"settings": {"workflow": {"mode": workflow.MODE}}}
        self.assertEqual("Tester", status.role_name("review_checkpoint", state))
        self.assertEqual("Tester", status.role_name("review_checkpoint_report_repair", state))
        # Without that routing, review_checkpoint is the completion job.
        self.assertEqual("Completion Reviewer", status.role_name("review_checkpoint"))
        self.assertEqual("Completion Reviewer", status.role_name("review_checkpoint", {"settings": {}}))
        # Final-audit routing is the completion job again, not Tester.
        final = {"settings": {"workflow": {"mode": workflow.FINAL_MODE}}}
        self.assertEqual("Completion Reviewer", status.role_name("review_checkpoint", final))

    def test_validation_routing_modes_match_the_workflow_module(self):
        self.assertEqual((workflow.MODE,), roles.VALIDATION_ROUTING_MODES)

    def test_plan_jobs_stay_plan_reviewer(self):
        for stage in ("review_plan", "finalize_plan", "plan_reviewer", "plan_finalizer"):
            self.assertEqual("Plan Reviewer", status.role_name(stage))

    def test_completion_is_completion_reviewer_not_bare_reviewer(self):
        for stage in ("review_completion", "decision_owner"):
            self.assertEqual("Completion Reviewer", status.role_name(stage))
        # A run an earlier runtime saved at a retired stage still shows that stage by its job.
        self.assertEqual("Code Reviewer", status.role_name("review_change"))
        self.assertEqual("Job recognizer", status.role_name("recognize_workflow"))

    def test_planner_draft_and_revision_job_names(self):
        # prompts/discovery.md and prompts/requirements-revise.md both open "You are the
        # Planner": draft_plan drafts the plan and revise_plan revises it (#2592).
        for stage in ("draft_plan", "revise_plan"):
            self.assertEqual("Planner", status.role_name(stage))
            self.assertEqual("Planner", status.role_name(stage + "_report_repair"))

    def test_figma_requirements_brief_and_resolver_job_names(self):
        # The Figma workflow's Requirements Planner writes both the UI brief and its
        # revision (autocode_ui prompts: "Act as the Requirements Planner").
        for stage in ("requirements_planner", "requirements_revision"):
            self.assertEqual("Requirements", status.role_name(stage))
        self.assertEqual("Resolver", status.role_name("resolve"))


if __name__ == "__main__":
    unittest.main()


class EachPipelineJobNamesTheSlotItRunsOn(unittest.TestCase):
    """route_for_job, which clients read, agrees with the slot the runner launches each stage on (#3087)."""

    JOINT = ("requirements", "planner", "plan_reviewer", "task_planner", "builder", "tester", "completion")

    def check(self, configured, joint, stages):
        state = {"settings": {"joint_planning": joint, "roles": {slot: {"model": slot} for slot in configured}}}
        for stage in stages:
            with self.subTest(stage=stage, joint=joint):
                job = roles.job_for_stage(stage, state)
                self.assertIn(job, roles.PIPELINE)
                self.assertEqual(autoplanner._resolve_route(state, stage), roles.route_for_job(job, configured))

    def test_a_joint_run_shows_the_plan_reviewers_own_slot_not_the_resolvers(self):
        planning = ("requirements_gather", "draft_plan", "review_plan", "revise_plan", "finalize_plan")
        self.check(self.JOINT, True, (*planning, "review_completion", "review_checkpoint"))
        self.assertEqual("plan_reviewer", roles.route_for_job("plan_reviewer", dict.fromkeys(self.JOINT)))

    def test_a_run_without_their_own_slots_reviews_on_astra(self):
        self.check(("task_planner", "builder", "tester"), False, ("plan_task", "review_plan", "review_completion"))

    def test_an_unconfigured_job_has_no_slot(self):
        self.assertIsNone(roles.route_for_job("requirements", {"task_planner": {}}))
        self.assertIsNone(roles.route_for_job("unknown", {"task_planner": {}}))
