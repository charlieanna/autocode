"""Provider selection stays independent from Autocode's workflow engine."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
import unittest

from tools import autocode, autocode_providers


class ProviderRegistryTests(unittest.TestCase):
    def test_opencode_is_the_builtin_default(self):
        provider = autocode_providers.resolve("opencode")
        self.assertEqual("tools.providers.opencode", provider.__name__)

    def test_external_provider_uses_the_documented_factory(self):
        module = ModuleType("autocode_provider_fixture")
        expected = ModuleType("fixture_provider")
        module.create_provider = lambda: expected
        previous = sys.modules.get(module.__name__)
        sys.modules[module.__name__] = module
        try:
            self.assertIs(expected, autocode_providers.resolve("fixture"))
        finally:
            if previous is None:
                del sys.modules[module.__name__]
            else:
                sys.modules[module.__name__] = previous

    def test_unknown_provider_fails_without_silent_opencode_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "provider plugin 'missing' is unavailable"):
            autocode_providers.resolve("missing")

    def test_resume_rejects_provider_drift(self):
        self.assertEqual("gocode", autocode_providers.select("gocode", {"provider": "gocode"}))
        with self.assertRaisesRegex(ValueError, "cannot change provider"):
            autocode_providers.select("opencode", {"provider": "gocode"})

    def test_gocode_joint_review_uses_the_supported_sol_route(self):
        settings = {
            "provider": "gocode", "transport_identity": {"engine": "gocode"},
            "roles": {role: {} for role in ("astra", "terra", "sol")},
        }
        args = SimpleNamespace(astra_model=None, terra_model=None, sol_model=None, glm_model=None)
        autocode.configure_joint(settings, args, fresh=True)
        self.assertEqual({
            "engine": "opencode", "provider": None, "model": "openai/gpt-5.6-sol",
            "reasoning_effort": "high", "model_pinned": True,
        }, settings["roles"]["plan_reviewer"])


if __name__ == "__main__":
    unittest.main()
