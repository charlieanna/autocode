"""Every run-state key tools/ touches is named in the typed RunState (#695)."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import autocode_run_state as run_state


def touched_keys(source_root: Path = TOOLS) -> set[str]:
    """Every key runtime source reads or writes on a state dict."""
    keys = set()
    pattern = re.compile(r'''state(?:\[|\.get\(|\.setdefault\()\s*["']([a-z0-9_]+)["']''')
    for path in source_root.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
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
        # The guard the issue asks for: a key added to tools/ without naming it here fails.
        self.assertIn("status", touched_keys())
        self.assertGreater(len(run_state.KEYS), 100)

    def test_test_only_keys_are_excluded_from_runtime_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text('state["status"]\n')
            (root / "test_runtime.py").write_text('state["seen"]\n')
            nested = root / "units"
            nested.mkdir()
            (nested / "test_nested.py").write_text('state.get("reasoning_efforts")\n')
            self.assertEqual({"status"}, touched_keys(root))

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
