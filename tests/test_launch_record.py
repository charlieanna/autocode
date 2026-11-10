"""The note a new run leaves about its own start, before it has a checkpoint to answer with.

It is display only: it names no action, and nothing that decides what a run does reads it. These
checks cover what a client can learn from it — the phase, how long the start has taken, and whether
the launcher that wrote it is still reporting.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_launch_record as launch_record


class Fixture(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        self.workspace = self.root / "project"
        self.run = self.root / "project" / ".autocode" / "runs" / "20261109-120000-chat-abcdef"
        self.run.mkdir(parents=True)  # a launch reserves the directory before it has anything to say

    def test_the_note_says_the_phase_it_was_asked_and_who_wrote_it(self):
        launch_record.write(self.run, "preparing_run", workspace=self.workspace, now="2026-11-09T12:00:00+00:00")
        note = launch_record.read(self.run)
        self.assertEqual(1, note["version"])
        self.assertEqual("preparing_run", note["phase"])
        self.assertEqual("preparing the run", note["label"])
        self.assertEqual("2026-11-09T12:00:00+00:00", note["at"])
        self.assertEqual(note["at"], note["started_at"], "the note's own clock is the start it counts from")
        self.assertEqual(str(self.workspace.resolve()), note["workspace"])
        self.assertEqual(str(self.run.resolve()), note["run_dir"])
        self.assertEqual(os.getpid(), note["pid"])
        self.assertTrue(note["owner_alive"], "this process wrote it and is still here")

    def test_a_later_phase_keeps_the_time_the_start_began(self):
        launch_record.write(self.run, "preparing_run", workspace=self.workspace, now="2026-11-09T12:00:00+00:00")
        launch_record.write(self.run, "resolving_models", workspace=self.workspace, now="2026-11-09T12:00:41+00:00")
        note = launch_record.read(self.run)
        self.assertEqual("resolving_models", note["phase"])
        self.assertEqual("resolving which model each stage runs", note["label"])
        self.assertEqual("2026-11-09T12:00:00+00:00", note["started_at"])
        self.assertEqual("2026-11-09T12:00:41+00:00", note["at"])

    def test_a_phase_the_core_does_not_name_is_refused(self):
        with self.assertRaises(ValueError) as refused:
            launch_record.write(self.run, "starting_builder", workspace=self.workspace)
        self.assertIn("unknown launch phase", str(refused.exception))
        self.assertIsNone(launch_record.read(self.run))

    def test_only_a_note_of_the_version_the_clients_read_answers(self):
        for body, why in (
            ({}, "empty"),
            ({"version": 2, "phase": "preparing_run"}, "a newer version than this client reads"),
            ({"version": 1, "phase": "did_something"}, "a phase the core does not name"),
            ({"phase": "preparing_run"}, "no version"),
        ):
            with self.subTest(why=why):
                launch_record.path(self.run).write_text(json.dumps(body))
                self.assertIsNone(launch_record.read(self.run), why)
        for unparseable in ("", "{trunc", "not json at all"):
            with self.subTest(body=unparseable):
                launch_record.path(self.run).write_text(unparseable)
                self.assertIsNone(launch_record.read(self.run), "a half-written note answers nothing")

    def test_a_run_that_has_saved_nothing_answers_nothing(self):
        self.assertIsNone(launch_record.read(self.root / "no-such-run"), "no directory at all")
        self.assertIsNone(launch_record.read(self.run), "a reserved run has not said what it is doing yet")

    def test_a_launcher_that_stopped_reporting_is_said_so(self):
        launch_record.write(self.run, "resolving_models", workspace=self.workspace)
        note = {**launch_record.read(self.run), "pid": 4242}
        launch_record.path(self.run).write_text(json.dumps(note))
        for error, expected, why in (
            (ProcessLookupError(), False, "the launcher exited"),
            (PermissionError(), True, "it is running under another user, not gone"),
            (OSError("cannot say"), None, "the check itself could not answer"),
        ):
            with self.subTest(why=why), mock.patch.object(os, "kill", side_effect=error):
                self.assertIs(expected, launch_record.read(self.run)["owner_alive"], why)

    def test_a_note_that_cannot_be_written_leaves_nothing_behind(self):
        """The start must not fail because its status line could not be saved, and must not half-save it."""
        blocked = self.root / "blocked"
        blocked.write_text("not a directory")
        launch_record.write(blocked / "launch.json", "preparing_run", workspace=self.workspace)
        self.assertEqual(b"not a directory", blocked.read_bytes())
        self.assertIsNone(launch_record.read(blocked / "launch.json"))

    def test_the_first_checkpoint_ends_the_note(self):
        launch_record.write(self.run, "preparing_run", workspace=self.workspace)
        launch_record.clear(self.run)
        self.assertIsNone(launch_record.read(self.run))
        self.assertFalse(launch_record.path(self.run).exists())
        launch_record.clear(self.run)  # clearing a run that said nothing is not an error
        launch_record.clear(self.root / "nowhere")


if __name__ == "__main__":
    unittest.main()
