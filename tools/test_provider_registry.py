"""Provider selection stays independent from Autocode's workflow engine."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tools import autocode, autocode_providers
from tools.providers import command


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        # Hermetic provider resolution: a contributor's own
        # ~/.config/autocode/{config.toml,providers/*.toml} must never shadow
        # the bundled provider configs these tests check against.
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        self._env_patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_home.name})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        previous_provider = os.environ.pop("AUTOCODE_PROVIDER", None)
        if previous_provider is not None:
            self.addCleanup(os.environ.__setitem__, "AUTOCODE_PROVIDER", previous_provider)

    def test_opencode_is_the_builtin_default(self):
        provider = autocode_providers.resolve("opencode")
        self.assertEqual("tools.providers.opencode", provider.__name__)

    def test_named_provider_loads_its_config(self):
        provider = autocode_providers.resolve("gocode")
        self.assertIsInstance(provider, command.CommandProvider)
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", provider.DEFAULT_MODELS["plan_reviewer"])
        self.assertFalse(provider.SUPPORTS_SESSIONS)

    def test_unknown_provider_fails_without_silent_opencode_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "no provider config for 'missing'"):
            autocode_providers.resolve("missing")

    def test_resume_rejects_provider_drift(self):
        self.assertEqual("gocode", autocode_providers.select("gocode", {"provider": "gocode"}))
        with self.assertRaisesRegex(ValueError, "cannot change provider"):
            autocode_providers.select("opencode", {"provider": "gocode"})

    def test_default_provider_comes_from_environment_then_user_config(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "autocode" / "config.toml"
            clean = {k: v for k, v in os.environ.items() if k != "AUTOCODE_PROVIDER"}
            with mock.patch.dict(os.environ, {**clean, "XDG_CONFIG_HOME": temp}, clear=True):
                self.assertEqual("opencode", autocode_providers.default_name())
                config.parent.mkdir(parents=True)
                config.write_text('default_provider = "kilocode"\n')
                self.assertEqual("kilocode", autocode_providers.select(None, None))
                os.environ["AUTOCODE_PROVIDER"] = "gocode"
                self.assertEqual("gocode", autocode_providers.select(None, None))
                # A saved run keeps its provider, and an explicit engine default wins over the setting.
                self.assertEqual("opencode", autocode_providers.select(None, {"provider": "opencode"}))
                self.assertEqual("opencode", autocode_providers.select(None, None, default="opencode"))
                os.environ["AUTOCODE_PROVIDER"] = "Not Valid"
                with self.assertRaisesRegex(ValueError, "AUTOCODE_PROVIDER must be a provider name"):
                    autocode_providers.select(None, None)
                del os.environ["AUTOCODE_PROVIDER"]
                config.write_text("default_provider = [\n")
                with self.assertRaisesRegex(ValueError, "not valid TOML"):
                    autocode_providers.default_name()

    def test_codex_engine_runs_ignore_a_configured_default_provider(self):
        args = SimpleNamespace(provider=None, engine="codex", figma_file=None, joint_planning=False, glm_model=None,
                               reasoning_effort=None, headroom=None, context_soft_tokens=None,
                               rotate_after_input_tokens=None, legacy_iteration_ceiling=15, max_iterations=None,
                               max_seconds=None, max_reported_tokens=None, no_progress_limit=None, pin_model_role=[],
                               **{f"{role}_{field}": None for role in ("astra", "terra", "sol", "completion")
                                  for field in ("model", "provider", "reasoning_effort")})
        with mock.patch.dict(os.environ, {"AUTOCODE_PROVIDER": "kilocode"}), \
             mock.patch.object(autocode.support, "local_settings", return_value={}):
            settings = autocode.configure(args, {"workspace": "/fixture"})
        self.assertEqual(("codex", "opencode"), (settings["engine"], settings["provider"]))

    def test_config_provider_models_keep_their_own_names(self):
        provider = command.load("kilocode")
        previous = autocode.opencode
        autocode.opencode = provider
        try:
            settings = {"provider": "kilocode", "transport_identity": {"engine": "kilocode"},
                        "roles": {role: {} for role in ("astra", "terra", "sol")}}
            args = SimpleNamespace(astra_model="kilo/~openai/gpt-astra-latest", terra_model=None,
                                   sol_model="gpt-5.6-sol", glm_model=None)
            autocode.configure_joint(settings, args, fresh=True)
        finally:
            autocode.opencode = previous
        self.assertEqual("kilo/~openai/gpt-astra-latest", settings["roles"]["astra"]["model"])
        self.assertEqual("gpt-5.6-sol", settings["roles"]["sol"]["model"])
        # Custom providers keep their own role names (including plan_reviewer).
        self.assertEqual("openai/gpt-5.6-sol", settings["roles"]["plan_reviewer"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", settings["roles"]["glm"]["model"])

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
            "engine": "opencode", "provider": None, "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "reasoning_effort": "high", "model_pinned": True,
        }, settings["roles"]["plan_reviewer"])
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", settings["roles"]["terra"]["model"])
        self.assertEqual("medium", settings["roles"]["glm"]["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
