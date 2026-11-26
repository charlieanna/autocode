"""`autocode board` over real saved runs: one fixture run stopped at its plan approval, and rows of two more projects.

The board reads only what the public commands print, so these tests start the real command (in a
terminal for what a person sees) and check that nothing it does changes a run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import autocode_board as board
import autocode_chat_polling as polling
import autocode_registry as registry
from autocode_taskrun import TaskRun

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux
from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


def listed_project(root: Path, name: str, env: dict[str, str], **runs: dict) -> Path:
    """A project under ``root`` whose saved runs are registered as real runs register themselves."""
    workspace = root / name
    (workspace / ".git").mkdir(parents=True)
    with unittest.mock.patch.dict(os.environ, {"AUTOCODE_HOME": env["AUTOCODE_HOME"]}):
        for run, state in runs.items():
            run_dir = workspace / ".autocode" / "runs" / run
            run_dir.mkdir(parents=True)
            saved = {"version": 3, "workspace": str(workspace), "task": f"{run} the {name}", **state}
            (run_dir / "state.json").write_text(json.dumps(saved), encoding="utf-8")
            registry.register_run(workspace, run_dir, saved)
    return workspace


def files(root: Path) -> dict[str, float]:
    return {str(path): path.stat().st_mtime for path in root.rglob("*") if path.is_file()}


class BoardCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace, env = fixture_workspace(SimpleNamespace(addCleanup=cls.addClassCleanup))
        cls.env = {**os.environ, **env, "TERM": "xterm-256color"}
        cls.root = cls.workspace.parent
        run = TaskRun.start(cls.workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=180)
        view = run.advance_until_input()
        assert (view.get("needs") or {}).get("kind") == "approve_plan", view.get("needs")
        cls.run_dir = run.run_dir
        cls.web = listed_project(cls.root, "web", cls.env, finish={"status": "TASK_COMPLETE"})
        cls.elsewhere = cls.root / "elsewhere"
        cls.elsewhere.mkdir()

    def test_the_reader_fills_the_board_from_the_two_public_reads_and_changes_nothing(self):
        launched = []

        def launch(argv, **options):
            launched.append(tuple(argv[len(board.AUTOCODE) :]))
            return subprocess.Popen(argv, **options)

        shown = board.Board(home=str(self.elsewhere))
        poller = polling.StatusPoller(board.AUTOCODE, env=self.env, launch=launch)
        self.addCleanup(poller.close)
        before = files(self.run_dir)

        def read(until):
            deadline = time.monotonic() + 90
            while not until():
                self.assertLess(time.monotonic(), deadline, f"the board never finished its read: {shown.error}")
                board.apply(shown, poller.tick(board.targets(shown)))
                time.sleep(0.05)

        read(lambda: shown.loaded)
        self.assertEqual("", shown.error)
        # The other test adds a project to the same registry, so this run is found, not counted.
        row = next(item for item in board.shown(shown) if item["run_dir"] == str(self.run_dir))
        self.assertEqual(board.NEEDS, row["bucket"])
        self.assertEqual(1, shown.counts["settled"])
        self.assertNotIn("finish the web", [item["title"] for item in board.shown(shown)])
        shown.picked = row["run_dir"]
        listed = "\n".join(line for line, _ in board.list_lines(shown, 300, time.time())[0])
        self.assertIn("--approve-goal", listed)
        self.assertIn(str(self.workspace), listed)

        self.assertEqual("reread", board.press(shown, "enter"))
        read(lambda: shown.view is not None or shown.view_error)
        self.assertEqual("", shown.view_error)
        opened = "\n".join(line for line, _ in board.run_lines(shown, 300))
        self.assertTrue(opened.startswith("Build a deterministic greeting CLI"), opened)
        for part in (
            "Pipeline",
            "Plan Reviewer",
            "Cost   ",
            "Plan to approve (revision",
            "Needs you",
            "--approve-goal",
        ):
            self.assertIn(part, opened)
        self.assertEqual(
            {
                ("runs", "--all", "--json"),
                ("--status", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)),
            },
            set(launched),
        )
        self.assertEqual(before, files(self.run_dir), "reading a run writes nothing in it")

    def test_a_person_watches_the_runs_and_opens_one_in_a_real_terminal(self):
        tmux = require_tmux(self)
        with TerminalScreen(
            [sys.executable, str(ROOT / "tools" / "autocode.py"), "board"],
            cwd=self.elsewhere,
            env=self.env,
            cols=120,
            rows=34,
            tmux=tmux,
        ) as screen:
            shown = screen.wait_for_all("1 needs you · 0 working · 1 settled", "needs you (1)", "> ", "--approve-goal")
            self.assertIn("Build a deterministic greeting CLI", shown)
            self.assertNotIn("finish the web", shown)
            screen.type("a")
            screen.wait_for_all("settled (1)", "finish the web")
            # A run that starts while the board is open appears without a key.
            listed_project(self.root, "jobs", self.env, aaa={"status": "PAUSED_REQUESTED", "stop_reason": "paused"})
            shown = screen.wait_for_all("2 needs you", "aaa the jobs", "Build a deterministic greeting CLI")
            self.assertRegex(shown, r"> +\S+ +Your decision +Build a deterministic", "the highlight stays on its run")
            screen.keys("Enter")
            screen.wait_for_all("Pipeline", "Plan Reviewer", "Plan to approve", "more below", "Esc back to the list")
            screen.keys("End")
            screen.wait_for_all("Needs you", "--approve-goal")
            screen.keys("Escape")
            screen.wait_for_all("needs you (2)", "Enter open")
            screen.type("q")
            self.assertEqual(0, screen.exit_status())


if __name__ == "__main__":
    unittest.main()
