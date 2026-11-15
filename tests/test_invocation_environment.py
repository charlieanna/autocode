"""Private capture ancestry changes invocation identity, never backend inputs."""

import unittest
from types import MappingProxyType

from autocode_invocation_environment import without_call_markers


class InvocationEnvironmentTests(unittest.TestCase):
    def test_capture_and_launcher_markers_do_not_change_semantic_inputs(self):
        settings = {
            "PATH": "/chosen/bin",
            "CODEX_HOME": "/chosen/profile",
            "KILO_CONFIG": "chosen-settings",
            "AUTOCODE_CAPTURE_FAMILY_PROFILE": "ordinary-setting",
        }
        first = {**settings, "CODEX_TOOL_CALL_ID": "one", "AUTOCODE_CAPTURE_FAMILY": "first-capture"}
        second = {**settings, "CODEX_TOOL_CALL_ID": "two", "AUTOCODE_CAPTURE_FAMILY": "second-capture"}
        original = dict(first)
        self.assertEqual(settings, without_call_markers(MappingProxyType(first)))
        self.assertEqual(settings, without_call_markers(second))
        self.assertEqual(original, first, "input mappings and the actual launch environment stay intact")

    def test_profile_changes_still_change_semantic_inputs(self):
        before = {"CODEX_HOME": "first", "AUTOCODE_CAPTURE_FAMILY": "same-capture"}
        after = {"CODEX_HOME": "second", "AUTOCODE_CAPTURE_FAMILY": "same-capture"}
        self.assertEqual({"CODEX_HOME": "first"}, without_call_markers(before))
        self.assertEqual({"CODEX_HOME": "second"}, without_call_markers(after))


if __name__ == "__main__":
    unittest.main()
