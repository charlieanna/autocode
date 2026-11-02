"""#2605: which stopped workflow jobs AutoCode retries itself on a longer stage deadline."""

import unittest

import autocode_job_deadline as job_deadline


def state(stage_seconds=900, max_seconds=5000, active=100, events=()):
    return {
        "settings": {"limits": {"stage_timeout_seconds": stage_seconds, "max_seconds": max_seconds}},
        "active_seconds": active,
        "user_events": list(events),
    }


FAILURE = {"kind": "timeout", "stage": "investigate_bug", "attempt_id": "001/investigator-01"}
RECOVERY = {"timeout_kind": "stage", "attempt_id": "001/investigator-01"}


def extended(stage="investigate_bug"):
    return {"kind": job_deadline.EVENT, "stage": stage}


class PlanTests(unittest.TestCase):
    def plan(self, run=None, failure=FAILURE, recovery=RECOVERY, abandoned=False):
        return job_deadline.plan(state() if run is None else run, failure, recovery, abandoned=abandoned)

    def test_a_hard_stage_deadline_doubles_on_the_same_route(self):
        self.assertEqual({"from": 900, "to": 1800, "extension": 1, "of": 2}, self.plan())

    def test_the_extension_never_passes_the_runs_remaining_time(self):
        self.assertEqual(1200, self.plan(state(max_seconds=1300, active=100))["to"])
        self.assertIsNone(self.plan(state(max_seconds=1000, active=100)))
        self.assertEqual(1800, self.plan(state(max_seconds=None))["to"])

    def test_each_job_has_two_extensions_in_a_run(self):
        self.assertEqual(2, self.plan(state(events=[extended()]))["extension"])
        self.assertIsNone(self.plan(state(events=[extended(), extended()])))
        self.assertEqual(1, self.plan(state(events=[extended("review_change")] * 2))["extension"])
        self.assertEqual(1, job_deadline.used(state(events=[extended(), {"kind": "other"}, "bad"]), "investigate_bug"))

    def test_other_stops_are_not_settled_here(self):
        for failure, recovery, abandoned in (
            (dict(FAILURE, kind="exit"), RECOVERY, False),
            (FAILURE, dict(RECOVERY, timeout_kind="idle"), False),
            (FAILURE, dict(RECOVERY, attempt_id="other"), False),
            (FAILURE, RECOVERY, True),
        ):
            with self.subTest(failure=failure, recovery=recovery, abandoned=abandoned):
                self.assertIsNone(self.plan(failure=failure, recovery=recovery, abandoned=abandoned))

    def test_an_unset_or_invalid_deadline_is_left_alone(self):
        for seconds in (None, 0, -5, 900.0, True):
            with self.subTest(seconds=seconds):
                self.assertIsNone(self.plan(state(stage_seconds=seconds)))

    def test_the_reason_names_both_deadlines_and_the_bound(self):
        text = job_deadline.reason("Investigator", {"from": 900, "to": 1800, "extension": 1, "of": 2})
        self.assertIn("900-second", text)
        self.assertIn("1800-second", text)
        self.assertIn("extension 1 of 2", text)
        self.assertIn("same model", text)


if __name__ == "__main__":
    unittest.main()
