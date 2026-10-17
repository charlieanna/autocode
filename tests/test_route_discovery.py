"""Route discovery (#1426 steps 4-5): the tool's own listings become the selector's candidates."""

import sys
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
        self.assertEqual("kilo/deepseek/deepseek-v4.1-flash", routes["terra"]["model"])
        self.assertEqual("kilo/z-ai/glm-5.3", routes["requirements"]["model"])
        self.assertEqual("kilo/openai/gpt-6-sol", routes["plan_reviewer"]["model"])
        self.assertEqual("kilo/openai/gpt-6-astra", routes["astra"]["model"])

    def test_a_subscription_plan_beats_the_cheaper_gateway_for_the_builder(self):
        with mock.patch.object(command.env_prep, "preflight_run", kilo_listing()):
            found = discovery.discover(self.provider)
        routes = selector.select(("terra",), found["candidates"])["routes"]
        # DeepSeek Flash is cheaper per token, but GLM on the Z.AI Coding Plan bills a
        # subscription the owner already pays for, and subscription always wins.
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
            command._validate_auth(
                {"command": ["x"], "routes": [{"models": "a/", "pattern": "(a)", "expect": "api", "name": " "}]}
            ),
        with self.assertRaisesRegex(ValueError, "billing"):
            command._validate_auth(
                {"command": ["x"], "routes": [{"models": "a/", "pattern": "(a)", "expect": "api", "billing": "free"}]}
            )


if __name__ == "__main__":
    unittest.main()
