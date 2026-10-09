"""Qwen engine run configuration: a new run needs no provider facade.

The Qwen engine has no provider TOML, so `autocode_providers.select` must not be
asked for a default: it falls back to `default_name()`, which is "opencode", and
the engine's own conflict check then refused every `--engine qwen` run before a
stage could launch.
"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_configure
import autocode_dispatch as dispatch
import autocode_milestones as milestones
import autocode_planning as planning
import autocode_qwen as qwen
import autopilot

try:
    from . import test_planning
except ImportError:  # the fixture module imports autocode_configure too
    test_planning = None

TRANSPORT = {"engine": "qwen", "executable": "/usr/local/bin/qwen", "version": "0.25.0"}


class QwenEngineConfigureTests(unittest.TestCase):
    def configure_args(self, **overrides):
        if test_planning is None:
            self.skipTest("tests.test_planning is not importable in this tree")
        return test_planning.PlanningTests.configure_args(self, **overrides)

    def configure(self, args, state=None):
        with patch.object(qwen, "local_settings", return_value=dict(TRANSPORT)):
            return autocode_configure.configure(
                args, state or {"workspace": "/tmp/fixture", "iteration": 0},
                planning=planning, milestones=milestones, autopilot=autopilot)

    def test_a_new_qwen_run_starts_without_a_provider(self):
        settings = self.configure(self.configure_args(engine="qwen"))
        self.assertEqual("qwen", settings["engine"])
        self.assertIsNone(settings["provider"])
        self.assertEqual(TRANSPORT, settings["transport_identity"])

    def test_an_ambient_default_provider_does_not_refuse_a_qwen_run(self):
        previous = os.environ.get("AUTOCODE_PROVIDER")
        os.environ["AUTOCODE_PROVIDER"] = "kilocode"
        if previous is None:
            self.addCleanup(os.environ.pop, "AUTOCODE_PROVIDER", None)
        else:
            self.addCleanup(os.environ.__setitem__, "AUTOCODE_PROVIDER", previous)
        self.assertEqual("qwen", self.configure(self.configure_args(engine="qwen"))["engine"])

    def test_an_explicitly_requested_provider_still_conflicts(self):
        with self.assertRaises(ValueError) as raised:
            self.configure(self.configure_args(engine="qwen", provider="kilocode"))
        self.assertIn("--provider is not supported with the Qwen engine", str(raised.exception))

    def test_a_saved_qwen_run_resumes_without_a_provider_conflict(self):
        fresh = self.configure(self.configure_args(engine="qwen"))
        state = {"workspace": "/tmp/fixture", "iteration": 1, "settings": fresh, "history": []}
        resumed = self.configure(self.configure_args(), state)
        self.assertEqual("qwen", resumed["engine"])
        self.assertIsNone(resumed["provider"])

    def test_default_roles_keep_every_verifier_independent(self):
        settings = self.configure(self.configure_args(engine="qwen"))
        roles = settings["roles"]
        for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer", "requirements"):
            self.assertEqual("qwen", roles[role]["engine"], role)
            self.assertTrue(roles[role]["model"].startswith("qwen/"), role)
        # The Planner and the Plan Reviewer shared one model, which paused every
        # default run with PAUSED_CROSS_MODEL before any agent was launched.
        self.assertNotEqual(roles["glm"]["model"], roles["plan_reviewer"]["model"])
        self.assertNotEqual(roles["terra"]["model"], roles["sol"]["model"])
        self.assertNotEqual(roles["terra"]["model"], roles["completion"]["model"])
        dispatch.enforce_cross_model_verification({"settings": settings})

    def test_single_model_still_assigns_every_role(self):
        settings = self.configure(self.configure_args(engine="qwen", single_model="qwen/qwen3.8-max"))
        self.assertTrue(settings["single_model_mode"])
        for role, entry in settings["roles"].items():
            self.assertEqual("qwen/qwen3.8-max", entry["model"], role)


if __name__ == "__main__":
    unittest.main()
