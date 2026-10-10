"""A stage pauses for repeated failure only when it keeps failing the same way."""

import unittest

import autocode_failures as failures

from . import test_autocode as base
from . import test_report_repair

runner, support = base.runner, base.s

# Three different problems that all surface as ValueError from the same stage and source.
DISTINCT = ("Missing response to concern P1", "Missing response to concern P2", "Malformed test receipt P3")


class LedgerTests(unittest.TestCase):
    """Pure ledger logic over in-memory stage rows; no files, no provider."""

    def setUp(self):
        self.state = {"stages": []}
        self.iteration = 0

    def attempt(self, error, *, revision="r1", stage="glm_revise"):
        self.iteration += 1
        row = {
            "stage": stage,
            "iteration": self.iteration,
            "source_revision": revision,
            "output": f"/run/iterations/{self.iteration:03d}/{stage}-01.json",
        }
        text = error(row) if callable(error) else error
        entry = failures.record(self.state, row, ValueError(text), "t")
        self.state["stages"].append(row)
        return row, entry

    def succeed(self, stage="glm_revise"):
        self.iteration += 1
        self.state["stages"].append(
            {
                "stage": stage,
                "iteration": self.iteration,
                "output": f"/run/iterations/{self.iteration:03d}/{stage}-01.json",
            }
        )

    def test_distinct_errors_of_one_class_are_not_a_stalled_loop(self):
        for text in DISTINCT:
            row, entry = self.attempt(text)
        self.assertIsNone(failures.repeated(self.state, row))
        self.assertIsNone(failures.repeated(self.state, {"stage": "glm_revise", "source_revision": "r1"}))
        # Nothing is forgotten: every attempt stays in the history for inspection.
        self.assertEqual(3, entry["count"])
        self.assertEqual(3, len(entry["attempts"]))
        self.assertEqual(DISTINCT[-1], entry["last_error"])
        self.assertEqual(1, entry["streak"])

    def test_the_same_error_on_consecutive_attempts_is_a_stalled_loop(self):
        for _ in range(3):
            row, entry = self.attempt(DISTINCT[0])
        self.assertIs(entry, failures.repeated(self.state, row))
        self.assertIs(entry, failures.repeated(self.state, {"stage": "glm_revise", "source_revision": "r1"}))
        self.assertEqual(3, entry["streak"])
        self.assertIn("output_probe", entry)

    def test_an_attempts_own_artifact_path_does_not_make_errors_differ(self):
        for _ in range(3):
            row, entry = self.attempt(lambda row: f"Unreadable report {row['output']}")
        self.assertIsNotNone(failures.repeated(self.state, row))

    def test_a_success_between_identical_failures_breaks_the_run(self):
        self.attempt(DISTINCT[0])
        self.attempt(DISTINCT[0])
        self.succeed()
        row, entry = self.attempt(DISTINCT[0])
        self.assertEqual(1, entry["streak"])
        self.assertIsNone(failures.repeated(self.state, row))
        # Another stage's rows in between do not interrupt this stage's attempts.
        self.succeed(stage="astra_challenge")
        self.attempt(DISTINCT[0])
        row, entry = self.attempt(DISTINCT[0])
        self.assertIsNotNone(failures.repeated(self.state, row))

    def test_a_different_error_ends_an_earlier_stalled_run(self):
        for _ in range(3):
            self.attempt(DISTINCT[0])
        row, _ = self.attempt(DISTINCT[1])
        self.assertIsNone(failures.repeated(self.state, row))
        self.assertIsNone(failures.repeated(self.state, {"stage": "glm_revise", "source_revision": "r1"}))

    def test_a_changed_source_starts_a_new_run(self):
        self.attempt(DISTINCT[0])
        self.attempt(DISTINCT[0])
        row, _ = self.attempt(DISTINCT[0], revision="r2")
        self.assertIsNone(failures.repeated(self.state, row))

    def test_the_ledger_carries_the_run_when_rows_are_not_appended(self):
        for n in range(3):
            row = {"stage": "sol", "iteration": n, "source_revision": "r1", "output": f"/run/sol-{n}.json"}
            entry = failures.record(self.state, row, ValueError("invalid validation"), "t")
        self.assertEqual(3, entry["streak"])
        self.assertIsNotNone(failures.repeated(self.state, {"stage": "sol", "source_revision": "r1"}))

    def test_ledgers_saved_before_streaks_existed_keep_their_count(self):
        self.state["failure_history"] = {
            "k": {
                "identity": {"stage": "terra", "artifact_hash": "r1", "error_class": "ValueError"},
                "count": 3,
                "attempts": ["a", "b", "c"],
            }
        }
        self.assertIsNotNone(failures.repeated(self.state, {"failure_key": "k"}))
        self.assertIsNotNone(failures.repeated(self.state, {"stage": "terra", "source_revision": "r1"}))


class PauseTests(unittest.TestCase):
    """The runner's rejection path picks the pause the ledger supports."""

    setUp = base.RetrofitTest.setUp
    tearDown = base.RetrofitTest.tearDown
    stage_record = test_report_repair.RepairTests.stage_record

    def reject(self, text, iteration):
        record = self.stage_record(iteration=iteration)
        with self.assertRaises(support.Paused) as caught:
            runner.reject_completed_stage(self.state, self.run, record, ValueError(text))
        return caught.exception.status

    def test_distinct_rejections_pause_as_invalid_output_not_repetition(self):
        self.state["settings"]["report_repair"] = {"max_attempts": 0}
        statuses = [self.reject(text, iteration) for iteration, text in enumerate(DISTINCT, 5)]
        self.assertEqual(["PAUSED_INVALID_OUTPUT"] * 3, statuses)
        (entry,) = self.state["failure_history"].values()
        self.assertEqual(3, entry["count"])

    def test_the_same_rejection_three_times_running_pauses_as_repeated(self):
        self.state["settings"]["report_repair"] = {"max_attempts": 0}
        statuses = [self.reject(DISTINCT[0], iteration) for iteration in (5, 6, 7)]
        self.assertEqual(["PAUSED_INVALID_OUTPUT", "PAUSED_INVALID_OUTPUT", "PAUSED_REPEATED_FAILURE"], statuses)


if __name__ == "__main__":
    unittest.main()
