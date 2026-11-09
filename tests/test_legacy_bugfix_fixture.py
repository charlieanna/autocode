"""Legacy launch emulation changes only initial policy, never saved checkpoints."""

import unittest
from types import SimpleNamespace

import autocode_requirements_chat as requirements_chat
import autocode_workflows as workflows


class LegacyBugfixFixtureTests(unittest.TestCase):
    def setUp(self):
        # install() swaps two module functions, as the launched CLI process does; put them back.
        for module, name in ((workflows, "first_stage"), (requirements_chat, "gather_first")):
            self.addCleanup(setattr, module, name, getattr(module, name))

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

    def test_a_job_starts_its_own_stage_until_the_saved_run_names_it(self):
        # Every new task goes through the full pipeline (#3010); the emulated older runtime started a
        # named job at its own stage until the job was named, then the current runtime takes over.
        from tests.legacy_bugfix_fixture import install

        current = workflows.first_stage
        runner = SimpleNamespace(configure=lambda args, state: {"implementation_flow": "standard"})
        install(runner, drop_marker=False)
        self.assertEqual({"implementation_flow": "standard"}, runner.configure(SimpleNamespace(run_dir=None), {}))
        for kind, stage in workflows.FIRST_STAGE.items():
            expected = "astra_discovery" if kind == "bugfix" else stage  # a marked bug fix plans, as before
            self.assertEqual(expected, workflows.first_stage(marked(), kind, "astra_discovery"))
        self.assertEqual("investigate_bug", workflows.first_stage({"settings": {}}, "bugfix", "astra_discovery"))
        pinned = {"workflow": {"kind": "review", "source": "user"}}
        self.assertFalse(requirements_chat.gather_first(pinned))
        # Requirements has not named the job of an unpinned saved run yet: the older runtime still names it.
        runner.configure(SimpleNamespace(run_dir="/saved/run"), {"workflow": {"then": "requirements_gather"}})
        self.assertEqual("review_change", workflows.first_stage({}, "review", "astra_discovery"))
        # Once the saved run names its job, every later invocation runs the current runtime.
        runner.configure(SimpleNamespace(run_dir="/saved/run"), pinned)
        self.assertIs(current, workflows.first_stage)
        self.assertEqual("astra_discovery", workflows.first_stage({}, "review", "astra_discovery"))


def marked():
    return {"settings": {workflows.IMPLEMENTATION_FLOW: workflows.STANDARD_IMPLEMENTATION}}


if __name__ == "__main__":
    unittest.main()
