"""Every run-state key tools/ touches is named in the typed RunState (#695)."""
from __future__ import annotations

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
    pattern = re.compile(r'''state(?:\[|\.get\(|\.setdefault\()\s*["']([a-z0-9_]+)["']''')
    for path in python_sources(root):
        keys.update(pattern.findall(path.read_text(errors="ignore")))
    return keys


class RunStateTests(unittest.TestCase):
    def test_every_touched_key_is_named(self):
        missing = sorted(touched_keys() - set(run_state.KEYS))
        self.assertEqual([], missing,
                         f"run-state keys used in tools/ but not named in autocode_run_state: {missing}")

    def test_the_contract_names_the_core_keys_with_a_writer_or_reader(self):
        source = Path(TOOLS / "autocode_run_state.py").read_text()
        for key in ("status", "goal_contract", "validation", "settings", "stages",
                    "current_task", "next_stage", "workspace"):
            self.assertIn(key, run_state.KEYS)
            self.assertIn(f"    {key}:", source, f"{key} must be annotated in RunState")

    def test_a_new_key_fails_this_test_until_it_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "units").mkdir()
            (root / "units" / "planner.py").write_text(
                'state["status"] = state.get("undeclared_runtime_key")')
            (root / "test_fixture.py").write_text('state["fixture_only_key"] = 1')
            keys = touched_keys(root)
            self.assertEqual({"status", "undeclared_runtime_key"}, keys)
            self.assertEqual({"undeclared_runtime_key"}, keys - run_state.KEYS)

    def test_keys_are_stable_and_unique(self):
        self.assertIsInstance(run_state.KEYS, frozenset)
        self.assertEqual(len(run_state.KEYS), len(set(run_state.KEYS)))
        for key in run_state.KEYS:
            self.assertRegex(key, r"^[a-z_][a-z0-9_]*$", key)


if __name__ == "__main__":
    unittest.main()
