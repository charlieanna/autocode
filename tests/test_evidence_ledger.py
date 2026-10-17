import unittest

import autocode_evidence_ledger as ledger


def validation(checks=(), criteria=()):
    return {
        "checks": [{"command": command, "exit_code": code} for command, code in checks],
        "criterion_results": [{"id": ident, "status": status} for ident, status in criteria],
    }


def finding(ident, status="open", blocking=True):
    return {"id": ident, "status": status, "blocking": blocking}


def row(evidence, seen, novelty="new"):
    return {"evidence": evidence, "signature": ledger.signature(seen), "failing": seen["failing"], "novelty": novelty}


class ObserveTests(unittest.TestCase):
    def test_failing_checks_unmet_criteria_and_open_blocking_findings_fail_and_their_opposites_are_resolved(self):
        seen = ledger.observe(
            validation(
                checks=[("pytest -q", 1), ("ruff check", 0)],
                criteria=[("C1", "FAIL"), ("C2", "UNVERIFIED"), ("C3", "PASS")],
            ),
            [finding("F-1"), finding("F-2", blocking=False), finding("F-3", status="resolved")],
            ["b.py", "a.py", "b.py"],
        )
        self.assertEqual(["check:pytest -q", "criterion:C1", "criterion:C2", "finding:F-1"], seen["failing"])
        self.assertEqual(["check:ruff check", "criterion:C3", "finding:F-3"], seen["resolved"])
        self.assertEqual(["a.py", "b.py"], seen["changed"])

    def test_a_check_that_failed_in_one_run_and_passed_in_another_is_still_failing(self):
        seen = ledger.observe(validation(checks=[("pytest -q", 0), ("pytest -q", 2)]), [], [])
        self.assertEqual(["check:pytest -q"], seen["failing"])
        self.assertEqual([], seen["resolved"])

    def test_missing_or_malformed_evidence_is_empty(self):
        self.assertEqual(
            {"failing": [], "resolved": [], "changed": []},
            ledger.observe(None, None, None),
        )
        self.assertEqual(
            {"failing": [], "resolved": [], "changed": []},
            ledger.observe({"checks": ["x", {"command": "y"}], "criterion_results": [None]}, ["F-1"], [3]),
        )


class SignatureTests(unittest.TestCase):
    def test_order_duplicates_and_resolved_items_do_not_change_the_signature(self):
        first = ledger.observe(validation(checks=[("a", 1), ("b", 1)]), [finding("F-9", "resolved")], ["x", "y"])
        second = ledger.observe(validation(checks=[("b", 1), ("a", 1), ("a", 1)]), [], ["y", "x"])
        self.assertEqual(ledger.signature(first), ledger.signature(second))

    def test_another_failing_set_or_changed_set_is_another_signature(self):
        base = ledger.observe(validation(checks=[("a", 1)]), [], ["x"])
        other_failure = ledger.observe(validation(checks=[("b", 1)]), [], ["x"])
        other_change = ledger.observe(validation(checks=[("a", 1)]), [], ["z"])
        self.assertNotEqual(ledger.signature(base), ledger.signature(other_failure))
        self.assertNotEqual(ledger.signature(base), ledger.signature(other_change))


class AssessTests(unittest.TestCase):
    def setUp(self):
        self.first = ledger.observe(validation(checks=[("pytest", 1)]), [finding("F-a")], ["app.py"])

    def test_the_first_failure_is_new(self):
        self.assertEqual("new", ledger.assess([], "e1", self.first))

    def test_recorded_evidence_is_a_replay_even_without_a_signature(self):
        self.assertEqual("replay", ledger.assess([row("e1", self.first)], "e1", self.first))
        self.assertEqual("replay", ledger.assess([{"evidence": "e0"}], "e0", self.first))

    def test_the_same_failing_items_and_changed_files_as_any_earlier_failure_are_a_repeat(self):
        other = ledger.observe(validation(checks=[("pytest", 1)]), [finding("F-a")], ["lib.py"])
        rows = [row("e1", self.first), row("e2", other)]
        self.assertEqual("repeat", ledger.assess(rows, "e3", self.first))

    def test_new_failing_items_after_every_earlier_one_was_resolved_are_progress(self):
        second = ledger.observe(validation(checks=[("pytest", 0)]), [finding("F-a", "resolved"), finding("F-b")], [])
        self.assertEqual("progress", ledger.assess([row("e1", self.first)], "e2", second))
        third = ledger.observe(
            validation(checks=[("pytest", 0)]),
            [finding("F-a", "resolved"), finding("F-b", "resolved"), finding("F-c")],
            ["app.py"],
        )
        rows = [row("e1", self.first), row("e2", second, "progress")]
        self.assertEqual("progress", ledger.assess(rows, "e3", third))

    def test_anything_short_of_progress_is_new(self):
        resolved = [finding("F-a", "resolved"), finding("F-b")]
        cases = {
            # An earlier failing item still fails.
            "still failing": ledger.observe(validation(checks=[("pytest", 1)]), resolved, []),
            # An earlier failing check was not run again, so nothing shows it passes.
            "not rechecked": ledger.observe(validation(), resolved, []),
            # The earlier finding is still open.
            "still open": ledger.observe(validation(checks=[("pytest", 0)]), [finding("F-a"), finding("F-b")], []),
        }
        for name, seen in cases.items():
            with self.subTest(name):
                self.assertEqual("new", ledger.assess([row("e1", self.first)], "e2", seen))

    def test_without_failing_items_a_failure_is_neither_a_repeat_nor_progress(self):
        empty = ledger.observe(None, None, ["app.py"])
        self.assertEqual("new", ledger.assess([row("e1", empty)], "e2", empty))
        later = ledger.observe(validation(checks=[("pytest", 1)]), [], [])
        self.assertEqual("new", ledger.assess([row("e1", empty)], "e2", later))

    def test_a_failure_recorded_before_the_ledger_rules_out_progress(self):
        second = ledger.observe(validation(checks=[("pytest", 0)]), [finding("F-a", "resolved"), finding("F-b")], [])
        self.assertEqual("new", ledger.assess([{"evidence": "e0"}, row("e1", self.first)], "e2", second))


if __name__ == "__main__":
    unittest.main()
