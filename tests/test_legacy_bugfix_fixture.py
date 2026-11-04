"""Legacy launch emulation changes only initial policy, never saved checkpoints."""

import unittest
from types import SimpleNamespace


class LegacyBugfixFixtureTests(unittest.TestCase):
    def test_only_initial_launch_uses_the_pre_retirement_policy(self):
        from tests.legacy_bugfix_fixture import install

        settings = {"implementation_flow": "standard", "roles": {"glm": {"model": "selected"}}}
        runner = SimpleNamespace(configure=lambda args, state: dict(settings))
        install(runner)
        launched = runner.configure(SimpleNamespace(run_dir=None), {})
        self.assertNotIn("implementation_flow", launched)
        self.assertEqual(settings["roles"], launched["roles"])
        self.assertEqual("standard", settings["implementation_flow"])
        resumed = runner.configure(SimpleNamespace(run_dir="/saved/run"), {})
        self.assertEqual(settings, resumed)
