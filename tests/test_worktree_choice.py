"""Where a chat changes files, and where a new chat may work (autocode_worktree_choice).

Seen 2026-11-11: every VS Code chat started --in-place, so chats in one folder changed the same files
on the same branch, and a chat reading the folder stopped when another one changed it. A chat now says
its worktree and branch, and a new chat chooses a new branch, an existing one, or the folder itself;
two chats never share one worktree or branch, and the folder says who else works there.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import autocode_build_worktree as build_worktree
import autocode_controller_isolation as isolation
import autocode_registry as registry
import autocode_run_view as run_view
import autocode_subcommands as subcommands
import autocode_util as util
import autocode_workspaces as workspaces
import autocode_worktree_choice as choice

IDENTITY = ["-c", "user.name=T", "-c", "user.email=t@example.test"]


def checkout(test: unittest.TestCase) -> Path:
    folder = tempfile.TemporaryDirectory(prefix="worktree-choice-")
    test.addCleanup(folder.cleanup)
    home = tempfile.TemporaryDirectory(prefix="worktree-choice-home-")
    test.addCleanup(home.cleanup)
    patch = mock.patch.dict("os.environ", {"AUTOCODE_HOME": home.name})
    patch.start()
    test.addCleanup(patch.stop)
    root = Path(folder.name).resolve() / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), *IDENTITY, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    return root


def git(root: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(root), *IDENTITY, *argv], capture_output=True, text=True, check=True).stdout


def chat(root: Path, name: str, task: str, **state) -> Path:
    run_dir = root / ".autocode" / "runs" / name
    run_dir.mkdir(parents=True)
    util.atomic_json(run_dir / "state.json", {"task": task, "workspace": str(root), "status": "RUNNING", **state})
    return run_dir


class WhereTests(unittest.TestCase):
    def test_a_chat_names_its_own_worktree_and_branch_or_the_person_s_folder(self):
        root = checkout(self)
        tree = workspaces.create(root, "Add a greeting")
        own = choice.where(
            {"workspace": tree["workspace"], "project_workspace": str(root), "task_branch": tree["branch"]}
        )
        self.assertEqual(("worktree", tree["workspace"], tree["branch"], str(root)), tuple(own.values())[:4])
        self.assertEqual(f"Own worktree · branch {tree['branch']}", own["label"])
        self.assertIn("its own branch", own["sentence"])
        self.assertFalse(own["existing_branch"])
        chose = choice.where(
            {
                "workspace": tree["workspace"],
                "project_workspace": str(root),
                "task_branch": "feature",
                "build_worktree": {"state": "building", "existing_branch": True},
            }
        )
        self.assertTrue(chose["existing_branch"])
        self.assertIn("on your branch", chose["sentence"])

        here = choice.where({"workspace": str(root)})
        self.assertEqual(("folder", "main"), (here["kind"], here["branch"]))
        self.assertEqual("Your folder · branch main", here["label"])
        self.assertIn("can conflict", here["sentence"])

        moving = choice.where({"workspace": str(root), "build_worktree": {"state": "planning"}})
        self.assertEqual(("moving", "main", None), (moving["kind"], moving["branch"], moving["moves_to_branch"]))
        self.assertEqual("Your folder until planning · then a new branch", moving["label"])
        onto = choice.where({"workspace": str(root), "build_worktree": {"state": "planning", "branch": "feature"}})
        self.assertEqual("feature", onto["moves_to_branch"])
        self.assertIn("your branch feature", onto["sentence"])

        project = choice.where({"workspace": str(root), "workspace_bootstrap": {"selected": "x", "project": str(root)}})
        self.assertEqual("project", project["kind"])
        self.assertIsNone(choice.where({}), "no workspace yet")

        git(root, "checkout", "-q", "--detach")
        self.assertIsNone(choice.where({"workspace": str(root)})["branch"], "a detached HEAD")
        self.assertIsNone(choice.branch_of(root.parent), "not a checkout")

    def test_the_status_view_carries_it(self):
        root = checkout(self)
        view = run_view.view({"workspace": str(root), "task": "x", "status": "RUNNING", "iteration": 1})
        self.assertEqual("folder", view["worktree"]["kind"])
        self.assertEqual("main", view["worktree"]["branch"])


class OptionsTests(unittest.TestCase):
    def test_self_build_setup_is_public_before_a_run_and_does_not_change_the_folder(self):
        root = checkout(self)
        (root / "tools").mkdir()
        for name in ("autocode.py", "autocode_controller_isolation.py"):
            (root / "tools" / name).write_text("# controller marker\n")
        (root / "AGENTS.md").write_text("instructions\n")
        before = git(root, "status", "--porcelain")
        with mock.patch.object(isolation, "__file__", str(root / "tools/autocode_controller_isolation.py")):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, choice.cli(["list", "--workspace", str(root), "--json"]))
            listing = json.loads(output.getvalue())
        setup = listing["controller"]
        self.assertFalse(setup["ready"])
        self.assertIn("new worktree", setup["detail"])
        self.assertIn("outside", setup["detail"])
        self.assertEqual(
            [
                {
                    "id": "select_cli",
                    "label": "Select isolated AutoCode CLI",
                    "next_command": ["worktrees", "list", "--workspace", str(root), "--json"],
                }
            ],
            setup["actions"],
        )
        self.assertNotIn("worktree add", choice.text(listing))
        self.assertIn(setup["detail"], choice.text(listing))
        self.assertEqual(before, git(root, "status", "--porcelain"))
        self.assertFalse((root / ".autocode").exists())

    def test_setup_checks_resolved_controller_location_not_just_executable_location(self):
        root = checkout(self)
        (root / "tools").mkdir()
        for name in ("autocode.py", "autocode_controller_isolation.py"):
            (root / "tools" / name).write_text("# marker\n")
        (root / "AGENTS.md").touch()
        link = root.parent / "editable-controller.py"
        link.symlink_to(root / "tools/autocode.py")
        self.assertFalse(isolation.setup_view(root, link)["ready"])
        external = isolation.setup_view(root, root.parent / "installed/autocode.py")
        self.assertTrue(external["ready"])
        self.assertEqual([], external["actions"])
        with self.assertRaisesRegex(ValueError, "Select an AutoCode CLI"):
            isolation.require_isolated(root, link)
        ordinary = isolation.setup_view(root.parent, link)
        self.assertTrue(ordinary["ready"], "ordinary projects still work")

    def test_a_new_chat_takes_its_own_branch_unless_the_person_picks_another_place(self):
        root = checkout(self)
        listing = choice.options(root)
        self.assertEqual(("new_branch", "main"), (listing["default"], listing["branch"]))
        places = {place["id"]: place for place in listing["places"]}
        self.assertEqual(["--build-in-worktree"], places["new_branch"]["start_args"])
        self.assertIsNone(places["new_branch"]["warning"])
        self.assertEqual(["--in-place"], places["folder"]["start_args"])
        self.assertEqual("This folder (branch main)", places["folder"]["label"])
        self.assertIn("can conflict", places["folder"]["warning"], "the folder always warns")
        self.assertNotIn("in this folder now", places["folder"]["warning"], "no other chat works here yet")
        main = places["branch"]["branches"]
        self.assertEqual(["main"], [row["name"] for row in main])
        self.assertIsNone(main[0]["start_args"], "the folder's own branch is worked on in the folder")
        self.assertIn("choose This folder", main[0]["unavailable"])

    def test_a_branch_another_worktree_or_chat_has_is_not_offered(self):
        root = checkout(self)
        git(root, "branch", "feature")
        git(root, "branch", "later")
        git(root, "branch", "autocode/builder-batch-1")
        other = workspaces.create(root, "Fix the parser")
        chat(
            Path(other["workspace"]),
            "r-other",
            "Fix the parser",
            project_workspace=str(root),
            task_branch=other["branch"],
        )
        chat(root, "r-moving", "Add colours", build_worktree={"state": "planning", "branch": "later"})
        chat(root, "r-here", "Rename the CLI")
        chat(root, "r-done", "Old finished chat", status="TASK_COMPLETE")
        archived = chat(root, "r-archived", "Archived chat")
        registry.register_run(root, archived, util.read(archived / "state.json") | {"workspace": str(root)})
        registry.archive_run(root, archived)
        chat(root, "r-moved", "Moved chat", build_worktree={"state": "moved", "moved_to": {}})

        listing = choice.options(root)
        titles = sorted(row["title"] for row in listing["active"])
        self.assertEqual(["Add colours", "Fix the parser", "Rename the CLI"], titles, "finished, archived, moved")
        places = {place["id"]: place for place in listing["places"]}
        rows = {row["name"]: row for row in places["branch"]["branches"]}
        self.assertNotIn("autocode/builder-batch-1", rows, "a Builder batch branch is never a chat's")
        self.assertEqual(["--build-in-worktree", "--worktree-branch", "feature"], rows["feature"]["start_args"])
        self.assertIsNone(rows["feature"]["unavailable"])
        self.assertIsNone(rows[other["branch"]]["start_args"])
        self.assertIn("“Fix the parser”", rows[other["branch"]]["unavailable"])
        self.assertIsNone(rows["later"]["start_args"])
        self.assertIn("“Add colours” plans and builds on it", rows["later"]["unavailable"])
        warning = places["folder"]["warning"]
        self.assertIn("2 other chats work in this folder now: “Rename the CLI”, “Add colours”.", warning)

        self.assertIsNone(choice.branch_problem(root, "feature"))
        self.assertIn("checked out in", choice.branch_problem(root, other["branch"]))
        self.assertIn("“Add colours”", choice.branch_problem(root, "later"))
        self.assertIn("no local branch", choice.branch_problem(root, "missing"))

    def test_a_folder_that_is_not_a_checkout_s_top_offers_only_itself(self):
        root = checkout(self)
        (root / "sub").mkdir()
        (root / "sub" / "nested").mkdir()
        (root / "sub" / "nested" / ".git").mkdir()  # inside a checkout: not a folder of checkouts
        empty = root.parent / "empty"
        empty.mkdir()
        for folder in (root / "sub", root.parent, empty):
            with self.subTest(folder=folder):
                listing = choice.options(folder)
                self.assertEqual(["folder"], [place["id"] for place in listing["places"]])
                self.assertEqual(["--build-in-worktree"], listing["places"][0]["start_args"])
                warning = listing["places"][0]["warning"]
                if folder == root.parent:
                    self.assertIsNotNone(warning)
                else:
                    self.assertIsNone(warning)

    def test_a_project_with_nothing_committed_offers_only_the_folder(self):
        # A worktree would start from AutoCode's empty first commit, without anything the folder holds.
        made = checkout(self).parent / "made"  # checkout: a registry of this test's own
        made.mkdir()
        workspaces.bootstrap(made)
        (made / "hello.py").write_text("print('hi')\n")
        listing = choice.options(made)
        self.assertEqual(["folder"], [place["id"] for place in listing["places"]])
        self.assertEqual("folder", listing["default"])
        self.assertEqual(["--build-in-worktree"], listing["places"][0]["start_args"])
        self.assertEqual(
            "Nothing in this folder is committed yet, so the chat works here.", listing["places"][0]["does"]
        )
        self.assertIsNone(listing["places"][0]["warning"])
        util.git(made, "add", "hello.py")
        util.git(made, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "mine")
        self.assertEqual(["new_branch", "branch", "folder"], [place["id"] for place in choice.options(made)["places"]])

    def test_a_folder_of_checkouts_warns_that_a_chat_there_changes_none_of_them(self):
        """#2066: a parent folder of repositories bootstraps an empty project; the person sees it first."""
        root = checkout(self)
        parent = root.parent
        history = parent / "group" / "history"
        history.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(history)], check=True)
        (parent / "notes").mkdir()
        out = io.StringIO()
        with redirect_stdout(out):
            code = subcommands.dispatch(["worktrees", "list", "--workspace", str(parent), "--json"])
        self.assertEqual(0, code)
        listing = json.loads(out.getvalue())
        self.assertEqual("folder", listing["default"])
        [place] = listing["places"]
        self.assertEqual(["--build-in-worktree"], place["start_args"])
        self.assertEqual(
            f"{parent} is not a Git checkout, but it holds the checkouts project, history. A chat here works "
            "in a new, empty private project, not in any of them. To change one of them, open that checkout "
            "and start the chat there.",
            place["warning"],
        )
        self.assertIn("Warning: " + place["warning"], choice.text(listing))
        only = parent / "group"
        self.assertIn(
            "holds the checkout history. A chat here works in a new, empty private project, not in it. To change it,",
            choice.options(only)["places"][0]["warning"],
        )

    def test_autocode_worktrees_list_prints_the_listing(self):
        root = checkout(self)
        out = io.StringIO()
        with redirect_stdout(out):
            code = subcommands.dispatch(["worktrees", "list", "--workspace", str(root), "--json"])
        self.assertEqual(0, code)
        self.assertEqual(choice.options(root), json.loads(out.getvalue()))
        out = io.StringIO()
        with redirect_stdout(out):
            subcommands.dispatch(["worktrees", "--workspace", str(root)])
        self.assertIn("New worktree and branch (default)", out.getvalue())


class ExistingBranchTests(unittest.TestCase):
    def test_a_chat_plans_and_builds_on_the_branch_the_person_chose_and_keeps_it(self):
        root = checkout(self)
        git(root, "branch", "feature")
        tip = git(root, "rev-parse", "feature").strip()
        state = {}
        self.assertTrue(build_worktree.request(state, root, "feature"))
        self.assertEqual({"state": "planning", "branch": "feature"}, state["build_worktree"])
        with self.assertRaisesRegex(ValueError, "checked out in"):
            build_worktree.request({}, root, "main")
        with self.assertRaisesRegex(ValueError, "cannot move to a worktree"):
            build_worktree.request({}, root / ".git", "feature")

        run_dir = chat(root, "20261111-020000-vscode-a1b2c3", "Add a greeting", build_worktree=state["build_worktree"])
        saved = util.read(run_dir / "state.json") | {"run_dir": str(run_dir)}
        target = build_worktree.move(saved, root, run_dir, util.atomic_json)
        self.assertEqual("feature", target["branch"])
        self.assertEqual("feature", git(Path(target["workspace"]), "branch", "--show-current").strip())
        child = util.read(Path(target["run_dir"]) / "state.json")
        self.assertEqual((tip, "feature"), (child["base_commit"], child["task_branch"]))
        self.assertTrue(child["build_worktree"]["existing_branch"])
        self.assertIn("on your branch, feature,", build_worktree.notice(child))
        self.assertIn("It starts from the last commit on feature;", build_worktree.notice(child))
        self.assertEqual("worktree", choice.where(child)["kind"])
        self.assertTrue(choice.where(child)["existing_branch"])

        # A worktree on an existing branch never takes the branch with it.
        data = workspaces.metadata(Path(target["workspace"]))
        self.assertIs(False, data["branch_owned"])
        for path in Path(target["workspace"], ".autocode", "runs").glob("*/state.json"):
            path.unlink()
        self.assertTrue(workspaces.discard_unused(data))
        self.assertEqual(tip, git(root, "rev-parse", "feature").strip(), "the person's branch stays")

    def test_a_branch_taken_while_the_chat_talked_waits_and_changes_nothing(self):
        root = checkout(self)
        git(root, "branch", "feature")
        run_dir = chat(root, "r1", "Add a greeting", build_worktree={"state": "planning", "branch": "feature"})
        saved = util.read(run_dir / "state.json") | {"run_dir": str(run_dir)}
        elsewhere = root.parent / "elsewhere"
        git(root, "worktree", "add", "-q", str(elsewhere), "feature")
        with self.assertRaisesRegex(build_worktree.checkout_lock.CheckoutBusy, "waits to plan on feature"):
            build_worktree.move(saved, root, run_dir, util.atomic_json)
        self.assertFalse(
            (root / ".autocode" / "worktrees").exists() and any((root / ".autocode" / "worktrees").iterdir())
        )
        self.assertEqual(
            {"state": "planning", "branch": "feature"}, util.read(run_dir / "state.json")["build_worktree"]
        )


if __name__ == "__main__":
    unittest.main()
