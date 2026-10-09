"""Task worktrees deliver to their branch and can be cleaned up; Builder checkouts retire."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_workspaces as w
import autocode_worktrees as worktrees

from . import test_subprocess


def git(root, *args):
    return w.git(root, *args)


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        (self.project / "app.txt").write_text("committed\n")
        git(self.project, "add", "app.txt")
        git(self.project, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "base")

    def task(self, status="TASK_COMPLETE", name="Add greeting"):
        data = w.create(self.project, name)
        tree = Path(data["workspace"])
        (tree / "greet.py").write_text("print('hi')\n")
        run = tree / ".autocode" / "runs" / "r1"
        run.mkdir(parents=True)
        state = {"status": status, "task": name, "run_dir": str(run), "workspace": str(tree),
                 "project_workspace": str(self.project), "goal_contract": {"revision": 1}}
        (run / "state.json").write_text(json.dumps(state))
        return data, tree, state

    def clean(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = worktrees.cli(["--workspace", str(self.project), *args])
        return code, out.getvalue()


class DeliverTests(Fixture):
    def test_completion_commits_to_the_branch_without_moving_head(self):
        data, tree, state = self.task()
        head = git(tree, "rev-parse", "HEAD")
        note = worktrees.deliver(state, tree)
        self.assertIn(data["branch"], note)
        self.assertEqual(head, git(tree, "rev-parse", "HEAD"), "the completion evidence is pinned to HEAD")
        self.assertEqual("print('hi')", git(self.project, "show", f"{data['branch']}:greet.py"))
        self.assertEqual(data["base_commit"], git(self.project, "rev-parse", f"{data['branch']}^"))
        self.assertNotIn(".autocode", git(self.project, "ls-tree", "-r", "--name-only", data["branch"]))
        tip = git(self.project, "rev-parse", data["branch"])
        worktrees.deliver(state, tree)
        self.assertEqual(tip, git(self.project, "rev-parse", data["branch"]), "repeating adds no commit")
        (tree / "greet.py").write_text("print('hello')\n")
        worktrees.deliver(state, tree)
        self.assertEqual(tip, git(self.project, "rev-parse", f"{data['branch']}^"), "later work stacks on top")

    def test_only_completed_task_worktrees_deliver(self):
        data, tree, state = self.task(status="PAUSED_NO_PROGRESS")
        self.assertEqual("", worktrees.deliver(state, tree))
        self.assertEqual("", worktrees.deliver({**state, "status": "TASK_COMPLETE"}, self.project))
        # A program writes the same metadata without kind "task"; its branch is the program's.
        meta = tree / ".autocode" / "task-workspace.json"
        meta.write_text(json.dumps({key: value for key, value in json.loads(meta.read_text()).items()
                                    if key != "kind"}))
        self.assertEqual("", worktrees.deliver({**state, "status": "TASK_COMPLETE"}, tree))
        self.assertEqual(data["base_commit"], git(self.project, "rev-parse", data["branch"]))


class CleanTests(Fixture):
    def test_dry_run_then_remove_keeps_branch_and_archives_records(self):
        data, tree, state = self.task()
        worktrees.deliver(state, tree)
        code, out = self.clean()
        self.assertEqual(0, code)
        self.assertIn("dry run", out)
        self.assertTrue(tree.is_dir())
        code, out = self.clean("--yes")
        self.assertEqual(0, code, out)
        self.assertFalse(tree.exists())
        self.assertIn(data["branch"], git(self.project, "branch", "--list", data["branch"]))
        archived = self.project / ".autocode" / "archive" / tree.name
        self.assertEqual("TASK_COMPLETE", json.loads((archived / "runs" / "r1" / "state.json").read_text())["status"])
        self.assertEqual("", git(self.project, "status", "--porcelain", "--untracked-files=all"))
        self.assertEqual(1, git(self.project, "worktree", "list", "--porcelain").count("worktree "))

    def test_unfinished_undelivered_program_and_locked_worktrees_are_kept(self):
        _, unfinished, _ = self.task(status="PAUSED_NO_PROGRESS", name="one")
        _, undelivered, _ = self.task(name="two")
        _, program, state = self.task(name="three")
        worktrees.deliver(state, program)
        programs = self.project / ".autocode" / "programs" / "p1"
        programs.mkdir(parents=True)
        (programs / "state.json").write_text(json.dumps({"workstreams": {"a": {"workspace": str(program)}}}))
        _, locked, state = self.task(name="four")
        worktrees.deliver(state, locked)
        with open(locked / ".autocode" / "runs" / "r1" / "writer.lock", "a+") as handle:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            code, out = self.clean("--yes")
        self.assertEqual(0, code, out)
        self.assertIn("PAUSED_NO_PROGRESS", out)
        self.assertIn("commit it to the branch first", out)
        self.assertIn("owned by an `autocode program`", out)
        self.assertIn("holds its lock", out)
        for tree in (unfinished, undelivered, program, locked):
            self.assertTrue((tree / ".git").exists(), tree)

    def test_nested_builder_checkout_is_removed_with_its_task_but_its_records_are_archived(self):
        data, tree, state = self.task()
        worktrees.deliver(state, tree)
        builder = tree / ".autocode" / "builders" / "b1" / "1"
        builder.parent.mkdir(parents=True)
        git(self.project, "worktree", "add", "-q", "-b", "autocode/builder-b1-1", str(builder), data["base_commit"])
        (builder / ".autocode" / "runs" / "builder-b1-1").mkdir(parents=True)
        (builder / ".autocode" / "runs" / "builder-b1-1" / "result.json").write_text("{}")
        code, out = self.clean("--yes")
        self.assertEqual(0, code, out)
        archived = self.project / ".autocode" / "archive" / tree.name
        self.assertTrue((archived / "builders" / "b1" / "1" / ".autocode" / "runs" / "builder-b1-1" / "result.json").is_file())
        self.assertFalse((archived / "builders" / "b1" / "1" / ".git").exists())
        self.assertEqual("", git(self.project, "branch", "--list", "autocode/builder-*"))
        self.assertEqual(1, git(self.project, "worktree", "list", "--porcelain").count("worktree "))


class RemoveCheckoutTests(Fixture):
    def test_records_stay_in_place_and_an_interrupted_removal_is_finished(self):
        tree = self.project / ".autocode" / "builders" / "b1" / "1"
        tree.parent.mkdir(parents=True)
        git(self.project, "worktree", "add", "-q", "-b", "autocode/builder-b1-1", str(tree), "HEAD")
        (tree / ".autocode" / "runs").mkdir(parents=True)
        (tree / ".autocode" / "runs" / "result.json").write_text("{}")
        (tree / "edited.txt").write_text("worker change\n")
        worktrees.remove_checkout(self.project, tree)
        self.assertEqual(["runs"], [p.name for p in (tree / ".autocode").iterdir()])
        self.assertEqual([".autocode"], [p.name for p in tree.iterdir()])
        # A crash after moving the records aside: the next call puts them back.
        os.replace(tree / ".autocode", tree.parent / ".1-records")
        worktrees.remove_checkout(self.project, tree)
        self.assertTrue((tree / ".autocode" / "runs" / "result.json").is_file())
        self.assertFalse((tree.parent / ".1-records").exists())


class CliTests(unittest.TestCase):
    def test_a_completed_worktree_run_delivers_stays_current_and_cleans_up(self):
        flow = test_subprocess.SubprocessFlow(); flow.setUp()
        self.addCleanup(flow.doCleanups)
        env = {**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"}
        run = lambda *args, **kw: subprocess.run([*flow.entry, *args], cwd=flow.root, env=env, text=True,
                                                 capture_output=True, timeout=60, **kw)
        result = run("--workspace", str(flow.project), "--engine", "codex", "--chat", "Build greeting", input="CLI\nyes\n")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Delivered on branch autocode/build-greeting-", result.stdout)
        (tree,) = (flow.project / ".autocode" / "worktrees").iterdir()
        run_dir = next(tree.glob(".autocode/runs/*"))
        status = json.loads(run("--workspace", str(flow.project), "--run-dir", str(run_dir), "--status").stdout)
        self.assertEqual("TASK_COMPLETE", status["status"])
        # From the project without --run-dir: the only run, finished, shown read-only.
        bare = subprocess.run([*flow.entry, "--status"], cwd=flow.project, env=env, text=True,
                              capture_output=True, timeout=60)
        self.assertEqual(0, bare.returncode, bare.stderr)
        self.assertEqual(status, json.loads(bare.stdout))
        self.assertIn(f"Using the saved run {run_dir.resolve()} (TASK_COMPLETE, the latest finished run)", bare.stderr)
        self.assertTrue(status["completion_current"])
        branch = status["task_branch"]
        self.assertIn("greet.py", git(flow.project, "ls-tree", "--name-only", branch))
        result = run("clean-worktrees", "--workspace", str(flow.project), "--yes")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(tree.exists())
        self.assertIn("greet.py", git(flow.project, "ls-tree", "--name-only", branch))
        self.assertEqual("", git(flow.project, "status", "--porcelain", "--untracked-files=all"))


if __name__ == "__main__":
    unittest.main()
