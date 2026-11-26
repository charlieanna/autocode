"""`autocode board`: the screens are what `runs --all --json` and `--status` said, and the board takes no action."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import autocode_board as board

NOW = time.mktime(time.strptime("2026-11-25T12:00:00", board.STAMP))


def row(name: str, bucket: str, **more) -> dict:
    return {
        "name": name,
        "run_dir": f"/work/{more.get('project', 'api')}/.autocode/runs/{name}",
        "workspace": f"/work/{more.pop('project', 'api')}",
        "title": f"Task {name}",
        "bucket": bucket,
        "status": "RUNNING",
        "updated_at": "2026-11-25T11:57:00",
        "next_command": None,
        **more,
    }


def listing(*rows: dict) -> str:
    counts = {bucket: sum(1 for item in rows if item["bucket"] == bucket) for bucket in board.BUCKETS}
    return json.dumps({"schema": 1, "scope": "registry", "counts": {"total": len(rows), **counts}, "runs": list(rows)})


def text(lines) -> str:
    return "\n".join(line for line, _ in lines)


class ListScreenTests(unittest.TestCase):
    def setUp(self):
        self.board = board.Board(home="/work")
        self.rows = (
            row(
                "plan",
                board.NEEDS,
                pause_category_label="Waiting for your approval",
                next_command="autocode --workspace /work/api --approve-goal TOKEN",
            ),
            row("build", board.WORKING, project="web", phase="BUILDING", iteration=2),
            row("old", board.SETTLED, status="TASK_COMPLETE", updated_at="2026-11-23T12:00:00"),
            row("lost", board.UNREADABLE, reason="Run state.json is unavailable"),
        )
        board.apply(self.board, [(("/work", None), listing(*self.rows), "")])

    def shown(self, width=120) -> str:
        return text(board.list_lines(self.board, width, NOW)[0])

    def test_it_reads_the_list_before_it_shows_one(self):
        fresh = board.Board(home="/work")
        self.assertEqual({("/work", None): board.INTERVAL}, board.targets(fresh))
        self.assertIn("Reading the runs", text(board.list_lines(fresh, 80, NOW)[0]))

    def test_runs_not_yet_settled_are_shown_grouped_with_the_picked_runs_command(self):
        shown = self.shown()
        self.assertIn("AutoCode runs   1 needs you · 1 working · 1 settled · 1 unreadable", shown)
        self.assertIn("needs you (1)", shown)
        self.assertIn(">   3m  Waiting for your approval  Task plan", shown)
        self.assertIn("-> autocode --workspace /work/api --approve-goal TOKEN", shown)
        self.assertIn("working (1)", shown)
        self.assertIn("    3m  RUNNING                    Task build", shown)
        self.assertRegex(shown, r"Task build +web")
        self.assertNotIn("Task old", shown, "a finished run stays out of the way until asked for")
        self.assertNotIn("Task lost", shown)

    def test_keys_move_filter_and_show_every_run(self):
        self.assertIsNone(board.press(self.board, "down"))
        self.assertEqual("Task build", board.picked(self.board)["title"])
        self.assertIn("BUILDING · iteration 2", self.shown())
        board.press(self.board, "down")
        self.assertEqual("Task build", board.picked(self.board)["title"], "the last row stays picked")
        board.press(self.board, "n")
        self.assertEqual(["Task plan"], [item["title"] for item in board.shown(self.board)])
        self.assertEqual("Task plan", board.picked(self.board)["title"], "a picked row that is hidden gives way")
        board.press(self.board, "a")
        shown = self.shown()
        self.assertIn("2d  TASK_COMPLETE", shown)
        self.assertIn("Task lost", shown)
        self.assertEqual("quit", board.press(self.board, "q"))
        self.assertEqual("reread", board.press(self.board, "r"))

    def test_the_picked_row_survives_a_reread_and_stays_on_screen(self):
        many = [row(f"run{n:02}", board.WORKING) for n in range(40)]
        board.apply(self.board, [(("/work", None), listing(*many), "")])
        for _ in range(30):
            board.press(self.board, "down")
        board.apply(self.board, [(("/work", None), listing(*reversed(many)), "")])
        self.assertEqual("Task run30", board.picked(self.board)["title"])
        frame = board.frame(self.board, 100, 20, NOW)
        self.assertEqual(20, len(frame))
        self.assertIn("> ", text(frame))
        self.assertIn("Task run30", text(frame))
        self.assertIn("more below", frame[-1][0])

    def test_a_failed_reread_keeps_the_last_list_and_says_so(self):
        board.apply(self.board, [(("/work", None), "", "status read timed out after 5s")])
        shown = self.shown()
        self.assertIn("Could not read the list: status read timed out after 5s. Trying again.", shown)
        self.assertIn("Task plan", shown)
        board.apply(self.board, [(("/work", None), '{"view": {}}', "")])
        self.assertIn("the list was not a runs listing", self.shown())
        board.apply(self.board, [(("/work", None), listing(*self.rows), "")])
        self.assertNotIn("Could not read", self.shown())

    def test_no_runs_says_which_runs_are_hidden(self):
        board.apply(self.board, [(("/work", None), listing(self.rows[2]), "")])
        self.assertIn("No run needs you or is working (1 run(s) in all; press a to show them).", self.shown())
        board.apply(self.board, [(("/work", None), listing(), "")])
        self.assertIn("No AutoCode run has been saved on this machine yet.", self.shown())

    def test_a_narrow_terminal_cuts_lines_and_never_overflows(self):
        for width in (40, 60, 200):
            lines, _ = board.list_lines(self.board, width, NOW)
            self.assertLessEqual(max(len(line) for line, _ in lines), width, width)

    def test_the_project_column_lines_up_whatever_the_titles_and_labels_are(self):
        """On a laptop with 158 runs a short title pulled its project left, and a fixed 28-column label
        left an 80-column terminal 14 characters of each title."""
        rows = (
            row("a", board.NEEDS, title="hi", pause_category_label="AutoCode"),
            row("b", board.NEEDS, title="x" * 300, project="a-much-longer-project-name-than-the-column"),
            row("c", board.WORKING, title="a middling title", project="web"),
        )
        board.apply(self.board, [(("/work", None), listing(*rows), "")])
        for width in (140, 80, 60):
            lines = [line for line in self.shown(width).splitlines() if "3m  " in line]
            self.assertEqual(3, len(lines), lines)
            starts = {max(line.rfind("  api"), line.rfind("  web"), line.rfind("  a-much")) for line in lines}
            self.assertEqual(1, len(starts), f"the project column starts at {starts} at width {width}: {lines}")
            self.assertTrue(all(len(line) <= width for line in lines))
        self.assertIn("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", self.shown(80), "the room goes to the title")

    def test_the_picked_row_says_why_it_needs_a_person(self):
        old = "An older AutoCode version saved this chat. Start a new chat for the task."
        board.apply(self.board, [(("/work", None), listing(row("old", board.NEEDS, needs_reason=old)), "")])
        self.assertIn(f"        {old}", self.shown(), "a row labelled only RUNNING said nothing more")

    def test_a_long_command_is_still_one_command_when_copied_from_the_screen(self):
        """The approval command of a real run was wrapped in the middle of a path, so pasting it failed."""
        command = (
            "AUTOCODE_APPROVE_GOAL_TOKEN=abc autocode --run-dir /Users/someone/Documents/workspace/"
            "idlecampus-ui-impl/.autocode/runs/20260922-152402-implement-the-refined-idlecampus-learning-catalog"
            " --workspace /Users/someone/Documents/workspace/idlecampus-ui-impl --approve-goal"
        )
        board.apply(self.board, [(("/work", None), listing(row("plan", board.NEEDS, next_command=command)), "")])
        for width in (200, 120, 80, 40):
            lines = self.shown(width).splitlines()
            copied = "\n".join(lines[next(n for n, line in enumerate(lines) if "-> " in line) :])
            self.assertLessEqual(max(map(len, lines)), width)
            # What a shell makes of the pasted lines: a backslash at the end of a line joins it to the next.
            self.assertEqual(shlex.split(command), shlex.split(copied.replace("\\\n", ""))[1:], width)
        quoted = "autocode --workspace '/work/my project/" + "c" * 60 + "' --status"
        board.apply(self.board, [(("/work", None), listing(row("plan", board.NEEDS, next_command=quoted)), "")])
        self.assertNotIn("\\", self.shown(40), "a quoted argument cannot be continued, so none is")
        self.assertLessEqual(max(map(len, self.shown(40).splitlines())), 40)

    def test_the_counts_and_when_they_were_read_stay_on_screen_far_down_a_long_list(self):
        many = [row(f"run{n:03}", board.WORKING) for n in range(158)]
        board.apply(self.board, [(("/work", None), listing(*many), "")], NOW - 7)
        board.frame(self.board, 100, 20, NOW)  # a page is what the window holds: 15 rows under the counts
        for key, picked in (("pagedown", "run015"), ("end", "run157"), ("pageup", "run142"), ("home", "run000")):
            board.press(self.board, key)
            frame = board.frame(self.board, 100, 20, NOW)
            self.assertEqual("AutoCode runs   0 needs you · 158 working · 0 settled   read 7s ago", frame[0][0])
            self.assertEqual(f"Task {picked}", board.picked(self.board)["title"], key)
            self.assertIn(f"Task {picked}", text(frame), "the highlighted row is on screen")
        board.apply(self.board, [(("/work", None), "", "status read timed out after 20s")], NOW)
        frame = board.frame(self.board, 100, 20, NOW)
        self.assertIn("read 7s ago", frame[0][0], "a failed reread leaves the list as old as it is")
        self.assertIn("Could not read the list", frame[1][0])
        tiny = board.frame(self.board, 100, 5, NOW)
        self.assertEqual(5, len(tiny))
        self.assertIn("Task run000", text(tiny), "a window too short to keep them gives its lines to the rows")

    def test_a_slow_first_read_says_how_long_it_has_taken_and_is_not_an_error(self):
        fresh = board.Board(home="/work", asked=NOW - 9)
        shown = text(board.list_lines(fresh, 80, NOW)[0])
        self.assertIn("Reading the runs… 9s", shown)
        self.assertNotIn("Could not read", shown)
        self.assertGreaterEqual(board.READ_TIMEOUT, 20, "five seconds was less than a real machine's listing takes")

    def test_backspace_goes_back_as_each_terminal_sends_it(self):
        """macOS's terminfo names Ctrl-H as Backspace while Terminal and iTerm2 send DEL, so there curses
        hands the board a plain character."""
        self.assertEqual(("back", "back", "back"), (board.KEYS["\x7f"], board.KEYS["\b"], board.KEYS["\x1b"]))


VIEW = {
    "task": "\nAdd billing history\nwith exports",
    "status": "RUNNING",
    "status_label": "Working",
    "worktree": {"label": "Its own worktree · branch autocode/billing"},
    "progress": {
        "line": "Building · 1 of 3 tasks done · nothing needed from you",
        "tasks": {
            "label": "1 of 3 tasks done",
            "items": [
                {"number": 1, "id": "M1", "label": "Store invoices", "state": "done", "current": False},
                {"number": 2, "id": "M2", "label": "List invoices", "state": "building", "current": True},
                {"number": 3, "id": "M3", "label": "Export invoices", "state": "building", "current": True},
            ],
        },
    },
    "pipeline": [
        {
            "label": "Requirements",
            "state": "ran",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "spent_usd": 0.002,
        },
        {"label": "Planner", "state": "ran", "model": "gpt-5.6-sol", "reasoning_effort": "high", "spent_usd": 0.12},
        {
            "label": "Builder",
            "state": "working",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "high",
            "spent_usd": 0.4,
            "spent_complete": False,
        },
        {"label": "Tester", "state": "pending", "model": None, "reasoning_effort": None, "spent_usd": None},
    ],
    "chat": {
        "now": [
            {"agent": "Builder · task M2", "text": "running a command"},
            {"agent": "Builder · task M3", "text": "reading files"},
        ],
        "last_output_at": "2026-11-25T19:59:48Z",
    },
    "cost": {"spent_usd": 0.52, "cap_usd": 5, "complete": False},
    "summary": {"sentence": "Working: the Builder has two tasks in hand."},
    "needs": {"kind": "continue"},
    "next_command": None,
}


class RunScreenTests(unittest.TestCase):
    def setUp(self):
        self.board = board.Board(home="/work")
        self.picked = row("build", board.WORKING)
        board.apply(self.board, [(("/work", None), listing(self.picked), "")])
        self.assertEqual("reread", board.press(self.board, "enter"))
        self.key = (self.picked["workspace"], self.picked["run_dir"])

    def shown(self, width=110) -> str:
        return text(board.run_lines(self.board, width))

    def test_opening_a_run_reads_its_status_and_shows_each_parallel_builder(self):
        self.assertEqual(
            {("/work", None): board.HIDDEN_INTERVAL, self.key: board.OPEN_INTERVAL},
            board.targets(self.board),
            "the list is off screen: it is reread now and then, the open run every few seconds",
        )
        self.assertIn("Reading the run", self.shown())
        board.apply(self.board, [(self.key, json.dumps({"view": VIEW}), "")])
        shown = self.shown()
        self.assertTrue(shown.startswith("Add billing history\n/work/api · Its own worktree · branch autocode/billing"))
        self.assertIn("Working\n  Building · 1 of 3 tasks done · nothing needed from you", shown)
        self.assertIn("  ran      Requirements         gpt-5.6-sol high             under $0.01", shown)
        self.assertIn("  ran      Planner              gpt-5.6-sol high             $0.12", shown)
        self.assertIn("  working  Builder              gpt-5.6-terra high           $0.40+", shown)
        self.assertRegex(shown, r"  pending  Tester\n")
        self.assertIn("Tasks   1 of 3 tasks done", shown)
        self.assertIn("  done     M1  Store invoices", shown)
        self.assertIn("  current  M3  Export invoices", shown)
        self.assertIn(
            "Working now (2)\n  Builder · task M2: running a command\n  Builder · task M3: reading files", shown
        )
        self.assertIn("last output at 2026-11-25T19:59:48Z", shown)
        self.assertIn("Cost   $0.52+ of a $5.00 cap", shown)
        self.assertIn("Result\n  Working: the Builder has two tasks in hand.", shown)
        self.assertNotIn("Needs you", shown)

    def test_a_run_that_needs_a_person_shows_the_command_and_never_runs_it(self):
        command = "AUTOCODE_APPROVE_GOAL_TOKEN=abc autocode --workspace /work/api --run-dir R --approve-goal"
        waiting = {
            **VIEW,
            "status_label": "Waiting for your approval",
            "needs": {"kind": "approve_plan", "token": "abc"},
            "next_command": command,
            "explanation": {"explanation": "The plan is ready for you to read and approve."},
            "cost": {"spent_usd": 0.1, "cap_usd": None, "complete": True},
        }
        plan = {
            "revision": 2,
            "token": "abc",
            "review_markdown": "Add billing history\n\n## Deliverables\n\n- billing\\.py\n- test\\_billing\\.py",
        }
        waiting["displayed_plan"] = plan
        board.apply(self.board, [(self.key, json.dumps({"view": waiting}), "")])
        shown = self.shown(200)
        self.assertIn(
            "Plan to approve (revision 2)\n  Add billing history\n\n  Deliverables\n\n  - billing.py\n  - test_billing.py\n\n"
            "Needs you",
            shown,
        )
        stale = {**waiting, "displayed_plan": {**plan, "token": "older"}}
        board.apply(self.board, [(self.key, json.dumps({"view": stale}), "")])
        self.assertNotIn("Plan to approve", self.shown(200), "a plan the command is not bound to is not shown as it")
        board.apply(self.board, [(self.key, json.dumps({"view": waiting}), "")])
        self.assertIn(f"Needs you\n  The plan is ready for you to read and approve.\n  -> {command}", shown)
        self.assertIn("Cost   $0.10, no cap", shown)
        for key in ("enter", "up", "down", "pagedown", "pageup", "end", "home", "n", "a"):
            self.assertIsNone(board.press(self.board, key), "no key on the run screen names an action")
        self.assertEqual(self.key, self.board.opened)

    def test_scrolling_stops_at_the_end_and_back_returns_to_the_same_row(self):
        board.apply(self.board, [(self.key, json.dumps({"view": VIEW}), "")])
        first = board.frame(self.board, 100, 12, NOW)
        self.assertIn("Add billing history", text(first))
        self.assertIn("more below", first[-1][0])
        board.press(self.board, "pagedown")
        self.assertEqual(6, self.board.scroll, "a page keeps two lines of the one before")
        paged = board.frame(self.board, 100, 12, NOW)
        self.assertEqual(
            ["Add billing history", "/work/api · Its own worktree · branch autocode/billing"],
            [line for line, _ in paged[:2]],
            "the task and where it runs stay at the top",
        )
        self.assertNotIn("Building · 1 of 3 tasks done", text(paged))
        for key in ("end", "down", "down"):
            board.press(self.board, key)
        frame = board.frame(self.board, 100, 12, NOW)
        self.assertEqual(12, len(frame))
        self.assertIn("Result", text(frame), "the last lines are reachable")
        self.assertNotIn("more below", frame[-1][0])
        self.assertEqual(len(board.run_lines(self.board, 100)) - 10, self.board.scroll, "scrolling stops at the end")
        board.press(self.board, "home")
        self.assertEqual(text(first), text(board.frame(self.board, 100, 12, NOW)))
        self.assertIn("Esc back to the list", frame[-1][0])
        self.assertEqual("reread", board.press(self.board, "back"), "the list was off screen, so it is read again")
        self.assertIsNone(self.board.opened)
        self.assertEqual("Task build", board.picked(self.board)["title"])
        self.assertEqual({("/work", None): board.INTERVAL}, board.targets(self.board))

    def test_a_failed_status_read_is_said_and_an_older_read_stays(self):
        board.apply(self.board, [(self.key, "", "status read exited 2")])
        self.assertIn("Could not read this run: status read exited 2.", self.shown())
        board.apply(self.board, [(self.key, json.dumps({"view": VIEW}), ""), (self.key, "{", "")])
        shown = self.shown()
        self.assertIn("Could not reread this run: the read was not JSON. Showing the last read.", shown)
        self.assertIn("Add billing history", shown)
        board.apply(self.board, [(("/work/other", "/work/other/run"), json.dumps({"view": {"task": "Other"}}), "")])
        self.assertNotIn("Other", self.shown(), "a read of a run that is no longer open is dropped")


class ReaderLimitTests(unittest.TestCase):
    def test_the_board_reads_with_its_own_limits_not_the_chats(self):
        """The chat stops a status read at five seconds, which is less than a listing of 158 runs takes."""
        made = {}

        def reader(command, **options):
            made.update(options, command=command)
            return mock.Mock()

        terminal = mock.Mock(isatty=lambda: True)
        with (
            mock.patch.object(board.polling, "StatusPoller", reader),
            mock.patch("curses.wrapper") as drawn,
            mock.patch.object(board.sys, "stdin", terminal),
            mock.patch.object(board.sys, "stdout", terminal),
        ):
            self.assertEqual(0, board.cli([]))
        self.assertEqual(1, drawn.call_count)
        self.assertEqual(board.AUTOCODE, made["command"])
        self.assertEqual(
            (board.READ_TIMEOUT, board.MAX_READ_TIMEOUT, board.REST), (made["timeout"], made["limit"], made["rest"])
        )
        self.assertGreater(board.MAX_READ_TIMEOUT, board.READ_TIMEOUT)


class NoTerminalTests(unittest.TestCase):
    def test_without_a_terminal_it_names_the_one_time_listing(self):
        with tempfile.TemporaryDirectory(prefix="board-") as home:
            done = subprocess.run(
                [*board.AUTOCODE, "board"],
                capture_output=True,
                text=True,
                env={**os.environ, "AUTOCODE_HOME": home},
                cwd=home,
                timeout=60,
            )
            self.assertEqual((2, ""), (done.returncode, done.stdout))
            self.assertIn("autocode runs --all --active", done.stderr)
            self.assertEqual([], list(Path(home).iterdir()))


if __name__ == "__main__":
    unittest.main()
