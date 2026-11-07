"""Route discovery (#1426 steps 4-5): the tool's own listings become the selector's candidates."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_route_discovery as discovery
import autocode_route_selector as selector
from providers import command

KILO_AUTH_LISTING = """
┌  Credentials ~/.local/share/kilo/auth.json
│
●  Kilo Gateway oauth
│
●  Z.AI Coding Plan api
│
●  OpenAI oauth
│
└  3 credentials
"""

KILO_MODELS = "\n".join(
    [
        "kilo/deepseek/deepseek-v4.1-flash",
        "kilo/z-ai/glm-5.3",
        "kilo/openai/gpt-6-sol",
        "kilo/openai/gpt-6-astra",
        "zai-coding-plan/glm-5.3",
        "openai/gpt-6-sol",
        "openai/gpt-6-astra",
        "xiaomi-token-plan-sgp/mimo-v2.6-pro",  # no Xiaomi login connected: not a candidate
        "mystery-vendor/model-x",  # no declared route: billing would be unverified
    ]
)

OPENCODE_AUTH_LISTING = """
┌  Credentials ~/.local/share/opencode/auth.json
│
●  Z.AI Coding Plan api
│
●  OpenAI oauth
│
└  2 credentials
"""

OPENCODE_MODELS = "\n".join(
    [
        "zai-coding-plan/glm-5.3",
        "openai/gpt-6-sol",
        "openai/gpt-6-astra",
        "xiaomi-token-plan-sgp/mimo-v2.6-pro",  # login not connected
        "opencode/glm-5.3-flash-free",  # free tier: no login needed
        "vendor-unknown/model-y",
    ]
)


def response(stdout="", returncode=0):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr="")


def kilo_listing(auth=KILO_AUTH_LISTING, models=KILO_MODELS, **kwargs):
    """A fake preflight_run that answers the auth listing and the models listing in order."""
    answers = [response(auth), response(models)]
    return mock.Mock(side_effect=lambda command, *a, **kw: answers.pop(0) if answers else response(), **kwargs)


class KiloDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.provider = command.load("kilocode")

    def test_connected_logins_become_accounts_and_candidates(self):
        with mock.patch.object(command.env_prep, "preflight_run", kilo_listing()):
            found = discovery.discover(self.provider)
        self.assertEqual("kilocode", found["tool"])
        accounts = {account["name"]: account for account in found["accounts"]}
        self.assertEqual(
            {"Kilo Gateway": "pay per token", "Z.AI Coding Plan": "subscription", "OpenAI": "subscription"},
            {name: account["billing"] for name, account in accounts.items()},
        )
        models = {candidate["model"]: candidate for candidate in found["candidates"]}
        self.assertIn("kilo/deepseek/deepseek-v4.1-flash", models)
        self.assertEqual("Kilo Gateway", models["kilo/deepseek/deepseek-v4.1-flash"]["account"])
        self.assertEqual("Z.AI Coding Plan", models["zai-coding-plan/glm-5.3"]["account"])
        self.assertEqual("OpenAI", models["openai/gpt-6-sol"]["account"])
        # A model whose login is not connected, and one no route covers, are not candidates.
        self.assertNotIn("xiaomi-token-plan-sgp/mimo-v2.6-pro", models)
        self.assertNotIn("mystery-vendor/model-x", models)
        # Nothing here launched a model or read a credential: one listing of each kind.
        for candidate in found["candidates"]:
            self.assertIsNone(candidate["levels"])

    def test_a_login_in_the_wrong_mode_does_not_serve_its_route(self):
        listing = kilo_listing(auth=KILO_AUTH_LISTING.replace("●  OpenAI oauth", "●  OpenAI api"))
        with mock.patch.object(command.env_prep, "preflight_run", listing):
            found = discovery.discover(self.provider)
        models = {candidate["model"] for candidate in found["candidates"]}
        # The route declares oauth; an api-mode login is a different connection, and
        # check_subscription_routes would refuse it, so discovery does not offer it.
        self.assertNotIn("openai/gpt-6-sol", models)

    def test_an_unreadable_listing_is_an_error_not_a_guess(self):
        with mock.patch.object(command.env_prep, "preflight_run", return_value=response(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, "cannot verify kilocode login"):
                discovery.discover(self.provider)

    def test_discovered_candidates_select_the_gateways_owner_table(self):
        keep = lambda line: "Z.AI" not in line and "OpenAI" not in line
        gateway_only = "\n".join(line for line in KILO_AUTH_LISTING.splitlines() if keep(line))
        with mock.patch.object(command.env_prep, "preflight_run", kilo_listing(auth=gateway_only)):
            found = discovery.discover(self.provider)
        routes = selector.select(
            ("requirements", "glm", "terra", "plan_reviewer", "sol", "completion", "astra"), found["candidates"]
        )["routes"]
        self.assertIsNotNone(routes)
        self.assertEqual("kilo/openai/gpt-6-sol", routes["terra"]["model"])  # the preferred Builder the gateway serves
        self.assertEqual("kilo/z-ai/glm-5.3", routes["requirements"]["model"])
        self.assertEqual("kilo/openai/gpt-6-sol", routes["plan_reviewer"]["model"])
        self.assertEqual("kilo/openai/gpt-6-astra", routes["astra"]["model"])

    def test_a_subscription_plan_beats_the_cheaper_gateway_for_the_builder(self):
        with mock.patch.object(command.env_prep, "preflight_run", kilo_listing()):
            found = discovery.discover(self.provider)
        # Among Builders of one preference (GPT models aside): DeepSeek Flash is cheaper per token, but GLM on
        # the Z.AI Coding Plan bills a subscription the owner already pays for, and subscription always wins.
        candidates = [row for row in found["candidates"] if "gpt-" not in row["model"]]
        routes = selector.select(("terra",), candidates)["routes"]
        self.assertEqual("zai-coding-plan/glm-5.3", routes["terra"]["model"])


class OpenCodeDiscoveryTests(unittest.TestCase):
    def test_logins_map_to_prefixes_and_the_free_tier_needs_none(self):
        logins = [("Z.AI Coding Plan", "api"), ("OpenAI", "oauth")]
        listed = set(OPENCODE_MODELS.splitlines())
        with mock.patch.object(discovery.opencode, "auth_logins", return_value=logins):
            with mock.patch.object(discovery.opencode, "available_models", return_value=listed):
                found = discovery.discover(discovery.opencode)
        self.assertEqual("opencode", found["tool"])
        models = {candidate["model"]: candidate for candidate in found["candidates"]}
        self.assertEqual("Z.AI Coding Plan", models["zai-coding-plan/glm-5.3"]["account"])
        self.assertEqual("subscription", models["zai-coding-plan/glm-5.3"]["billing"])
        self.assertEqual("subscription", models["openai/gpt-6-sol"]["billing"])  # oauth, not an API key
        self.assertEqual("OpenCode Zen", models["opencode/glm-5.3-flash-free"]["account"])
        self.assertNotIn("xiaomi-token-plan-sgp/mimo-v2.6-pro", models)
        self.assertNotIn("vendor-unknown/model-y", models)

    def test_an_api_openai_login_bills_per_token(self):
        with mock.patch.object(discovery.opencode, "auth_logins", return_value=[("OpenAI", "api")]):
            with mock.patch.object(discovery.opencode, "available_models", return_value={"openai/gpt-6-sol"}):
                found = discovery.discover(discovery.opencode)
        self.assertEqual("pay per token", found["candidates"][0]["billing"])

    def test_an_unreadable_login_list_stops_discovery(self):
        with mock.patch.object(discovery.opencode, "auth_logins", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Cannot read OpenCode's logins"):
                discovery.discover(discovery.opencode)


class VariantOmissionTests(unittest.TestCase):
    def test_a_configured_kilo_connection_lists_its_variants_like_the_bundled_one(self):
        """#2006: --single-model fits efforts to the model's declared variants, but discovery read variants only
        for the name "kilocode", so a copy of kilocode.toml under another name declared nothing."""
        with tempfile.TemporaryDirectory() as folder:
            user = Path(folder) / "autocode" / "providers"
            user.mkdir(parents=True)
            text = command.load("kilocode")._path.read_text().replace('name = "kilocode"', 'name = "kilo_mine"', 1)
            (user / "kilo_mine.toml").write_text(text)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": folder}):
                mine = command.load("kilo_mine")
            verbose = 'zai-coding-plan/glm-5.3\n{"variants": {"low": {}, "high": {}, "max": {}}}\n'

            def listing(command, *a, **kw):
                if "auth" in command:
                    return response(KILO_AUTH_LISTING)
                return response(verbose if "--verbose" in command else KILO_MODELS)

            with mock.patch.object(discovery.env_prep, "preflight_run", side_effect=listing):
                found = discovery.discover(mine, Path("/tmp/w"))
                bundled_found = discovery.discover(command.load("kilocode"), Path("/tmp/w"))
        levels = {row["model"]: row.get("levels") for row in found["candidates"]}
        self.assertEqual({"low", "high", "max"}, set(levels.get("zai-coding-plan/glm-5.3") or ()), levels)
        self.assertEqual(
            levels, {row["model"]: row.get("levels") for row in bundled_found["candidates"]}, "as the bundled name"
        )

    def test_candidates_carry_the_listed_model_facts_and_never_a_price(self):
        """#2049: selection checks a model's family and tool calling from the tool's own listing."""
        verbose = (
            'zai-coding-plan/glm-5.3\n{"family": "glm", "capabilities": {"toolcall": true, "reasoning": true},'
            ' "limit": {"context": 200000}, "release_date": "2026-09-01", "cost": {"input": 0.6, "output": 2.2},'
            ' "variants": {"low": {}, "high": {}}}\n'
        )

        def listing(command, *a, **kw):
            if "auth" in command:
                return response(KILO_AUTH_LISTING)
            return response(verbose if "--verbose" in command else KILO_MODELS)

        with mock.patch.object(discovery.env_prep, "preflight_run", side_effect=listing):
            found = discovery.discover(command.load("kilocode"), Path("/tmp/w"))
        facts = {row["model"]: row["metadata"] for row in found["candidates"]}
        self.assertEqual(
            {
                "family": "glm",
                "toolcall": True,
                "reasoning": True,
                "context": 200000,
                "release_date": "2026-09-01",
            },
            facts.get("zai-coding-plan/glm-5.3"),
            facts,
        )
        unlisted = [model for model, fact in facts.items() if model != "zai-coding-plan/glm-5.3"]
        self.assertTrue(unlisted, "the fixture serves models the verbose listing does not describe")
        self.assertTrue(all(facts[model] is None for model in unlisted), facts)

    def test_a_route_with_no_level_omits_the_variant_arguments(self):
        provider = command.load("kilocode")
        argv, _env, _meta = provider.launch("terra", "/tmp/w", "/tmp/r", None, "kilo/z-ai/glm-5.3", None, True)
        self.assertNotIn("--variant", argv)
        self.assertNotIn("", argv)
        argv, _env, _meta = provider.launch("terra", "/tmp/w", "/tmp/r", None, "kilo/z-ai/glm-5.3", "high", True)
        self.assertIn("--variant", argv)
        self.assertEqual("high", argv[argv.index("--variant") + 1])

    def test_bad_route_names_and_billings_are_refused(self):
        with self.assertRaisesRegex(ValueError, "name"):
            (
                command._validate_auth(
                    {"command": ["x"], "routes": [{"models": "a/", "pattern": "(a)", "expect": "api", "name": " "}]}
                ),
            )
        with self.assertRaisesRegex(ValueError, "billing"):
            command._validate_auth(
                {"command": ["x"], "routes": [{"models": "a/", "pattern": "(a)", "expect": "api", "billing": "free"}]}
            )


if __name__ == "__main__":
    unittest.main()


class AutoSelectConfigureTests(unittest.TestCase):
    """--auto-select: the flag-gated wiring that makes selection a launch choice (#1426)."""

    def setUp(self):
        from . import test_planning

        test_planning.PlanningTests.setUp(self)
        for name in ("planning", "milestones", "autopilot", "support", "runner"):
            setattr(
                self,
                name,
                test_planning.__dict__.get(name) or __import__(f"autocode_{name}" if name != "runner" else "autocode")
                if name != "runner"
                else __import__("autocode"),
            )
        self.planning = test_planning.planning
        self.milestones = test_planning.milestones
        self.autopilot = test_planning.autopilot
        import autocode as runner
        import autocode_configure
        import autocode_support as support

        self.runner = runner
        self.configure_module = autocode_configure
        self.support = support

    def args(self, **overrides):
        from . import test_planning

        return test_planning.PlanningTests.configure_args(self, **overrides)

    def configure(self, args, state=None):
        state = state if state is not None else {"workspace": "/tmp/fixture", "iteration": 0}
        with (
            mock.patch.object(self.support, "local_settings", return_value={"auth_mode": "ChatGPT"}),
            mock.patch.object(self.runner.opencode, "local_settings", return_value={"engine": "opencode"}),
        ):
            return self.configure_module.configure(
                args, state, planning=self.planning, milestones=self.milestones, autopilot=self.autopilot
            )

    def gateway_found(self):
        return {
            "tool": "opencode",
            "accounts": [{"name": "Kilo Gateway", "mode": "oauth", "billing": "pay per token", "prefixes": ["kilo/"]}],
            "candidates": [
                {
                    "model": model,
                    "tool": "opencode",
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

    def test_fully_pinned_default_keeps_explicit_profile_policy(self):
        models = {
            "astra": "openai/gpt-6-astra",
            "glm": "zai-coding-plan/glm-5.3",
            "requirements": "zai-coding-plan/glm-5.3",
            "terra": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "sol": "zai-coding-plan/glm-5.3",
            "completion": "zai-coding-plan/glm-5.3",
            "plan_reviewer": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
        }
        with mock.patch.object(discovery, "discover", return_value={"candidates": []}):
            settings = self.configure(self.args(auto_select=None, **{f"{r}_model": m for r, m in models.items()}))
        self.assertFalse(settings.get("route_selection"))
        for role, model in models.items():
            self.assertEqual(model, settings["roles"][role]["model"])

    def test_the_selection_becomes_the_runs_routes_and_origins(self):
        with mock.patch.object(discovery, "discover", return_value=self.gateway_found()):
            settings = self.configure(self.args(auto_select=True))
        self.assertEqual("kilo/openai/gpt-6-sol", settings["roles"]["terra"]["model"])  # Luna is not connected
        self.assertEqual("kilo/z-ai/glm-5.3", settings["roles"]["glm"]["model"])
        self.assertEqual("kilo/openai/gpt-6-astra", settings["roles"]["astra"]["model"])
        self.assertEqual("auto_selected", settings["route_origins"]["terra"])
        self.assertEqual("kilo/openai/gpt-6-sol", settings["route_selection"]["routes"]["terra"]["model"])
        self.assertIn("kilo/openai/gpt-6-sol", settings["route_selection"]["probe"])

    def test_explicit_effort_survives_selection_and_matches_the_probe(self):
        for role in ("terra", "sol", "astra", "completion", "requirements", "glm", "plan_reviewer"):
            with self.subTest(role=role), mock.patch.object(discovery, "discover", return_value=self.gateway_found()):
                settings = self.configure(self.args(auto_select=True, **{f"{role}_reasoning_effort": "low"}))
                self.assertEqual("low", settings["roles"][role]["reasoning_effort"])
                self.assertEqual("low", settings["route_selection"]["routes"][role]["reasoning_effort"])

    def test_global_effort_is_used_unless_a_role_overrides_it(self):
        with mock.patch.object(discovery, "discover", return_value=self.gateway_found()):
            settings = self.configure(self.args(auto_select=True, reasoning_effort="low", sol_reasoning_effort="high"))
        for role, route in settings["route_selection"]["routes"].items():
            expected = "high" if role == "sol" else "low"
            self.assertEqual(expected, route["reasoning_effort"])
            self.assertEqual(expected, settings["roles"][role]["reasoning_effort"])

    def test_an_unsupported_qualified_level_keeps_the_model_default(self):
        found = self.gateway_found()
        for candidate in found["candidates"]:
            candidate["levels"] = ["low"]
        with mock.patch.object(discovery, "discover", return_value=found):
            settings = self.configure(self.args(auto_select=True))
        for role in ("glm", "plan_reviewer", "terra", "sol"):
            self.assertIsNone(settings["roles"][role]["reasoning_effort"])
            self.assertIsNone(settings["route_selection"]["routes"][role]["reasoning_effort"])

    def test_a_role_flag_wins_over_the_selection(self):
        with mock.patch.object(discovery, "discover", return_value=self.gateway_found()):
            settings = self.configure(self.args(auto_select=True, terra_model="openai/gpt-6-sol"))
        self.assertEqual("openai/gpt-6-sol", settings["roles"]["terra"]["model"])
        self.assertEqual("user_explicit", settings["route_origins"]["terra"])
        self.assertEqual("user_explicit", settings["route_selection"]["routes"]["terra"]["origin"])

    def test_a_selection_that_cannot_field_a_checker_refuses_to_start(self):
        one_family = {
            "tool": "opencode",
            "accounts": [
                {"name": "Z.AI Coding Plan", "mode": "api", "billing": "subscription", "prefixes": ["zai-coding-plan/"]}
            ],
            "candidates": [
                {
                    "model": "zai-coding-plan/glm-5.3",
                    "tool": "opencode",
                    "account": "Z.AI Coding Plan",
                    "billing": "subscription",
                    "levels": None,
                    "probe": None,
                }
            ],
        }
        with mock.patch.object(discovery, "discover", return_value=one_family):
            with self.assertRaisesRegex(ValueError, "Connect a second model family"):
                self.configure(self.args(auto_select=True))

    def test_saved_runs_and_single_model_refuse_the_flag(self):
        saved = {"workspace": "/tmp/fixture", "iteration": 3, "settings": {"engine": "opencode", "roles": {}}}
        with self.assertRaisesRegex(ValueError, "saved run keeps the routes"):
            self.configure(self.args(auto_select=True), state=saved)
        with self.assertRaisesRegex(ValueError, "--single-model"):
            self.configure(self.args(auto_select=True, single_model="openai/gpt-6-sol"))

    def test_the_soft_default_selects_and_the_flag_opts_in_and_out(self):
        with mock.patch.object(discovery, "discover", return_value=self.gateway_found()):
            settings = self.configure(self.args())  # no flag yet: opt-in, fixed routes
        self.assertNotIn("route_selection", settings)
        with mock.patch.object(discovery, "discover", return_value=self.gateway_found()):
            settings = self.configure(self.args(auto_select=None))  # the soft default
        self.assertEqual("kilo/openai/gpt-6-sol", settings["roles"]["terra"]["model"])
        self.assertEqual("auto_selected", settings["route_origins"]["terra"])
        with mock.patch.object(discovery, "discover", return_value=self.gateway_found()) as probe:
            settings = self.configure(self.args(auto_select=False))  # the explicit opt-out
        probe.assert_not_called()
        self.assertNotIn("route_selection", settings)
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["terra"]["model"])
        self.assertEqual("runner_default", settings["route_origins"]["terra"])

    def test_a_soft_default_falls_back_when_the_accounts_field_no_checker(self):
        nothing = {"tool": "opencode", "accounts": [], "candidates": []}
        with mock.patch.object(discovery, "discover", return_value=nothing):
            settings = self.configure(self.args(auto_select=None))  # the stop is printed, run proceeds
        self.assertNotIn("route_selection", settings)
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["terra"]["model"])
        with mock.patch.object(discovery, "discover", return_value=nothing):
            with self.assertRaisesRegex(ValueError, "no qualified model"):  # insisted: refuses
                self.configure(self.args(auto_select=True))

    def test_selection_needs_an_opencode_version_the_conformance_matrix_qualifies(self):
        # #2060: on an OpenCode version with no conformant matrix row, nothing is selected.
        def configure(args, version):
            state = {"workspace": "/tmp/fixture", "iteration": 0}
            with (
                mock.patch.object(self.support, "local_settings", return_value={"auth_mode": "ChatGPT"}),
                mock.patch.object(
                    self.runner.opencode, "local_settings", return_value={"engine": "opencode", "version": version}
                ),
                mock.patch.object(discovery, "discover", return_value=self.gateway_found()),
            ):
                settings = self.configure_module.configure(
                    args, state, planning=self.planning, milestones=self.milestones, autopilot=self.autopilot
                )
            return settings

        settings = configure(self.args(auto_select=None), "9.9.9")  # soft default: fixed routes
        self.assertNotIn("route_selection", settings)
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["terra"]["model"])
        with self.assertRaisesRegex(ValueError, "opencode 9.9.9 has not passed the offline conformance suite"):
            configure(self.args(auto_select=True), "9.9.9")  # insisted: refuses
        settings = configure(self.args(auto_select=True), "1.18.33")  # a known-good version selects
        self.assertEqual("kilo/openai/gpt-6-sol", settings["roles"]["terra"]["model"])

    def test_a_soft_default_falls_back_to_fixed_routes_when_listings_cannot_be_read(self):
        broken = mock.Mock(side_effect=RuntimeError("cannot verify kilocode login"))
        with mock.patch.object(discovery, "discover", broken):
            settings = self.configure(self.args(auto_select=None))  # soft default: fallback
        self.assertNotIn("route_selection", settings)
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["terra"]["model"])
        with mock.patch.object(discovery, "discover", broken):
            with self.assertRaisesRegex(ValueError, "listings could not be read"):  # insisted: refuses
                self.configure(self.args(auto_select=True))

    def test_single_model_skips_selection_without_error(self):
        with mock.patch.object(discovery, "discover") as probe:
            settings = self.configure(self.args(single_model="openai/gpt-6-sol"))
        # Discovery may be read for the one model's declared variants (#2006); nothing is selected.
        self.assertLessEqual(probe.call_count, 1)
        self.assertNotIn("route_selection", settings)
        self.assertTrue(settings["single_model_mode"])
