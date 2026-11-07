import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_chat_history as chat_history
import autocode_project_delivery as delivery
import autocode_workspaces as workspaces


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class ProjectDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.selected = Path(temporary.name).resolve() / "home"
        self.selected.mkdir()
        (self.selected / "notes.txt").write_text("the person's own file\n")
        self.project = workspaces.bootstrap(self.selected)
        self.baseline = git(self.project, "rev-parse", "HEAD")

    def state(self, **changes):
        state = {
            "status": "TASK_COMPLETE",
            "workflow": {"kind": "build"},
            "task": "hi",
            "run_dir": str(self.project / ".autocode" / "runs" / "20261107-chat-abc"),
            "workspace_bootstrap": {"selected": str(self.selected), "project": str(self.project)},
            "goal_contract": {"body": {"intended_outcome": "A command-line todo list that saves tasks to a file"}},
        }
        state.update(changes)
        return state

    def visible(self):
        return sorted(path.name for path in self.selected.iterdir())

    def build(self, name="todo.py", text="print('todo')\n"):
        (self.project / name).write_text(text)

    def test_a_chat_that_builds_nothing_leaves_nothing_among_the_persons_files(self):
        self.assertEqual("", delivery.deliver(self.state(), self.project))
        self.assertEqual("", delivery.deliver(self.state(workflow={"kind": "discuss"}), self.project))
        self.build()
        self.assertEqual("", delivery.deliver(self.state(workflow={"kind": "discuss"}), self.project))
        self.assertEqual("", delivery.deliver(self.state(status="WAITING_FOR_USER"), self.project))
        self.assertEqual([".autocode", "notes.txt"], self.visible())

    def test_a_completed_build_becomes_a_named_project_on_main(self):
        self.build()
        line = delivery.deliver(self.state(), self.project)

        target = self.selected / "command-line-todo"
        self.assertEqual([".autocode", "command-line-todo", "notes.txt"], self.visible())
        self.assertIn(str(target), line)
        self.assertEqual("main", git(target, "branch", "--show-current"))
        self.assertEqual("print('todo')\n", (target / "todo.py").read_text())
        self.assertEqual(["todo.py"], git(target, "ls-files").split())
        self.assertEqual(str(self.project), git(target, "remote", "get-url", delivery.REMOTE))
        # The scratch project's HEAD, which the completion evidence is pinned to, does not move.
        self.assertEqual(self.baseline, git(self.project, "rev-parse", "HEAD"))
        # Repeating it (a resumed completion prints again) delivers nothing twice.
        self.assertEqual(line, delivery.deliver(self.state(), self.project))
        self.assertEqual(1, int(git(target, "rev-list", "--count", "HEAD")) - 1)

    def test_a_follow_up_fast_forwards_the_delivered_project(self):
        self.build()
        delivery.deliver(self.state(), self.project)
        target = self.selected / "command-line-todo"
        self.build("done.py", "print('done')\n")
        turn = [{"say": "Add a done command", "event_id": "event-1"}]

        line = delivery.deliver(self.state(turns=turn), self.project)

        self.assertIn("updated your project", line)
        self.assertEqual("print('done')\n", (target / "done.py").read_text())
        self.assertEqual(git(self.project, "rev-parse", delivery.BRANCH), git(target, "rev-parse", "HEAD"))
        self.assertEqual({0, 1}, set(delivery.notes(self.state(turns=turn))))

    def test_a_project_with_the_persons_own_changes_is_left_alone(self):
        self.build()
        delivery.deliver(self.state(), self.project)
        target = self.selected / "command-line-todo"
        (target / "todo.py").write_text("print('mine')\n")
        self.build("done.py", "print('done')\n")

        line = delivery.deliver(self.state(turns=[{"say": "Add done", "event_id": "event-1"}]), self.project)

        self.assertIn("left it as it is", line)
        self.assertIn(f"git -C {target} pull {delivery.REMOTE} {delivery.BRANCH}", line)
        self.assertEqual("print('mine')\n", (target / "todo.py").read_text())
        self.assertFalse((target / "done.py").exists())

    def test_an_existing_folder_of_that_name_is_never_reused(self):
        (self.selected / "command-line-todo").mkdir()
        self.build()
        line = delivery.deliver(self.state(), self.project)
        self.assertIn(str(self.selected / "command-line-todo-2"), line)
        self.assertEqual([], list((self.selected / "command-line-todo").iterdir()))

    def test_a_project_outside_a_scratch_store_is_not_delivered(self):
        state = self.state(workspace_bootstrap={"selected": str(self.selected), "project": str(self.selected)})
        self.build()
        self.assertEqual("", delivery.deliver(state, self.project))
        self.assertEqual({}, delivery.notes(state))

    def test_the_chat_shows_each_delivery_after_its_turns_answer(self):
        self.build()
        delivery.deliver(self.state(), self.project)
        state = self.state(
            turns=[{"say": "Add done", "event_id": "event-1", "previous": {"chat_response": "Built it."}}],
            user_events=[{"kind": "brief_feedback", "id": "event-1", "text": "Add done"}],
        )
        rows = chat_history.messages(state, completed=True)
        ids = [row["id"] for row in rows]
        self.assertEqual(ids.index("event-1-response") + 1, ids.index(f"{chat_history.DELIVERY_NOTICE}-0"))
        self.assertIn(
            str(self.selected / "command-line-todo"), rows[ids.index(f"{chat_history.DELIVERY_NOTICE}-0")]["text"]
        )
        notice = rows[ids.index(chat_history.BOOTSTRAP_NOTICE)]["text"]
        self.assertIn("private project", notice)

    def test_names_keep_the_words_that_say_what_the_project_is(self):
        self.assertEqual("todo-python", delivery.name("Build me a todo app in Python"))
        self.assertEqual("command-line-todo", delivery.name("A command-line todo list that saves tasks"))
        self.assertEqual("project", delivery.name("!!!"))
        self.assertEqual("make-it", delivery.name("make it"))


if __name__ == "__main__":
    unittest.main()
