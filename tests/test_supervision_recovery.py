"""Pure ownership recovery policy, including terminal responses at the deadline."""

import copy
import unittest

import autocode_supervision_recovery as policy


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.record = {"supervision": {"nonce": "bound"}, "finished_at": "collected", "exit_code": 0}
        self.receipt = {"phase": "stopped", "cause": "controller_finished", "cleanup_error": None}

    def hold(self, record=None, receipt=None):
        return policy.hold(
            self.record if record is None else record,
            self.receipt if receipt is None else receipt,
            attempt="001/builder-01",
        )

    def test_ordinary_collected_result_can_reconcile_without_mutation(self):
        before = copy.deepcopy((self.record, self.receipt))
        self.assertIsNone(self.hold())
        self.assertEqual(before, (self.record, self.receipt))

    def test_terminal_response_and_exit_zero_do_not_override_independent_deadline(self):
        self.receipt["cause"] = "stage_deadline"
        held = self.hold()
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", held["status"])
        self.assertTrue(held["timed_out"])

    def test_saved_timeout_remains_a_timeout_after_normal_cleanup(self):
        self.record["timed_out"] = True
        held = self.hold()
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", held["status"])
        self.assertTrue(held["timed_out"])

    def test_existing_timeout_explanation_survives_the_supervised_hold(self):
        self.record.update(timed_out=True, timeout_reason="Builder exceeded its inactivity limit")
        before = copy.deepcopy(self.record)
        held = self.hold()
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", held["status"])
        self.assertTrue(held["reason"].startswith(self.record["timeout_reason"]))
        self.assertIn("not accepted as a successful result", held["reason"])
        self.assertEqual(before, self.record)

    def test_unsaved_exit_is_uncertain_even_with_deadline_and_terminal_response(self):
        del self.record["finished_at"]
        self.receipt["cause"] = "stage_deadline"
        held = self.hold()
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", held["status"])
        self.assertFalse(held["timed_out"])
        self.assertIn("--abandon-stage 001/builder-01", held["reason"])

    def test_finished_checkpoint_without_collected_integer_exit_remains_uncertain(self):
        for code in (None, False, True, "0"):
            with self.subTest(exit_code=code):
                record = {**self.record, "exit_code": code}
                held = self.hold(record)
                self.assertEqual("PAUSED_UNCERTAIN_STAGE", held["status"])
                self.assertFalse(held["timed_out"])
                self.assertIn("--abandon-stage", held["reason"])
        record = {key: value for key, value in self.record.items() if key != "exit_code"}
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", self.hold(record)["status"])

    def test_interrupted_record_and_owner_loss_never_adopt_a_collected_response(self):
        for record, receipt in (
            ({**self.record, "interrupted": True}, self.receipt),
            (self.record, {**self.receipt, "cause": "owner_lost"}),
        ):
            with self.subTest(record=record, receipt=receipt):
                self.assertEqual("PAUSED_UNCERTAIN_STAGE", self.hold(record, receipt)["status"])

    def test_missing_uncertain_or_failed_cleanup_stays_unknown(self):
        for receipt in (
            None,
            {},
            {**self.receipt, "phase": "armed"},
            {**self.receipt, "phase": "uncertain"},
            {**self.receipt, "cleanup_error": "denied"},
            {**self.receipt, "cause": "lifeline_failure"},
        ):
            with self.subTest(receipt=receipt):
                held = policy.hold(self.record, receipt, attempt="001/builder-01")
                self.assertEqual("PAUSED_UNCERTAIN_STAGE", held["status"])

    def test_legacy_record_uses_existing_legacy_reconciliation(self):
        self.assertIsNone(policy.hold({"finished_at": "collected"}, None, attempt="legacy"))
