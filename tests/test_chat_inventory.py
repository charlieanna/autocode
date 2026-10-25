"""Frozen public run listings and fair subscriptions use one bounded read pool."""

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_inventory as inventory
import autocode_chat_polling as polling

from tests.test_chat_polling import Child
from tests.test_chat_tui import screen_ui

WORKSPACE = Path("/tmp/inventory-project")
RUNS = [WORKSPACE / "runs" / f"r{number:02}" for number in range(25)]
ROWS = [
    {"workspace": str(WORKSPACE), "run_dir": str(run), "title": f"Public entry {number:02}"}
    for number, run in enumerate(RUNS)
]


class OverviewReads(unittest.TestCase):
    def setUp(self):
        self.now, self.children, self.admitted = 100.0, [], []

        def launch(argv, **kwargs):
            child = Child(kwargs["stdout"], len(self.children))
            child.argv = argv
            self.children.append(child)
            self.admitted.append(argv)
            return child

        self.client = chat.ChatClient(WORKSPACE, RUNS[0], say=Mock())
        self.client.close()
        self.client._poller = polling.StatusPoller(("autocode",), clock=lambda: self.now, launch=launch)
        self.client._overview = inventory.Overview(self.client._poller, self.client.say)
        self.client.history = chat.History(WORKSPACE)
        self.kill = patch.object(polling.os, "killpg").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.client.close)

    def finish(self, rows=ROWS):
        for key, read in list(self.client._poller.active.items()):
            if isinstance(key, polling.ProjectRuns) or key[1] is None:
                read.output.write(json.dumps({"runs": rows}).encode())
                read.child.code = 0
            else:
                read.child.finish({"workspace": key[0], "run_dir": key[1], "needs": {"kind": "continue"}})

    def listed(self, rows=ROWS):
        with patch.object(chat.subprocess, "run", side_effect=AssertionError("synchronous listing")):
            self.assertIn("Reading the run list", self.client.slash("/runs", ""))
        for _ in range(3):
            self.client.poll()
            self.finish(rows)
        self.client.poll()
        self.assertEqual(25, len(self.client.history.rows), "the initial requested list appears when read")
        shown = self.client.slash("/runs", "")
        self.assertIn(rows[0]["title"], shown)
        self.assertEqual(25, len(self.client.history.rows))

    def test_25_listed_rows_do_not_subscribe_until_explicitly_opened_and_numbers_stay_frozen(self):
        self.listed()
        statuses = [argv[argv.index("--run-dir") + 1] for argv in self.admitted if "--status" in argv]
        self.assertEqual({str(RUNS[0])}, set(statuses), "inventory is not 25 status subscriptions")
        shown = list(self.client.history.rows)
        self.client.poll()
        self.finish(list(reversed(ROWS[:2])))
        self.client.poll()
        self.assertEqual(shown, self.client.history.rows, "arriving reads cannot retarget displayed /open numbers")
        row = shown[3]
        with self.assertRaises(chat.Switched):
            self.client.slash("/open", "4")
        self.assertEqual(Path(row["run_dir"]), self.client.run_dir)
        self.assertEqual(2, len(self.client.tabs()))

    def test_late_correct_a_response_after_opening_b_updates_only_a_and_preserves_b_draft_and_target(self):
        self.listed()
        for _ in range(3):
            self.client.poll()
            self.finish()
        self.now += 2
        self.client.poll()
        old = self.client._poller.active[(str(WORKSPACE), str(RUNS[0]))].child
        ui = screen_ui()
        ui.set_context(WORKSPACE, RUNS[0])
        self.client._observe, self.client.switched = ui.observe, ui.switch
        number = next(
            number for number, row in enumerate(self.client.history.rows, 1) if row["run_dir"] == str(RUNS[1])
        )
        with self.assertRaises(chat.Switched):
            self.client.slash("/open", str(number))
        self.client.poll()
        current = self.client._poller.active[(str(WORKSPACE), str(RUNS[1]))].child
        b = {
            "workspace": str(WORKSPACE),
            "run_dir": str(RUNS[1]),
            "needs": {"kind": "approve_plan", "token": "B"},
            "chat": {"context": "B-context"},
            "usage": {"spent_usd": 2},
        }
        current.finish(b)
        self.client.poll()
        ui._composer.insert("B draft 你好")
        ui._composer.left()
        before = ui._composer.text, ui._composer.cursor
        old.finish(
            {
                "workspace": str(WORKSPACE),
                "run_dir": str(RUNS[0]),
                "done": True,
                "chat": {"context": "A-context", "messages": [{"text": "A transcript"}]},
                "usage": {"spent_usd": 999},
            }
        )
        with patch.object(self.client, "take") as take:
            self.client.poll()
        take.assert_not_called()
        self.assertEqual((WORKSPACE, RUNS[1]), (self.client.workspace, self.client.run_dir))
        self.assertEqual(b, self.client._status_view)
        self.assertEqual(b, ui._view)
        self.assertEqual(before, (ui._composer.text, ui._composer.cursor))
        self.assertEqual("done", next(row["state"] for row in self.client.tabs() if row["run"] == str(RUNS[0])))

    def test_identical_titles_across_projects_open_and_tab_to_exact_workspace_run_identities(self):
        rows = [{**row, "title": "Identical request"} for row in ROWS]
        elsewhere, other = Path("/tmp/other-inventory-project"), Path("/tmp/other-inventory-project/runs/r01")
        rows[1] = {**rows[1], "workspace": str(elsewhere), "run_dir": str(other)}
        self.listed(rows)
        number = next(number for number, row in enumerate(self.client.history.rows, 1) if row["run_dir"] == str(other))
        with self.assertRaises(chat.Switched):
            self.client.slash("/open", str(number))
        self.assertEqual((elsewhere, other), (self.client.workspace, self.client.run_dir))
        for _ in range(3):
            self.client.poll()
            self.finish(rows)
        self.assertTrue(
            any(
                "--status" in argv
                and argv[argv.index("--workspace") + 1] == str(elsewhere)
                and argv[argv.index("--run-dir") + 1] == str(other)
                for argv in self.admitted
            )
        )
        with self.assertRaises(chat.Switched):
            self.client.slash("/tab", "previous")
        self.assertEqual((WORKSPACE, RUNS[0]), (self.client.workspace, self.client.run_dir))
        with self.assertRaises(chat.Switched):
            self.client.slash("/tab", "next")
        self.assertEqual((elsewhere, other), (self.client.workspace, self.client.run_dir))

    def test_uncached_open_finishes_the_requested_navigation_after_its_reads(self):
        self.client.slash("/open", "2")
        with self.assertRaises(chat.Switched):
            for _ in range(4):
                self.client.poll()
                self.finish()
        expected = self.client.history.rows[1]
        self.assertEqual(
            (Path(expected["workspace"]), Path(expected["run_dir"])), (self.client.workspace, self.client.run_dir)
        )

    def test_a_newer_runs_snapshot_cancels_initial_open_rendering_before_late_refresh(self):
        self.client._overview.accept(polling.ProjectRuns(str(WORKSPACE)), json.dumps({"runs": ROWS}), "")
        self.client._overview.accept((str(WORKSPACE), None), json.dumps({"runs": ROWS}), "")
        self.client.slash("/open", "2")
        self.client.slash("/runs", "")
        displayed = list(self.client.history.rows)
        changed = [
            {**row, "title": "Changed arrival", "updated_at": f"2026-10-25T12:{number:02}:00"}
            for number, row in enumerate(ROWS)
        ]
        for _ in range(4):
            self.client.poll()
            self.finish(changed)
        self.assertEqual(
            displayed, self.client.history.rows, "a late first-list callback cannot replace displayed numbers"
        )
        self.assertEqual(RUNS[0], self.client.run_dir, "the newer /runs cancelled pending navigation too")

    def test_uncached_open_cannot_jump_after_a_source_switch_newer_navigation_or_changed_question(self):
        self.client.slash("/open", "2")
        self.client.slash("/runs", "")  # the newer explicit navigation supersedes the pending open
        for _ in range(4):
            self.client.poll()
            self.finish()
        self.assertEqual(RUNS[0], self.client.run_dir)
        self.client.history.rows = []
        self.client.slash("/open", "2")
        self.client.talk_to(WORKSPACE, RUNS[4])
        for _ in range(4):
            self.client.poll()
            self.finish()
        self.assertEqual(RUNS[4], self.client.run_dir)
        self.client.history.rows = []
        self.client.slash("/open", "2")
        self.client._status_view = {
            "workspace": str(WORKSPACE),
            "run_dir": str(RUNS[4]),
            "chat": {"context": "new-question"},
        }
        for _ in range(4):
            self.client.poll()
            self.finish()
        self.assertEqual(RUNS[4], self.client.run_dir)

    def test_25_explicit_subscriptions_remain_fair_with_hung_unreadable_and_removed_reads(self):
        self.listed()
        for number, row in enumerate(self.client.history.rows, 1):
            if Path(row["run_dir"]) != self.client.run_dir:
                with self.assertRaises(chat.Switched):
                    self.client.slash("/open", str(number))
        self.assertEqual(25, len(self.client.tabs()))
        self.admitted.clear()
        for _ in range(15):
            self.client.poll()
            self.assertLessEqual(len(self.client._poller.active), polling.MAX_READS)
            for key, read in self.client._poller.active.items():
                if isinstance(key, polling.ProjectRuns) or key[1] is None:
                    read.output.write(json.dumps({"runs": ROWS}).encode())
                    read.child.code = 0
                elif key[1] == str(RUNS[1]):
                    read.output.write(b"unreadable JSON")
                    read.child.code = 0
                elif key[1] == str(RUNS[2]):
                    read.child.code = 2  # public --status refusal after the run was removed
                elif key[1] != str(RUNS[3]):
                    read.child.finish({"run_dir": key[1], "done": True})
            self.now += polling.READ_TIMEOUT
        statuses = {argv[argv.index("--run-dir") + 1] for argv in self.admitted if "--status" in argv}
        self.assertEqual(set(map(str, RUNS)), statuses, "errors cannot starve a subscribed run")
        tabs = {row["run"]: row for row in self.client.tabs()}
        for run in RUNS[1:4]:
            self.assertEqual("unavailable", tabs[str(run)]["state"])
        self.client.close()
        self.assertTrue(all(child.reaped and child.output.closed for child in self.children))

    def test_failed_refresh_keeps_labelled_snapshot_and_an_empty_first_list_does_not_read_synchronously(self):
        self.listed()
        before = list(self.client.history.rows)
        for key in tuple(self.client._overview.pending):
            self.client._overview.accept(key, "", "status read timed out")
        text = self.client.slash("/runs", "")
        self.assertIn("Displaying the last snapshot", text)
        self.assertEqual(before, self.client.history.rows)
        self.client.history.rows = []
        with patch.object(chat.subprocess, "run", side_effect=AssertionError("synchronous /open listing")):
            self.client.slash("/open", "1")
