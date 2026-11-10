"""The line a chat draws while a run is starting and has answered no status view yet.

It renders the core's launch note (``autocode_launch_record``, which ``--status`` answers with as
``starting``) plus what the chat itself chose for the run. Pure display: no action, no route, no
state read.
"""

import unittest
from datetime import UTC, datetime, timedelta

import autocode_chat_boot as boot

START = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)


def note(**extra) -> dict:
    return {
        "phase": "resolving_models",
        "label": "resolving which model each stage runs",
        "at": START.isoformat(),
        "started_at": START.isoformat(),
        "owner_alive": True,
        **extra,
    }


class BootLineTests(unittest.TestCase):
    def test_it_names_the_phase_and_the_time_the_start_has_taken(self):
        self.assertEqual(
            "Starting the run · resolving which model each stage runs · 41s",
            boot.line(note(), now=START + timedelta(seconds=41)),
        )

    def test_a_start_past_a_minute_is_counted_in_minutes(self):
        self.assertTrue(boot.line(note(), now=START + timedelta(minutes=4, seconds=7)).endswith("· 4m07s"))

    def test_the_engine_and_mode_the_chat_launched_with_are_named_too(self):
        line = boot.line(note(engine="opencode", mode="Plan first"), now=START + timedelta(seconds=6))
        self.assertEqual(
            "Starting the run · resolving which model each stage runs · on OpenCode · Plan first · 6s", line
        )

    def test_an_engine_the_labels_do_not_name_is_written_as_the_chat_chose_it(self):
        """A connection id, not one of the three engines, is what the person picked; it is not dropped."""
        self.assertIn(
            "· on kilocode ·",
            boot.line(note(engine="kilocode"), now=START + timedelta(seconds=6)),
        )

    def test_a_note_that_names_neither_engine_nor_mode_says_nothing_about_them(self):
        self.assertEqual(
            "Starting the run · resolving which model each stage runs · 6s",
            boot.line(note(engine=None, mode=""), now=START + timedelta(seconds=6)),
        )

    def test_a_launcher_that_stopped_reporting_is_not_still_called_starting(self):
        line = boot.line(note(owner_alive=False), now=START + timedelta(seconds=20))
        self.assertIn(boot.DEAD, line)
        self.assertNotIn("resolving which model", line, "it cannot still be doing the phase it stopped naming")
        self.assertTrue(line.endswith("· 20s"))

    def test_the_time_counts_from_the_start_not_from_the_last_phase(self):
        """A start that has passed through two phases is measured by how long the person has waited."""
        late = note(at=(START + timedelta(minutes=2)).isoformat())
        self.assertEqual(
            "Starting the run · resolving which model each stage runs · 2m00s",
            boot.line(late, now=START + timedelta(minutes=2)),
        )

    def test_a_note_without_a_readable_clock_shows_no_time(self):
        for missing in ({"started_at": None}, {"started_at": "yesterday"}, {"started_at": ""}):
            with self.subTest(**missing):
                self.assertEqual(2, len(boot.line(note(**missing), now=START).split(" · ")))
        self.assertIsNone(boot.seconds({}))
        self.assertIsNone(boot.seconds({"started_at": "not a time"}))

    def test_a_clock_a_head_of_this_machine_counts_as_no_time_yet(self):
        self.assertEqual(0.0, boot.seconds(note(started_at=(START + timedelta(days=1)).isoformat()), now=START))

    def test_a_note_written_without_a_timezone_is_read_as_the_utc_the_core_records(self):
        naive = note(started_at=START.replace(tzinfo=None).isoformat())
        self.assertEqual(5.0, boot.seconds(naive, now=START + timedelta(seconds=5)))

    def test_a_note_that_names_an_unknown_phase_still_says_the_run_is_starting(self):
        self.assertEqual("Starting the run · preparing the run", boot.line({"owner_alive": True}, now=START))


if __name__ == "__main__":
    unittest.main()
