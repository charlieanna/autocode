"""A held recovery survives resuming from another checkout of the same config."""

import copy
import unittest

import autocode_resolver_recovery as recovery


def identity(path, sha="a" * 64, version="7.8.12"):
    return {
        "engine": "kilocode",
        "identity_version": 1,
        "executable": "/bin/kilo",
        "version": version,
        "config_path": path,
        "config_sha256": sha,
    }


class RecoveryRelocation(unittest.TestCase):
    def setUp(self):
        self.roles = {"terra": {"engine": "opencode", "model": "plan/glm", "reasoning_effort": "low"}}
        self.state = {
            "goal_contract": {"hash": "h", "revision": 4},
            "current_task": {"id": "task-1"},
            "settings": self.settings(identity("/one/tools/providers/configs/kilocode.toml")),
        }
        self.saved = recovery._binding(self.state, "rev")

    def settings(self, transport):
        return {
            "transport_identity": copy.deepcopy(transport),
            "transport_identities": {"opencode": copy.deepcopy(transport)},
            "roles": copy.deepcopy(self.roles),
        }

    def move(self, transport):
        """Resume with --accept-transport-change: the settings history records the move."""
        previous = copy.deepcopy(self.state["settings"])
        self.state["settings"] = self.settings(transport)
        self.state.setdefault("configuration_changes", []).append(
            {"previous": previous, "selected": copy.deepcopy(self.state["settings"])}
        )

    def test_a_content_identical_config_in_another_checkout_keeps_the_binding(self):
        self.move(identity("/two/tools/providers/configs/kilocode.toml"))
        self.move(identity("/three/tools/providers/configs/kilocode.toml"))
        self.assertNotEqual(self.saved, recovery._binding(self.state, "rev"))
        self.assertTrue(recovery._holds(self.saved, self.state, "rev"))

    def test_a_move_without_its_settings_history_is_still_a_changed_setting(self):
        self.move(identity("/two/tools/providers/configs/kilocode.toml"))
        self.state["configuration_changes"] = []
        self.assertFalse(recovery._holds(self.saved, self.state, "rev"))

    def test_a_changed_config_or_tool_is_still_a_changed_setting(self):
        for changed in (
            identity("/two/tools/providers/configs/kilocode.toml", sha="b" * 64),
            identity("/two/tools/providers/configs/kilocode.toml", version="8.0.0"),
        ):
            with self.subTest(changed=changed):
                self.setUp()
                self.move(changed)
                self.assertFalse(recovery._holds(self.saved, self.state, "rev"))

    def test_another_setting_changed_with_the_move_is_still_a_changed_setting(self):
        self.move(identity("/two/tools/providers/configs/kilocode.toml"))
        self.state["settings"]["roles"]["terra"]["model"] = "plan/other"
        self.assertFalse(recovery._holds(self.saved, self.state, "rev"))

    def test_the_holding_check_leaves_the_state_unchanged(self):
        self.move(identity("/two/tools/providers/configs/kilocode.toml"))
        before = copy.deepcopy(self.state)
        recovery._holds(self.saved, self.state, "rev")
        self.assertEqual(before, self.state)


if __name__ == "__main__":
    unittest.main()
