"""A provider config moved with its checkout is the same transport (#2773).

Resuming a run from another AutoCode checkout reads the bundled config at a new path.
Same bytes, same tool: no pause, no person, the saved identity unchanged, the move
recorded once. Anything else about the tool that changed is still a transport change.
"""

import copy
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_access_identity as access_identity
import autocode_kilo_isolation as kilo_isolation
import autocode_resolver_recovery as recovery
import autocode_role_transport as role_transport
import autocode_transport_relocation as relocation
import autocode_util as util

from tools.providers import command

SHA = "a" * 64


def identity(path="/one/tools/providers/configs/kilocode.toml", **changes):
    return {
        "engine": "kilocode",
        "identity_version": 1,
        "executable": "/bin/kilo",
        "version": "7.8.12",
        "config_path": path,
        "config_sha256": SHA,
        **changes,
    }


MOVED = identity("/two/tools/providers/configs/kilocode.toml")


class PathOnly(unittest.TestCase):
    def test_the_same_bytes_at_another_absolute_path_is_a_move(self):
        self.assertTrue(access_identity.path_only(MOVED, identity()))
        self.assertTrue(access_identity.path_only(identity(), MOVED))

    def test_an_unchanged_identity_is_not_a_move(self):
        self.assertFalse(access_identity.path_only(identity(), identity()))

    def test_any_other_changed_field_is_a_real_change(self):
        for field, value in (
            ("config_sha256", "b" * 64),
            ("version", "7.9.0"),
            ("executable", "/opt/kilo"),
            ("engine", "other"),
        ):
            with self.subTest(field=field):
                self.assertFalse(access_identity.path_only({**MOVED, field: value}, identity()))

    def test_a_tool_without_a_version_command_moves_when_both_sides_say_none(self):
        self.assertTrue(access_identity.path_only({**MOVED, "version": None}, identity(version=None)))
        self.assertFalse(access_identity.path_only({**MOVED, "version": None}, identity()))
        self.assertFalse(access_identity.path_only(MOVED, identity(version=None)))

    def test_an_incomplete_or_unfamiliar_identity_never_moves(self):
        for field, value in (
            ("config_sha256", None),
            ("config_sha256", "unknown"),
            ("config_path", "relative/kilocode.toml"),
            ("executable", "kilo"),
            ("version", ""),
            ("version", 7),
            ("identity_version", 2),
            ("identity_version", True),
        ):
            for side in ("current", "saved"):
                with self.subTest(field=field, side=side):
                    current, saved = dict(MOVED), identity()
                    (current if side == "current" else saved)[field] = value
                    self.assertFalse(access_identity.path_only(current, saved))
        extra = {**MOVED, "launch_policy_sha256": SHA}
        self.assertFalse(access_identity.path_only(extra, {**identity(), "launch_policy_sha256": SHA}))
        missing = dict(MOVED)
        missing.pop("version")
        self.assertFalse(access_identity.path_only(missing, identity()))
        self.assertFalse(access_identity.path_only(None, identity()))
        self.assertFalse(access_identity.path_only(MOVED, "identity"))


class Notes(unittest.TestCase):
    def test_a_move_is_recorded_once_as_metadata_and_settings_stay(self):
        state = {"settings": {"transport_identity": identity()}}
        before = copy.deepcopy(state["settings"])
        self.assertTrue(relocation.note(state, "opencode", MOVED, identity(), at="t1"))
        self.assertFalse(relocation.note(state, "opencode", MOVED, identity(), at="t2"))
        self.assertEqual(
            [
                {
                    "at": "t1",
                    "reason": relocation.REASON,
                    "transport": "opencode",
                    "config_path": MOVED["config_path"],
                    "saved_config_path": identity()["config_path"],
                }
            ],
            state["configuration_changes"],
        )
        self.assertEqual(before, state["settings"])
        # A further move, and the same move of another tool, are each their own decision.
        third = identity("/three/kilocode.toml")
        self.assertTrue(relocation.note(state, "opencode", third, identity(), at="t3"))
        self.assertTrue(relocation.note(state, "role:sol", MOVED, identity(), at="t4"))
        self.assertEqual(3, len(state["configuration_changes"]))

    def test_no_note_without_a_move(self):
        state = {}
        for current in (identity(), {**MOVED, "config_sha256": "b" * 64}):
            self.assertFalse(relocation.note(state, "opencode", current, identity(), at="t"))
        self.assertNotIn("configuration_changes", state)

    def test_the_settings_history_walk_skips_the_note(self):
        """A note is metadata: an earlier explicit move stays provable after it."""
        roles = {"terra": {"model": "plan/glm"}}

        def settings(transport):
            return {
                "transport_identity": copy.deepcopy(transport),
                "transport_identities": {"opencode": copy.deepcopy(transport)},
                "roles": copy.deepcopy(roles),
            }

        state = {
            "settings": settings(MOVED),
            "configuration_changes": [{"previous": settings(identity()), "selected": settings(MOVED)}],
        }
        relocation.note(state, "opencode", identity("/three/kilocode.toml"), MOVED, at="t")
        self.assertEqual([identity()], access_identity.relocations(state))

    def test_follow_notes_the_run_tool_and_each_role_bound_tool(self):
        class Facade:
            CONFIGURED = True

            def __init__(self, current):
                self.current = current

            def local_settings(self, workspace):
                return self.current

        state = {
            "settings": {
                "transport_identity": identity(),
                "transport_identities": {
                    "opencode": identity(),
                    "role:sol": {"provider": "kilocode", "identity": identity()},
                    "role:task_planner": {"provider": "native", "identity": identity()},
                },
            }
        }
        facades = {"kilocode": Facade(MOVED), "native": type("Native", (), {"CONFIGURED": False})()}
        relocation.follow(state, "/workspace", MOVED, at="t", resolve=facades.__getitem__)
        self.assertEqual(["opencode", "role:sol"], [row["transport"] for row in state["configuration_changes"]])
        relocation.follow(state, "/workspace", MOVED, at="t2", resolve=facades.__getitem__)
        self.assertEqual(2, len(state["configuration_changes"]))

    def test_follow_without_saved_identities_does_nothing(self):
        for state in ({}, {"settings": {}}, {"settings": {"transport_identities": {"role:x": "malformed"}}}):
            relocation.follow(state, "/workspace", MOVED, at="t", resolve=None)
            self.assertNotIn("configuration_changes", state)


class Expands(unittest.TestCase):
    def test_only_a_newer_identity_version_replaces_the_saved_identity(self):
        self.assertTrue(relocation.expands({"identity_version": 2}, {"identity_version": 1}))
        self.assertTrue(relocation.expands({"identity_version": 2}, {}))
        self.assertFalse(relocation.expands(MOVED, identity()))
        self.assertFalse(relocation.expands({"identity_version": 2}, {"identity_version": 2}))
        self.assertFalse(relocation.expands({"identity_version": 1}, {"identity_version": 2}))


class CommandTool(unittest.TestCase):
    """The real command-tool facade, its config read from a new checkout."""

    BODY = textwrap.dedent("""\
        name = "reloc"
        command = ["sh", "-c", "true", "{report}"]
        models = ["demo"]

        [roles]
        astra = { model = "demo", effort = "high" }
        terra = { model = "demo", effort = "medium" }
        sol = { model = "demo", effort = "high" }
        glm = { model = "demo", effort = "medium" }
        completion = { model = "demo", effort = "high" }
        plan_reviewer = { model = "demo", effort = "high" }
        """)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.previous = os.environ.get("XDG_CONFIG_HOME")
        self.addCleanup(self.restore)
        self.first = self.write(self.root / "one", self.BODY)

    def restore(self):
        if self.previous is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.previous

    def write(self, home, body):
        path = home / "autocode" / "providers" / "reloc.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def identity_at(self, home):
        os.environ["XDG_CONFIG_HOME"] = str(home)
        provider = command.load("reloc")
        return provider, provider.local_settings(self.root)

    def test_same_bytes_in_another_checkout_are_no_drift_and_other_bytes_are(self):
        _provider, saved = self.identity_at(self.root / "one")
        shutil.copytree(self.root / "one", self.root / "two")
        provider, moved = self.identity_at(self.root / "two")
        self.assertNotEqual(saved["config_path"], moved["config_path"])
        self.assertFalse(provider.transport_drift(moved, saved))
        self.assertFalse(provider.transport_drift(saved, saved))
        self.write(self.root / "three", self.BODY + "\n# different bytes\n")
        provider, changed = self.identity_at(self.root / "three")
        self.assertTrue(provider.transport_drift(changed, saved))
        self.assertTrue(provider.transport_drift({**moved, "version": "2"}, saved))

    def test_a_stage_pinned_tool_follows_its_moved_config_and_refuses_changed_bytes(self):
        _provider, saved = self.identity_at(self.root / "one")
        settings = {
            "engine": "opencode",
            "roles": {"sol": {"engine": "opencode", "provider": "reloc", "model": "demo"}},
            "transport_identities": {"role:sol": {"provider": "reloc", "identity": saved}},
        }
        shutil.copytree(self.root / "one", self.root / "two")
        self.identity_at(self.root / "two")
        fallback = object()
        facade = role_transport.adapter(settings, "sol", fallback, self.root)
        self.assertIsNot(fallback, facade)
        self.assertEqual(saved, settings["transport_identities"]["role:sol"]["identity"])
        state = {"settings": settings}
        relocation.follow(state, self.root, None, at="t")
        self.assertEqual(["role:sol"], [row["transport"] for row in state["configuration_changes"]])
        self.write(self.root / "three", self.BODY + "\n# different bytes\n")
        self.identity_at(self.root / "three")
        with self.assertRaises(util.Paused) as stopped:
            role_transport.adapter(settings, "sol", fallback, self.root)
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", stopped.exception.status)


class NativeKiloConfig(unittest.TestCase):
    """Native Kilo isolation checks the bytes of the config it launches with, wherever it lies."""

    def test_the_saved_bytes_in_another_checkout_pass_and_other_bytes_or_no_file_do_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / "one" / "kilocode.toml", root / "two" / "kilocode.toml"
            for path in (old, new):
                path.parent.mkdir()
                path.write_text('name = "kilocode"\n')
            saved = identity(str(old), config_sha256=util.file_hash(old))
            self.assertTrue(kilo_isolation.saved_config(old, saved))
            self.assertTrue(kilo_isolation.saved_config(new, saved))
            new.write_text('name = "kilocode"\n# changed\n')
            self.assertFalse(kilo_isolation.saved_config(new, saved))
            self.assertFalse(kilo_isolation.saved_config(root / "missing.toml", saved))
            self.assertFalse(kilo_isolation.saved_config(old, {**saved, "config_sha256": None}))


class HeldRepair(unittest.TestCase):
    """A repair bound before a move still holds after it, however the move was made."""

    def setUp(self):
        self.roles = {"terra": {"engine": "opencode", "model": "plan/glm", "reasoning_effort": "low"}}
        self.state = {
            "goal_contract": {"hash": "h", "revision": 4},
            "current_task": {"id": "task-1"},
            "settings": self.settings(identity()),
        }
        self.saved = recovery._binding(self.state, "rev")

    def settings(self, transport):
        return {
            "transport_identity": copy.deepcopy(transport),
            "transport_identities": {"opencode": copy.deepcopy(transport)},
            "roles": copy.deepcopy(self.roles),
        }

    def test_a_followed_move_leaves_the_binding_identical(self):
        relocation.follow(self.state, "/workspace", MOVED, at="t")
        self.assertEqual(self.saved, recovery._binding(self.state, "rev"))


if __name__ == "__main__":
    unittest.main()
