"""A run's status in a person's words, for lists of chats (autocode_status_label)."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_pause_category as pause_category
import autocode_status_label as status_label

RAW_CODE = re.compile(r"[A-Z]{2,}_[A-Z_]+")


class StatusLabelTests(unittest.TestCase):
    def test_known_statuses_read_as_words(self):
        self.assertEqual("Working", status_label.label("RUNNING"))
        self.assertEqual("Finished", status_label.label("TASK_COMPLETE"))
        self.assertEqual("Plan ready for your approval", status_label.label("AWAITING_GOAL_APPROVAL"))
        self.assertEqual("Waiting for your answer", status_label.label("WAITING_FOR_USER"))
        self.assertEqual("Paused at your request", status_label.label("PAUSED_REQUESTED"))

    def test_a_pause_says_whose_move_it_is(self):
        self.assertEqual("Paused for your decision", status_label.label("PAUSED_COST_LIMIT"))
        self.assertEqual("Paused: check your environment", status_label.label("PAUSED_AUTH"))
        self.assertEqual("Paused: model or provider problem", status_label.label("PAUSED_RATE_LIMIT"))
        self.assertEqual("Paused by AutoCode", status_label.label("PAUSED_PLANNING_BUDGET"))
        self.assertEqual("Paused by AutoCode", status_label.label("PAUSED_SOMETHING_NEW"))

    def test_no_status_any_table_names_shows_a_raw_code(self):
        statuses = {status for group in pause_category.STATUS_GROUPS.values() for status in group}
        for status in sorted(statuses | set(status_label.FIXED) | {"INTERRUPTED", "HONEST_BLOCKER"}):
            with self.subTest(status=status):
                words = status_label.label(status)
                self.assertTrue(words)
                self.assertIsNone(RAW_CODE.search(words), words)
                self.assertNotIn(status, words)

    def test_nothing_to_describe(self):
        self.assertIsNone(status_label.label(None))
        self.assertIsNone(status_label.label(""))


if __name__ == "__main__":
    unittest.main()
