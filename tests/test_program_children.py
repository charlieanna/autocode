"""The program's adapter to its child runs (autocode_program_children).

The status-view mapping is tested on real status views (autocode_run_view)
of small saved states. The invocation rules are tested against a stand-in
child CLI, patched in at the subprocess boundary the task-run client uses:
it saves a run under the worktree and answers ``--status`` with the view of
what it saved. No provider and no Git repository are involved.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_program as program
import autocode_program_children as children
import autocode_run_view as run_view
import autocode_taskrun as taskrun

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def view(status, **state):
    return run_view.view({"status": status, **state})


class ApplyViewTests(unittest.TestCase):
    def mapped(self, record_status, child_status, **record):
        record = {"status": record_status, **record}
        children.apply_view(record, view(child_status))
        return record

    def test_a_completed_child_is_complete_once(self):
        record = self.mapped("RUNNING", "TASK_COMPLETE")
        self.assertEqual(("COMPLETE", "TASK_COMPLETE"), (record["status"], record["run_status"]))
        self.assertIn("finished_at", record)
        self.assertIsNone(record["needs"])
        for status in ("COMPLETE", "CONFLICT"):
            again = self.mapped(status, "TASK_COMPLETE", finished_at="earlier")
            self.assertEqual((status, "earlier"), (again["status"], again["finished_at"]))

    def test_a_reopened_child_is_no_longer_complete(self):
        for status in ("COMPLETE", "CONFLICT"):
            record = self.mapped(status, "RUNNING", finished_at="earlier")
            self.assertEqual("WAITING", record["status"])
            self.assertNotIn("finished_at", record)

    def test_a_child_at_a_question_or_plan_approval_waits(self):
        for status in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
            self.assertEqual("WAITING", self.mapped("RUNNING", status)["status"])

    def test_any_other_stop_pauses_and_running_keeps_the_record(self):
        self.assertEqual("PAUSED", self.mapped("RUNNING", "PAUSED_REPORT_REPAIR_LIMIT")["status"])
        self.assertEqual("PAUSED", self.mapped("WAITING", "BLOCKED_ENVIRONMENT")["status"])
        for status in ("RUNNING", "WAITING", "PAUSED", "FAILED"):
            self.assertEqual(status, self.mapped(status, "RUNNING")["status"])

    def test_merged_is_never_downgraded(self):
        for status in ("RUNNING", "AWAITING_GOAL_APPROVAL", "PAUSED_REPORT_REPAIR_LIMIT", "TASK_COMPLETE"):
            record = self.mapped("MERGED", status)
            self.assertEqual(("MERGED", status), (record["status"], record["run_status"]))

    def test_an_undisplayed_plan_is_left_to_a_person(self):
        # The view asks to "continue" until the plan is displayed; the program still waits for a person.
        record = self.mapped("RUNNING", "AWAITING_GOAL_APPROVAL")
        self.assertEqual({"kind": "continue"}, record["needs"])
        self.assertFalse(program.resumable(record))
        self.assertTrue(program.resumable(self.mapped("WAITING", "RUNNING")))

    def test_needs_and_progress_say_what_the_child_waits_for(self):
        record = {"status": "RUNNING"}
        displayed = {**view("AWAITING_GOAL_APPROVAL", displayed_goal="r1:abc"),
                     "progress": {"line": "Waiting for you to approve the plan", "headline": "Waiting for you"}}
        children.apply_view(record, displayed)
        self.assertEqual({"kind": "approve_plan", "token": "r1:abc"}, record["needs"])
        self.assertEqual("Waiting for you to approve the plan", record["progress"])
        children.apply_view(record, view("RUNNING"))
        self.assertEqual({"kind": "continue"}, record["needs"])
        self.assertNotIn("progress", record)

    def test_a_question_keeps_what_kind_of_request_it_is(self):
        # A program must tell a child asking to change what it inherited from an ordinary question (#23).
        record = {"status": "RUNNING"}
        asked = {"status": "WAITING_FOR_USER", "needs": {
            "kind": "answer", "request_kind": "goal_change", "resolver_request_id": "req-1",
            "resolver_token": "secret-token", "resolver_scope": "goal_change",
            "questions": [{"id": "Q1", "question": "Drop P2?", "why": "...", "options": ["yes", "no"]}]}}
        children.apply_view(record, asked)
        self.assertEqual({"kind": "answer", "request_kind": "goal_change", "resolver_scope": "goal_change",
                          "resolver_request_id": "req-1", "questions": [{"id": "Q1", "question": "Drop P2?"}]},
                         record["needs"])

    def test_a_view_without_a_status_fails_the_workstream(self):
        record = {"status": "WAITING", "run_status": "RUNNING", "needs": {"kind": "continue"}, "progress": "Working"}
        children.apply_view(record, {"needs": None})
        self.assertEqual(("FAILED", None), (record["status"], record["run_status"]))
        self.assertFalse({"needs", "progress"} & set(record))  # what the child needs is unknown


class FakeChild:
    """A stand-in for the child CLI behind TaskRun's subprocess call."""

    def __init__(self):
        self.status = "RUNNING"  # what each start or relaunch saves
        self.saved = {}  # other state each start or relaunch saves
        self.progress = None  # the view's progress line
        self.exit = 2
        self.stderr = ""
        self.creates_run = True
        self.status_fails = False
        self.calls = []
        self.started = 0

    def __call__(self, command, **kwargs):
        if command[0] == "git":
            raise AssertionError("the adapter runs no git commands")
        self.calls.append((list(command), kwargs))
        if kwargs.get("cwd") is not None and not Path(kwargs["cwd"]).is_dir():
            raise FileNotFoundError(2, "No such file or directory", str(kwargs["cwd"]))  # as subprocess.run does
        workspace = Path(command[command.index("--workspace") + 1])
        if "--status" in command:
            state = Path(command[command.index("--run-dir") + 1]) / "state.json"
            if self.status_fails or not state.is_file():
                return subprocess.CompletedProcess(command, 1, "", "autocode: no saved run\n")
            shown = run_view.view(json.loads(state.read_text()))
            if self.progress:
                shown["progress"] = {"line": self.progress}
            return subprocess.CompletedProcess(command, 0, json.dumps({"view": shown}), "")
        if "--run-dir" in command:
            run = Path(command[command.index("--run-dir") + 1])
        elif self.creates_run:
            self.started += 1
            run = workspace / f".autocode/runs/run-{self.started}"
            run.mkdir(parents=True)
        else:
            return subprocess.CompletedProcess(command, self.exit, "", self.stderr)
        (run / "state.json").write_text(json.dumps({**self.saved, "status": self.status}))
        return subprocess.CompletedProcess(command, self.exit, "child output\n", self.stderr)


class InvocationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "worktree"
        self.workspace.mkdir()
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.child = FakeChild()
        for target, name in ((taskrun.subprocess, "run"), (taskrun, "run_captured")):
            patcher = patch.object(target, name, side_effect=self.child)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.record = {"status": "RUNNING", "workspace": str(self.workspace)}

    def start(self, record=None):
        record = self.record if record is None else record
        children.prepare_start(record, self.workspace)  # as the program does before saving the record
        return children.start(record, self.workspace, "PROGRAM WORKSTREAM a (code)",
                              ["--engine", "codex", "--astra-model", "m"], log_dir=self.logs)

    def foreign_run(self, name="person"):
        """A run that is not the workstream's, e.g. a person's own run in the shared integration worktree."""
        run = self.workspace / ".autocode/runs" / name
        run.mkdir(parents=True)
        (run / "state.json").write_text('{"status": "TASK_COMPLETE"}')
        return run

    def launches(self):
        return [command for command, _ in self.child.calls if "--status" not in command]

    def test_start_passes_start_options_once_and_runs_in_the_worktree(self):
        self.start()
        command, kwargs = self.child.calls[0]
        self.assertEqual([*children.CHILD_COMMAND, "PROGRAM WORKSTREAM a (code)", "--in-place", "--no-chat",
                          "--engine", "codex", "--astra-model", "m", "--workspace", str(self.workspace)], command)
        self.assertEqual(self.workspace, kwargs["cwd"])
        self.assertEqual(str(self.workspace / ".autocode/runs/run-1"), self.record["run_dir"])
        self.assertEqual(command, self.record["command"])
        self.record["status"] = "RUNNING"
        children.advance(self.record, log_dir=self.logs)
        self.assertEqual([*children.CHILD_COMMAND, "--no-chat", "--workspace", str(self.workspace),
                          "--run-dir", self.record["run_dir"]], self.launches()[1])

    def test_exit_two_with_a_running_view_waits(self):
        self.start()
        self.assertEqual(("WAITING", "RUNNING", 2), (self.record["status"], self.record["run_status"],
                                                     self.record["exit_code"]))
        self.assertEqual("child output\n", (self.logs / "stdout.log").read_text())

    def test_a_relaunch_the_child_refuses_with_exit_two_still_waits(self):
        # e.g. a second writer refused by the child's own workspace lock.
        self.start()
        self.record["status"] = "RUNNING"
        self.child.stderr = "autocode: another process holds the run lock\n"
        children.advance(self.record, log_dir=self.logs)
        self.assertEqual(("WAITING", 2), (self.record["status"], self.record["exit_code"]))
        self.assertIn("run lock", (self.logs / "stderr.log").read_text())

    def test_any_other_exit_with_a_running_view_fails(self):
        self.child.exit = 1
        self.start()
        self.assertEqual(("FAILED", "RUNNING", 1), (self.record["status"], self.record["run_status"],
                                                    self.record["exit_code"]))
        self.assertIn("run_dir", self.record)  # a later retry resumes this run

    def test_a_start_that_fails_after_saving_its_run_is_reattached(self):
        self.foreign_run()
        self.child.exit, self.child.status = 1, "AWAITING_GOAL_APPROVAL"
        self.start()
        self.assertEqual(str(self.workspace / ".autocode/runs/run-1"), self.record["run_dir"])
        self.assertEqual(("WAITING", "AWAITING_GOAL_APPROVAL"), (self.record["status"], self.record["run_status"]))

    def test_a_start_without_a_run_fails(self):
        self.foreign_run()  # never adopted in place of the run the start did not create
        self.child.creates_run, self.child.exit, self.child.stderr = False, 2, "autocode: startup failed\n"
        self.assertIsNone(self.start())
        self.assertEqual(("FAILED", 2), (self.record["status"], self.record["exit_code"]))
        self.assertNotIn("run_dir", self.record)
        self.assertEqual("autocode: startup failed\n", (self.logs / "stderr.log").read_text())
        self.assertIsNone(children.read(self.record))
        self.assertNotIn("run_dir", self.record)

    def test_a_start_never_adopts_a_run_already_in_the_worktree(self):
        foreign = self.foreign_run()
        self.child.status, self.child.exit = "TASK_COMPLETE", 0
        self.start()
        self.assertEqual(str(self.workspace / ".autocode/runs/run-1"), self.record["run_dir"])
        self.assertEqual(1, len(self.launches()))
        self.assertNotIn("--run-dir", self.launches()[0])
        self.assertFalse([command for command, _ in self.child.calls if str(foreign) in command])
        self.assertEqual("COMPLETE", self.record["status"])

    def test_several_new_runs_fail_with_the_old_message(self):
        self.foreign_run()
        record = {"status": "RUNNING", "workspace": str(self.workspace), "needs": {"kind": "continue"}}
        children.prepare_start(record, self.workspace)
        for name in ("one", "two"):
            self.foreign_run(name)
        self.assertIsNone(children.read(record))
        self.assertEqual(("FAILED", children.MULTIPLE), (record["status"], record["error"]))
        self.assertNotIn("needs", record)
        self.assertEqual([], self.child.calls)

    def test_an_unreadable_status_leaves_run_status_unknown(self):
        self.child.status, self.child.saved, self.child.progress = (
            "AWAITING_GOAL_APPROVAL", {"displayed_goal": "r1:abc"}, "Waiting for you to approve the plan")
        self.start()
        self.assertEqual(({"kind": "approve_plan", "token": "r1:abc"}, "Waiting for you to approve the plan"),
                         (self.record["needs"], self.record["progress"]))
        self.child.status_fails = True
        self.assertIsNone(children.read(self.record))
        self.assertEqual(("FAILED", None), (self.record["status"], self.record["run_status"]))
        self.assertTrue(self.record["error"].startswith("Cannot read child status: status exited 1"))
        # Neither the old approval token nor the old progress line describes a child whose state is unknown.
        self.assertFalse({"needs", "progress"} & set(self.record))

    def test_a_removed_worktree_fails_the_record_instead_of_raising(self):
        self.child.status, self.child.saved = "AWAITING_GOAL_APPROVAL", {"displayed_goal": "r1:abc"}
        self.start()
        shutil.rmtree(self.workspace)  # e.g. `git worktree remove`
        self.assertIsNone(children.read(self.record))
        self.assertEqual(("FAILED", None), (self.record["status"], self.record["run_status"]))
        self.assertIn("No such file or directory", self.record["error"])
        self.assertNotIn("needs", self.record)
        self.record["status"] = "RUNNING"  # a relaunch fails the same way
        self.assertIsNone(children.advance(self.record, log_dir=self.logs))
        self.assertEqual(("FAILED", None), (self.record["status"], self.record["run_status"]))
        fresh = {"status": "RUNNING", "workspace": str(self.workspace)}
        self.assertIsNone(self.start(fresh))
        self.assertEqual("FAILED", fresh["status"])
        self.assertIn("start could not run", fresh["error"])

    def test_reading_a_record_without_a_run_does_nothing(self):
        self.assertIsNone(children.read(self.record))
        self.assertEqual({"status": "RUNNING", "workspace": str(self.workspace)}, self.record)
        self.assertIsNone(children.read({"status": "PENDING"}))
        self.assertEqual([], self.child.calls)

    def test_an_interrupted_controller_reattaches_to_the_run_it_started(self):
        self.foreign_run()
        children.prepare_start(self.record, self.workspace)
        saved = dict(self.record)  # the checkpoint the program saves before invoking the child
        self.start()
        lost = {**saved, "status": "RUNNING"}
        self.assertEqual("RUNNING", children.read(lost)["status"])
        self.assertEqual(self.record["run_dir"], lost["run_dir"])
        self.assertTrue(program.resumable(lost))
        # Without the saved boundary nothing is adopted.
        self.assertIsNone(children.read({"status": "RUNNING", "workspace": str(self.workspace)}))


class BoundaryTests(unittest.TestCase):
    """The program reaches child runs only through the adapter, and the adapter only through TaskRun."""

    def tree(self, name):
        return ast.parse((TOOLS / name).read_text())

    def test_the_adapter_never_opens_a_checkpoint_or_lists_runs(self):
        tree = self.tree("autocode_program_children.py")
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node)}
        constants = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                     and isinstance(node.value, str) and id(node) not in docstrings}
        self.assertFalse({text for text in constants if "state.json" in text or ".autocode/runs" in text})
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {"glob", "rglob", "iterdir", "read_text", "open"})
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names}
        self.assertEqual({"annotations", "nullcontext", "Path", "autocode_taskrun", "autocode_util"}, imported)

    def test_running_and_reading_a_program_never_opens_a_child_checkpoint(self):
        tree = self.tree("autocode_program.py")
        self.assertNotIn("runs_before", ast.unparse(tree))
        self.assertNotIn(".autocode/runs", ast.unparse(tree))
        # `program derive` still reads the approved plan from its run's checkpoint (#22 moves it next).
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name != "cli_derive"]
        for function in functions:
            for node in ast.walk(function):
                if isinstance(node, ast.BinOp) and getattr(node.right, "value", None) == "state.json":
                    # Only the program's own checkpoint: .autocode/programs/<key>/state.json.
                    self.assertEqual("program_dir", getattr(node.left, "id", None), f"{function.name}: {ast.unparse(node)}")
            calls = {node.func.attr for node in ast.walk(function)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
            self.assertNotIn("glob", calls, function.name)


if __name__ == "__main__":
    unittest.main()
