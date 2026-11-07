"""Exact retries survive config relocation, without weakening their authority."""

import copy
import unittest

from autocode_job_failure import retry_configuration_matches


class RetryConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(
            engine="kilocode",
            identity_version=1,
            executable="/bin/kilo",
            version="7.8.3",
            config_path="/old/provider.toml",
            config_sha256="a" * 64,
        )
        self.saved = dict(
            transport_identity=copy.deepcopy(self.identity),
            transport_identities={"opencode": copy.deepcopy(self.identity)},
            roles={"sol": {"model": "glm", "reasoning_effort": "low"}},
            limits={"max_seconds": 600, "max_cost_usd": 0.75},
        )

    def relocated(self):
        current = copy.deepcopy(self.saved)
        current["transport_identity"]["config_path"] = "/new/provider.toml"
        current["transport_identities"]["opencode"]["config_path"] = "/new/provider.toml"
        return current

    def test_only_path_changes_match_without_mutation(self):
        current = self.relocated()
        before = copy.deepcopy((current, self.saved))
        self.assertTrue(retry_configuration_matches(current, self.saved))
        self.assertEqual(before, (current, self.saved))

    def test_every_real_identity_change_remains_stale(self):
        for field, changed in (
            ("engine", "other"),
            ("identity_version", 2),
            ("executable", "/other/kilo"),
            ("version", "8.0"),
            ("config_sha256", "b" * 64),
        ):
            for location in ("transport_identity", "transport_identities"):
                with self.subTest(field=field, location=location):
                    current = self.relocated()
                    target = current[location]
                    if location == "transport_identities":
                        target = target["opencode"]
                    target[field] = changed
                    self.assertFalse(retry_configuration_matches(current, self.saved))

    def test_legacy_missing_or_malformed_identity_keeps_path_binding(self):
        for field, value in (
            ("config_sha256", "invalid"),
            ("identity_version", True),
            ("engine", ""),
            ("executable", None),
            ("version", 7),
        ):
            with self.subTest(field=field):
                saved = copy.deepcopy(self.saved)
                saved["transport_identity"][field] = value
                current = copy.deepcopy(saved)
                current["transport_identity"]["config_path"] = "/new/provider.toml"
                self.assertFalse(retry_configuration_matches(current, saved))
        saved = copy.deepcopy(self.saved)
        del saved["transport_identity"]["config_sha256"]
        current = copy.deepcopy(saved)
        current["transport_identity"]["config_path"] = "/new/provider.toml"
        self.assertFalse(retry_configuration_matches(current, saved))

    def test_relocation_never_ignores_models_efforts_caps_or_other_settings(self):
        for field, value in (
            ("roles", {"sol": {"model": "other"}}),
            ("limits", {"max_seconds": 900}),
            ("test_command", "other"),
        ):
            with self.subTest(field=field):
                current = self.relocated()
                current[field] = value
                self.assertFalse(retry_configuration_matches(current, self.saved))
