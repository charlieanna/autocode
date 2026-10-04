"""The user closes reviewer findings by name, as an audited decision (#300)."""
import copy
import unittest

import autocode_finding_close as finding_close


def ledger(*rows):
    return {"findings_ledger": [{"id": ident, "source": source, "status": status, "blocking": True,
                                 "not_rechecked_in": "validator.json"} for ident, source, status in rows],
            "status": "WAITING_FOR_USER", "stop_reason": "asked"}


class FindingCloseTests(unittest.TestCase):
    def test_named_open_findings_close_as_the_users_decision(self):
        state = ledger(("F-1", "sol", "open"), ("F-2", "astra", "open"))
        withdrawn = []
        closed = finding_close.close(state, ["F-1"], "Duplicate of F-0, settled by option A",
                                     current=lambda s: None, supersede=lambda s, why: withdrawn.append(why))
        self.assertEqual(["F-1"], closed)
        row, other = state["findings_ledger"]
        self.assertEqual(("resolved", "user", "user_cli", "Duplicate of F-0, settled by option A"),
                         (row["status"], row["resolved_by"], row["resolved_in"], row["resolution_evidence"]))
        self.assertNotIn("not_rechecked_in", row)
        self.assertEqual("open", other["status"])
        event = state["user_events"][-1]
        self.assertEqual(("findings_closed", "user_cli", ["F-1"]), (event["kind"], event["actor"], event["ids"]))
        self.assertEqual([], withdrawn)  # no request asked about it
        self.assertEqual("WAITING_FOR_USER", state["status"])

    def test_bad_input_changes_nothing(self):
        state = ledger(("F-1", "sol", "open"), ("F-2", "sol", "resolved"))
        before = copy.deepcopy(state)
        for ids, reason, message in ((["F-1"], " ", "needs --close-reason"),
                                     (["F-9"], "x", "unknown F-9"),
                                     (["F-1", "F-2"], "x", "already resolved F-2")):
            with self.subTest(ids=ids):
                with self.assertRaisesRegex(ValueError, message):
                    finding_close.close(state, ids, reason, current=lambda s: None, supersede=lambda s, why: True)
                self.assertEqual(before, state)

    def test_closing_a_finding_a_validation_stop_asked_about_answers_it(self):
        request = {"request": {"finding_ids": ["F-1", "F-2"]}}
        def current(state):  # bound to user events, like resolver_human.current
            return request if not state.get("user_events") else None
        for named, answered in ((["F-3"], False), (["F-1"], True), (["F-1", "F-2"], True)):
            with self.subTest(named=named):
                state, withdrawn = ledger(("F-1", "sol", "open"), ("F-2", "astra", "open"), ("F-3", "sol", "open")), []
                finding_close.close(state, named, "Settled by the user's option A", current=current,
                                    supersede=lambda s, why: withdrawn.append(why) or True)
                self.assertEqual(answered, bool(withdrawn))
                self.assertEqual("RUNNING" if answered else "WAITING_FOR_USER", state["status"])


if __name__ == "__main__":
    unittest.main()
