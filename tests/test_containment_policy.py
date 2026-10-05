"""Run-setup tool-containment policy (#413): refuse early, or a recorded explicit opt-out. Pure functions."""
import copy
import unittest
from unittest.mock import patch

import tests  # noqa: F401 - runtime import path
import autocode_containment_policy as policy
import autocode_run_view as run_view

OPENCODE = {"engine": "opencode", "provider": "opencode",
            "roles": {"terra": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3"},
                      "sol": {"engine": "opencode", "model": "openai/gpt-6-sol"}}}
CODEX = {"engine": "codex", "provider": "opencode",
         "roles": {"terra": {"model": "gpt-5.6-terra"}, "sol": {"model": "gpt-5.6-sol"}}}
AT = "2026-10-04T00:00:00+00:00"


class ContainmentPolicyTests(unittest.TestCase):
    def configure(self, state, settings, *, allow=False, configured_tool=False, problem=None):
        with patch.object(policy.tool_containment, "unavailable", return_value=problem) as check:
            policy.configure(state, settings, allow=allow, configured_tool=configured_tool,
                             workspace="/workspace", now=lambda: AT)
        return check

    def test_only_builtin_opencode_runs_are_covered(self):
        self.assertTrue(policy.applies(OPENCODE))
        self.assertTrue(policy.applies({**OPENCODE, "provider": None}))
        self.assertTrue(policy.applies({"engine": "opencode", "provider": "opencode", "roles": {}}))
        self.assertFalse(policy.applies(CODEX))
        self.assertFalse(policy.applies({"roles": {"terra": {"model": "gpt-5.6-terra"}}}))
        self.assertFalse(policy.applies({**OPENCODE, "provider": "kilocode"}))
        self.assertFalse(policy.applies(OPENCODE, configured_tool=True))
        self.assertFalse(policy.applies(None))

    def test_unavailable_boundary_refuses_with_reason_and_flag_and_changes_nothing(self):
        for reason in ("strict tool containment requires macOS sandbox-exec; this machine is linux",
                       "strict tool containment is qualified only for OpenCode 1.18.33; "
                       "this machine has OpenCode 1.18.34"):
            with self.subTest(reason=reason):
                state, settings = {"status": "PAUSED_TOOL_CONTAINMENT"}, copy.deepcopy(OPENCODE)
                with self.assertRaises(ValueError) as caught:
                    self.configure(state, settings, problem=reason)
                self.assertIn(reason, str(caught.exception))
                self.assertIn("--allow-uncontained-tools", str(caught.exception))
                self.assertIn("before any stage", str(caught.exception))
                self.assertEqual(({"status": "PAUSED_TOOL_CONTAINMENT"}, OPENCODE), (state, settings))

    def test_available_boundary_changes_nothing(self):
        state, settings = {}, copy.deepcopy(OPENCODE)
        self.configure(state, settings).assert_called_once_with("/workspace")
        self.assertEqual(({}, OPENCODE), (state, settings))
        self.assertEqual("contained", run_view.view({"status": "RUNNING", "settings": settings})["tool_containment"])

    def test_explicit_opt_out_is_saved_once_with_who_when_and_why(self):
        state, settings = {"user_events": [{"kind": "answer"}]}, copy.deepcopy(OPENCODE)
        self.configure(state, settings, allow=True, problem="this machine is linux")
        self.assertIs(True, settings["allow_uncontained_tools"])
        self.assertEqual({"kind": "uncontained_tools_accepted", "actor": "user_cli", "at": AT,
                          "flag": "--allow-uncontained-tools", "reason": "this machine is linux"},
                         state["user_events"][-1])
        # Saved: later resumes need no flag, repeat nothing and run no availability check.
        for allow in (False, True):
            self.configure(state, settings, allow=allow, problem="this machine is linux").assert_not_called()
        self.assertEqual(2, len(state["user_events"]))
        self.assertEqual("uncontained_user_accepted",
                         run_view.view({"status": "RUNNING", "settings": settings})["tool_containment"])

    def test_opt_out_on_a_supported_setup_is_still_the_users_choice(self):
        state, settings = {}, copy.deepcopy(OPENCODE)
        self.configure(state, settings, allow=True, problem=None)
        self.assertTrue(policy.accepted(settings))
        self.assertIn("available", state["user_events"][0]["reason"])

    def test_runs_without_a_contained_stage_are_unaffected_and_reject_the_flag(self):
        for settings, configured in ((CODEX, False), ({**OPENCODE, "provider": "kilocode"}, False), (OPENCODE, True)):
            with self.subTest(settings=settings, configured=configured):
                state, saved = {}, copy.deepcopy(settings)
                self.configure(state, saved, configured_tool=configured, problem="linux").assert_not_called()
                self.assertEqual(({}, settings), (state, saved))
                with self.assertRaisesRegex(ValueError, "applies only to built-in OpenCode runs"):
                    self.configure(state, saved, allow=True, configured_tool=configured, problem="linux")
                self.assertEqual(({}, settings), (state, saved))
        self.assertIsNone(run_view.view({"status": "RUNNING", "settings": CODEX})["tool_containment"])
        self.assertIsNone(run_view.view({"status": "RUNNING"})["tool_containment"])

    def test_only_a_literal_true_setting_counts_as_acceptance(self):
        for value in ("true", 1, {"accepted": True}, None):
            self.assertFalse(policy.accepted({**OPENCODE, "allow_uncontained_tools": value}))


if __name__ == "__main__":
    unittest.main()
