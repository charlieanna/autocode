"""A Resolver whose route comes from a role on another tool keeps its recovery admissible.

A run that gives the task planner its own tool (``--role-transport task_planner=TOOL``) derives the
Resolver's route and tool binding from it at the Resolver's first launch (``units.autoresolver``).
The recovery packet bound before that launch already counted the route as derived; it must count the
tool binding the same way, or the launch it was made for is refused as a stale handoff and the run
pauses for a person (PAUSED_STALE_HANDOFF) instead of resolving.
"""

import copy
import unittest

import autocode_resolver_recovery as recovery
import autocode_role_transport as role_transport

IDENTITY = {"provider": "fixture_command", "identity": {"engine": "fixture_command", "config_sha256": "a" * 64}}


def state():
    return {
        "goal_contract": {"hash": "contract", "revision": 3},
        "current_task": {"id": "task-1"},
        "settings": {
            "engine": "opencode",
            "provider": "opencode",
            "roles": {
                "builder": {"model": "glm", "engine": "opencode"},
                "task_planner": {"model": "fixture", "engine": "opencode", "provider": "fixture_command"},
            },
            "transport_identities": {"role:task_planner": copy.deepcopy(IDENTITY)},
        },
    }


def launch_resolver(run):
    """What the Resolver's preparation does to the settings before it launches (units.autoresolver)."""
    roles = run["settings"]["roles"]
    roles.setdefault("resolver", copy.deepcopy(roles["task_planner"]))
    role_transport.inherit(run["settings"], "resolver", "task_planner")


class ResolverTransportBindingTests(unittest.TestCase):
    def test_the_resolvers_first_launch_keeps_the_recovery_it_was_bound_for(self):
        run = state()
        saved = recovery._binding(run, "revision")
        launch_resolver(run)
        self.assertIn("role:resolver", run["settings"]["transport_identities"], "the launch derives a binding")
        self.assertTrue(recovery._holds(saved, run, "revision"))

    def test_a_resolver_already_launched_binds_the_same(self):
        run = state()
        launched = state()
        launch_resolver(launched)
        self.assertEqual(recovery._binding(run, "revision"), recovery._binding(launched, "revision"))

    def test_a_changed_tool_binding_still_stales_the_recovery(self):
        run = state()
        saved = recovery._binding(run, "revision")
        launch_resolver(run)
        run["settings"]["transport_identities"]["role:resolver"]["identity"]["config_sha256"] = "b" * 64
        self.assertFalse(recovery._holds(saved, run, "revision"))
        other = state()
        other["settings"]["transport_identities"]["role:task_planner"]["identity"]["config_sha256"] = "c" * 64
        self.assertFalse(recovery._holds(saved, other, "revision"))

    def test_a_run_on_one_tool_is_unchanged(self):
        run = state()
        del run["settings"]["roles"]["task_planner"]["provider"]
        run["settings"]["transport_identities"] = {}
        saved = recovery._binding(run, "revision")
        launch_resolver(run)
        self.assertTrue(recovery._holds(saved, run, "revision"))


if __name__ == "__main__":
    unittest.main()
