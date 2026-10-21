"""AutoCode's own Git calls: hooks off, a time limit, and one lock for creating worktrees (#1123)."""

import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_util as util
import autocode_workspaces as workspaces


def plain(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class GitHelperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="git-helper-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        self.root.mkdir()
        plain(self.root, "init", "-q", "-b", "main")
        plain(self.root, "commit", "-q", "--allow-empty", "-m", "base")

    def test_it_returns_stripped_output_and_raises_git_s_own_message_on_failure(self):
        self.assertEqual("main", util.git(self.root, "rev-parse", "--abbrev-ref", "HEAD"))
        with self.assertRaisesRegex(ValueError, "not a git command|unknown revision|bad revision|Needed a single"):
            util.git(self.root, "rev-parse", "--verify", "no-such-ref")
        self.assertEqual("", util.git(self.root, "rev-parse", "--verify", "-q", "no-such-ref", check=False))

    def test_a_users_hook_does_not_run_inside_autocodes_own_git_call(self):
        marker = self.root / "hook-ran"
        hook = self.root / ".git/hooks/post-checkout"
        hook.write_text(f'#!/bin/sh\necho ran >> "{marker}"\n')
        hook.chmod(0o755)
        plain(self.root, "worktree", "add", "-q", "-b", "control", str(self.root.parent / "control"))
        self.assertTrue(marker.exists(), "the control: Git does run the user's post-checkout hook")
        marker.unlink()
        util.git(self.root, "worktree", "add", "-q", "-b", "isolated", str(self.root.parent / "isolated"))
        self.assertFalse(marker.exists(), "AutoCode's own worktree add ran the user's hook")

    def test_a_git_that_does_not_finish_is_stopped_with_a_clear_error(self):
        slow = self.root.parent / "slow-bin"
        slow.mkdir()
        (slow / "git").write_text("#!/bin/sh\nsleep 30\n")
        (slow / "git").chmod(0o755)
        environment = {**os.environ, "PATH": f"{slow}{os.pathsep}{os.environ['PATH']}"}
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, r"git worktree add did not finish in 0\.5 seconds"):
            util.git(self.root, "worktree", "add", "x", timeout=0.5, env=environment)
        self.assertLess(time.monotonic() - started, 10)

    def test_every_call_has_a_time_limit_by_default(self):
        with patch.object(util.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok\n", "")) as run:
            self.assertEqual("ok", util.git(self.root, "status"))
        self.assertEqual(util.GIT_TIMEOUT, run.call_args.kwargs["timeout"])
        self.assertIn("core.hooksPath=" + os.devnull, run.call_args.args[0])

    def test_the_workspaces_wrapper_is_the_same_helper(self):
        self.assertIs(util.git, workspaces.git)


class WorktreeLockTests(unittest.TestCase):
    def test_a_task_worktree_waits_for_another_being_created_in_the_same_project(self):
        # #180 serialized task worktree creation across processes with a file lock: two tasks started
        # together in one project each run `git worktree add`, which git does not make safe.
        with tempfile.TemporaryDirectory(prefix="worktree-lock-") as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            plain(project, "init", "-q", "-b", "main")
            plain(project, "commit", "-q", "--allow-empty", "-m", "base")
            held, release, added = threading.Event(), threading.Event(), []

            def other_process():
                with workspaces.creating(project):  # another autocode process creating a task worktree
                    held.set()
                    release.wait(10)

            holder = threading.Thread(target=other_process)
            holder.start()
            self.assertTrue(held.wait(10))
            real = workspaces.git
            with patch.object(
                workspaces,
                "git",
                side_effect=lambda *args, **kwargs: (
                    "worktree" in args and added.append(time.monotonic()),
                    real(*args, **kwargs),
                )[1],
            ):
                worker = threading.Thread(target=workspaces.create, args=(project, "task x"))
                worker.start()
                time.sleep(0.6)
                self.assertEqual([], added, "a worktree was added while another was being created")
                released = time.monotonic()
                release.set()
                worker.join(30)
            holder.join(10)
            self.assertEqual(1, len(added))
            self.assertGreaterEqual(added[0], released)


if __name__ == "__main__":
    unittest.main()
