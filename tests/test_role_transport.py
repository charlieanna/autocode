"""Saved whole-run provider identities use the same model rules as role overrides."""

import copy
import unittest

import autocode_role_transport as transport


class ConfiguredTransportTests(unittest.TestCase):
    def settings(self):
        identity = {
            "engine": "gocode",
            "identity_version": 1,
            "executable": "/tools/gocode",
            "config_path": "/config/gocode.toml",
            "config_sha256": "a" * 64,
        }
        return {
            "engine": "opencode",
            "provider": "gocode",
            "transport_identity": identity,
            "transport_identities": {"opencode": copy.deepcopy(identity)},
            "roles": {"sol": {"engine": "opencode", "provider": None}},
        }

    def test_whole_run_provider_is_configured_without_a_live_facade(self):
        settings = self.settings()
        before = copy.deepcopy(settings)
        self.assertTrue(transport.configured(settings, "sol"))
        self.assertEqual(before, settings)

    def test_legacy_single_transport_identity_is_enough(self):
        settings = self.settings()
        del settings["transport_identities"]
        self.assertTrue(transport.configured(settings, "sol"))

    def test_only_the_roles_effective_engine_can_use_the_whole_run_provider(self):
        settings = self.settings()
        settings["roles"]["sol"]["engine"] = "codex"
        self.assertFalse(transport.configured(settings, "sol"))

    def test_a_provider_name_alone_or_malformed_identity_grants_no_model_format(self):
        for identity in (
            None,
            {},
            {"engine": "gocode"},
            {
                "engine": "other",
                "identity_version": 1,
                "executable": "/tools/gocode",
                "config_sha256": "a" * 64,
            },
        ):
            settings = self.settings()
            settings["transport_identity"] = identity
            settings["transport_identities"] = {"opencode": identity}
            with self.subTest(identity=identity):
                self.assertFalse(transport.configured(settings, "sol"))

    def test_the_native_opencode_provider_does_not_become_a_configured_tool(self):
        settings = self.settings()
        settings["provider"] = "opencode"
        self.assertFalse(transport.configured(settings, "sol"))

    def test_a_live_configured_facade_remains_a_valid_fallback_for_older_runs(self):
        self.assertTrue(transport.configured({"roles": {"sol": {}}}, "sol", True))

    def test_an_external_role_without_its_immutable_identity_still_refuses(self):
        settings = self.settings()
        settings["roles"]["sol"]["provider"] = "other"
        with self.assertRaisesRegex(ValueError, "identity is missing"):
            transport.configured(settings, "sol")
