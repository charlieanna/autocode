"""Regression for false transport drift across sibling Git workspaces.

Issue #2347: transport_drift compared absolute paths for nonexistent project configs,
causing PAUSED_TRANSPORT_CHANGED when coordinating runs across sibling checkouts.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from providers import opencode


class SiblingWorkspaceTransportDrift(unittest.TestCase):
    def setUp(self):
        self.temp1 = tempfile.TemporaryDirectory()
        self.temp2 = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp1.cleanup)
        self.addCleanup(self.temp2.cleanup)
        self.ws1 = Path(self.temp1.name).resolve()
        self.ws2 = Path(self.temp2.name).resolve()
        (self.ws1 / ".git").mkdir()
        (self.ws2 / ".git").mkdir()
        self.env = {
            "HOME": str(self.ws1 / "home"),
            "PATH": "/usr/bin",
            "OPENCODE_TEST_MANAGED_CONFIG_DIR": str(self.ws1 / "managed"),
        }

    def mock_identity(self, workspace):
        with (
            mock.patch.object(opencode.env_prep, "resolve_executable", return_value="/native/opencode"),
            mock.patch.object(
                opencode.env_prep, "preflight_run", return_value=SimpleNamespace(returncode=0, stdout="1.18.33\n")
            ),
        ):
            return opencode.local_settings(workspace, env=self.env)

    def test_sibling_checkouts_without_configs_do_not_drift(self):
        id1 = self.mock_identity(self.ws1)
        id2 = self.mock_identity(self.ws2)
        self.assertEqual(id1["executable"], id2["executable"])
        self.assertEqual(id1["version"], id2["version"])
        self.assertFalse(opencode.transport_drift(id1, id2))

    def test_exact_normalization_preserves_real_config_differences(self):
        id1 = self.mock_identity(self.ws1)
        id1_copy = dict(id1)
        id1_copy["config_hashes"] = dict(id1["config_hashes"])
        id1_copy["config_hashes"]["/shared/global.json"] = None
        id1_copy["config_hashes"]["/shared/local.json"] = None
        id2 = self.mock_identity(self.ws2)
        id2_copy = dict(id2)
        id2_copy["config_hashes"] = dict(id2["config_hashes"])
        id2_copy["config_hashes"]["/shared/global.json"] = None
        id2_copy["config_hashes"]["/shared/local.json"] = "abc123"
        self.assertTrue(opencode.transport_drift(id2_copy, id1_copy))
        id2_copy["config_hashes"]["/shared/local.json"] = None
        self.assertFalse(opencode.transport_drift(id2_copy, id1_copy))

    def test_changed_executable_still_drifts(self):
        id1 = self.mock_identity(self.ws1)
        id2 = dict(id1, executable="/different/opencode")
        self.assertTrue(opencode.transport_drift(id2, id1))

    def test_new_real_config_drifts(self):
        id1 = self.mock_identity(self.ws1)
        config = self.ws2 / "opencode.json"
        config.write_text('{"model":"different"}')
        id2 = self.mock_identity(self.ws2)
        self.assertIsNotNone(id2["config_hashes"].get(str(config)))
        self.assertTrue(opencode.transport_drift(id2, id1))

    def test_v1_deleted_real_config_drifts(self):
        config = self.ws1 / "opencode.json"
        config.write_text('{"model":"original"}')
        id1 = self.mock_identity(self.ws1)
        del id1["identity_version"]
        config.unlink()
        id2 = self.mock_identity(self.ws1)
        del id2["identity_version"]
        self.assertTrue(opencode.transport_drift(id2, id1))

    def test_v2_deleted_real_config_drifts(self):
        config = self.ws1 / "opencode.json"
        config.write_text('{"model":"original"}')
        id1 = self.mock_identity(self.ws1)
        config.unlink()
        id2 = self.mock_identity(self.ws1)
        self.assertTrue(opencode.transport_drift(id2, id1))

    def test_additional_metadata_field_drift_detection(self):
        id1 = self.mock_identity(self.ws1)
        for key, value in (
            ("custom_region", "us-west-2"),
            ("deployment_id", "deploy-abc123"),
            ("feature_flag_set", {"alpha": True}),
            ("nested_metadata", {"level1": {"level2": "value"}}),
        ):
            with self.subTest(key=key):
                id2 = dict(id1, **{key: value})
                self.assertTrue(opencode.transport_drift(id2, id1))
                self.assertFalse(opencode.transport_drift(id1, id1))

    def test_missing_vs_present_metadata_field_drift(self):
        id1 = self.mock_identity(self.ws1)
        id2 = dict(id1)
        id2["optional_feature"] = "enabled"
        self.assertIn("optional_feature", id2)
        self.assertNotIn("optional_feature", id1)
        self.assertTrue(opencode.transport_drift(id2, id1))
        self.assertTrue(opencode.transport_drift(id1, id2))

    def test_input_dicts_unchanged_after_drift_check(self):
        id1 = self.mock_identity(self.ws1)
        original_id1 = dict(id1)
        id2 = self.mock_identity(self.ws2)
        original_id2 = dict(id2)
        _ = opencode.transport_drift(id1, id2)
        self.assertEqual(id1, original_id1)
        self.assertEqual(id2, original_id2)
        id1_with_custom = dict(id1, custom_field="value")
        original_custom = dict(id1_with_custom)
        _ = opencode.transport_drift(id1_with_custom, id2)
        self.assertEqual(id1_with_custom, original_custom)

    def test_nested_nonconfig_none_vs_missing_not_ignored(self):
        id1 = self.mock_identity(self.ws1)
        id2 = dict(id1)
        id2["custom_nested"] = {"config_hashes": None}
        self.assertTrue(opencode.transport_drift(id2, id1))

    def test_legacy_v1_raw_inert_checkpoint_normalized(self):
        config = self.ws1 / "opencode.json"
        config.write_text("{}")
        id1 = self.mock_identity(self.ws1)
        del id1["identity_version"]
        legacy_checkpoint = dict(id1)
        legacy_checkpoint["config_hashes"] = {str(config): hashlib.sha256(b"{}").hexdigest()}
        id2 = self.mock_identity(self.ws1)
        id2 = self.mock_identity(self.ws1)
        del id2["identity_version"]
        id2_hashes_before = dict(id2["config_hashes"])
        checkpoint_hashes_before = dict(legacy_checkpoint["config_hashes"])
        self.assertFalse(opencode.transport_drift(id2, legacy_checkpoint))
        self.assertEqual(id2["config_hashes"], id2_hashes_before)
        self.assertEqual(legacy_checkpoint["config_hashes"], checkpoint_hashes_before)


if __name__ == "__main__":
    unittest.main()
