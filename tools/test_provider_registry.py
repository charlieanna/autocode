"""Provider selection stays independent from Autocode's workflow engine."""
from __future__ import annotations

from types import SimpleNamespace
import unittest

from tools import autocode, autocode_providers
from tools.providers import command


class ProviderRegistryTests(unittest.TestCase):
    def test_opencode_is_the_builtin_default(self):
        provider = autocode_providers.resolve("opencode")
        self.assertEqual("tools.providers.opencode", provider.__name__)

    def test_named_provider_loads_its_config(self):
        provider = autocode_providers.resolve("gocode")
        self.assertIsInstance(provider, command.CommandProvider)
        self.assertEqual("gpt-5.6-sol", provider.DEFAULT_MODELS["plan_reviewer"])
        self.assertFalse(provider.SUPPORTS_SESSIONS)

    def test_unknown_provider_fails_without_silent_opencode_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "no provider config for 'missing'"):
            autocode_providers.resolve("missing")

    def test_resume_rejects_provider_drift(self):
        self.assertEqual("gocode", autocode_providers.select("gocode", {"provider": "gocode"}))
        with self.assertRaisesRegex(ValueError, "cannot change provider"):
            autocode_providers.select("opencode", {"provider": "gocode"})

    def test_config_provider_joint_review_uses_the_config_role_defaults(self):
        provider = command.load("gocode")
        previous = autocode.opencode
        autocode.opencode = provider
        try:
            settings = {
                "provider": "gocode", "transport_identity": {"engine": "gocode"},
                "roles": {role: {} for role in ("astra", "terra", "sol")},
            }
            args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
            autocode.configure_joint(settings, args, fresh=True)
        finally:
            autocode.opencode = previous
        self.assertEqual({
            "engine": "opencode", "provider": None, "model": "gpt-5.6-sol",
            "reasoning_effort": "high", "model_pinned": True,
        }, settings["roles"]["plan_reviewer"])
        self.assertEqual("gpt-5.6-terra", settings["roles"]["terra"]["model"])
        self.assertEqual("medium", settings["roles"]["glm"]["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
