"""A chat talks with the person in their folder and plans and builds on a branch of its own (#3077, #3081).

Seen 2026-11-10: every terminal chat started its run with --in-place, so two chats in one folder waited
for each other for the whole build, and each build wrote into the person's checkout. Then a pull in the
person's folder, while a plan waited for approval there, stranded the plan's review (#3080). A run
started with --build-in-worktree runs Requirements in the folder (it only reads it and talks with the
person), then moves to a new task worktree and branch, where the plan is made, reviewed, approved and
built. These drive the CLI through the task-run interface with the offline fixture provider; the
status view, the checkout, the branch and the run listings are the oracles.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_checkout_lock as checkout_lock
import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

DELIVERED = ("greet.py", "test_greet.py", "README.md")


class BuildInWorktreeCliTests(unittest.TestCase):
    def setUp(self):
        self.workspace, self.env = fixture_workspace(self)

    def start(self, workspace):
        return taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=FIXTURE_OPTIONS,
            start_options=("--build-in-worktree",),
            env=self.env,
            timeout=300,
        )

    def autocode(self, *argv):
        proc = subprocess.run([*taskrun.AUTOCODE, *argv], capture_output=True, text=True, env=self.env)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def git(self, *argv):
        return subprocess.run(["git", "-C", str(self.workspace), *argv], capture_output=True, text=True, check=True)

    def test_the_plan_is_made_and_built_on_a_branch_of_its_own(self):
        (self.workspace / "notes.txt").write_text("the person's own uncommitted note\n")
        run = self.start(self.workspace)
        self.assertTrue(list(run.run_dir.glob("iterations/*/requirements-gather-*.jsonl")), "Requirements ran here")
        self.assertEqual([], list(run.run_dir.glob("iterations/*/discovery-*.jsonl")), "the Planner did not")
        planned = run.advance_until_input()  # the run is named by the path it had in the folder
        self.assertEqual("approve_plan", planned["needs"]["kind"], planned["needs"])
        tree = Path(planned["workspace"])
        self.assertEqual(self.workspace / ".autocode" / "worktrees", tree.parent, "the plan is made in the worktree")
        self.assertTrue(list(Path(planned["run_dir"]).glob("iterations/*/discovery-*.jsonl")), "the Planner ran there")
        # The folder moves on while the plan waits: its review stands, since it was made elsewhere (#3080).
        (self.workspace / "upstream.txt").write_text("pulled meanwhile\n")
        self.git("-c", "user.name=T", "-c", "user.email=t@example.test", "add", "upstream.txt")
        self.git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "upstream change")
        # Another chat now works in the folder: the approved plan is built anyway, since it is built elsewhere.
        other = self.workspace / ".autocode" / "runs" / "20261110-090000-chat-a1b2c3"
        held = checkout_lock.exclusive(self.workspace, other)
        held.__enter__()
        self.addCleanup(held.__exit__, None, None, None)

        run.approve_plan(planned["needs"]["token"])
        view = run.advance_until_input()  # the run is named by the path it had in the folder

        self.assertTrue(view["done"], {key: view.get(key) for key in ("status", "needs", "stop_reason")})
        self.assertEqual(str(tree), view["workspace"])
        self.assertEqual(
            tree / ".autocode" / "runs" / run.run_dir.name, Path(view["run_dir"]), "the run keeps its name"
        )
        for name in DELIVERED:
            self.assertFalse((self.workspace / name).exists(), f"{name} is not written into the person's folder")
            self.assertTrue((tree / name).is_file(), name)
        self.assertEqual("the person's own uncommitted note\n", (self.workspace / "notes.txt").read_text())
        self.assertFalse((tree / "notes.txt").exists(), "uncommitted files are not part of the build")
        self.assertEqual(other, Path(checkout_lock.holder(self.workspace)["run_dir"]), "the folder was never taken")
        self.assertEqual([], list(run.run_dir.glob("iterations/*/builder-*.jsonl")), "nothing was built in the folder")
        self.assertTrue(list(Path(view["run_dir"]).glob("iterations/*/builder-*.jsonl")))

        # The delivery is committed on the run's own branch; the person's branch does not move.
        branches = self.git("for-each-ref", "--format=%(refname:short)", "refs/heads/autocode/").stdout.split()
        self.assertEqual(1, len(branches), branches)
        self.assertEqual((tree / "greet.py").read_text(), self.git("show", f"{branches[0]}:greet.py").stdout)
        self.assertNotIn("autocode/", self.git("branch", "--show-current").stdout)
        self.assertNotIn("greet.py", self.git("ls-files").stdout)

        # Said once, where the run moved: where it works, what it leaves out, and that merging is the person's.
        notices = [row["text"] for row in view["chat"]["messages"] if row["id"] == "build-worktree"]
        self.assertEqual(1, len(notices), view["chat"]["messages"])
        self.assertIn(f"I plan and build this on its own branch, {branches[0]}, in a separate copy of", notices[0])
        self.assertIn("Not included from your folder: notes.txt.", notices[0])
        self.assertIn("review and merge the branch; I never merge it myself.", notices[0])

        # One chat, listed once: the folder's run list and the registry name the run where it is now.
        listed = self.autocode("runs", "--workspace", str(self.workspace), "--json")["runs"]
        self.assertEqual([view["run_dir"]], [row["run_dir"] for row in listed])
        registered = self.autocode("registry", "list", "--json")["runs"]
        self.assertEqual([view["run_dir"]], [row["run_dir"] for row in registered])

        # Once that worktree is gone, the folder's old record says so; it never builds again in the folder.
        self.git("worktree", "remove", "--force", str(tree))
        stale = subprocess.run(
            [*taskrun.AUTOCODE, "--workspace", str(self.workspace), "--run-dir", str(run.run_dir), "--status"],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(2, stale.returncode, stale.stdout + stale.stderr)
        self.assertIn("This chat moved to its own worktree to build.", stale.stderr)

    def test_a_folder_outside_git_becomes_a_project_and_builds_in_it(self):
        # A folder AutoCode makes a project of is already the run's own: it builds there, as before.
        with tempfile.TemporaryDirectory(prefix="build-worktree-plain-") as folder:
            plain = Path(folder).resolve()
            run = self.start(plain)
            view = run.status()
            self.assertEqual("approve_plan", view["needs"]["kind"], view["needs"])
            run.approve_plan(view["needs"]["token"])
            view = run.advance_until_input()
            self.assertTrue(view["done"], view.get("stop_reason"))
            self.assertEqual(str(plain), view["workspace"])
            self.assertTrue((plain / "greet.py").is_file())
            self.assertFalse((plain / ".autocode" / "worktrees").exists())
            self.assertNotIn("build-worktree", [row["id"] for row in view["chat"]["messages"]])


if __name__ == "__main__":
    unittest.main()
