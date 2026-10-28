"""Catalogue search + role suggestion (offline; no live opencode spend)."""

from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HERE))

import autocode_builder_policy as policy  # noqa: E402
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

    def test_family_is_the_shared_identity_rule(self):
        # One rule for every caller (autocode_model_identity): a gateway in front of a model does not
        # make it another family, and GPT tiers are one family (2026-10-20).
        self.assertEqual(mc.family("openai/gpt-6-luna"), mc.family("kilo/openai/gpt-6-sol"))
        self.assertEqual(mc.family("anthropic/claude-sonnet-5.5"), mc.family("kilo/anthropic/claude-opus-5.5"))
        self.assertNotEqual(mc.family("zai-coding-plan/glm-5.3"), mc.family("openai/gpt-6-sol"))

    def test_new_mimo_routes_keep_the_existing_family_independence_rule(self):
        self.assertEqual("mimo", mc.family("mimo-token-plan/mimo-v2.6-pro"))
        self.assertFalse(mc.independent("mimo-token-plan/mimo-v2.6-pro", "opencode/mimo-v2.6-flash-free"))

    def test_a_producer_never_takes_the_model_that_checks_it(self):
        advice = mc.advise(routes(), ["openai/gpt-6-sol", "openai/gpt-6-astra", "zai/glm-5.3"], openai_auth="oauth")
        # The Tester runs GPT-6 Sol, so no GPT tier may build: GLM does (GPT tiers are one family, 2026-10-20).
        self.assertEqual("zai/glm-5.3", advice["suggestions"]["terra"]["model"])
        self.assertEqual("zai/glm-5.3", advice["suggestions"]["glm"]["model"])  # the Plan Reviewer is a GPT tier

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

    def climbing(self):
        """A run whose judges are on GPT-6 Luna, with the default stronger Builder attempt (#612)."""
        settings = new_settings(
            plan_reviewer="openai/gpt-6-luna", sol="openai/gpt-6-luna", completion="openai/gpt-6-luna"
        )
        settings["builder_retry"] = {
            "strong_model": "openai/gpt-6-sol",
            "strong_reasoning_effort": "xhigh",
            "checker_model": "zai-coding-plan/glm-5.3",
        }
        return settings

    def test_the_same_list_leaves_a_run_without_a_stronger_attempt_it_does_not_offer(self):
        settings = mc.choose(
            self.climbing(), FakeProvider(self.AVAILABLE - {"openai/gpt-6-sol"}), Path("."), interactive=False
        )
        self.assertIsNone(settings["builder_retry"]["strong_model"])
        self.assertEqual("openai/gpt-6-sol", settings["builder_retry"]["not_offered"])
        offered = mc.choose(self.climbing(), FakeProvider(self.AVAILABLE), Path("."), interactive=False)
        self.assertEqual("openai/gpt-6-sol", offered["builder_retry"]["strong_model"])

    def test_in_chat_the_accepted_routes_are_checked_for_the_stronger_attempt_too(self):
        settings = self.climbing()
        settings["roles"]["sol"]["model"] = "openai/gpt-7"
        chosen = mc.choose(
            settings,
            FakeProvider(self.AVAILABLE - {"openai/gpt-6-sol"}),
            Path("."),
            interactive=True,
            ask=lambda _prompt: "y",
            out=lambda _text: None,
        )
        self.assertEqual("openai/gpt-6-luna", chosen["roles"]["sol"]["model"])
        self.assertIsNone(chosen["builder_retry"]["strong_model"])
        self.assertEqual("openai/gpt-6-sol", chosen["builder_retry"]["not_offered"])
        # The Builder then makes one more ordinary retry and pauses, naming the model its provider lacks.
        chosen["builder_retry"].update(enabled=True, ordinary_retries=1)
        state = {
            "settings": chosen,
            "goal_contract": {"hash": "approved"},
            "current_task": {"id": "task1", "milestone_id": "M1"},
            "status": "RUNNING",
        }
        actions = [policy.failure(state, f"e{n}", "failure") for n in (1, 2, 3)]
        self.assertEqual(["retry", "retry", "pause"], actions)
        self.assertEqual(
            "Execution failure: " + policy.NOT_OFFERED.format(model="openai/gpt-6-sol"), state["stop_reason"]
        )

    def test_a_stronger_model_the_person_named_and_the_list_lacks_stops_the_new_run(self):
        settings = self.climbing()
        settings["builder_retry"]["strong_model"] = "openai/gpt-7-nope"
        before = copy.deepcopy(settings)
        with self.assertRaises(RuntimeError) as raised:
            mc.choose(
                settings, FakeProvider(self.AVAILABLE), Path("."), interactive=False, named_strong="openai/gpt-7-nope"
            )
        self.assertIn("--builder-strong-model openai/gpt-7-nope is not offered", str(raised.exception))
        self.assertTrue(str(raised.exception).endswith("Nothing was launched"))
        self.assertEqual(before, settings)

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


class PricesCliTest(unittest.TestCase):
    """`autocode models --prices` (#721): the dated table and each default route's rate, offline."""

    def prices(self, *argv, defaults=None):
        provider = SimpleNamespace(
            NAME="OpenCode",
            DEFAULT_MODELS=defaults or {"sol": "openai/gpt-6-sol", "terra": "local/unpriced-model"},
            available_models=lambda workspace: self.fail("--prices must not list the provider's models"),
        )
        out = io.StringIO()
        with (
            patch("autocode_providers.default_name", return_value="opencode"),
            patch("autocode_providers.resolve", return_value=provider),
            redirect_stdout(out),
        ):
            code = mc.cli(["--prices", *argv])
        return code, out.getvalue()

    def test_the_table_is_dated_and_a_model_without_a_row_has_no_rate(self):
        code, text = self.prices()
        self.assertEqual(0, code)
        self.assertIn("Prices as of 20", text)
        self.assertRegex(text, r"openai/gpt-6-sol +input 2  cached input 0\.2  output 10 ")
        self.assertIn("local/unpriced-model  no rate: its cost is unknown", text)
        self.assertNotRegex(text, r"unpriced-model.*input 0")

    def test_json_names_the_row_each_default_route_is_priced_by(self):
        code, text = self.prices("--json", defaults={"astra": "gpt-6-astra", "terra": "local/unpriced-model"})
        report = json.loads(text)
        self.assertEqual(0, code)
        self.assertEqual("openai/gpt-6-astra", report["defaults"]["astra"]["priced_as"])
        self.assertEqual(50.0, report["defaults"]["astra"]["rate"]["output"])
        self.assertEqual(
            {"model": "local/unpriced-model", "priced_as": None, "rate": None}, report["defaults"]["terra"]
        )
        self.assertIn("openai/gpt-6-sol", report["models"])


class SelectionViewTest(unittest.TestCase):
    """`autocode models --selection`: the choice and its exclusions, from local listings only."""

    FOUND = {
        "tool": "kilocode",
        "accounts": [
            {"name": "Kilo Gateway", "mode": "oauth", "billing": "pay per token", "prefixes": ["kilo/"]},
        ],
        "candidates": [
            {
                "model": model,
                "tool": "kilocode",
                "account": "Kilo Gateway",
                "billing": "pay per token",
                "levels": None,
                "probe": None,
            }
            for model in (
                "kilo/z-ai/glm-5.3",
                "kilo/deepseek/deepseek-v4.1-flash",
                "kilo/openai/gpt-6-sol",
                "kilo/openai/gpt-6-astra",
            )
        ],
    }

    def run_selection(self, found=FOUND):
        import autocode_route_discovery as discovery

        out = io.StringIO()
        with patch.object(discovery, "discover", return_value=found):
            with redirect_stdout(out):
                code = mc.cli(["--selection", "--provider", "kilocode"])
        return code, out.getvalue()

    def test_the_choice_and_every_exclusion_is_printed(self):
        code, text = self.run_selection()
        self.assertEqual(0, code)
        self.assertIn("Automatic selection on kilocode", text)
        self.assertIn("Kilo Gateway (oauth, pay per token)", text)
        self.assertIn("Builder: kilo/openai/gpt-6-sol", text)  # Luna is not connected here: Sol is next
        # An unconnected qualified model says so in account terms.
        self.assertIn("xiaomi-token-plan-sgp/mimo-v2.6-pro", text)
        self.assertIn("not connected", text)
        # The probe debt is named.
        self.assertIn("Still to prove", text)
        # Each listed entry says what admitted it (#2059).
        self.assertIn("[provisional: no AutoCode run yet]", text)

    def test_selection_discloses_role_metric_scope_and_missing_coverage_without_changing_choice(self):
        import autocode_route_selector as selector

        table = copy.deepcopy(selector.table())
        for item in table["roles"]["terra"]:
            if item["model"] == "openai/gpt-6-sol":
                item["runs"] = [
                    {"date": "2026-10-27", "run": "a", "minutes": 2, "cost_usd": 0},
                    {"date": "2026-10-27", "run": "b", "minutes": None, "cost_usd": None},
                ]
        with patch.object(selector, "table", return_value=table):
            code, text = self.run_selection()
        self.assertEqual(0, code)
        self.assertIn("Builder: kilo/openai/gpt-6-sol", text)
        self.assertIn("accepted-only", text)
        self.assertIn("1/2 runs", text)
        self.assertIn("$0.00", text)
        self.assertIn("not whole-task totals or invoices", text)

    def test_a_stopped_selection_prints_the_stop_and_fails(self):
        one_family = {
            "tool": "kilocode",
            "accounts": [
                {
                    "name": "Z.AI Coding Plan",
                    "mode": "api",
                    "billing": "subscription",
                    "prefixes": ["zai-coding-plan/"],
                },
            ],
            "candidates": [
                {
                    "model": "zai-coding-plan/glm-5.3",
                    "tool": "kilocode",
                    "account": "Z.AI Coding Plan",
                    "billing": "subscription",
                    "levels": None,
                    "probe": None,
                }
            ],
        }
        code, text = self.run_selection(one_family)
        self.assertEqual(1, code)
        self.assertIn("Connect a second model family", text)

    def test_json_mode_carries_the_result_and_the_accounts(self):
        import json as jsonlib

        import autocode_route_discovery as discovery

        out = io.StringIO()
        with patch.object(discovery, "discover", return_value=self.FOUND):
            with redirect_stdout(out):
                code = mc.cli(["--selection", "--provider", "kilocode", "--json"])
        self.assertEqual(0, code)
        payload = jsonlib.loads(out.getvalue())
        self.assertEqual("kilocode", payload["tool"])
        self.assertEqual("kilo/openai/gpt-6-sol", payload["routes"]["terra"]["model"])


SEED_ONLY = "zhipuai-coding-plan/glm-5.3"
SELECTED = ["zai-coding-plan/glm-5.3", "openai/gpt-6-luna", "openai/gpt-6-sol", "openai/gpt-6-astra"]
KILO_PROFILE = {
    "requirements": SEED_ONLY,
    "glm": SEED_ONLY,
    "terra": "openai/gpt-6-luna",
    "plan_reviewer": "openai/gpt-6-sol",
    "sol": SEED_ONLY,
    "completion": SEED_ONLY,
    "astra": "openai/gpt-6-astra",
}


def kilo_provider(lists_routes=True, listed=SELECTED):
    """A Kilo login whose Z.AI plan is served under `zai-coding-plan/`, not the profile's prefix."""
    provider = SimpleNamespace(
        NAME="Kilo", DEFAULT_MODELS=dict(KILO_PROFILE), available_models=lambda workspace=None: listed
    )
    if lists_routes:
        provider.auth_table = lambda: {"command": ["kilo"], "routes": []}
    return provider


def kilo_found(models):
    return {
        "tool": "kilocode",
        "accounts": [],
        "candidates": [
            {"model": model, "tool": "kilocode", "account": "Z.AI Coding Plan", "billing": "subscription"}
            for model in models
        ],
    }


class RouteAdviceTest(unittest.TestCase):
    """#1995: the check weighs the routes a new run takes, not a profile route it would never use."""

    def advice(self, provider, listed=SELECTED, discover=None):
        import autocode_route_discovery as discovery

        stub = patch.object(
            discovery, "discover", **({"return_value": kilo_found(listed)} if discover is None else discover)
        )
        with stub:
            return mc.route_advice(provider, listed, None)

    def test_selection_covers_a_seed_route_the_provider_does_not_offer(self):
        result = self.advice(kilo_provider())
        self.assertEqual("selection", result["source"])
        self.assertEqual({}, result["missing"])
        self.assertEqual((SEED_ONLY, "zai-coding-plan/glm-5.3"), result["replaced"]["requirements"])

    def test_a_fully_offered_profile_is_weighed_without_listing_again(self):
        offered = list(KILO_PROFILE.values())
        result = self.advice(kilo_provider(), listed=offered, discover={"side_effect": AssertionError("listed twice")})
        self.assertEqual("profile", result["source"])
        self.assertEqual({}, result["missing"])

    def test_a_provider_that_declares_no_listings_is_never_listed(self):
        result = self.advice(
            kilo_provider(lists_routes=False), discover={"side_effect": AssertionError("listed anyway")}
        )
        self.assertEqual("profile", result["source"])
        self.assertIn("requirements", result["missing"])

    def test_listings_that_cannot_be_read_keep_the_gap_and_say_why(self):
        result = self.advice(
            kilo_provider(), discover={"side_effect": RuntimeError("Cannot read the tool's model listing")}
        )
        self.assertEqual("profile", result["source"])
        self.assertIn("requirements", result["missing"])
        self.assertIn("Cannot read the tool's model listing", result["reason"])


class ModelsCliTest(unittest.TestCase):
    """`autocode models` says what a new run can use, in the words a person acts on (#1995)."""

    def models(self, *argv, provider=None, discover=None):
        provider = provider or kilo_provider()
        out = io.StringIO()
        import autocode_route_discovery as discovery

        stub = patch.object(
            discovery, "discover", **({"return_value": kilo_found(SELECTED)} if discover is None else discover)
        )
        with (
            stub,
            patch("autocode_providers.default_name", return_value="kilocode"),
            patch("autocode_providers.resolve", return_value=provider),
            redirect_stdout(out),
        ):
            code = mc.cli(list(argv))
        return code, out.getvalue()

    def test_a_replaced_seed_route_is_not_reported_as_unusable(self):
        code, text = self.models()
        self.assertEqual(0, code)
        self.assertNotIn("Cannot use with Kilo", text)
        self.assertIn("Every route a new run takes can be used:", text)
        self.assertIn(f"Requirements {SEED_ONLY} -> zai-coding-plan/glm-5.3", text)

    def test_a_gap_selection_cannot_cover_still_fails_with_a_suggestion(self):
        code, text = self.models(discover={"side_effect": RuntimeError("no listings here")})
        self.assertEqual(1, code)
        self.assertIn("Cannot use with Kilo", text)

    def test_the_profile_case_keeps_its_own_wording(self):
        offered = list(KILO_PROFILE.values())
        code, text = self.models(provider=kilo_provider(lists_routes=False, listed=offered))
        self.assertEqual(0, code)
        self.assertIn("Every default route can be used:", text)


class ProjectEngineTests(unittest.TestCase):
    """`autocode models` follows the project's engine when no --provider is given (#2369)."""

    def project(self, engine: str) -> Path:
        import tempfile

        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        (root / ".autocode").mkdir()
        (root / ".autocode" / "config.toml").write_text(f'engine = "{engine}"\n')
        return root

    def test_a_codex_project_is_told_its_routes_are_fixed_instead_of_opencode_not_on_path(self):
        root = self.project("codex")
        out = io.StringIO()
        with patch("subprocess.run", side_effect=AssertionError("no tool was to be launched")):
            with redirect_stdout(out):
                code = mc.cli(["--workspace", str(root)])
        self.assertEqual(0, code)
        self.assertIn(f"this project's engine is codex (set in {root / '.autocode' / 'config.toml'})", out.getvalue())
        self.assertIn("docs/models.md#default-models", out.getvalue())
        self.assertIn("--provider opencode", out.getvalue())

    def test_a_claude_project_lists_the_claude_provider(self):
        import autocode_providers

        root = self.project("claude")
        resolved = []

        def resolve(name):
            resolved.append(name)
            raise RuntimeError("stop here")

        with patch.object(autocode_providers, "resolve", resolve):
            with redirect_stdout(io.StringIO()):
                mc.cli(["--workspace", str(root)])
        self.assertEqual(["claude"], resolved)

    def test_an_explicit_provider_wins_over_the_project_engine(self):
        import autocode_providers

        root = self.project("codex")
        resolved = []

        def resolve(name):
            resolved.append(name)
            raise RuntimeError("stop here")

        with patch.object(autocode_providers, "resolve", resolve):
            with redirect_stdout(io.StringIO()):
                mc.cli(["--workspace", str(root), "--provider", "opencode"])
        self.assertEqual(["opencode"], resolved)
