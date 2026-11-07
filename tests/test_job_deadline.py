"""#2605: which stopped workflow jobs AutoCode retries itself on a longer stage deadline."""

import unittest

import autocode_job_deadline as job_deadline
import autocode_quota_route as quota_route


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

    def test_a_retry_on_an_extended_deadline_doubles_its_own_deadline(self):
        # #2823: the run's limit stays 900; the stopped retry ran on 1800, so the next one gets 3600.
        run = state(events=[extended()])
        self.assertEqual(
            {"from": 1800, "to": 3600, "extension": 2, "of": 2},
            job_deadline.plan(run, FAILURE, RECOVERY, abandoned=False, deadline=1800),
        )
        self.assertIsNone(job_deadline.plan(run, FAILURE, RECOVERY, abandoned=False, deadline=0))

    def test_the_reason_names_both_deadlines_and_the_bound(self):
        text = job_deadline.reason("Investigator", {"from": 900, "to": 1800, "extension": 1, "of": 2})
        self.assertIn("900-second", text)
        self.assertIn("1800-second", text)
        self.assertIn("extension 1 of 2", text)
        self.assertIn("same model", text)

    def test_an_exhausted_job_is_told_its_retry_is_all_that_is_left(self):
        self.assertIn("on 2 longer deadlines on the same model", job_deadline.exhausted("Investigator", 2))
        self.assertIn("on 1 longer deadline on", job_deadline.exhausted("Investigator", 1))


class LaunchLimitTests(unittest.TestCase):
    """#2823: an extended deadline belongs to the stopped job's authorized retry, never to the run."""

    def run_state(self, **authorization):
        return {
            "job_failure": {
                "stage": "investigate_stuck",
                "job_retry_token": "jr:1",
                "retry_stage_timeout_seconds": 480,
            },
            "job_retry_authorization": {
                "token": "jr:1",
                "stage": "investigate_stuck",
                "consumed": False,
                **authorization,
            },
        }

    def test_the_authorized_retry_runs_on_its_own_deadline(self):
        self.assertEqual(480, job_deadline.launch_limit(self.run_state(), "investigate_stuck", 120))

    def test_every_other_launch_keeps_the_runs_limit(self):
        run = self.run_state()
        for stage, kwargs in (("astra_discovery", {}), ("terra", {}), ("investigate_stuck", {"report_only": True})):
            with self.subTest(stage=stage, **kwargs):
                self.assertEqual(120, job_deadline.launch_limit(run, stage, 120, **kwargs))
        for authorization in ({"consumed": True}, {"token": "jr:old"}, {"stage": "review_change"}):
            with self.subTest(authorization=authorization):
                self.assertEqual(
                    120, job_deadline.launch_limit(self.run_state(**authorization), "investigate_stuck", 120)
                )
        self.assertEqual(120, job_deadline.launch_limit({}, "investigate_stuck", 120))
        unsaved = self.run_state()
        unsaved["job_failure"].pop("retry_stage_timeout_seconds")
        self.assertEqual(120, job_deadline.launch_limit(unsaved, "investigate_stuck", 120))


class DeadlineStopTests(unittest.TestCase):
    def test_a_stage_deadline_is_never_a_model_stop(self):
        stop = {"kind": "timeout", "timeout_kind": "stage"}
        self.assertTrue(quota_route.deadline_stop(stop))
        self.assertIsNone(quota_route.job_stop_status(stop))
        # A failure saved with its model question before #2605 keeps it.
        saved = dict(stop, pause_status=quota_route.TIMEOUT_STATUS)
        self.assertEqual(quota_route.TIMEOUT_STATUS, quota_route.job_stop_status(saved))

    def test_other_limits_and_older_timeouts_keep_their_model_question(self):
        for stop in ({"kind": "timeout", "timeout_kind": "progress"}, {"kind": "timeout"}):
            with self.subTest(stop=stop):
                self.assertFalse(quota_route.deadline_stop(stop))
                self.assertEqual(quota_route.TIMEOUT_STATUS, quota_route.job_stop_status(stop))
        self.assertFalse(quota_route.deadline_stop({"kind": "exit", "timeout_kind": "stage"}))


if __name__ == "__main__":
    unittest.main()
