"""Which issue runs hold a slot, and which waiting issue starts next (autocode_issue_admission)."""

import unittest

import autocode_issue_admission as admission


class SettledTests(unittest.TestCase):
    def test_a_run_waiting_for_a_person_keeps_its_slot(self):
        for kind in ("answer", "approve_plan", "review", "resume", "planning_budget", "dependency"):
            with self.subTest(kind=kind):
                self.assertFalse(admission.settled({"status": "PAUSED", "needs": {"kind": kind}}))

    def test_a_working_run_keeps_its_slot(self):
        for status in admission.WORKING_STATUSES:
            with self.subTest(status=status):
                self.assertFalse(admission.settled({"status": status}))
        self.assertFalse(admission.settled({"status": "RUNNING", "needs": {"kind": "continue"}}))

    def test_a_finished_or_stopped_run_frees_its_slot(self):
        self.assertTrue(admission.settled({"status": "COMPLETE", "done": True}))
        self.assertTrue(admission.settled({"status": "STOPPED"}))
        self.assertTrue(admission.settled({"status": "STOPPED", "needs": {"kind": "continue"}}))

    def test_a_run_nothing_can_resume_frees_its_slot(self):
        view = {"status": "PAUSED", "needs": {"kind": "resume", "new_run_required": True}}
        self.assertTrue(admission.settled(view))


class LimitTests(unittest.TestCase):
    def test_a_blank_limit_is_no_limit(self):
        self.assertIsNone(admission.parse_limit(None))
        self.assertIsNone(admission.parse_limit("  "))

    def test_a_limit_is_a_whole_number_of_runs(self):
        self.assertEqual(3, admission.parse_limit(" 3 "))
        for text in ("0", "-1", "two", "1.5"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "1 or more"):
                admission.parse_limit(text)


class AdmitTests(unittest.TestCase):
    def test_no_limit_starts_every_waiting_issue(self):
        decision = admission.admit([7, 8, 9], active=5, limit=None)
        self.assertEqual(((7, 8, 9), ()), (decision.start, decision.waiting))

    def test_waiting_issues_start_in_order_into_the_free_slots(self):
        decision = admission.admit([7, 8, 9], active=1, limit=3)
        self.assertEqual(((7, 8), (9,)), (decision.start, decision.waiting))

    def test_a_full_bridge_starts_nothing(self):
        decision = admission.admit([7], active=2, limit=2)
        self.assertEqual(((), (7,)), (decision.start, decision.waiting))

    def test_more_active_runs_than_the_limit_start_nothing(self):
        # A follow-up on a delivered run takes its slot again, so active can pass the limit.
        decision = admission.admit([7], active=4, limit=2)
        self.assertEqual(((), (7,)), (decision.start, decision.waiting))
