"""Advisory independence is distinct from the mandatory verifier policy."""

import copy
import unittest

import autocode_dispatch as dispatch
import autocode_model_identity as identity
import autocode_route_advisory as advisory


def settings(**models):
    return {"joint_planning": True, "roles": {role: {"model": model} for role, model in models.items()}}


class RouteAdvisoryTests(unittest.TestCase):
    def test_output_limit_reroute_exposes_the_new_contract_build_link(self):
        before = settings(
            requirements="glm-5.3",
            planner="glm-5.3",
            task_planner="glm-5.3",
            builder="mimo-v2.6-pro",
            tester="glm-5.3",
            completion="glm-5.3",
            plan_reviewer="gpt-6-sol",
        )
        after = copy.deepcopy(before)
        after["roles"]["planner"]["model"] = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
        held = copy.deepcopy((before, after))
        [message] = advisory.warnings(before, after)
        # The glm slot runs the Planner's work (954f51e3 relabelled it from Requirements).
        self.assertIn("Planner -> Builder", message)
        self.assertIn("--planner-model / --builder-model", message)
        self.assertIn("glm", before["roles"]["planner"]["model"])
        self.assertIn("mimo-v2.6-pro", message)
        self.assertIn("original authorship", message)
        dispatch.enforce_cross_model_verification({"settings": after})
        self.assertEqual(held, (before, after))

    def test_requirements_and_plan_routes_are_reported(self):
        before = settings(requirements="glm-5.3", planner="glm-5.3", task_planner="gpt-6-sol", builder="mimo-v2.6-pro")
        after = copy.deepcopy(before)
        after["roles"]["task_planner"]["model"] = "zai-coding-plan/glm-5.2"
        [message] = advisory.warnings(before, after)
        self.assertIn("Requirements -> Planner", message)

    def test_builder_change_reports_every_new_link_in_order(self):
        before = settings(requirements="glm-5.3", planner="glm-5.2", task_planner="glm-5.3", builder="mimo-v2.6-pro")
        after = copy.deepcopy(before)
        after["roles"]["builder"]["model"] = "glm-5.3-flash"
        messages = advisory.warnings(before, after)
        self.assertEqual(3, len(messages))
        self.assertIn("Requirements -> Builder", messages[0])
        self.assertIn("--requirements-model / --builder-model", messages[0])
        # The glm and astra slots both run the Planner's work; their flags tell the links apart.
        self.assertIn("Planner -> Builder", messages[1])
        self.assertIn("--planner-model / --builder-model", messages[1])
        self.assertIn("Planner -> Builder", messages[2])
        self.assertIn("--task-planner-model / --builder-model", messages[2])

    def test_v2_uses_actual_planner_route_and_job_names(self):
        before = settings(
            requirements="glm-5.3", planner="gpt-6-sol", task_planner="mimo-v2.6-pro", builder="mimo-v2.6-pro"
        )
        before["planning_flow"] = "v2"
        after = copy.deepcopy(before)
        after["roles"]["planner"]["model"] = "glm-5.2"
        [message] = advisory.warnings(before, after)
        self.assertIn("Requirements -> Planner", message)
        self.assertNotIn("Builder", message)

    def test_existing_shared_link_and_effort_only_change_are_quiet(self):
        before = settings(planner="glm-5.3", builder="glm-5.2")
        after = copy.deepcopy(before)
        after["roles"]["planner"].update(model="glm-5.3-flash", reasoning_effort="high")
        self.assertEqual([], advisory.warnings(before, after))
        self.assertEqual([], advisory.warnings(after, copy.deepcopy(after)))

    def test_leaving_shared_family_is_quiet(self):
        before = settings(planner="glm-5.3", builder="glm-5.2")
        after = settings(planner="mimo-v2.6-pro", builder="glm-5.2")
        self.assertEqual([], advisory.warnings(before, after))

    def test_new_run_and_missing_routes_are_quiet(self):
        selected = settings(planner="glm-5.3", builder="glm-5.3")
        self.assertEqual([], advisory.warnings(None, selected))
        self.assertEqual([], advisory.warnings({}, selected))
        self.assertEqual([], advisory.warnings(settings(planner="glm-5.3"), settings(planner="mimo-v2.6-pro")))

    def test_gpt_aliases_and_tiers_share_the_openai_family_but_other_prefixes_do_not(self):
        self.assertTrue(identity.shared_family("gpt-6-sol", "openai/gpt-6-sol"))
        self.assertTrue(identity.shared_family("gpt-6-sol", "openai/gpt-6-astra"))
        self.assertFalse(identity.shared_family("custom/gpt-6-sol", "gpt-6-sol"))
        self.assertFalse(identity.shared_family("openai/custom-model", "custom-model"))
        self.assertFalse(identity.shared_family("", ""))

    def test_a_codex_run_reports_one_shared_gpt_model_not_the_gpt_family(self):
        # Codex reaches only GPT models, so the independence rule counts one model there, not the family.
        before = settings(
            requirements="gpt-5.6-sol", planner="gpt-5.6-sol", task_planner="gpt-5.6-sol", builder="gpt-5.6-terra"
        )
        before["engine"] = "codex"
        after = copy.deepcopy(before)
        after["roles"]["task_planner"]["model"] = "gpt-5.6-terra"
        [message] = advisory.warnings(before, after)
        self.assertIn("Planner -> Builder", message)
        self.assertIn("gpt-5.6-terra / gpt-5.6-terra", message)
        # On any other engine every GPT model is one family: the link was shared already, nothing is new.
        del before["engine"], after["engine"]
        self.assertEqual([], advisory.warnings(before, after))

    def test_single_model_opt_in_retains_advisory_for_a_new_link(self):
        before = settings(planner="glm-5.3", builder="mimo-v2.6-pro")
        before["single_model_mode"] = True
        after = copy.deepcopy(before)
        after["roles"]["planner"]["model"] = "mimo-v2.6-pro"
        self.assertEqual(1, len(advisory.warnings(before, after)))


if __name__ == "__main__":
    unittest.main()
