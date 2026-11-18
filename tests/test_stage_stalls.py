"""A stage that stops repeatedly on the same source pauses for the Investigator; a person's stop is not a stall."""

import unittest

import autocode_stage_stalls as stage_stalls


def recovery(at, kind="stage"):
    return {"stage": "tester", "source_revision": "r1", "at": at, "timeout_kind": kind}


class RepeatedStopTests(unittest.TestCase):
    def test_a_stage_stopped_twice_on_the_same_source_is_repeated(self):
        state = {
            "automatic_timeout_recoveries": [
                recovery("2026-11-18T01:00:00+00:00"),
                recovery("2026-11-18T02:00:00+00:00"),
            ]
        }
        rows = stage_stalls.repeated(state, "tester", "r1")
        self.assertEqual(2, len(rows))
        self.assertTrue(stage_stalls.escapes_streak(state, rows))

    def test_a_person_stopping_the_stage_never_counts_as_it_stalling(self):
        # Stop now and Send now (#3263, #3259) end the stage through the timeout path; pressing them twice
        # must not send the run to the Investigator as if the Tester had stalled.
        state = {
            "automatic_timeout_recoveries": [
                recovery("2026-11-18T01:00:00+00:00", "person"),
                recovery("2026-11-18T02:00:00+00:00", "person"),
            ]
        }
        rows = stage_stalls.repeated(state, "tester", "r1")
        self.assertEqual([], rows)
        self.assertFalse(stage_stalls.escapes_streak(state, rows))


if __name__ == "__main__":
    unittest.main()
