"""Every run-state key tools/ touches is named in the typed RunState (#695)."""

from __future__ import annotations

import ast
import re
import sys
import tempfile
import unittest
from pathlib import Path

from tests.source_inventory import python_sources

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import autocode_run_state as run_state


def touched_keys(root: Path = TOOLS) -> set[str]:
    """Every key application source reads or writes on a state dict."""
    keys = set()
    for path in python_sources(root):
        keys.update(_touched_keys_in(path.read_text(errors="ignore")))
    return keys


def _touched_keys_in(source: str) -> set[str]:
    keys = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Subscript) and _is_state(node.value):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _is_state(node.func.value):
            if node.func.attr in ("get", "setdefault", "pop"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    keys.add(node.args[0].value)
            elif node.func.attr == "update":
                for keyword in node.keywords:
                    if keyword.arg:
                        keys.add(keyword.arg)
                if node.args and isinstance(node.args[0], ast.Dict):
                    for key in node.args[0].keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            keys.add(key.value)
    return keys


def _is_state(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "state"


class RunStateTests(unittest.TestCase):
    def test_every_touched_key_is_named(self):
        missing = sorted(touched_keys() - set(run_state.KEYS))
        self.assertEqual([], missing, f"run-state keys used in tools/ but not named in autocode_run_state: {missing}")

    def test_agents_md_key_count_tracks_the_contract(self):
        agents = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text()
        match = re.search(r"dict of about (\d+) named keys", agents)
        self.assertIsNotNone(match, "AGENTS.md rule 4 must state the run-state key count")
        documented, actual = int(match.group(1)), len(run_state.KEYS)
        self.assertLessEqual(
            abs(documented - actual), actual * 0.1, f"AGENTS.md says {documented} keys, the contract names {actual}"
        )

    def test_the_contract_names_the_core_keys_with_a_writer_or_reader(self):
        source = Path(TOOLS / "autocode_run_state.py").read_text()
        for key in (
            "status",
            "goal_contract",
            "validation",
            "settings",
            "stages",
            "current_task",
            "next_stage",
            "workspace",
        ):
            self.assertIn(key, run_state.KEYS)
            self.assertIn(f"    {key}:", source, f"{key} must be annotated in RunState")

    def test_a_new_key_fails_this_test_until_it_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "units").mkdir()
            (root / "units" / "planner.py").write_text('state["status"] = state.get("undeclared_runtime_key")')
            (root / "tests").mkdir()
            (root / "tests" / "test_fixture.py").write_text('state["fixture_only_key"] = 1')
            keys = touched_keys(root)
            self.assertEqual({"status", "undeclared_runtime_key"}, keys)
            self.assertEqual({"undeclared_runtime_key"}, keys - run_state.KEYS)

    def test_test_only_keys_are_excluded_from_runtime_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text('state["status"]\n')
            (root / "tests").mkdir()
            (root / "tests" / "test_runtime.py").write_text('state["seen"]\n')
            nested = root / "tests" / "units"
            nested.mkdir()
            (nested / "test_nested.py").write_text('state.get("reasoning_efforts")\n')
            self.assertEqual({"status"}, touched_keys(root))

    def test_pop_and_update_writes_count_as_touched_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text(
                'state.pop("completion_actor", None)\n'
                "state.update(resolver_human_proposal={})\n"
                'state.update({"seen": 1})\n'
            )
            self.assertEqual({"completion_actor", "resolver_human_proposal", "seen"}, touched_keys(root))

    def test_unknown_runtime_keys_still_fail_the_contract_comparison(self):
        unknown = {"unknown_runtime_scan_fixture", "unknown_nested_runtime_scan_fixture"}
        self.assertTrue(unknown.isdisjoint(run_state.KEYS))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text('state["unknown_runtime_scan_fixture"]\n')
            nested = root / "units"
            nested.mkdir()
            (nested / "runtime.py").write_text('state.get("unknown_nested_runtime_scan_fixture")\n')
            missing = sorted(touched_keys(root) - set(run_state.KEYS))
            self.assertEqual(sorted(unknown), missing)

    def test_keys_are_stable_and_unique(self):
        self.assertIsInstance(run_state.KEYS, frozenset)
        self.assertEqual(len(run_state.KEYS), len(set(run_state.KEYS)))
        for key in run_state.KEYS:
            self.assertRegex(key, r"^[a-z_][a-z0-9_]*$", key)


if __name__ == "__main__":
    unittest.main()
