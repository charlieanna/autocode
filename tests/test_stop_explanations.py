"""Every pause state has a plain-English explanation (#715)."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import autocode_stop_explanations as stop_explanations
import autocode_run_view as run_view


def named_pause_states(source_root: Path = TOOLS) -> set[str]:
    """Pause states named in runtime modules, excluding legacy test fixtures."""
    named = set()
    for path in source_root.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        text = path.read_text()
        named.update(re.findall(r"PAUSED_[A-Z_]+", text))
        named.update(re.findall(r"WAITING_FOR_USER|AWAITING_GOAL_APPROVAL|BLOCKED_HUMAN|RESOLVER_PENDING", text))
    # Truncated prefixes (PAUSED_MILESTONE_ etc.) are not states.
    return {status for status in named if not status.endswith("_")}


class StopExplanationTests(unittest.TestCase):
    def test_every_pause_state_in_the_table_has_three_parts(self):
        self.assertGreater(len(stop_explanations.TABLE), 50)
        for status, entry in stop_explanations.TABLE.items():
            with self.subTest(status=status):
                means, does = entry
                self.assertTrue(means.strip(), status)
                self.assertTrue(does.strip(), status)
                self.assertGreater(len(means), 20, status)
                self.assertGreater(len(does), 20, status)

    def test_every_pause_state_named_in_tools_has_an_explanation(self):
        # The issue's bar: a state without an explanation fails this test.
        missing = sorted(named_pause_states() - set(stop_explanations.TABLE))
        self.assertEqual([], missing, f"pause states without an explanation: {missing}")

    def test_test_only_pause_states_are_excluded_from_runtime_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text('status = "PAUSED_BUDGET"\n')
            (root / "test_runtime.py").write_text('status = "PAUSED_TEST"\nstatus = "PAUSED_TEST_DISPATCH"\n')
            self.assertEqual({"PAUSED_BUDGET"}, named_pause_states(root))

    def test_unknown_runtime_pause_still_fails_the_table_comparison(self):
        unknown = "PAUSED_UNKNOWN_RUNTIME_SCAN_FIXTURE"
        self.assertNotIn(unknown, stop_explanations.TABLE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text(f'status = "{unknown}"\n')
            missing = sorted(named_pause_states(root) - set(stop_explanations.TABLE))
            self.assertEqual([unknown], missing)

    def test_explain_is_three_short_paragraphs_for_a_person(self):
        text = stop_explanations.explain(
            "PAUSED_BUDGET", stop_reason="iteration ceiling")
        self.assertIn("PAUSED_BUDGET", text["what_happened"])
        self.assertIn("iteration ceiling", text["what_happened"])
        self.assertIn("budget", text["what_it_means"].lower())
        self.assertIn("--resume-paused", text["what_the_command_does"])
        self.assertIn(text["what_it_means"], text["explanation"])

    def test_the_view_carries_the_explanation_never_renamed(self):
        state = {"status": "WAITING_FOR_USER", "stop_reason": "a question is open",
                 "settings": {"roles": {}}, "acceptance_criteria": []}
        view = run_view.view(state)
        self.assertIn("explanation", view)
        self.assertIn("person", view["explanation"]["what_happened"].lower()
                      + view["explanation"]["what_it_means"].lower())
        self.assertTrue(view["explanation"]["what_the_command_does"])

    def test_an_unknown_status_still_explains(self):
        text = stop_explanations.explain("PAUSED_NOT_A_REAL_STATE")
        self.assertIn("PAUSED_NOT_A_REAL_STATE", text["what_happened"])
        self.assertTrue(text["what_it_means"])
        self.assertTrue(text["what_the_command_does"])


if __name__ == "__main__":
    unittest.main()
