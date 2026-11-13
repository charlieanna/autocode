"""Real Git/process tests for concurrent tasks originating in one project."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import autocode_support as support
import autocode_workspaces as w

from . import test_subprocess


class TaskWorkspaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        w.git(self.project, "init", "-q")
        (self.project / "app.txt").write_text("committed\n")
        w.git(self.project, "add", "app.txt")
        w.git(self.project, "-c", "user.name=Test", "-c", "user.email=t@example.test", "commit", "-qm", "base")

    def test_two_tasks_isolate_files_branches_and_writer_locks(self):
        (self.project / "app.txt").write_text("user unsaved work\n")
        with support.workspace_lock(self.project):
            one = w.create(self.project, "Same task")
            two = w.create(self.project, "Same task")
            left, right = Path(one["workspace"]), Path(two["workspace"])
            with support.workspace_lock(left), support.workspace_lock(right):
                (left / "app.txt").write_text("task one\n")
                (right / "app.txt").write_text("task two\n")
                self.assertEqual("user unsaved work\n", (self.project / "app.txt").read_text())
                with self.assertRaises(support.Paused):
                    with support.workspace_lock(left):
                        pass
        self.assertNotEqual(one["branch"], two["branch"])
        self.assertEqual(one["base_commit"], two["base_commit"])
        self.assertEqual("task one\n", (left / "app.txt").read_text())
        self.assertEqual(left, w.resume_workspace(self.project, one))
        self.assertEqual(str(self.project), w.metadata(right)["project_workspace"])

    def test_unrelated_project_cannot_resume_task(self):
        one = w.create(self.project, "First")
        with self.assertRaises(ValueError):
            w.resume_workspace(self.root, one)

    def test_a_removed_worktree_names_its_archive_and_what_still_works(self):
        """#2402: after merge and clean-worktrees the refusal said only "Saved task worktree is missing"."""
        one = w.create(self.project, "Archived")
        tree = Path(one["workspace"])
        archive = self.project / ".autocode" / "archive" / tree.name
        shutil.copytree(tree / ".autocode", archive)
        subprocess.run(["git", "-C", str(self.project), "worktree", "remove", "--force", str(tree)], check=True)
        with self.assertRaises(ValueError) as refused:
            w.resume_workspace(self.project, one)
        message = str(refused.exception)
        self.assertIn(f"The task worktree {tree} was removed by autocode clean-worktrees", message)
        self.assertIn(f"after branch {one['branch']} was merged", message)
        self.assertIn(f"archived in {archive}", message)
        self.assertIn("autocode bundle --run-dir RUN", message)
        shutil.rmtree(archive)
        with self.assertRaises(ValueError) as gone:
            w.resume_workspace(self.project, one)
        self.assertEqual(
            f"Saved task worktree {tree} is missing or does not belong to this project", str(gone.exception)
        )

    def test_discard_unused_keeps_a_worktree_holding_a_run_or_a_change(self):
        saved = w.create(self.project, "Saved")
        run = Path(saved["workspace"]) / ".autocode/runs/one"
        run.mkdir(parents=True)
        (run / "state.json").write_text("{}")
        changed = w.create(self.project, "Changed")
        (Path(changed["workspace"]) / "app.txt").write_text("edited\n")
        unused = w.create(self.project, "Unused")
        self.assertFalse(w.discard_unused(saved))
        self.assertFalse(w.discard_unused(changed))
        self.assertTrue(w.discard_unused(unused))
        self.assertFalse(w.discard_unused(None))
        self.assertTrue(Path(saved["workspace"]).is_dir())
        self.assertTrue(Path(changed["workspace"]).is_dir())
        self.assertFalse(Path(unused["workspace"]).exists())
        self.assertEqual("", w.git(self.project, "branch", "--list", unused["branch"]))

    def unborn(self, name="unborn"):
        folder = self.root / name
        folder.mkdir()
        w.git(folder, "init", "-q")
        return folder

    def test_a_repository_with_no_commit_gets_an_empty_first_one_and_keeps_the_persons_files_as_they_are(self):
        # A run there stopped and told the person to type `git commit --allow-empty -m 'Start'`.
        folder = self.unborn()
        (folder / "pager.py").write_text("def page_count(total, size):\n    return total // size\n")
        (folder / "staged.py").write_text("x = 1\n")
        w.git(folder, "add", "staged.py")
        before = self.untracked(folder)
        self.assertEqual(["pager.py", "staged.py"], w.own_files(folder))
        self.assertTrue(w.first_commit(folder))
        self.assertEqual(w.BASELINE, w.git(folder, "log", "--format=%s"))
        self.assertEqual("", w.git(folder, "ls-tree", "-r", "HEAD"))  # the person's staged file was not committed
        self.assertEqual(before, self.untracked(folder))
        self.assertIn("A  staged.py", before)
        self.assertIn("?? pager.py", before)
        # Once is enough, and a repository with a commit of its own is never given one.
        self.assertFalse(w.first_commit(folder))
        self.assertFalse(w.first_commit(self.project))
        self.assertEqual("1", w.git(folder, "rev-list", "--count", "HEAD"))
        self.assertEqual("1", w.git(self.project, "rev-list", "--count", "HEAD"))
        self.assertFalse(w.first_commit(self.root))  # not a repository

    def test_a_first_commit_made_in_between_is_kept(self):
        # Two starts at once, or the person committing meanwhile: the commit already there stays.
        folder = self.unborn()
        real = w.git

        def racing(root, *args, **options):
            if args[:1] == ("update-ref",):
                real(
                    folder,
                    "-c",
                    "user.name=T",
                    "-c",
                    "user.email=t@example.test",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "mine",
                )
            return real(root, *args, **options)

        with unittest.mock.patch.object(w, "git", racing):
            self.assertFalse(w.first_commit(folder))
        self.assertEqual("mine", w.git(folder, "log", "--format=%s"))

    def test_a_refusal_from_git_is_raised_as_it_is(self):
        folder = self.unborn()
        real = w.git

        def refusing(root, *args, **options):
            if args[:1] == ("update-ref",):
                raise ValueError("cannot lock ref")
            return real(root, *args, **options)

        with unittest.mock.patch.object(w, "git", refusing), self.assertRaisesRegex(ValueError, "cannot lock ref"):
            w.first_commit(folder)

    def test_a_branch_that_holds_only_autocodes_empty_first_commit_has_nothing_committed(self):
        folder = self.unborn()
        self.assertFalse(w.only_first_commit(folder))  # no commit at all
        w.first_commit(folder)
        self.assertTrue(w.only_first_commit(folder))
        made = self.root / "made"
        made.mkdir()
        self.assertTrue(w.only_first_commit(w.bootstrap(made)))
        # AutoCode's own refs and a task branch still at the first commit hold nothing either.
        (folder / "hello.py").write_text("print('hi')\n")
        tree = w.git(folder, "write-tree")
        other = w.git(folder, "-c", "user.name=A", "-c", "user.email=a@example.test", "commit-tree", tree, "-m", "x")
        w.git(folder, "update-ref", "refs/autocode/launch/run-1", other)
        w.git(folder, "branch", "autocode/task-1")
        self.assertTrue(w.only_first_commit(folder))
        # The person's first commit ends it, and a repository of theirs never qualifies.
        w.git(folder, "add", "hello.py")
        w.git(folder, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "mine")
        self.assertFalse(w.only_first_commit(folder))
        self.assertFalse(w.only_first_commit(self.project))
        self.assertFalse(w.only_first_commit(self.root))
        mine = self.unborn("mine")  # an empty first commit of the person's own is theirs
        w.git(mine, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "--allow-empty", "-qm", "Start")
        self.assertFalse(w.only_first_commit(mine))

    def test_a_task_worktree_starts_from_the_first_commit_autocode_made(self):
        folder = self.unborn()
        created = w.create(folder, "task")
        self.assertEqual(w.git(folder, "rev-parse", "HEAD"), created["base_commit"])
        self.assertEqual(w.BASELINE, w.git(folder, "log", "--format=%s"))
        self.assertEqual([], w.own_files(folder))

    def test_a_branch_with_no_commit_in_a_repository_that_has_some_is_left_alone(self):
        # The person's own arrangement (git checkout --orphan): nothing is committed for them, and the
        # refusal says what is so without a command to type.
        w.git(self.project, "checkout", "-q", "--orphan", "fresh")
        self.assertFalse(w.first_commit(self.project))
        with self.assertRaisesRegex(ValueError, "has no commit yet, and a task starts from a commit") as refused:
            w.create(self.project, "task")
        self.assertNotIn("git -C", str(refused.exception))
        self.assertNotIn("--allow-empty", str(refused.exception))
        with self.assertRaises(ValueError):
            w.git(self.project, "rev-parse", "--verify", "HEAD")
        self.assertFalse((self.project / ".autocode/worktrees").exists())

    def test_non_git_folder_bootstraps_a_safe_task_project(self):
        empty = self.root / "new-project"
        empty.mkdir()
        project = w.bootstrap(empty)
        self.assertEqual(empty.resolve(), project)
        self.assertTrue((project / ".git").is_dir())
        self.assertTrue(w.git(project, "rev-parse", "--verify", "HEAD"))

    def test_bootstrap_refuses_missing_paths_and_folders_inside_a_repository(self):
        missing = self.root / "mistyped"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            w.bootstrap(missing)
        self.assertFalse(missing.exists())
        nested = self.project / "src"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "inside the Git repository"):
            w.bootstrap(nested)
        self.assertEqual([], list(nested.iterdir()))

    def test_populated_folder_gets_a_child_project_without_becoming_a_repository(self):
        populated = self.root / "home-like"
        populated.mkdir()
        (populated / "keep.txt").write_text("do not version this folder")
        project = w.bootstrap(populated)
        # A private scratch project in the folder's own AutoCode store: nothing new among its files.
        self.assertEqual(populated.resolve() / ".autocode" / "projects", project.parent)
        self.assertEqual({".autocode", "keep.txt"}, {path.name for path in populated.iterdir()})
        self.assertTrue((project / ".git").is_dir())
        self.assertFalse((populated / ".git").exists())
        self.assertEqual("do not version this folder", (populated / "keep.txt").read_text())

    def test_a_folder_of_code_is_made_a_repository_where_it_is_with_nothing_committed(self):
        code = self.root / "pager"
        (code / "tests").mkdir(parents=True)
        (code / "pager.py").write_text("def page_count(total, size):\n    return total // size\n")
        (code / "tests" / "test_pager.py").write_text("import pager\n")
        (code / "node_modules" / "left").mkdir(parents=True)
        (code / "node_modules" / "left" / "index.js").write_text("module.exports = 1\n")
        self.assertEqual(code.resolve(), w.bootstrap(code, in_place=True))
        # AutoCode's empty first commit and none of the person's files: they are as they were, untracked.
        self.assertTrue(w.only_first_commit(code))
        self.assertEqual("", w.git(code, "ls-files"))
        self.assertEqual(
            ["pager.py", "tests/test_pager.py"],
            w.git(code, "ls-files", "--others", "--exclude-standard").splitlines(),
        )
        self.assertFalse((code / ".gitignore").exists())
        self.assertFalse((code / ".autocode" / "projects").exists())
        self.assertEqual("module.exports = 1\n", (code / "node_modules" / "left" / "index.js").read_text())
        # A folder that is not one project of code keeps the private project, as does any caller that
        # does not ask for the folder itself.
        papers = self.root / "papers"
        papers.mkdir()
        (papers / "notes.txt").write_text("mine")
        self.assertEqual(papers.resolve() / ".autocode" / "projects", w.bootstrap(papers, in_place=True).parent)
        self.assertFalse((papers / ".git").exists())
        other = self.root / "other"
        other.mkdir()
        (other / "tool.py").write_text("x = 1\n")
        self.assertEqual(other.resolve() / ".autocode" / "projects", w.bootstrap(other).parent)
        self.assertFalse((other / ".git").exists())

    def untracked(self, tree):
        return w.git(tree, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")

    def test_task_worktrees_and_run_files_stay_out_of_the_users_git_status(self):
        one = w.create(self.project, "one")
        tree = Path(one["workspace"])
        (tree / ".autocode/runs/r1").mkdir(parents=True)
        (tree / ".autocode/runs/r1/state.json").write_text("{}")
        self.assertEqual("", self.untracked(self.project))
        self.assertEqual("", w.git(self.project, "add", "--all", "--dry-run"))
        self.assertEqual("", self.untracked(tree))

    def test_an_existing_gitignore_in_the_directory_is_left_alone(self):
        (self.project / ".autocode").mkdir()
        (self.project / ".autocode/.gitignore").write_text("runs/\n")
        w.keep_out_of_git(self.project)
        self.assertEqual("runs/\n", (self.project / ".autocode/.gitignore").read_text())
        component = w.keep_out_of_git(self.project, ".autocode-components")
        (component / "c1").mkdir()
        (component / "c1/app.txt").write_text("component work\n")
        self.assertNotIn(".autocode-components", self.untracked(self.project))

    def test_project_snapshot_excludes_nested_task_edits(self):
        before = support.snapshot(self.project)
        one = w.create(self.project, "one")
        (Path(one["workspace"]) / "app.txt").write_text("other task")
        self.assertEqual(before, support.snapshot(self.project))


class IsolatedCli(unittest.TestCase):
    def test_non_git_workspace_bootstraps_a_task_project_without_initializing_the_parent(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        folder = flow.root / "plain-folder"
        folder.mkdir()
        (folder / "keep.txt").write_text("preserve parent files")
        result = subprocess.run(
            [*flow.entry, "--workspace", str(folder), "--engine", "codex", "--no-chat", "Build calculator"],
            cwd=flow.root,
            env={**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"},
            capture_output=True,
            text=True,
            timeout=35,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        projects = list((folder / ".autocode" / "projects").iterdir())
        self.assertEqual(1, len(projects))
        self.assertTrue((projects[0] / ".git").is_dir())
        self.assertFalse((folder / ".git").exists())

    def test_a_folder_of_code_outside_git_is_worked_on_where_it_is(self):
        # A bug fix in a folder of code that was not a repository ran in an empty private project: on a
        # real run Requirements could not find the file the person named, and the Planner asked where it was.
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        for name, flags in (("plain", ()), ("chat", ("--build-in-worktree",))):
            with self.subTest(name=name):
                folder = flow.root / name
                (folder / "tests").mkdir(parents=True)
                (folder / "pager.py").write_text("def page_count(total, size):\n    return total // size\n")
                (folder / "tests" / "test_pager.py").write_text("import pager\n")
                (folder / "node_modules" / "left").mkdir(parents=True)
                (folder / "node_modules" / "left" / "index.js").write_text("module.exports = 1\n")
                result = subprocess.run(
                    [*flow.entry, "--workspace", str(folder), "--engine", "codex", "--no-chat", *flags, "Fix it"],
                    cwd=flow.root,
                    env={**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                output = result.stdout + result.stderr
                self.assertIn(f"{folder} was not a Git repository, so AutoCode made it one", output)
                self.assertIn("your files were not added to it. The task works on the files there.", output)
                self.assertNotIn("Created task project", output)
                # The run is saved in the folder and works there: no private project, no task worktree.
                [run] = (folder / ".autocode" / "runs").iterdir()
                self.assertTrue((run / "state.json").is_file(), output)
                self.assertFalse((folder / ".autocode" / "projects").exists())
                self.assertFalse((folder / ".autocode" / "worktrees").exists())
                self.assertTrue(w.only_first_commit(folder))
                # The run starts from the person's files: its base holds them, and no installed dependency.
                base = w.git(folder, "rev-parse", f"refs/autocode/launch/{run.name}")
                self.assertEqual(
                    ["pager.py", "tests/test_pager.py"],
                    w.git(folder, "ls-tree", "-r", "--name-only", base).splitlines(),
                )
                status = subprocess.run(
                    [*flow.entry, "--workspace", str(folder), "--run-dir", str(run), "--status", "--json"],
                    cwd=flow.root,
                    env=flow.env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                view = json.loads(status.stdout)
                view = view.get("view", view)
                self.assertEqual(str(folder), view["workspace"])
                self.assertEqual("folder", view["worktree"]["kind"])

    def test_a_repository_with_no_commit_starts_the_task_in_the_folder(self):
        # `git init` and nothing more. A start stopped with a Git command for the person to type, and a
        # chat's start (--build-in-worktree) with Git's own "ambiguous argument 'HEAD'".
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        for name, files, flags in (
            ("empty", {}, ()),
            ("empty-chat", {}, ("--build-in-worktree",)),
            ("code", {"pager.py": "def page_count(total, size):\n    return total // size\n"}, ()),
            ("code-chat", {"pager.py": "x = 1\n", "staged.py": "y = 2\n"}, ("--build-in-worktree",)),
        ):
            with self.subTest(name=name):
                folder = flow.root / name
                folder.mkdir()
                w.git(folder, "init", "-q")
                for path, text in files.items():
                    (folder / path).write_text(text)
                if "staged.py" in files:
                    w.git(folder, "add", "staged.py")
                before = w.git(folder, "status", "--porcelain", "--untracked-files=all")
                result = subprocess.run(
                    [*flow.entry, "--workspace", str(folder), "--engine", "codex", "--no-chat", *flags, "Build it"],
                    cwd=flow.root,
                    env={**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                output = result.stdout + result.stderr
                self.assertIn("This repository had no commit, so AutoCode made an empty first one", output)
                self.assertNotIn("--allow-empty", output)
                self.assertNotIn("ambiguous argument", output)
                self.assertEqual(w.BASELINE, w.git(folder, "log", "--format=%s"))
                self.assertEqual("", w.git(folder, "ls-tree", "-r", "HEAD"))
                # The run was saved and works in the folder itself: a worktree of the empty commit would
                # hide what the person has there.
                self.assertEqual(1, len(list((folder / ".autocode" / "runs").glob("*/state.json"))), output)
                self.assertFalse((folder / ".autocode" / "worktrees").exists())
                self.assertEqual(before, w.git(folder, "status", "--porcelain", "--untracked-files=all"))
                self.assertEqual(bool(files), "your files were not added to it" in output)

    def test_a_second_task_in_a_project_with_nothing_committed_works_in_the_folder(self):
        # A folder AutoCode made a project of holds what the first task built, uncommitted. The second
        # task ("add an option to hello.py") got a worktree of the empty first commit, without hello.py.
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        folder = flow.root / "made"
        folder.mkdir()

        def start(*flags):
            result = subprocess.run(
                [*flow.entry, "--workspace", str(folder), "--engine", "codex", "--no-chat", *flags, "Add an option"],
                cwd=flow.root,
                env={**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"},
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout + result.stderr

        self.assertIn("Created task project", start())
        (folder / "hello.py").write_text("print('hi')\n")
        runs = lambda: len(list((folder / ".autocode" / "runs").glob("*/state.json")))  # noqa: E731
        for count, flags in ((2, ()), (3, ("--build-in-worktree",)), (4, ("--in-place",))):
            with self.subTest(flags=flags):
                output = start(*flags)
                self.assertEqual(count, runs(), output)
                self.assertFalse((folder / ".autocode" / "worktrees").exists(), output)
                self.assertNotIn("Task worktree:", output)
                self.assertEqual(
                    not flags, f"Nothing in {folder} is committed yet, so the task works in the folder" in output
                )
        # Once the person commits, a task gets a worktree of its own again, from that commit.
        w.git(folder, "add", "hello.py")
        w.git(folder, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "mine")
        output = start()
        self.assertIn("Task worktree:", output)
        [tree] = (folder / ".autocode" / "worktrees").iterdir()
        self.assertTrue((tree / "hello.py").is_file())

    def test_read_only_commands_never_make_the_first_commit(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        folder = flow.root / "unborn"
        folder.mkdir()
        w.git(folder, "init", "-q")
        for flag in ("--dry-run", "--explain"):
            subprocess.run(
                [*flow.entry, "--workspace", str(folder), "--engine", "codex", flag, "Build calculator"],
                cwd=flow.root,
                env=flow.env,
                capture_output=True,
                text=True,
                timeout=35,
            )
            with self.assertRaises(ValueError):
                w.git(folder, "rev-parse", "--verify", "HEAD")

    def test_read_only_commands_never_bootstrap_a_plain_folder(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        folder = flow.root / "plain-folder"
        folder.mkdir()
        (folder / "keep.txt").write_text("preserve parent files")
        for flag in ("--dry-run", "--status", "--explain"):
            result = subprocess.run(
                [*flow.entry, "--workspace", str(folder), "--engine", "codex", flag, "Build calculator"],
                cwd=flow.root,
                env=flow.env,
                capture_output=True,
                text=True,
                timeout=35,
            )
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("not a Git repository", result.stderr)
        missing = flow.root / "mistyped"
        result = subprocess.run(
            [*flow.entry, "--workspace", str(missing), "--engine", "codex", "--no-chat", "Build calculator"],
            cwd=flow.root,
            env=flow.env,
            capture_output=True,
            text=True,
            timeout=35,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["keep.txt"], [p.name for p in folder.iterdir()])
        self.assertFalse(missing.exists())

    def test_explain_in_a_new_git_workspace_never_starts_a_provider_or_creates_a_run(self):
        from autocode_stop_explanations import explain

        expected = explain("RUNNING")
        expected_text = "\n\n".join(
            expected[key] for key in ("what_happened", "what_it_means", "what_the_command_does")
        )
        for extra in ([], ["--in-place"]):
            with self.subTest(extra=extra):
                flow = test_subprocess.SubprocessFlow()
                flow.setUp()
                self.addCleanup(flow.doCleanups)
                marker = flow.root / "provider-was-started"
                codex = flow.root / "fixture-bin/codex"
                codex.write_text(
                    "#!"
                    + sys.executable
                    + "\nfrom pathlib import Path\n"
                    + "Path("
                    + repr(str(marker))
                    + ").touch()\nraise SystemExit(1)\n"
                )
                before_worktrees = w.git(flow.project, "worktree", "list", "--porcelain")
                result = subprocess.run(
                    [
                        *flow.entry,
                        "--workspace",
                        str(flow.project),
                        "--engine",
                        "codex",
                        "--no-chat",
                        "--explain",
                        *extra,
                        "Build calculator",
                    ],
                    cwd=flow.root,
                    env=flow.env,
                    capture_output=True,
                    text=True,
                    timeout=35,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(expected_text, result.stdout.strip())
                self.assertFalse(marker.exists(), "Explanation must not launch a provider")
                self.assertFalse((flow.project / ".autocode").exists(), "Explanation must not create a run")
                self.assertEqual(before_worktrees, w.git(flow.project, "worktree", "list", "--porcelain"))

    def test_a_launch_stopped_before_its_first_save_leaves_no_worktree_or_branch(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        path = os.pathsep.join([str(flow.root / "fixture-bin"), str(Path(sys.executable).parent), "/usr/bin", "/bin"])
        if shutil.which("opencode", path=path):
            self.skipTest("an opencode on the system PATH would start the run")
        result = subprocess.run(
            [*flow.entry, "--workspace", str(flow.project), "--engine", "opencode", "--no-chat", "Build greeting"],
            cwd=flow.root,
            env={**flow.env, "PATH": path},
            capture_output=True,
            text=True,
            timeout=35,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("OpenCode is not on PATH", result.stderr)
        self.assertIn("Removed the unused task worktree", result.stderr)
        self.assertEqual([], list(flow.project.glob(".autocode/worktrees/*")))
        self.assertEqual(1, len(w.git(flow.project, "worktree", "list", "--porcelain").split("\n\n")))
        self.assertEqual("", w.git(flow.project, "branch", "--list", "autocode/*"))

    def test_an_in_place_launch_stopped_before_its_first_save_leaves_no_run_directory(self):
        """The chat starts runs in place with --new-run-dir; a refusal left RUN/writer.lock alone, and
        `autocode runs` and the chat's /runs listed it as "cannot be read" from then on."""
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        path = os.pathsep.join([str(flow.root / "fixture-bin"), str(Path(sys.executable).parent), "/usr/bin", "/bin"])
        if shutil.which("opencode", path=path):
            self.skipTest("an opencode on the system PATH would start the run")
        run_dir = flow.project / ".autocode" / "runs" / "chat-refused"
        result = subprocess.run(
            [
                *flow.entry,
                "--workspace",
                str(flow.project),
                "--engine",
                "opencode",
                "--in-place",
                "--no-chat",
                "--new-run-dir",
                str(run_dir),
                "Build greeting",
            ],
            cwd=flow.root,
            env={**flow.env, "PATH": path},
            capture_output=True,
            text=True,
            timeout=35,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("OpenCode is not on PATH", result.stderr)
        self.assertIn(f"Removed the empty run directory {run_dir}", result.stderr)
        self.assertFalse(run_dir.exists())
        self.assertEqual([], list((flow.project / ".autocode" / "runs").glob("*")))

    def test_runs_leave_the_users_git_status_showing_only_source_changes(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        env = {**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"}
        status = lambda tree: w.git(tree, "status", "--porcelain", "--untracked-files=all")
        for extra in ([], ["--in-place"]):
            with self.subTest(extra=extra):
                result = subprocess.run(
                    [
                        *flow.entry,
                        "--workspace",
                        str(flow.project),
                        "--engine",
                        "codex",
                        "--no-chat",
                        *extra,
                        "Build greeting",
                    ],
                    cwd=flow.root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=35,
                )
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertTrue(list(flow.project.glob(".autocode/**/runs/*/state.json")))
                self.assertEqual("", status(flow.project))
        for tree in flow.project.glob(".autocode/worktrees/*"):
            self.assertEqual("", status(tree))

    def test_two_concurrent_cli_tasks_and_resume_from_original_project(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        env = {**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"}
        command = [*flow.entry, "--workspace", str(flow.project), "--engine", "codex", "--no-chat"]
        processes = [
            subprocess.Popen(
                [*command, title], cwd=flow.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            for title in ("Build greeting one", "Build greeting two")
        ]
        outputs = []
        for process in processes:
            out, err = process.communicate(timeout=35)
            outputs.append(out + err)
            self.assertEqual(2, process.returncode, out + err)
        runs = list(flow.project.glob(".autocode/worktrees/*/.autocode/runs/*/state.json"))
        # Both launches exit 2 whether or not they created a run, so say why one did not.
        self.assertEqual(2, len(runs), "\n---\n".join(outputs))
        states = [json.loads(path.read_text()) for path in runs]
        self.assertEqual(2, len({s["workspace"] for s in states}))
        for path, state in zip(runs, states, strict=False):
            self.assertEqual(str(flow.project), state["project_workspace"])
            result = subprocess.run(
                [*flow.entry, "--workspace", str(flow.project), "--run-dir", str(path.parent), "--status"],
                cwd=flow.root,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(state["workspace"], json.loads(result.stdout)["workspace"])
        # Without --run-dir the project holds two unfinished runs: --status lists both, picks neither.
        result = subprocess.run(
            [*flow.entry, "--status"], cwd=flow.project, env=env, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stdout)
        self.assertIn("2 unfinished AutoCode runs", result.stderr)
        for path in runs:
            self.assertIn(f"autocode --run-dir {path.parent} --status", result.stderr)
        self.assertFalse((flow.project / "greet.py").exists())


if __name__ == "__main__":
    unittest.main()
