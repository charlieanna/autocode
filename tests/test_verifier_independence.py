"""The one verifier-independence rule every route change asks (#612). Pure."""

import unittest

import autocode_verifier_independence as independence

GLM = "zai-coding-plan/glm-5.3"
GLM_OLDER = "zai-coding-plan/glm-5.2"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def settings(**models):
    return {"roles": {role: {"model": model} for role, model in models.items()}}


class VerifierIndependenceTests(unittest.TestCase):
    def test_the_builder_is_checked_by_the_tester_and_completion_reviewer(self):
        self.assertEqual(("sol", "completion"), independence.verifiers("terra"))
        self.assertEqual(("plan_reviewer",), independence.verifiers("glm"))
        self.assertEqual((), independence.verifiers("astra"))

    def test_identical_models_and_glm_or_mimo_families_clash(self):
        self.assertEqual("identical model openai/gpt-6-sol", independence.clash("openai/gpt-6-sol", "gpt-6-sol"))
        self.assertEqual(f"same family glm ({GLM} / {GLM_OLDER})", independence.clash(GLM, GLM_OLDER))
        self.assertIsNone(independence.clash(GLM, MIMO))
        self.assertIsNone(independence.clash("openai/gpt-6-sol", "openai/gpt-6-luna"))

    def test_violations_name_every_pair_in_order(self):
        problems = independence.violations(
            settings(terra=GLM, sol=GLM_OLDER, completion=GLM, glm=MIMO, plan_reviewer=MIMO)
        )
        self.assertEqual(
            [
                f"Builder/Tester: same family glm ({GLM} / {GLM_OLDER})",
                f"Builder/Completion Reviewer: identical model {GLM}",
                f"Planner/Plan Reviewer: identical model {MIMO}",
            ],
            problems,
        )

    def test_missing_routes_and_single_model_mode_have_no_violations(self):
        self.assertEqual([], independence.violations(settings(terra=GLM)))
        self.assertEqual([], independence.violations(None))
        same = settings(terra=GLM, sol=GLM)
        self.assertNotEqual([], independence.violations(same))
        self.assertEqual([], independence.violations({**same, "single_model_mode": True}))

    def test_another_providers_openai_prefix_may_share_but_does_not_clash(self):
        # The guard refuses only proven clashes; moving a checker covers the possible ones too.
        self.assertFalse(independence.shares("openai/custom-model", "custom-model"))
        self.assertTrue(independence.may_share("openai/custom-model", "custom-model"))
        self.assertTrue(independence.may_share(GLM, GLM_OLDER))
        self.assertFalse(independence.may_share("custom/gpt-6-sol", "gpt-6-sol"))
        self.assertFalse(independence.may_share(GLM, None))


if __name__ == "__main__":
    unittest.main()
