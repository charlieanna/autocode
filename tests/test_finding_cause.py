"""Same-cause finding resolution inheritance (#300)."""
import unittest

import autocode_finding_cause as finding_cause


class InheritResolutionTests(unittest.TestCase):
    def _rows(self):
        return [
            {"id": "F-a", "status": "resolved", "evidence": "iterations/001/sol-01.json",
             "finding": "proof FAIL", "blocking": True},
            {"id": "F-b", "status": "open", "evidence": "iterations/001/sol-01.json",
             "finding": "proof fail!", "blocking": True},
            {"id": "F-c", "status": "open", "evidence": "other.json",
             "finding": "proof FAIL", "blocking": True},
        ]

    def test_an_open_row_with_the_same_cause_inherits_the_resolution(self):
        rows = self._rows()
        closed = finding_cause.inherit_resolution(
            rows, "F-a", resolved_in="rep", evidence="user accepted A", at="now")
        self.assertEqual(["F-b"], closed)
        self.assertEqual("resolved", rows[1]["status"])
        self.assertEqual("F-a", rows[1]["inherited_from"])
        self.assertEqual("open", rows[2]["status"])

    def test_permission_answer_resolves_named_rows_and_siblings(self):
        rows = self._rows()
        rows[0]["status"] = "open"
        state = {"findings_ledger": rows}
        event = {"question_id": "q1", "text": "yes", "at": "now"}
        closed = finding_cause.resolve_named(state, ["F-a"], event)
        self.assertIn("F-a", closed)
        self.assertIn("F-b", closed)
        self.assertNotIn("F-c", closed)


if __name__ == "__main__":
    unittest.main()
