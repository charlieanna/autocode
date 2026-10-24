"""Viewer receipt age is distinct from core sample time and task progress."""

import json
import unittest
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_observation as observation

from tests.test_chat_context import WORKSPACE, A, B, view
from tests.test_chat_tui import screen_ui


def sampled(run=A):
    result = view(run, "current-context")
    result["observation"] = {
        "version": 1,
        "workspace": str(WORKSPACE),
        "run_dir": str(run),
        "checkpoint_token": "c" * 64,
        "sampled_at": "2026-10-24T05:00:00+00:00",
    }
    return result


class ReadMetadata(unittest.TestCase):
    def test_local_age_and_core_sample_stay_distinct_and_repeated_reads_are_not_progress(self):
        receipt = observation.Read()
        receipt.accept(sampled(), 100)
        self.assertEqual("7s ago", receipt.age(107))
        self.assertIn("05:00:00+0000", receipt.compact(107))
        before = receipt.snapshot.copy()
        receipt.accept(sampled(), 110)
        self.assertEqual(before, receipt.snapshot)
        self.assertEqual("0s ago", receipt.age(110))
        self.assertIn("does not measure meaningful work progress", receipt.details(110))

    def test_missing_malformed_and_other_run_samples_are_unknown_without_losing_real_receipt(self):
        for sample in (
            None,
            [],
            {"version": 2},
            {**sampled()["observation"], "run_dir": str(B)},
            {**sampled()["observation"], "sampled_at": "invalid"},
            {**sampled()["observation"], "sampled_at": "2026-10-24T05:00:00"},
        ):
            with self.subTest(sample=sample):
                receipt = observation.Read()
                receipt.accept({**sampled(), "observation": sample}, 5)
                self.assertIsNone(receipt.snapshot)
                self.assertEqual("2s ago", receipt.age(7))
                self.assertIn("core sample unknown", receipt.compact(7))

    def test_failure_retains_last_sample_and_recovery_updates_only_the_read_receipt(self):
        receipt = observation.Read()
        receipt.accept(sampled(), 10)
        before = receipt.snapshot.copy()
        receipt.fail("read timed out")
        self.assertEqual(before, receipt.snapshot)
        self.assertIn("unavailable", receipt.details(20))
        self.assertEqual("10s ago", receipt.age(20))
        receipt.accept(sampled(), 25)
        self.assertIsNone(receipt.error)
        self.assertEqual(before, receipt.snapshot)
        self.assertEqual("2s ago", receipt.age(27))

    def test_identical_view_still_records_a_successful_read_and_error_recovery_without_scroll(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        with patch("autocode_chat_tui.time.monotonic", return_value=10):
            ui.observe(sampled())
        ui._scroll = 4
        ui.status_unavailable("status read failed")
        with patch("autocode_chat_tui.time.monotonic", return_value=30):
            ui.observe(sampled())
        self.assertEqual(30, ui._observation.received)
        self.assertIsNone(ui._observation.error)
        self.assertEqual(4, ui._scroll)

    def test_switching_restores_each_runs_receipt_without_making_it_current_authority(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(sampled())
        ui.status_unavailable("A unavailable")
        ui.switch(WORKSPACE, B)
        self.assertIsNone(ui._observation.received)
        ui.observe(sampled(B))
        ui.switch(WORKSPACE, A)
        self.assertEqual("A unavailable", ui._observation.error)
        self.assertEqual(str(A), ui._observation.snapshot["run_dir"])
        self.assertIsNone(ui._view)

    def test_read_failure_and_recovery_reach_the_renderer_without_a_task_action(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        client = chat.ChatClient(WORKSPACE, A, observe=ui.observe, unavailable=ui.status_unavailable, say=Mock())
        self.addCleanup(client.close)
        key = (str(WORKSPACE), str(A))
        with (
            patch.object(
                client._poller,
                "tick",
                side_effect=[
                    [(key, json.dumps({"view": sampled()}), "")],
                    [(key, "", "status read timed out")],
                    [(key, json.dumps({"view": sampled()}), "")],
                ],
            ),
            patch.object(client, "take") as take,
        ):
            client.poll()
            client.poll()
            self.assertEqual("status read timed out", ui._observation.error)
            client.poll()
            self.assertIsNone(ui._observation.error)
            take.assert_not_called()

    def test_a_run_that_has_never_answered_is_not_announced_as_an_outage(self):
        """#1998: the first read of a run this client is still creating has no last view to lose."""

        unavailable, said = Mock(), []
        client = chat.ChatClient(WORKSPACE, A, observe=Mock(), unavailable=unavailable, say=said.append)
        self.addCleanup(client.close)
        key = (str(WORKSPACE), str(A))
        with (
            patch.object(
                client._poller,
                "tick",
                side_effect=[
                    [(key, "", "status read exited 2")],  # --status: the state file is not written yet
                    [(key, json.dumps({"view": sampled()}), "")],
                    [(key, "", "status read exited 1")],  # the same failure once a view *was* shown
                ],
            ),
            patch.object(client, "take"),
        ):
            client.poll()
            self.assertEqual([], [text for text in said if "Status" in text])
            self.assertTrue(unavailable.called)  # the failed read is still recorded, just not announced
            self.assertEqual("unavailable", client.tabs()[0]["state"])
            client.poll()  # the run answers: no recovery from an outage that was never announced
            self.assertEqual([], [text for text in said if "Status" in text])
            self.assertNotEqual("unavailable", client.tabs()[0]["state"])
            client.poll()
        self.assertTrue(any("Status unavailable" in text for text in said))

    def test_unavailable_header_and_working_line_are_honest_at_80_columns(self):
        ui = screen_ui()
        ui.set_context(WORKSPACE, A)
        ui.observe(sampled())
        ui.status_unavailable("read failed")
        drawn = []
        ui._put = lambda row, col, text, attr=0: drawn.append((row, text))
        layout = ui._layout(24, 80)
        ui._header(layout)
        ui._question_or_status(layout)
        self.assertIn("Status unavailable", next(text for row, text in drawn if row == 2))
        self.assertIn(
            "Status unavailable; last snapshot retained",
            next(text for row, text in drawn if row == layout.status_start),
        )
        self.assertTrue(all(len(text) <= 79 for _, text in drawn))
