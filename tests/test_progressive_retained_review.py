"""The saved final review may hold a retained-contract reference (#1937)."""

import unittest

from tools import autocode_progressive_state as state


class ExpandRetained(unittest.TestCase):
    reviewed = {
        "acceptance_criteria": [{"id": "C1"}],
        "initial_task": {"objective": "first"},
        "open_blocking_questions": [{"id": "Q1"}],
    }

    def test_full_contract_is_unchanged(self):
        full = {"acceptance_criteria": [{"id": "C1"}]}
        self.assertIs(state._expand_retained(full, self.reviewed), full)

    def test_reference_takes_retained_text_from_the_reviewed_contract(self):
        saved = {"retain_contract": "r1:x", "initial_task": {"objective": "first"}, "open_blocking_questions": []}
        self.assertEqual(state._expand_retained(saved, self.reviewed), self.reviewed)

    def test_reference_keeps_its_authored_first_task_compared(self):
        saved = {"retain_contract": "r1:x", "initial_task": {"objective": "other"}}
        expanded = state._expand_retained(saved, self.reviewed)
        self.assertIsNotNone(state._first_difference(expanded, self.reviewed))

    def test_reference_question_missing_from_the_report_is_a_difference(self):
        saved = {"retain_contract": "r1:x", "open_blocking_questions": [{"id": "Q2"}]}
        expanded = state._expand_retained(saved, self.reviewed)
        self.assertIsNotNone(state._first_difference(expanded, self.reviewed))


if __name__ == "__main__":
    unittest.main()
