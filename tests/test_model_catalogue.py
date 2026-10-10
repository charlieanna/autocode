"""Catalogue search + role suggestion (offline; no live opencode spend)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HERE))

import model_catalogue as mc  # noqa: E402

CATALOGUE = [
    "zai-coding-plan/glm-5.3",
    "zai-coding-plan/glm-5.3-flash",
    "zai-coding-plan/glm-5.2-highspeed",
    "xiaomi-token-plan-sgp/mimo-v2.6-pro",
    "xiaomi-token-plan-sgp/mimo-v2.6-flash",
    "mimo-token-plan/mimo-v2.6-pro",
    "opencode/mimo-v2.6-flash-free",
    "openai/gpt-5.6-sol",
    "openai/gpt-6-astra",
    "openai/gpt-6-sol",
    "not a model",
]


class UsableTest(unittest.TestCase):
    def test_all_provider_routes_are_offered_without_model_blacklists(self):
        self.assertEqual(sorted(set(CATALOGUE) - {"not a model"}), mc.usable(CATALOGUE))
        self.assertEqual(
            ["new-plan/future-model"],
            mc.usable(
                [
                    "new-plan/future-model",
                    "new-plan/future-model",
                    "/missing-provider",
                    "missing-model/",
                    "model with space",
                    "plan/model\t",
                ]
            ),
        )


class SuggestTest(unittest.TestCase):
    def test_prefers_subscription_pair_when_present(self):
        roles = mc.suggest(mc.usable(CATALOGUE))
        self.assertEqual("zai-coding-plan/glm-5.3", roles["builder"]["model"])
        self.assertEqual("openai/gpt-6-sol", roles["reviewer"]["model"])
        self.assertEqual("medium", roles["builder"]["effort"])
        self.assertEqual("openai/gpt-6-sol", roles["validator"]["model"])
        self.assertEqual("openai/gpt-6-sol", roles["completion"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", roles["requirements"]["model"])
        self.assertEqual("medium", roles["requirements"]["effort"])

    def test_verifier_never_equals_producer(self):
        roles = mc.suggest(mc.usable(CATALOGUE))
        for producer, verifier in mc.INDEPENDENCE:
            self.assertNotEqual(
                mc.family(roles[producer]["model"]), mc.family(roles[verifier]["model"]), f"{producer}/{verifier}"
            )

    def test_falls_back_within_catalogue_when_preferred_missing(self):
        roles = mc.suggest(["openai/gpt-5.6-sol", "openai/gpt-6-astra"])
        self.assertTrue(roles["builder"]["model"].startswith("openai/"))
        self.assertNotEqual(
            mc.family(roles["builder"]["model"]) and roles["builder"]["model"], roles["validator"]["model"]
        )
        self.assertEqual("openai/", roles["validator"]["model"][:7])


class RenderTest(unittest.TestCase):
    def test_render_lists_models_and_role_table(self):
        models = mc.usable(CATALOGUE)
        text = mc.render(models, mc.suggest(models))
        self.assertIn("openai/gpt-6-sol", text)
        self.assertIn("xiaomi-token-plan-sgp/mimo-v2.6-pro", text)
        self.assertIn("| builder |", text)
        self.assertIn("verifier", text.lower())


# The OpenCode default routes (tools/providers/opencode.py DEFAULT_MODELS).
DEFAULTS = {
    "requirements": "zai-coding-plan/glm-5.3",
    "glm": "zai-coding-plan/glm-5.3",
    "terra": "zai-coding-plan/glm-5.3",
    "plan_reviewer": "openai/gpt-6-sol",
    "sol": "openai/gpt-6-sol",
    "completion": "openai/gpt-6-sol",
    "astra": "openai/gpt-6-astra",
}


def routes(**changes):
    return {role: {"model": model, "reasoning_effort": "high"} for role, model in {**DEFAULTS, **changes}.items()}


class AdviseTest(unittest.TestCase):
    def test_nothing_is_missing_when_every_route_is_listed(self):
        advice = mc.advise(routes(), CATALOGUE, openai_auth="oauth")
        self.assertEqual({}, advice["missing"])
        self.assertEqual([], advice["flags"])

    def test_missing_judges_get_a_usable_model_and_say_it_is_not_a_judge(self):
        advice = mc.advise(
            routes(), ["zai-coding-plan/glm-5.3", "openai/gpt-6-astra", "openai/gpt-6-luna"], openai_auth="oauth"
        )
        self.assertEqual({"plan_reviewer", "sol", "completion"}, set(advice["missing"]))
        for role in ("plan_reviewer", "sol", "completion"):
            self.assertEqual("openai/gpt-6-luna", advice["suggestions"][role]["model"])
            self.assertIn("no strong judge is usable", advice["suggestions"][role]["why"])
        self.assertEqual(
            [
                "--plan-reviewer-model",
                "openai/gpt-6-luna",
                "--sol-model",
                "openai/gpt-6-luna",
                "--completion-model",
                "openai/gpt-6-luna",
            ],
            advice["flags"],
        )

    def test_a_subscription_comes_before_per_token_billing_then_the_wanted_tier(self):
        judges = [
            "zai-coding-plan/glm-5.3",
            "openai/gpt-6-astra",
            "openai/gpt-6-luna",
            "openai/gpt-5.6-sol",
            "kilo/gpt-6-sol",
        ]
        self.assertEqual(
            "openai/gpt-5.6-sol", mc.advise(routes(), judges, openai_auth="oauth")["suggestions"]["sol"]["model"]
        )
        no_subscription_judge = ["zai-coding-plan/glm-5.3", "openai/gpt-6-luna", "kilo/gpt-6-sol"]
        self.assertEqual(
            "openai/gpt-6-luna",
            mc.advise(routes(), no_subscription_judge, openai_auth="oauth")["suggestions"]["sol"]["model"],
        )

    def test_a_checker_never_shares_its_builders_glm_family(self):
        advice = mc.advise(
            routes(),
            ["zai-coding-plan/glm-5.3", "zai/glm-5.3", "openai/gpt-6-luna", "openai/gpt-6-astra"],
            openai_auth="oauth",
        )
        self.assertEqual("openai/gpt-6-luna", advice["suggestions"]["sol"]["model"])

    def test_new_mimo_routes_keep_the_existing_family_independence_rule(self):
        self.assertEqual("mimo", mc.family("mimo-token-plan/mimo-v2.6-pro"))
        self.assertFalse(mc.independent("mimo-token-plan/mimo-v2.6-pro", "opencode/mimo-v2.6-flash-free"))

    def test_a_producer_never_takes_the_model_that_checks_it(self):
        advice = mc.advise(routes(), ["openai/gpt-6-sol", "openai/gpt-6-astra", "zai/glm-5.3"], openai_auth="oauth")
        self.assertEqual("openai/gpt-6-astra", advice["suggestions"]["terra"]["model"])
        self.assertEqual("openai/gpt-6-astra", advice["suggestions"]["glm"]["model"])

    def test_resolver_tier_can_be_suggested_for_other_roles(self):
        advice = mc.advise(routes(), ["zai-coding-plan/glm-5.3", "openai/gpt-6-astra"], openai_auth="oauth")
        for role in ("plan_reviewer", "sol", "completion"):
            self.assertEqual("openai/gpt-6-astra", advice["suggestions"][role]["model"])

    def test_api_authenticated_openai_routes_are_offered_and_suggested(self):
        available = ["zai-coding-plan/glm-5.3", "openai/gpt-6-astra", "openai/gpt-6-sol", "kilo/some-judge"]
        advice = mc.advise(routes(sol="openai/gpt-7"), available, openai_auth="api")
        self.assertEqual({"sol": "openai/gpt-7"}, advice["missing"])
        self.assertEqual("openai/gpt-6-sol", advice["suggestions"]["sol"]["model"])
        self.assertEqual("pay per token", mc.plan("openai/gpt-6-sol", "api")[1])
        self.assertEqual({}, mc.advise(routes(), available, openai_auth="api")["missing"])

    def test_mimo_token_plan_free_flash_and_unknown_routes_can_replace_missing_models(self):
        for model in (
            "mimo-token-plan/mimo-v2.6-pro",
            "opencode/mimo-v2.6-flash-free",
            "zai-coding-plan/glm-5.2-highspeed",
            "new-plan/future-model",
        ):
            with self.subTest(model=model):
                advice = mc.advise(routes(terra=model), [model, "openai/gpt-6-sol"])
                self.assertEqual(model, advice["suggestions"]["glm"]["model"])
                self.assertIn(model, [entry["model"] for entry in advice["catalogue"]])

    def test_the_stop_message_groups_by_plan_and_tier_and_names_per_token_billing(self):
        available = ["zai-coding-plan/glm-5.3", "kilo/some-judge", "opencode/x-free"]
        text = mc.render_advice(mc.advise(routes(), available, openai_auth="oauth"))
        self.assertIn("Cannot use with OpenCode: openai/gpt-6-sol (Plan Reviewer, Tester, Completion Reviewer)", text)
        self.assertIn("Z.AI Coding Plan · subscription", text)
        self.assertIn("Kilo Gateway · pay per token", text)
        self.assertRegex(text, r"zai-coding-plan/glm-5\.3 +cheap worker +default for Requirements, Planner, Builder")
        self.assertIn("opencode/x-free", text)
        self.assertNotIn("not offered", text)
        self.assertIn("--sol-model kilo/some-judge", text)
        self.assertIn("kilo/some-judge bills per token, not by subscription.", text)
        self.assertTrue(text.endswith("AutoCode never changes a model without you."))


class FakeProvider:
    NAME = "OpenCode"

    def __init__(self, models, auth="oauth"):
        self.models, self.auth, self.auth_calls = models, auth, 0

    def available_models(self, workspace):
        if isinstance(self.models, Exception):
            raise self.models
        return self.models

    def openai_auth(self, workspace):
        self.auth_calls += 1
        return self.auth


def new_settings(**changes):
    return {
        "engine": "opencode",
        "roles": {role: {**route, "engine": "opencode"} for role, route in routes(**changes).items()},
    }


class ChooseTest(unittest.TestCase):
    AVAILABLE = {"zai-coding-plan/glm-5.3", "openai/gpt-6-astra", "openai/gpt-6-sol", "openai/gpt-6-luna"}

    def test_a_run_whose_models_are_all_listed_starts_without_asking(self):
        provider = FakeProvider(self.AVAILABLE)
        settings = new_settings()
        self.assertIs(settings, mc.choose(settings, provider, Path("."), interactive=True, ask=self.fail))
        self.assertEqual(0, provider.auth_calls)

    def test_an_unlisted_model_stops_the_run_with_the_list_and_the_flags(self):
        settings = new_settings(sol="openai/gpt-7")
        before = copy.deepcopy(settings)
        with self.assertRaises(RuntimeError) as raised:
            mc.choose(settings, FakeProvider(self.AVAILABLE), Path("."), interactive=False)
        self.assertIn("Cannot use with OpenCode: openai/gpt-7 (Tester).", str(raised.exception))
        self.assertIn("--sol-model openai/gpt-6-sol", str(raised.exception))
        self.assertEqual(before, settings)

    def test_in_chat_the_user_can_accept_the_replacements(self):
        shown = []
        chosen = mc.choose(
            new_settings(sol="openai/gpt-7", completion="openai/gpt-7"),
            FakeProvider(self.AVAILABLE),
            Path("."),
            interactive=True,
            ask=lambda _prompt: "y",
            out=shown.append,
        )
        self.assertEqual(
            ("openai/gpt-6-sol", "high"), (chosen["roles"]["sol"]["model"], chosen["roles"]["sol"]["reasoning_effort"])
        )
        self.assertEqual("openai/gpt-6-sol", chosen["roles"]["completion"]["model"])
        self.assertIn("Suggested replacements:", shown[0])

    def test_declining_in_chat_changes_nothing(self):
        settings = new_settings(sol="openai/gpt-7")
        before = copy.deepcopy(settings)
        with self.assertRaisesRegex(RuntimeError, "No model was changed"):
            mc.choose(
                settings,
                FakeProvider(self.AVAILABLE),
                Path("."),
                interactive=True,
                ask=lambda _prompt: "",
                out=lambda _text: None,
            )
        self.assertEqual(before, settings)

    def test_a_role_without_an_independent_replacement_stops_without_asking(self):
        with self.assertRaises(RuntimeError) as raised:
            mc.choose(
                new_settings(), FakeProvider({"zai-coding-plan/glm-5.3"}), Path("."), interactive=True, ask=self.fail
            )
        self.assertIn("choose one from the list with --sol-model", str(raised.exception))

    def test_replacements_offer_api_authenticated_openai(self):
        provider = FakeProvider(self.AVAILABLE | {"kilo/some-judge"}, auth="api")
        with self.assertRaises(RuntimeError) as raised:
            mc.choose(new_settings(sol="openai/gpt-7"), provider, Path("."), interactive=False)
        self.assertIn("--sol-model openai/gpt-6-sol", str(raised.exception))
        self.assertEqual(1, provider.auth_calls)

    def test_a_listed_api_authenticated_openai_route_is_accepted(self):
        settings = new_settings()
        self.assertIs(
            settings, mc.choose(settings, FakeProvider(self.AVAILABLE, auth="api"), Path("."), interactive=False)
        )

    def test_a_provider_that_cannot_list_or_a_codex_run_starts_as_before(self):
        settings = new_settings(sol="openai/gpt-7")
        for provider in (
            FakeProvider(RuntimeError("Cannot list")),
            FakeProvider(OSError("no opencode")),
            FakeProvider(None),
            object(),
        ):
            self.assertIs(settings, mc.choose(settings, provider, Path("."), interactive=False))
        codex = {"engine": "codex", "roles": {"sol": {"model": "gpt-7"}}}
        self.assertIs(codex, mc.choose(codex, FakeProvider(self.AVAILABLE), Path("."), interactive=False))


if __name__ == "__main__":
    unittest.main()
