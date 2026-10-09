"""Offline pure policy tests, not runtime integration or authority coverage."""
import json
import unittest
from copy import deepcopy

import autocode_progressive_budget as budget


def first_pool(**settings):
    return budget.allocate(budget.new_ledger(**settings), "allocation-1", "pool-1",
                           approved_work=["approved-unit-1"])


class ProgressiveBudgetTests(unittest.TestCase):
    def test_defaults_and_review_exhaustion(self):
        ledger = first_pool()
        self.assertEqual(43200, ledger["run_limit"])
        self.assertEqual(5400, ledger["pools"]["pool-1"]["seconds_limit"])
        ledger = budget.admit(ledger, "review-1", "pool-1", review=True)
        ledger = budget.admit(ledger, "review-2", "pool-1", review=True)
        with self.assertRaisesRegex(budget.BudgetExhausted, "reviews"):
            budget.admit(ledger, "review-3", "pool-1", review=True)
        self.assertTrue(budget.check_budget(ledger, "pool-1")["allowed"])

    def test_initial_usage_seeded_once_without_aggregate_double_charge(self):
        ledger = budget.new_ledger(run_seconds_used=600)
        args = dict(approved_work=["approved-unit-1"], seed_reviews=2, seed_seconds=600)
        seeded = budget.allocate(ledger, "initial", "pool-1", **args)
        self.assertEqual(seeded, budget.allocate(seeded, "initial", "pool-1", **args))
        self.assertEqual(600, seeded["run_seconds"])
        self.assertEqual(600, seeded["pools"]["pool-1"]["seconds_used"])
        with self.assertRaises(budget.BudgetExhausted):
            budget.admit(seeded, "repeat-first-review", "pool-1", review=True)
        with self.assertRaises(ValueError):
            budget.allocate(seeded, "double-seed", "pool-2", approved_work=["unit-2"],
                            seed_seconds=600)
        self.assertEqual({}, ledger["pools"])

    def test_genuine_unallocated_work_gets_capacity_not_new_labels(self):
        ledger = first_pool()
        ledger = budget.admit(ledger, "r1", "pool-1", review=True)
        ledger = budget.admit(ledger, "r2", "pool-1", review=True)
        with self.assertRaises(ValueError):
            budget.allocate(ledger, "NEW", "renamed")
        with self.assertRaises(ValueError):
            budget.allocate(ledger, "replacement", "renamed",
                            approved_work=["approved-unit-1"])
        ledger = budget.allocate(ledger, "second", "pool-2", approved_work=["unit-2"])
        self.assertTrue(budget.check_budget(ledger, "pool-2", review=True)["allowed"])
        self.assertFalse(budget.check_budget(ledger, "pool-1", review=True)["allowed"])

    def test_split_retry_and_replacement_share_pool(self):
        ledger = first_pool()
        for allocation in ("split-left", "split-right", "retry", "replacement"):
            ledger = budget.allocate(ledger, allocation, "pool-1",
                                     inherit_work=["approved-unit-1"])
        ledger = budget.admit(ledger, "left-review", "pool-1", review=True)
        ledger = budget.admit(ledger, "right-review", "pool-1", review=True)
        with self.assertRaises(budget.BudgetExhausted):
            budget.admit(ledger, "retry-review", "pool-1", review=True)
        with self.assertRaises(ValueError):
            budget.allocate(ledger, "refill", "pool-1", inherit_work=["approved-unit-1"],
                            seed_reviews=1)
        with self.assertRaises(ValueError):
            budget.allocate(ledger, "unknown", "pool-1", inherit_work=["unknown"])
        ledger = budget.allocate(ledger, "second", "pool-2", approved_work=["unit-2"])
        with self.assertRaises(ValueError):
            budget.allocate(ledger, "ambiguous", "pool-1",
                            inherit_work=["approved-unit-1", "unit-2"])

    def test_allocation_and_admission_conflicts_fail_closed(self):
        ledger = first_pool()
        with self.assertRaises(ValueError):
            budget.allocate(ledger, "allocation-1", "pool-1", approved_work=["other"])
        ledger = budget.admit(ledger, "attempt", "pool-1", review=True)
        ledger = budget.admit(ledger, "second", "pool-1", review=True)
        restarted = json.loads(json.dumps(ledger))
        self.assertEqual(ledger, budget.admit(restarted, "attempt", "pool-1", review=True))
        with self.assertRaises(ValueError):
            budget.admit(ledger, "attempt", "pool-1", review=False)
        ledger = budget.allocate(ledger, "a2", "pool-2", approved_work=["unit-2"])
        with self.assertRaises(ValueError):
            budget.admit(ledger, "attempt", "pool-2", review=True)

    def test_timeout_and_nonzero_refund_once_never_time(self):
        for outcome in ({"exit_code": 1}, {"exit_code": -15, "timed_out": True},
                        {"exit_code": 0, "timed_out": True}):
            with self.subTest(outcome=outcome):
                ledger = budget.admit(first_pool(), "attempt", "pool-1", review=True)
                ledger = budget.record_time(ledger, "time", "attempt", 30)
                refunded = budget.refund_review(ledger, "attempt", **outcome)
                self.assertEqual(refunded, budget.refund_review(refunded, "attempt", **outcome))
                self.assertEqual(0, refunded["pools"]["pool-1"]["reviews_used"])
                self.assertEqual(30, refunded["pools"]["pool-1"]["seconds_used"])
                self.assertEqual(30, refunded["run_seconds"])
                self.assertEqual(refunded, budget.admit(refunded, "attempt", "pool-1", review=True))

    def test_exit_zero_rejected_review_counts_and_outcome_is_immutable(self):
        ledger = budget.admit(first_pool(), "rejected-report", "pool-1", review=True)
        ledger = budget.refund_review(ledger, "rejected-report", exit_code=0)
        self.assertEqual(1, ledger["pools"]["pool-1"]["reviews_used"])
        with self.assertRaises(ValueError):
            budget.refund_review(ledger, "rejected-report", exit_code=1)
        with self.assertRaises(KeyError):
            budget.refund_review(ledger, "unadmitted", exit_code=1)

    def test_repair_consumes_time_not_review(self):
        ledger = budget.admit(first_pool(), "repair", "pool-1")
        ledger = budget.record_time(ledger, "repair-time", "repair", 100)
        ledger = budget.refund_review(ledger, "repair", exit_code=1)
        self.assertEqual(0, ledger["pools"]["pool-1"]["reviews_used"])
        self.assertEqual(100, ledger["run_seconds"])

    def test_late_time_receipt_bound_to_launch_pool_and_idempotent(self):
        ledger = budget.admit(first_pool(), "old-attempt", "pool-1")
        ledger = budget.allocate(ledger, "next", "pool-2", approved_work=["unit-2"])
        ledger = budget.admit(ledger, "new-attempt", "pool-2")
        recorded = budget.record_time(ledger, "receipt", "old-attempt", 42)
        self.assertEqual(recorded, budget.record_time(recorded, "receipt", "old-attempt", 42))
        self.assertEqual(42, recorded["pools"]["pool-1"]["seconds_used"])
        self.assertEqual(0, recorded["pools"]["pool-2"]["seconds_used"])
        for receipt, attempt, seconds in (("receipt", "old-attempt", 43),
                                          ("receipt", "new-attempt", 42),
                                          ("new-receipt", "old-attempt", 42)):
            with self.assertRaises(ValueError):
                budget.record_time(recorded, receipt, attempt, seconds)

    def test_local_and_aggregate_exhaustion_are_separate(self):
        ledger = budget.admit(first_pool(), "a1", "pool-1")
        ledger = budget.record_time(ledger, "t1", "a1", 5400)
        self.assertEqual(["local_seconds"], budget.check_budget(ledger, "pool-1")["exhausted"])
        ledger = budget.allocate(ledger, "a2", "pool-2", approved_work=["unit-2"])
        self.assertTrue(budget.check_budget(ledger, "pool-2")["allowed"])
        ledger = budget.admit(ledger, "a2", "pool-2")
        ledger = budget.record_time(ledger, "t2", "a2", 5400)
        self.assertEqual(10800, ledger["run_seconds"])
        ledger = budget.allocate(ledger, "a3", "pool-3", approved_work=["unit-3"])
        ledger = budget.admit(ledger, "a3", "pool-3")
        # A stage can overshoot; enforcement is at the next stage boundary.
        ledger = budget.record_time(ledger, "t3", "a3", 32400)
        ledger = budget.allocate(ledger, "a4", "pool-4", approved_work=["unit-4"])
        self.assertEqual(["run_seconds"], budget.check_budget(ledger, "pool-4")["exhausted"])
        with self.assertRaisesRegex(budget.BudgetExhausted, "run_seconds"):
            budget.admit(ledger, "blocked", "pool-4")

    def test_configured_limits_and_finite_existing_extension(self):
        ledger = budget.new_ledger(review_limit=4, local_seconds_limit=6000,
                                   run_seconds_limit=50000, limit_provenance="user-event-1")
        ledger = budget.allocate(ledger, "initial", "pool-1", approved_work=["unit-1"],
                                 seed_reviews=4, review_limit=5, provenance="extension-event")
        ledger = budget.admit(ledger, "fifth", "pool-1", review=True)
        with self.assertRaises(budget.BudgetExhausted):
            budget.admit(ledger, "sixth", "pool-1", review=True)
        self.assertEqual("extension-event", ledger["pools"]["pool-1"]["provenance"])
        self.assertEqual(6000, ledger["pools"]["pool-1"]["seconds_limit"])
        self.assertEqual("user-event-1", ledger["run_limit_provenance"])

    def test_finite_recovery_grants_preserve_usage_and_are_not_refunded(self):
        grants = [{"id": "used-grant", "provenance": "resolver-1", "used_by": "old"},
                  {"id": "available-grant", "provenance": "resolver-2"}]
        ledger = budget.allocate(budget.new_ledger(), "initial", "pool-1",
                                 approved_work=["unit-1"], seed_reviews=3,
                                 recovery_grants=grants)
        ledger = budget.admit(ledger, "recovery", "pool-1", review=True,
                              recovery_grant="available-grant")
        ledger = budget.refund_review(ledger, "recovery", exit_code=1)
        self.assertEqual(4, ledger["pools"]["pool-1"]["reviews_used"])
        for grant in ("used-grant", "available-grant", "fabricated"):
            with self.assertRaises(ValueError):
                budget.admit(ledger, "another", "pool-1", review=True, recovery_grant=grant)
        with self.assertRaises(budget.BudgetExhausted):
            budget.admit(ledger, "ordinary", "pool-1", review=True)

    def test_explicit_limit_changes_preserve_usage_and_receipts(self):
        ledger = budget.admit(first_pool(), "attempt", "pool-1", review=True)
        ledger = budget.record_time(ledger, "time", "attempt", 5400)
        original = deepcopy(ledger)
        for kind, limit, pool in (("reviews", 5, "pool-1"),
                                  ("local_seconds", 10000, "pool-1"),
                                  ("run_seconds", 60000, None)):
            with self.assertRaises(ValueError):
                budget.change_limit(ledger, kind, kind, limit, pool_id=pool,
                                    provenance="automatic-default-extension", explicit=False)
            ledger = budget.change_limit(ledger, kind, kind, limit, pool_id=pool,
                                         provenance="user-event", explicit=True)
            self.assertEqual(ledger, budget.change_limit(ledger, kind, kind, limit,
                             pool_id=pool, provenance="user-event", explicit=True))
        self.assertEqual(original["attempts"], ledger["attempts"])
        self.assertEqual(original["time_receipts"], ledger["time_receipts"])
        self.assertEqual(5400, ledger["run_seconds"])
        self.assertEqual(5400, ledger["pools"]["pool-1"]["seconds_used"])
        self.assertEqual(1, ledger["pools"]["pool-1"]["reviews_used"])
        self.assertEqual("user-event", ledger["run_limit_provenance"])
        self.assertTrue(budget.check_budget(ledger, "pool-1", review=True)["allowed"])
        with self.assertRaises(ValueError):
            budget.change_limit(ledger, "run_seconds", "run_seconds", 70000,
                                provenance="user-event", explicit=True)
        self.assertEqual(43200, original["run_limit"])

    def test_zero_limits_are_honored_not_replaced_by_defaults(self):
        ledger = first_pool(review_limit=0, local_seconds_limit=0,
                            run_seconds_limit=0, limit_provenance="explicit-unlimited")
        for index in range(3):
            ledger = budget.admit(ledger, str(index), "pool-1", review=True)
            ledger = budget.record_time(ledger, "time-" + str(index), str(index), 50000)
        self.assertTrue(budget.check_budget(ledger, "pool-1", review=True)["allowed"])

    def test_recovery_grant_cannot_bypass_time_exhaustion(self):
        ledger = budget.allocate(budget.new_ledger(), "initial", "pool-1",
                                 approved_work=["unit-1"], recovery_grants=[
                                     {"id": "grant", "provenance": "resolver"}])
        ledger = budget.admit(ledger, "build", "pool-1")
        ledger = budget.record_time(ledger, "time", "build", 5400)
        with self.assertRaisesRegex(budget.BudgetExhausted, "local_seconds"):
            budget.admit(ledger, "recovery", "pool-1", review=True, recovery_grant="grant")
        self.assertNotIn("used_by", ledger["pools"]["pool-1"]["recovery_grants"]["grant"])

    def test_lower_ceiling_does_not_clear_spent_time(self):
        ledger = budget.admit(first_pool(), "attempt", "pool-1")
        ledger = budget.record_time(ledger, "time", "attempt", 100)
        ledger = budget.change_limit(ledger, "lower", "run_seconds", 50,
                                     provenance="user-event", explicit=True)
        self.assertEqual(100, ledger["run_seconds"])
        self.assertEqual(["run_seconds"], budget.check_budget(ledger, "pool-1")["exhausted"])

    def test_invalid_or_unadmitted_time_receipts_do_not_account(self):
        ledger = budget.admit(first_pool(), "attempt", "pool-1")
        original = deepcopy(ledger)
        for seconds in (-1, float("nan"), float("inf"), True):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                budget.record_time(ledger, "receipt", "attempt", seconds)
        with self.assertRaises(KeyError):
            budget.record_time(ledger, "receipt", "unknown", 10)
        self.assertEqual(original, ledger)

    def test_invalid_numbers_and_unproven_configured_limits(self):
        for value in (-1, float("nan"), float("inf"), True, "5400"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                budget.new_ledger(local_seconds_limit=value, limit_provenance="config")
        with self.assertRaises(ValueError):
            budget.new_ledger(review_limit=2.0)
        with self.assertRaises(ValueError):
            budget.new_ledger(run_seconds_limit=60000)

    def test_all_operations_leave_inputs_unchanged(self):
        ledger = first_pool()
        original = deepcopy(ledger)
        admitted = budget.admit(ledger, "attempt", "pool-1", review=True)
        self.assertEqual(original, ledger)
        original = deepcopy(admitted)
        budget.record_time(admitted, "time", "attempt", 4)
        budget.refund_review(admitted, "attempt", exit_code=1)
        budget.change_limit(admitted, "change", "reviews", 5, pool_id="pool-1",
                            provenance="user", explicit=True)
        self.assertEqual(original, admitted)


if __name__ == "__main__":
    unittest.main()
