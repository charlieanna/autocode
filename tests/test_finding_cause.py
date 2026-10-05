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

    def test_parts_of_one_split_finding_do_not_inherit_each_others_resolution(self):
        # #447: an approved revision split F-a's criteria over milestones; F-p and F-q are its other parts.
        rows = self._rows()
        rows[0]["status"] = "open"
        rows += [{**rows[0], "id": "F-p", "split_from": "F-a"}, {**rows[0], "id": "F-q", "split_from": "F-a"}]
        closed = finding_cause.resolve_named({"findings_ledger": rows}, ["F-p"], {"question_id": "q1", "at": "now"})
        self.assertEqual(["F-p", "F-b"], closed)  # F-b is a separately raised duplicate
        self.assertEqual(["open", "open"], [rows[0]["status"], rows[4]["status"]])


if __name__ == "__main__":
    unittest.main()
