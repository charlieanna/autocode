"""A stage pauses for repeated failure only when it keeps failing the same way."""
from pathlib import Path
import tempfile
import unittest

import autocode_check_replay as check_replay
import autocode_failures as failures
from . import test_autocode as base, test_report_repair

runner, support = base.runner, base.s

# Three different problems that all surface as ValueError from the same stage and source.
DISTINCT = ("Missing response to concern P1", "Missing response to concern P2", "Malformed test receipt P3")
RUN = "/work/project/.autocode/runs/20261005-015924-build-greeting-91cf522b"
RECEIPT = f"{RUN}/check-replay/validator-01-{'a1' * 16}"


class NormalizeTests(unittest.TestCase):
    """What differs only per attempt never makes two errors differ (#254); what names the failure does."""

    def normal(self, text):
        return failures.normalize(text, roots=failures.run_roots(f"{RUN}/iterations/001/validator-01.json"))

    def test_per_attempt_noise_collapses(self):
        pairs = {
            "another attempt's replay receipt": (f"Receipt: {RECEIPT}/replay.json.",
                                                 f"Receipt: {RUN}/check-replay/validator-report-repair-02-{'f0' * 16}/replay.json."),
            "an attempt's artifacts": (f"see {RUN}/iterations/001/validator-01.jsonl",
                                       f"see {RUN}/iterations/004/validator-report-repair-01.jsonl"),
            "a replayed check's directory": (f"{RECEIPT}/check-01/scratch/tree/test_greet.py:3: AssertionError",
                                             f"{RECEIPT}/check-02/scratch/tree/test_greet.py:3: AssertionError"),
            "an archived attempt": (f"{RUN}/iterations/001/archived-validator-01-569a91/validator-01.json",
                                    f"{RUN}/iterations/001/archived-validator-report-repair-01-0f3e2d/validator-report-repair-01.json"),
            "a mkdtemp directory": ("No such file: /tmp/tmpab12cd_9/out.log", "No such file: /tmp/tmpzz98_k7q/out.log"),
            "a pytest temp directory": ("/tmp/pytest-of-u/pytest-7/test_a0/x", "/tmp/pytest-of-ci/pytest-12/test_a0/x"),
            "a macOS temp directory": ("/var/folders/ab/cd12/T/x.json", "/private/var/folders/zz/yy/T/x.json"),
            "a UUID in either case": ("session 123e4567-e89b-12d3-a456-426614174000 failed",
                                      "session 9F3E4567-E89B-12D3-A456-4266141740AB failed"),
            "an ISO timestamp": ("stopped at 2026-10-05T01:59:24.123456+00:00", "stopped at 2026-10-06T11:00:00Z"),
            "a unittest duration": ("FAILED (failures=1) Ran 3 tests in 0.004s", "FAILED (failures=1) Ran 3 tests in 0.017s"),
            "a pytest duration": ("1 failed, 2 passed in 0.12s", "1 failed, 2 passed in 3.40 seconds"),
        }
        for name, (first, second) in pairs.items():
            with self.subTest(name):
                self.assertEqual(self.normal(first), self.normal(second))

    def test_what_names_the_failure_stays_distinct(self):
        pairs = {
            "the command": ("Check `pytest tests/test_a.py` exited 1", "Check `pytest tests/test_b.py` exited 1"),
            "the test id": ("FAILED tests/test_a.py::test_one", "FAILED tests/test_a.py::test_two"),
            "the exit code": ("it exited 1", "it exited 2"),
            "the line number": ('File "greet.py", line 12', 'File "greet.py", line 13'),
            "the concern id": DISTINCT[:2],
            "a repository file inside the replay tree": (f"{RECEIPT}/check-01/scratch/tree/test_v2.py",
                                                         f"{RECEIPT}/check-01/scratch/tree/test_v3.py"),
            "distinct files under /tmp": ("cannot read /tmp/a.log", "cannot read /tmp/b.log"),
            "a configured timeout": ("timed out after 900 seconds", "timed out after 1800 seconds"),
        }
        for name, (first, second) in pairs.items():
            with self.subTest(name):
                self.assertNotEqual(self.normal(first), self.normal(second))


class ReplayRejectionTests(unittest.TestCase):
    """The runner's own replay error for each attempt, with each attempt's own receipt directory."""

    def test_an_identical_replay_failure_of_an_original_and_its_repairs_stalls(self):
        self.assertIsNotNone(self.rejections("test -f .autocode/validator-only"))

    def test_the_same_noise_with_different_commands_never_stalls(self):
        self.assertIsNone(self.rejections("test -f one", "test -f two", "test -f three"))

    def rejections(self, *commands):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        run = Path(temp.name) / "runs" / "20261005-build"
        state = {"stages": [], "run_dir": str(run)}
        for index, stem in enumerate(("validator-01", "validator-report-repair-01", "validator-report-repair-02")):
            row = {"stage": "sol_report_repair" if index else "sol", "original_stage": "sol", "iteration": 1,
                   "source_revision": "r1", "output": str(run / "iterations" / "001" / f"{stem}.json")}
            command = commands[min(index, len(commands) - 1)]
            with self.assertRaises(ValueError) as rejected:
                check_replay.replay([{"command": command, "exit_code": 0, "evidence_ref": "event:check"}],
                                    Path(temp.name), run, row, lambda *_, **__: {"exit_code": 1, "tail": ""})
            self.assertIn(f"check-replay/{stem}-", str(rejected.exception))
            entry = failures.record(state, row, rejected.exception, "t")
            state["stages"].append(row)
        self.assertEqual(3 if len(commands) == 1 else 1, entry["streak"])
        return failures.repeated(state, row)


class LedgerTests(unittest.TestCase):
    """Pure ledger logic over in-memory stage rows; no files, no provider."""

    def setUp(self):
        self.state = {"stages": []}
        self.iteration = 0

    def attempt(self, error, *, revision="r1", stage="glm_revise"):
        self.iteration += 1
        row = {"stage": stage, "iteration": self.iteration, "source_revision": revision,
               "output": f"/run/iterations/{self.iteration:03d}/{stage}-01.json"}
        text = error(row) if callable(error) else error
        entry = failures.record(self.state, row, ValueError(text), "t")
        self.state["stages"].append(row)
        return row, entry

    def succeed(self, stage="glm_revise"):
        self.iteration += 1
        self.state["stages"].append({"stage": stage, "iteration": self.iteration,
                                     "output": f"/run/iterations/{self.iteration:03d}/{stage}-01.json"})

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
        self.state["failure_history"] = {"k": {"identity": {"stage": "terra", "artifact_hash": "r1",
                                                            "error_class": "ValueError"},
                                               "count": 3, "attempts": ["a", "b", "c"]}}
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
        entry, = self.state["failure_history"].values()
        self.assertEqual(3, entry["count"])

    def test_the_same_rejection_three_times_running_pauses_as_repeated(self):
        self.state["settings"]["report_repair"] = {"max_attempts": 0}
        statuses = [self.reject(DISTINCT[0], iteration) for iteration in (5, 6, 7)]
        self.assertEqual(["PAUSED_INVALID_OUTPUT", "PAUSED_INVALID_OUTPUT", "PAUSED_REPEATED_FAILURE"], statuses)

    def test_a_stalled_rejection_is_never_repaired_while_attempts_remain(self):
        # #254: a streak carried over from earlier attempts stalls a fresh execution at once.
        self.state["settings"]["report_repair"] = {"max_attempts": 0}
        self.reject(DISTINCT[0], 5)
        self.reject(DISTINCT[0], 6)
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        self.assertEqual("PAUSED_REPEATED_FAILURE", self.reject(DISTINCT[0], 7))
        self.assertNotIn("pending_report_repair", self.state)
        # A different rejection afterwards is new information and gets its repairs.
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, self.stage_record(iteration=8), ValueError(DISTINCT[1]))

    reject_repair = test_report_repair.RepairTests.reject_repair

    def test_an_open_repair_round_stops_at_the_stall_and_keeps_its_evidence_paired(self):
        self.state["settings"]["report_repair"] = {"max_attempts": 0}
        self.reject(DISTINCT[0], 5)
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        with self.assertRaises(runner.ReportRepairQueued):
            runner.reject_completed_stage(self.state, self.run, self.stage_record(iteration=6), ValueError(DISTINCT[0]))
        self.state["pending_report_repair"]["attempts"] = 1  # one repair dispatched, one left
        with self.assertRaises(support.Paused) as caught:
            self.reject_repair(7, ValueError(DISTINCT[0]))
        self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)
        pending = self.state["pending_report_repair"]
        self.assertEqual(1, pending["attempts"])
        self.assertEqual(7, pending["latest_rejected"]["iteration"])
        self.assertEqual(DISTINCT[0], pending["error"])
        self.assertTrue(all(Path(path).is_file() for path in pending["pins"]))


if __name__ == "__main__":
    unittest.main()
