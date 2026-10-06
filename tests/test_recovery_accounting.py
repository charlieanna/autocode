"""The automatic-recovery budget (tools/autocode_recovery_accounting.py)."""
import copy
import unittest

import autocode_recovery_accounting as accounting


class SpentTests(unittest.TestCase):
    def test_a_run_saved_before_the_aggregate_counter_counts_its_larger_older_counter(self):
        self.assertEqual(2, accounting.spent({"consecutive_timeout_recoveries": 1, "no_progress_batches": 2,
                                              "automatic_timeout_recoveries": [{}]}))
        self.assertEqual(0, accounting.spent({}))

    def test_unchanged_implementation_batches_are_not_recoveries(self):
        """#448: a run that never recovered paused as recovery-exhausted at its third unchanged batch.

        Every counted recovery writes the aggregate key, so its absence with no timeout history
        means none was spent; the no-progress limit, not the recovery budget, governs those batches.
        """
        self.assertEqual(0, accounting.spent({"no_progress_batches": 3}))
        self.assertEqual(0, accounting.spent({"no_progress_batches": 3, "automatic_permission_recoveries": [{}]}))
        self.assertEqual(1, accounting.spent({"no_progress_batches": 3, "consecutive_timeout_recoveries": 1}))
        self.assertEqual(2, accounting.spent({"no_progress_batches": 3, "automatic_recoveries_since_resume": 2}))

    def test_reading_writes_nothing(self):
        # An issued request binds these keys' raw values: a default written on read would make it stale.
        state = {"consecutive_timeout_recoveries": 1, "no_progress_batches": 2, "automatic_timeout_recoveries": [{}]}
        before = copy.deepcopy(state)
        accounting.spent(state)
        accounting.consecutive_timeouts(state)
        self.assertEqual(before, state)

    def test_counting_starts_from_what_a_legacy_run_spent(self):
        state = {"consecutive_timeout_recoveries": 2}
        accounting.count(state)
        self.assertEqual(3, state["automatic_recoveries_since_resume"])
        accounting.count(state)
        self.assertEqual(4, accounting.spent(state))


class GrantTests(unittest.TestCase):
    def test_a_grant_lowers_the_count_resets_the_timeout_run_and_keeps_a_receipt(self):
        state = {"automatic_recoveries_since_resume": 3, "consecutive_timeout_recoveries": 2}
        self.assertEqual(1, accounting.record_grant(state, 2, "req-1", 3))
        self.assertEqual(1, accounting.spent(state))
        self.assertEqual(0, accounting.consecutive_timeouts(state))
        receipt = state["recovery_grants"][-1]
        self.assertEqual({"actor": "user_cli", "amount": 2, "request_id": "req-1",
                          "previous_count": 3, "remaining_count": 1},
                         {key: value for key, value in receipt.items() if key != "at"})

    def test_a_grant_larger_than_the_count_leaves_none_counted(self):
        state = {"automatic_recoveries_since_resume": 3}
        self.assertEqual(0, accounting.record_grant(state, 5, None, 3))
        self.assertEqual(0, accounting.spent(state))


class StageSavedTests(unittest.TestCase):
    def test_a_saved_stage_ends_the_timeout_run_but_not_the_budget(self):
        state = {"automatic_recoveries_since_resume": 2, "consecutive_timeout_recoveries": 2}
        accounting.stage_saved(state)
        self.assertEqual((2, 0), (accounting.spent(state), accounting.consecutive_timeouts(state)))


if __name__ == "__main__":
    unittest.main()
