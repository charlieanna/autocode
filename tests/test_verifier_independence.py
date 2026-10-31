"""The one verifier-independence rule every route change asks (#612). Pure."""

import unittest

import autocode_verifier_independence as independence

GLM = "zai-coding-plan/glm-5.3"
GLM_OLDER = "zai-coding-plan/glm-5.2"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def settings(**models):
    return {"roles": {role: {"model": model} for role, model in models.items()}}


SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-4-5-20251001"


class VerifierIndependenceTests(unittest.TestCase):
    def test_the_builder_is_checked_by_the_tester_and_completion_reviewer(self):
        self.assertEqual(("sol", "completion"), independence.verifiers("terra"))
        self.assertEqual(("plan_reviewer",), independence.verifiers("glm"))
        self.assertEqual((), independence.verifiers("astra"))

    def test_identical_models_and_glm_or_mimo_families_clash(self):
        self.assertEqual("identical model openai/gpt-6-sol", independence.clash("openai/gpt-6-sol", "gpt-6-sol"))
        self.assertEqual(f"same family glm ({GLM} / {GLM_OLDER})", independence.clash(GLM, GLM_OLDER))
        self.assertIsNone(independence.clash(GLM, MIMO))
        self.assertEqual(
            "same family openai (openai/gpt-6-sol / openai/gpt-6-luna)",
            independence.clash("openai/gpt-6-sol", "openai/gpt-6-luna"),
        )
        self.assertIsNone(independence.clash("openai/gpt-6-sol", "openai/gpt-6-luna", vendor_engine=True))

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

    def test_a_workflow_without_producers_has_no_pairs_to_break(self):
        same = settings(terra=GLM, sol=GLM, completion=GLM, glm=MIMO, plan_reviewer=MIMO)
        self.assertEqual([], independence.clashes(same, "review"))
        self.assertEqual([], independence.violations(same, "review"))
        # The discuss workflow's only stage is the Analyst's answer: nothing is built to grade.
        self.assertEqual([], independence.clashes(same, "discuss"))
        self.assertEqual([], independence.violations(same, "discuss"))
        self.assertEqual(3, len(independence.violations(same, "build")))
        self.assertEqual(3, len(independence.violations(same, "bugfix")))
        self.assertEqual(3, len(independence.violations(same)))

    def test_another_providers_openai_prefix_may_share_but_does_not_clash(self):
        # The guard refuses only proven clashes; moving a checker covers the possible ones too.
        self.assertFalse(independence.shares("openai/custom-model", "custom-model"))
        self.assertTrue(independence.may_share("openai/custom-model", "custom-model"))
        self.assertTrue(independence.may_share(GLM, GLM_OLDER))
        self.assertFalse(independence.may_share("custom/gpt-6-sol", "gpt-6-sol"))
        self.assertFalse(independence.may_share(GLM, None))

    def test_one_model_reached_through_a_gateway_is_still_one_model(self):
        # The Kilo Gateway and a direct login run the same weights, so neither may check the
        # other's work; a leading segment that *is* the vendor keeps two models distinct.
        gateway = "kilo/anthropic/claude-opus-5.5"
        self.assertEqual(f"identical model {gateway}", independence.clash(gateway, "anthropic/claude-opus-5.5"))
        self.assertEqual(
            ["Builder/Tester: identical model " + gateway],
            independence.violations(settings(terra=gateway, sol="anthropic/claude-opus-5.5")),
        )
        self.assertIsNone(independence.clash(gateway, GLM))
        self.assertFalse(independence.shares("custom/gpt-6-sol", "gpt-6-sol"))
        # A gateway in front of the OpenAI login is that GPT tier, in either spelling.
        routed = "kilo/openai/gpt-6-sol"
        self.assertEqual(f"identical model {routed}", independence.clash(routed, "openai/gpt-6-sol"))
        self.assertTrue(independence.shares(routed, "gpt-6-sol"))
        self.assertTrue(independence.shares(routed, "openai/gpt-6-luna"), "another GPT tier is the same family")
        # The Kilo Gateway's MiMo route and the Xiaomi Token Plan's are one family, not one spelling.
        self.assertEqual(
            f"same family mimo (kilo/xiaomi/mimo-v2.6-pro / {MIMO})",
            independence.clash("kilo/xiaomi/mimo-v2.6-pro", MIMO),
        )


class ClaudeFamilyTests(unittest.TestCase):
    def test_two_claude_models_are_one_family_however_they_are_reached(self):
        self.assertEqual(f"same family claude ({SONNET} / {OPUS})", independence.clash(SONNET, OPUS))
        self.assertEqual(
            "same family claude (anthropic/claude-opus-5-5 / kilo/anthropic/claude-sonnet-5-5)",
            independence.clash("anthropic/claude-opus-5-5", "kilo/anthropic/claude-sonnet-5-5"),
        )
        self.assertIsNone(independence.clash(SONNET, "openai/gpt-6-sol"))
        self.assertIsNone(independence.clash(OPUS, GLM))

    def test_a_claude_model_beside_another_vendor_passes_the_guard(self):
        chosen = settings(
            terra=HAIKU, sol="openai/gpt-6-sol", completion="openai/gpt-6-sol", glm=GLM, plan_reviewer=OPUS
        )
        self.assertEqual([], independence.violations({**chosen, "provider": "opencode"}))

    def test_claude_checking_claude_is_refused_on_an_engine_that_reaches_other_vendors(self):
        same = settings(terra=HAIKU, sol=SONNET, completion=OPUS, glm=SONNET, plan_reviewer=OPUS)
        self.assertEqual(
            [
                f"Builder/Tester: same family claude ({HAIKU} / {SONNET})",
                f"Builder/Completion Reviewer: same family claude ({HAIKU} / {OPUS})",
                f"Planner/Plan Reviewer: same family claude ({SONNET} / {OPUS})",
            ],
            independence.violations({**same, "provider": "opencode"}),
        )

    def test_the_claude_engine_reaches_only_claude_so_its_default_routes_keep_working(self):
        defaults = {
            **settings(terra=HAIKU, sol=SONNET, completion=OPUS, glm=SONNET, plan_reviewer=OPUS),
            "provider": "claude",
        }
        self.assertEqual([], independence.violations(defaults))
        # The exemption is for different Claude models, not for one model grading itself.
        itself = {**defaults, "roles": {**defaults["roles"], "sol": {"model": HAIKU}}}
        self.assertEqual([f"Builder/Tester: identical model {HAIKU}"], independence.violations(itself))


if __name__ == "__main__":
    unittest.main()
