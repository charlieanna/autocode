"""Where a chat's approved plan is built (autocode_build_worktree): the pure rules beside the CLI tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_build_worktree as build_worktree
import autocode_client_actions as actions
import autocode_util as util
import autocode_workspaces as workspaces


def checkout(test: unittest.TestCase) -> Path:
    folder = tempfile.TemporaryDirectory(prefix="build-worktree-")
    test.addCleanup(folder.cleanup)
    root = Path(folder.name).resolve() / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    identity = ["-c", "user.name=T", "-c", "user.email=t@example.test"]
    subprocess.run(["git", "-C", str(root), *identity, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    return root


class RequestTests(unittest.TestCase):
    def test_only_the_root_of_a_person_s_checkout_plans_there_and_builds_elsewhere(self):
        root = checkout(self)
        state = {}
        self.assertTrue(build_worktree.request(state, root))
        self.assertEqual({"state": "planning"}, state["build_worktree"])
        (root / "sub").mkdir()
        for workspace, before in (
            (root / "sub", {}),  # not the checkout's root
            (root, {"workspace_bootstrap": {"selected": str(root), "project": str(root)}}),  # made a project now
            (Path(workspaces.create(root, "task")["workspace"]), {}),  # already a task's own worktree
            (root.parent, {}),  # not in Git
        ):
            with self.subTest(workspace=workspace, state=before):
                state = dict(before)
                self.assertFalse(build_worktree.request(state, workspace))
                self.assertNotIn("build_worktree", state)


class DueTests(unittest.TestCase):
    def test_a_run_moves_before_its_planner_unless_another_run_is_linked_to_this_one(self):
        # #3081: the Planner and the Plan Reviewer work in the run's own worktree, so a pull in the folder
        # cannot strand the plan's review or approval. Requirements stays in the folder.
        planning = {
            "build_worktree": {"state": "planning"},
            "run_dir": "/nowhere/.autocode/runs/r1",
            "next_stage": "astra_discovery",
        }
        self.assertTrue(build_worktree.due(planning))
        self.assertFalse(build_worktree.due({**planning, "next_stage": "requirements_gather"}), "Requirements")
        self.assertFalse(build_worktree.due({**planning, "settings": {"coordination_member": True}}))
        self.assertFalse(build_worktree.due({**planning, "build_worktree": {"state": "building"}}))
        self.assertFalse(build_worktree.due({"run_dir": planning["run_dir"], "next_stage": "astra_discovery"}))
        with mock.patch.object(build_worktree.changeset_store, "find", return_value={"id": "set"}):
            self.assertFalse(build_worktree.due(planning))
        with mock.patch.object(build_worktree.coordination_store, "read", side_effect=ValueError("unreadable")):
            self.assertFalse(build_worktree.due(planning), "an unreadable link record is never moved past")

    def test_a_plan_drafted_in_the_folder_moves_only_once_it_is_approved(self):
        # A run saved while planning in the folder (#3077) is not moved in the middle of its review.
        drafted = {
            "build_worktree": {"state": "planning"},
            "run_dir": "/nowhere/.autocode/runs/r1",
            "next_stage": "astra_challenge",
            "goal_contract": {"approval_status": "draft"},
        }
        with mock.patch.object(build_worktree.contract, "approved", return_value=False):
            self.assertFalse(build_worktree.due(drafted))
        with mock.patch.object(build_worktree.contract, "approved", return_value=True):
            self.assertTrue(build_worktree.due({**drafted, "next_stage": "astra_plan"}))


class ResolveTests(unittest.TestCase):
    def test_a_moved_record_is_followed_only_to_the_run_that_came_from_it(self):
        root = checkout(self)
        old = root / ".autocode" / "runs" / "r1"
        tree = Path(workspaces.create(root, "task")["workspace"])
        child = tree / ".autocode" / "runs" / "r1"
        child.mkdir(parents=True)
        record = {"build_worktree": {"state": "moved", "moved_to": {"workspace": str(tree), "run_dir": str(child)}}}

        def save(origin, workspace=tree):
            saved = {
                "workspace": str(workspace),
                "build_worktree": {"state": "building", "moved_from": {"run_dir": str(origin)}},
            }
            (child / "state.json").write_text(json.dumps(saved))

        save(old)
        found = build_worktree.resolve(root, old, record)
        self.assertEqual((tree, child), found[:2])
        self.assertIsNone(build_worktree.resolve(root, root / ".autocode" / "runs" / "r2", record), "another name")
        self.assertIsNone(build_worktree.resolve(tree, old, record), "another checkout's worktree")
        save(root / ".autocode" / "runs" / "elsewhere")
        self.assertIsNone(build_worktree.resolve(root, old, record), "a run that did not come from this record")
        save(old, workspace=root)
        self.assertIsNone(build_worktree.resolve(root, old, record), "a run that is not in that worktree")
        self.assertIsNone(build_worktree.resolve(root, old, {"build_worktree": {"state": "planning"}}))


class MoveTests(unittest.TestCase):
    def test_a_move_interrupted_after_its_worktree_was_made_finishes_in_that_worktree(self):
        root = checkout(self)
        home = tempfile.TemporaryDirectory(prefix="build-worktree-home-")
        self.addCleanup(home.cleanup)
        run_dir = root / ".autocode" / "runs" / "20261110-090000-chat-a1b2c3"
        run_dir.mkdir(parents=True)
        state = {"task": "Add a greeting", "workspace": str(root), "run_dir": str(run_dir), "status": "RUNNING"}
        state.update(launch_sources={"base_commit": "launch"}, sessions={"glm": "planner"})
        state["build_worktree"] = {"state": "planning"}
        util.atomic_json(run_dir / "state.json", state)
        with mock.patch.dict("os.environ", {"AUTOCODE_HOME": home.name}):
            with mock.patch.object(build_worktree.run_destination, "reserve", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    build_worktree.move(state, root, run_dir, util.atomic_json)
            saved = util.read(run_dir / "state.json")
            intent = saved["build_worktree"]["moving_to"]
            self.assertEqual("planning", saved["build_worktree"]["state"], "the run still plans in the checkout")
            target = build_worktree.move(saved, root, run_dir, util.atomic_json)
        self.assertEqual((intent["workspace"], intent["run_dir"]), (target["workspace"], target["run_dir"]))
        self.assertEqual(1, len(list((root / ".autocode" / "worktrees").iterdir())), "no second worktree")
        child = util.read(Path(target["run_dir"]) / "state.json")
        self.assertEqual(target["workspace"], child["workspace"])
        self.assertEqual(str(run_dir), child["build_worktree"]["moved_from"]["run_dir"])
        self.assertEqual({}, child["sessions"], "provider sessions belong to the folder they ran in")
        self.assertNotIn("launch_sources", child, "an in-place launch record does not hold in the worktree")
        parent = util.read(run_dir / "state.json")
        self.assertEqual(target, build_worktree.moved(parent))
        self.assertEqual(Path(target["run_dir"]), build_worktree.resolve(root, run_dir, parent)[1])

        workspaces.git(root, "worktree", "remove", "--force", target["workspace"])
        self.assertIsNone(build_worktree.resolve(root, run_dir, parent))
        self.assertTrue(
            build_worktree.gone(root, parent).startswith("This chat moved to its own worktree to build. "),
            build_worktree.gone(root, parent),
        )


class NoticeTests(unittest.TestCase):
    def test_the_notice_names_the_branch_and_what_the_build_leaves_out(self):
        self.assertIsNone(build_worktree.notice({"build_worktree": {"state": "planning"}}))
        self.assertIsNone(build_worktree.notice({}))
        record = {
            "state": "building",
            "moved_from": {"workspace": "/home/person/app"},
            "branch": "autocode/add-greeting-0123456789",
            "left_out": [f"notes-{number}.txt" for number in range(7)],
        }
        text = build_worktree.notice({"build_worktree": record})
        self.assertTrue(
            text.startswith(
                "I build this on its own branch, autocode/add-greeting-0123456789, in a separate copy of /home/person/app"
            ),
            text,
        )
        self.assertIn("uncommitted changes and ignored files", text)
        self.assertIn(
            "Not included from your folder: notes-0.txt, notes-1.txt, notes-2.txt, notes-3.txt, notes-4.txt and 2 more.",
            text,
        )
        self.assertTrue(text.endswith("I never merge it myself."), text)
        self.assertNotIn("Not included", build_worktree.notice({"build_worktree": {**record, "left_out": []}}))

    def test_a_run_that_moved_before_planning_says_so_after_the_message_before_the_move(self):
        import autocode_chat_history as chat_history

        record = {
            "state": "building",
            "moved_from": {"workspace": "/home/person/app"},
            "branch": "autocode/add-greeting-0123456789",
            "left_out": [],
            "after_events": 1,
        }
        text = build_worktree.notice({"build_worktree": record})
        self.assertTrue(text.startswith("I plan and build this on its own branch, autocode/add-greeting"), text)
        events = [
            {"kind": "answer", "id": "a1", "actor": "user_cli", "text": "a CLI"},
            {"kind": "brief_feedback", "id": "f1", "actor": "user_cli", "text": "also a README"},
        ]
        state = {"task": "Add a greeting", "user_events": events, "build_worktree": record}
        ids = [row["id"] for row in chat_history.messages(state)]
        self.assertEqual(["initial", "a1", chat_history.WORKTREE_NOTICE, "f1"], ids)
        # Nothing said since the move yet: the notice is the last message.
        ids = [row["id"] for row in chat_history.messages({**state, "user_events": events[:1]})]
        self.assertEqual(["initial", "a1", chat_history.WORKTREE_NOTICE], ids)


class ClientPathTests(unittest.TestCase):
    def test_a_chat_that_moved_to_a_task_worktree_of_its_folder_is_the_same_run(self):
        folder = Path("/home/person/app")
        asked = folder / ".autocode" / "runs" / "r1"
        tree = folder / ".autocode" / "worktrees" / "add-greeting-0123456789"

        def view(workspace, run):
            return {"workspace": str(workspace), "run_dir": str(run)}

        self.assertTrue(actions.same_run(view(tree, tree / ".autocode/runs/r1"), folder, asked))
        self.assertFalse(actions.same_run(view(tree, tree / ".autocode/runs/r2"), folder, asked), "another name")
        other = Path("/home/other/.autocode/worktrees/add-greeting-0123456789")
        self.assertFalse(actions.same_run(view(other, other / ".autocode/runs/r1"), folder, asked), "another folder")
        nested = tree / "deeper"
        self.assertFalse(actions.same_run(view(nested, nested / ".autocode/runs/r1"), folder, asked))

    def test_new_chats_and_the_chat_list_belong_to_the_folder_not_a_worktree(self):
        folder = Path("/home/person/app")
        self.assertEqual(folder, actions.project_folder(folder / ".autocode" / "worktrees" / "add-greeting-0123"))
        self.assertEqual(folder, actions.project_folder(folder))
        self.assertEqual(folder / "worktrees" / "x", actions.project_folder(folder / "worktrees" / "x"))


if __name__ == "__main__":
    unittest.main()
