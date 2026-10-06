"""A command that names no run acts on the one saved run it can only mean (autocode_run_finder).

Real Git projects and task worktrees in temporary directories, with hand-written run
states; the CLI-level tests drive autocode.main() in process with the provider patched
to fail, so nothing here can reach a model.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

import autocode
import autocode_args
import autocode_opencode as opencode
import autocode_program
import autocode_run_finder as finder
import autocode_subcommands
import autocode_workspaces as w
from providers import opencode as opencode_provider

TASK = "Add greeting"


def git(root, *args):
    return w.git(root, *args)


def tree_snapshot(root: Path) -> dict:
    """Every path under root with its kind and, for files, bytes and mtime."""
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            found[str(path)] = ("link", os.readlink(path))
        elif path.is_file():
            found[str(path)] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
        else:
            found[str(path)] = ("dir",)
    return found


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        # A detached maintenance child must not race the read-only tree snapshots.
        git(self.project, "config", "maintenance.auto", "false")
        (self.project / "app.txt").write_text("committed\n")
        git(self.project, "add", "app.txt")
        git(self.project, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "base")
        home = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        home.start()
        self.addCleanup(home.stop)
        self.hour = 0

    def run_in(self, checkout, name=None, *, status="WAITING_FOR_USER", task=TASK, **extra):
        """A saved run in ``checkout``, each one created an hour after the previous one."""
        self.hour += 1
        name = name or f"20261004-{self.hour:02d}0000-run-{self.hour}"
        run = checkout / ".autocode" / "runs" / name
        run.mkdir(parents=True)
        state = {"version": 3, "task": task, "workspace": str(checkout), "status": status, "iteration": 1,
                 "sessions": {}, "stages": [], "history": [], "next_stage": "astra_plan",
                 "created_at": f"2026-10-04T{self.hour:02d}:00:00+00:00", **extra}
        (run / "state.json").write_text(json.dumps(state))
        return run

    def worktree(self, task=TASK):
        return Path(w.create(self.project, task)["workspace"])

    def worktree_run(self, task=TASK, **extra):
        tree = self.worktree(task)
        return tree, self.run_in(tree, task=task, project_workspace=str(self.project), **extra)

    def owned_by_program(self, tree, run):
        """Record ``tree`` and ``run`` as a workstream of an ``autocode program``."""
        state = self.project / ".autocode" / "programs" / f"p{self.hour}" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"project_workspace": str(self.project), "workstreams": {
            "api": {"workspace": str(tree), "run_dir": str(run)}}}))

    def refused(self, start, action="advance", flags="", unit=None):
        with self.assertRaises(finder.RunNotFound) as caught:
            finder.choose(start, action, flags, unit)
        return str(caught.exception)

    def parse(self, *argv, cwd=None, unit=None):
        with patch.object(Path, "cwd", return_value=cwd or self.project), \
             contextlib.redirect_stderr(io.StringIO()) as error:
            args, _ = autocode_args.parse(unit, list(argv), opencode.DEFAULT_MODELS)
        return args, error.getvalue()

    def parse_error(self, *argv, cwd=None, unit=None):
        with patch.object(Path, "cwd", return_value=cwd or self.project), \
             contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as caught:
            autocode_args.parse(unit, list(argv), opencode.DEFAULT_MODELS)
        self.assertEqual(2, caught.exception.code)
        # A usage error, or a finder refusal: the message alone, so the runs it lists stay readable.
        self.assertTrue(error.getvalue().startswith(("usage:", "autocode: ")), error.getvalue())
        if error.getvalue().startswith("autocode: "):
            self.assertNotIn("usage:", error.getvalue())
        self.assertNotIn("Using the saved run", error.getvalue(), "no notice before a refusal")
        return error.getvalue()


class WhereRunsAreFound(Fixture):
    def test_an_in_place_run_is_found_from_the_project_root(self):
        run = self.run_in(self.project)
        chosen = finder.choose(self.project, "advance")
        self.assertEqual((run, self.project, "WAITING_FOR_USER"), (chosen.run_dir, chosen.workspace, chosen.status))
        self.assertEqual("the only unfinished run", chosen.reason)

    def test_a_worktree_run_is_found_from_the_project_the_worktree_its_subdirectories_and_its_run_dir(self):
        tree, run = self.worktree_run()
        (self.project / "src").mkdir()
        (tree / "src" / "deep").mkdir(parents=True)
        (run / "iterations" / "001").mkdir(parents=True)
        for start in (self.project, self.project / "src", self.project / ".autocode", tree,
                      tree / "src" / "deep", run, run / "iterations" / "001"):
            with self.subTest(start=start):
                chosen = finder.choose(start, "advance")
                self.assertEqual((run, tree), (chosen.run_dir, chosen.workspace))

    def test_inside_a_task_worktree_only_its_own_runs_count(self):
        one, first = self.worktree_run("Build greeting one")
        two, second = self.worktree_run("Build greeting two")
        self.run_in(self.project, task="In place")
        self.assertIn("3 unfinished AutoCode runs", self.refused(self.project))
        self.assertEqual(first, finder.choose(one, "advance").run_dir)
        self.assertEqual(second, finder.choose(two / ".autocode", "read").run_dir)

    def test_a_builder_worker_run_stands_for_its_parent_run(self):
        tree, run = self.worktree_run()
        builder = tree / ".autocode" / "builders" / "b1" / "1"
        builder.parent.mkdir(parents=True)
        git(self.project, "worktree", "add", "-q", "-b", "autocode/builder-b1-1", str(builder), "HEAD")
        worker = self.run_in(builder, "builder-b1-1", status="RUNNING", parent_run=str(run))
        for start in (worker, builder, tree, self.project):
            with self.subTest(start=start):
                self.assertEqual(run, finder.choose(start, "advance").run_dir)
        self.assertEqual([run], [found.run_dir for found in finder.candidates(builder)])

    def test_a_run_directory_whose_state_cannot_be_used_is_refused_never_swapped_for_another(self):
        sibling = self.run_in(self.project)
        runs = self.project / ".autocode" / "runs"
        truncated = runs / "20261004-230000-bad-bbbbbbbb"
        (truncated / "logs").mkdir(parents=True)
        (truncated / "state.json").write_text("{trunc")
        moved = self.run_in(self.project, "20261004-220000-moved-cccccccc", workspace="/old/location/p")
        tree, run = self.worktree_run()
        builder = tree / ".autocode" / "builders" / "b1" / "1"
        builder.parent.mkdir(parents=True)
        git(self.project, "worktree", "add", "-q", "-b", "autocode/builder-b1-1", str(builder), "HEAD")
        orphan = self.run_in(builder, "builder-b1-1", status="RUNNING", parent_run=str(tree / "gone"))
        stateless = runs / "20261004-210000-empty-dddddddd"
        (stateless / "iterations" / "001").mkdir(parents=True)
        linked = runs / "20261004-200000-linked-eeeeeeee"
        linked.mkdir()
        (linked / "state.json").symlink_to(sibling / "state.json")
        # What autocode clean-worktrees leaves: the removed worktree's .autocode, under archive/.
        archived = self.project / ".autocode" / "archive" / "old-task-ffffffffff" / "runs" / "20261003-100000-old"
        archived.mkdir(parents=True)
        (archived / "state.json").write_text(json.dumps({"task": TASK, "status": "TASK_COMPLETE",
                                                         "workspace": str(self.root / "removed")}))
        for start, why in ((truncated, "its state.json could not be read"),
                           (stateless / "iterations" / "001", "it has no state.json"),
                           (linked, "its state.json is a symbolic link"),
                           (archived, "autocode clean-worktrees archived it"),
                           (truncated / "logs", "its state.json could not be read"),
                           (moved, f"names the workspace /old/location/p, not {self.project}"),
                           (orphan, f"its parent run {tree / 'gone'} cannot be used")):
            for action in finder.ACTIONS:
                with self.subTest(start=start, action=action):
                    message = self.refused(start, action)
                    inside = {truncated / "logs": truncated, stateless / "iterations" / "001": stateless}
                    self.assertIn(f"The saved run {inside.get(start, start)} you are in cannot be used", message)
                    self.assertIn(why, message)
                    self.assertIn("No other run was chosen", message)
                    self.assertNotIn(str(sibling), message)
                    self.assertNotIn(str(run), message)

    def test_task_projects_bootstrapped_in_a_plain_folder_are_found_from_it(self):
        folder = self.root / "plain"
        project = folder / "autocode-projects" / "build-calculator-1a2b3c4d"
        project.mkdir(parents=True)
        (folder / "keep.txt").write_text("user file\n")
        git(project, "init", "-q")
        run = self.run_in(project, task="Build calculator")
        chosen = finder.choose(folder, "read")
        self.assertEqual((run, project), (chosen.run_dir, chosen.workspace))

    def test_no_run_refuses_and_says_where_it_looked(self):
        message = self.refused(self.project)
        self.assertIn(f"No AutoCode run found in {self.project}", message)
        self.assertIn(str(self.project / ".autocode" / "runs"), message)
        self.assertIn('autocode "your task"', message)
        self.assertIn("--run-dir", message)
        plain = self.root / "plain"
        plain.mkdir()
        self.assertIn(f"No AutoCode run found in {plain}", self.refused(plain, "read"))
        self.assertIn("is not a directory", self.refused(self.root / "mistyped", "read"))

    def test_layouts_that_are_not_user_runs_are_ignored(self):
        outside = self.root / "outside"
        outside_run = self.run_in(outside)
        worktrees = self.project / ".autocode" / "worktrees"
        handmade = worktrees / "handmade"
        self.run_in(handmade)                                           # no task-workspace.json
        (worktrees / "linked").symlink_to(outside, target_is_directory=True)
        runs = self.project / ".autocode" / "runs"
        runs.mkdir(parents=True)
        # Only the symlink guard can refuse these: the run they reach names the project as its workspace.
        claims_project = self.run_in(self.root / "elsewhere", workspace=str(self.project))
        (runs / "linked-run").symlink_to(claims_project, target_is_directory=True)
        (runs / "linked-state").mkdir()
        (runs / "linked-state" / "state.json").symlink_to(claims_project / "state.json")
        (runs / "backup-only").mkdir()
        (runs / "backup-only" / "state.pre-v2.json").write_text(json.dumps(
            {"task": "x", "workspace": str(self.project), "status": "RUNNING"}))
        self.run_in(self.project, "copied-from-elsewhere", **{"workspace": str(outside)})
        (runs / "truncated").mkdir()
        (runs / "truncated" / "state.json").write_text('{"version": 3, "status": "RUNNIN')
        ui = self.project / ".autocode-ui" / "runs" / "u1"
        ui.mkdir(parents=True)
        (ui / "state.json").write_text(json.dumps({"target": "figma", "workspace": str(self.project),
                                                   "status": "RUNNING", "task": "Design"}))
        self.run_in(self.project / ".autocode" / "archive" / "old-tree")
        for folder in ("programs/p1", "task-flows/k1"):
            checkpoint = self.project / ".autocode" / folder
            checkpoint.mkdir(parents=True)
            (checkpoint / "state.json").write_text(json.dumps(
                {"project_workspace": str(self.project), "workspace": str(self.project), "status": "RUNNING"}))
        message = self.refused(self.project, "read")
        self.assertIn("No AutoCode run found", message)
        self.assertIn("Skipped 1 run directory whose state.json could not be read", message)
        self.assertIn(str(runs / "truncated"), message)
        run = self.run_in(self.project)
        self.assertEqual([run], [found.run_dir for found in finder.candidates(self.project)])

    def test_finding_runs_writes_nothing(self):
        tree, run = self.worktree_run()
        self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T23:00:00+00:00")
        bare = self.root / "bare"
        bare.mkdir()
        git(bare, "init", "-q")
        before = tree_snapshot(self.root)
        for start in (self.project, tree, run, bare, self.root):
            for action in finder.ACTIONS:
                try:
                    finder.choose(start, action, "--status")
                except finder.RunNotFound:
                    pass
            finder.candidates(start)
        finder.checkout_of(run)
        self.assertEqual(before, tree_snapshot(self.root))
        self.assertFalse((bare / ".autocode").exists())

    def test_checkout_of_a_run_is_the_checkout_its_state_names(self):
        tree, run = self.worktree_run()
        self.assertEqual(tree, finder.checkout_of(run))
        copied = self.run_in(self.project, "copied", **{"workspace": str(tree)})
        self.assertIsNone(finder.checkout_of(copied))
        self.assertIsNone(finder.checkout_of(self.root))
        builder = tree / ".autocode" / "builders" / "b1" / "1"
        builder.parent.mkdir(parents=True)
        git(self.project, "worktree", "add", "-q", "-b", "autocode/builder-b1-1", str(builder), "HEAD")
        worker = self.run_in(builder, "builder-b1-1", status="RUNNING", parent_run=str(run))
        self.assertIsNone(finder.checkout_of(worker), "its orchestrator drives a parallel Builder's run")


class WhichRunIsChosen(Fixture):
    def test_several_unfinished_runs_are_listed_newest_first_with_a_command_for_each(self):
        runs = [self.worktree_run(f"Build part {index}")[1] for index in range(3)]
        self.run_in(self.project, status="TASK_COMPLETE", task="Finished earlier")
        for action in ("advance", "act", "read"):
            with self.subTest(action=action):
                message = self.refused(self.project, action, "--answer 'Q1=CLI only'")
                self.assertIn(f"3 unfinished AutoCode runs in {self.project}", message)
                positions = [message.index(f"autocode --run-dir {run} --answer 'Q1=CLI only'") for run in runs]
                self.assertEqual(sorted(positions, reverse=True), positions, "newest first")
                self.assertIn("Not listed: 1 finished run.", message)
                self.assertNotIn("Finished earlier", message)
                self.assertEqual(message, self.refused(self.project, action, "--answer 'Q1=CLI only'"))

    def test_a_long_list_is_capped(self):
        for index in range(finder.LIST_LIMIT + 2):
            self.run_in(self.project, task=f"Run {index}")
        message = self.refused(self.project, "read")
        self.assertEqual(finder.LIST_LIMIT, message.count("autocode --run-dir"))
        self.assertIn("... and 2 older (not shown)", message)

    def test_a_bare_command_never_relaunches_a_finished_run(self):
        run = self.run_in(self.project, status="TASK_COMPLETE", task="Build greeting",
                          completed_at="2026-10-04T20:00:00+00:00")
        for action in ("advance", "act"):
            message = self.refused(self.project, action)
            self.assertIn(f"No unfinished AutoCode run in {self.project}", message)
            self.assertIn(f"autocode --run-dir {run} --status", message)
            self.assertIn("autocode --status", message)
            self.assertIn('autocode --follow-up "TEXT"', message)
            self.assertIn('autocode "your task"', message)
        self.assertIn("nothing to resume", self.refused(self.project, "advance"))
        here = self.refused(run, "advance")
        self.assertIn("The run in this directory has finished (TASK_COMPLETE)", here)
        self.assertEqual(run, finder.choose(run, "read").run_dir)
        inside = self.refused(run, "act")
        self.assertIn("this command needs an unfinished run; to act on it anyway, name it with --run-dir", inside)
        self.assertEqual(run, finder.choose(run, "follow_up").run_dir)

    def test_a_stopped_run_is_finished_and_takes_no_follow_up(self):
        run = self.run_in(self.project, status="PAUSED_INTERVENTION",
                          applied_interventions=[{"kind": "pause"}, {"kind": "stop", "at": "t"}])
        (found,) = finder.candidates(self.project)
        self.assertTrue(found.stopped and found.finished and not found.complete)
        message = self.refused(self.project, "advance")
        self.assertIn("nothing to resume", message)
        self.assertNotIn("--follow-up", message)
        self.assertEqual(run, finder.choose(self.project, "read").run_dir)
        self.assertIn("no run in", self.refused(self.project, "follow_up"))

    def test_status_with_no_unfinished_run_shows_the_latest_finished_one(self):
        self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T22:00:00+00:00")
        later = self.worktree_run(status="TASK_COMPLETE", completed_at="2026-10-04T23:00:00+00:00")[1]
        self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T21:00:00+00:00")
        chosen = finder.choose(self.project, "read")
        self.assertEqual((later, "the latest finished run"), (chosen.run_dir, chosen.reason))

    def test_follow_up_continues_the_most_recently_completed_run(self):
        self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T12:00:00+00:00")
        latest = self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T20:00:00+00:00")
        self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T15:00:00+00:00")
        self.run_in(self.project, status="WAITING_FOR_USER")
        self.assertEqual(latest, finder.choose(self.project, "follow_up").run_dir)

    def test_follow_up_never_reopens_an_older_run_behind_a_newer_unfinished_one(self):
        # The user finished one run, then started another that now waits for plan approval: a
        # follow-up is meant for the conversation they are in, which takes approval or feedback.
        done = self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T01:30:00+00:00")
        waiting = self.run_in(self.project, status="AWAITING_GOAL_APPROVAL")
        message = self.refused(self.project, "follow_up")
        self.assertIn("started after the latest one finished has not finished", message)
        self.assertIn(f"AWAITING_GOAL_APPROVAL: {TASK}", message)
        self.assertIn(f"autocode --run-dir {waiting} --status", message)
        self.assertIn(f'autocode --run-dir {done} --follow-up "TEXT"', message)

    def test_follow_up_prefers_a_run_no_program_drives_even_when_one_completed_later(self):
        mine = self.run_in(self.project, status="TASK_COMPLETE", completed_at="2026-10-04T02:00:00+00:00")
        tree, program_run = self.worktree_run(status="TASK_COMPLETE", completed_at="2026-10-04T09:00:00+00:00")
        self.owned_by_program(tree, program_run)
        self.assertEqual(mine, finder.choose(self.project, "follow_up").run_dir)

    def test_follow_up_without_a_completed_run_points_at_the_unfinished_ones(self):
        run = self.run_in(self.project)
        message = self.refused(self.project, "follow_up")
        self.assertIn("--follow-up continues a finished run", message)
        self.assertIn(f"autocode --run-dir {run} --status", message)

    def test_program_and_task_flow_runs_are_listed_but_never_advanced_by_a_bare_command(self):
        program_tree, program_run = self.worktree_run("Program workstream")
        meta = program_tree / ".autocode" / "task-workspace.json"
        meta.write_text(json.dumps({key: value for key, value in json.loads(meta.read_text()).items()
                                    if key != "kind"}))
        state = self.project / ".autocode" / "programs" / "p1" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"project_workspace": str(self.project), "workstreams": {
            "api": {"workspace": str(program_tree), "run_dir": str(program_run)}}}))
        chosen = finder.choose(self.project, "read")
        self.assertEqual((program_run, "program"), (chosen.run_dir, chosen.owner))
        self.assertEqual(program_run, finder.choose(self.project, "act").run_dir)
        message = self.refused(self.project, "advance")
        self.assertIn("is driven by `autocode program`; a bare autocode does not advance it", message)
        self.assertIn("autocode program run MANIFEST", message)
        self.assertIn(f"autocode --run-dir {program_run} --status", message)
        self.assertIn("driven by", self.refused(program_tree, "advance"), "also from inside its worktree")

        lane_tree, lane_run = self.worktree_run("Lane task")
        flow = self.project / ".autocode" / "task-flows" / "k1" / "state.json"
        flow.parent.mkdir(parents=True)
        flow.write_text(json.dumps({"project_workspace": str(self.project), "lanes": {
            "a": {"workspace": str(lane_tree), "tasks": {"t1": {"status": "PAUSED"}}}}}))
        message = self.refused(self.project, "advance")
        self.assertIn("2 unfinished AutoCode runs", message)
        self.assertIn("driven by `autocode program`", message)
        self.assertIn("driven by `autocode tasks`", message)

        mine = self.worktree_run("My own task")[1]
        chosen = finder.choose(self.project, "advance")
        self.assertEqual(mine, chosen.run_dir)
        self.assertEqual("the only unfinished run no program or task flow drives", chosen.reason)
        self.assertIn("3 unfinished AutoCode runs", self.refused(self.project, "read"))

    def test_a_program_plan_run_is_not_relaunched_in_the_project_checkout(self):
        self.assertTrue(autocode_program.PLAN_PREAMBLE.startswith(finder.PROGRAM_PLAN_PREFIX))
        plan = self.run_in(self.project, task=autocode_program.PLAN_PREAMBLE + "Build an order system")
        (found,) = finder.candidates(self.project)
        self.assertEqual(("program", True, "Build an order system"), (found.owner, found.program_plan, found.task))
        message = self.refused(self.project, "advance")
        self.assertIn(f"autocode --run-dir {plan} --unit autoplanner", message)
        self.assertIn("autocode program derive", message)
        self.assertEqual(plan, finder.choose(self.project, "act").run_dir)

    def test_a_listed_program_plan_run_keeps_its_planning_unit(self):
        plan = self.run_in(self.project, task=autocode_program.PLAN_PREAMBLE + "Build an order system")
        runs = [self.worktree_run(task)[1] for task in ("One", "Two")]
        for flags, unit in (("", None), ("--no-chat", None), ("--no-chat", "autocode")):
            with self.subTest(flags=flags, unit=unit):
                lines = [line.strip() for line in self.refused(self.project, "advance", flags, unit).splitlines()]
                own = " ".join(part for part in ("--unit autoplanner", flags) if part)
                self.assertIn(f"autocode --run-dir {plan} {own}", lines)
                self.assertNotIn(f"autocode --run-dir {plan} {flags}".strip(), lines, "never a plain relaunch")
                for run in runs:
                    expected = " ".join(part for part in (f"--unit {unit}" if unit else "", flags) if part)
                    self.assertIn(f"autocode --run-dir {run} {expected}".strip(), lines)
                self.assertTrue(any("derive the program with autocode program derive" in line for line in lines))
        lines = self.refused(self.project, "read", "--status").splitlines()
        self.assertIn(f"autocode --run-dir {plan} --status", [line.strip() for line in lines], "reading is safe")
        for argv in ([], ["--no-chat"], ["resume"]):
            with self.subTest(argv=argv):
                message = self.parse_error(*argv)
                self.assertIn(f"autocode --run-dir {plan} --unit autoplanner", message)
                self.assertNotIn(f"autocode --run-dir {plan}\n", message)

    def test_the_line_after_a_user_action_says_how_to_go_on_with_that_run(self):
        plan_task = autocode_program.PLAN_PREAMBLE + "Build an order system"
        plan = self.run_in(self.project, task=plan_task)
        hint = finder.continue_hint(plan, json.loads((plan / "state.json").read_text()))
        self.assertIn(f"Continue planning with: autocode --run-dir {plan} --unit autoplanner", hint)
        self.assertIn(f"autocode program derive --run-dir {plan} --output program.json", hint)
        tree, program_run = self.worktree_run("Program workstream")
        self.owned_by_program(tree, program_run)
        hint = finder.continue_hint(program_run, json.loads((program_run / "state.json").read_text()))
        self.assertIn("`autocode program` drives it", hint)
        self.assertNotIn("plain autocode", hint)
        mine = self.worktree_run("Mine")[1]
        state = json.loads((mine / "state.json").read_text())
        self.assertEqual(f"Continue with: autocode --run-dir {mine} (or plain autocode from its project while it "
                         "is the only unfinished run there)", finder.continue_hint(mine, state))
        hint = finder.continue_hint(mine, state, "autoplanner")
        self.assertEqual(f"Continue with: autocode --run-dir {mine} --unit autoplanner (or autocode --unit "
                         "autoplanner from its project while it is the only unfinished run there)", hint,
                         "an action under a unit continues that unit, not every unit")
        for status in ("PAUSED_PLANNING_BUDGET", "PAUSED_INVALID_OUTPUT", "BLOCKED_HUMAN", "RESOLVER_PENDING"):
            with self.subTest(status=status):
                self.assertEqual(f"Continue with: autocode --run-dir {mine} resume (or autocode resume from its "
                                 "project while it is the only unfinished run there)",
                                 finder.continue_hint(mine, {**state, "status": status}),
                                 "plain autocode only shows a pause")
        self.assertIn("--unit autoplanner resume (or autocode --unit autoplanner resume from",
                      finder.continue_hint(mine, {**state, "status": "PAUSED_PLANNING_BUDGET"}, "autoplanner"))
        hint = finder.continue_hint(mine, {**state, "status": "TASK_COMPLETE"})
        self.assertIn(f"It has finished (TASK_COMPLETE); show it with: autocode --run-dir {mine} --status", hint)
        self.assertIn(f'autocode --run-dir {mine} --follow-up "TEXT"', hint)
        hint = finder.continue_hint(mine, {**state, "applied_interventions": [{"kind": "stop"}]})
        self.assertIn("It has finished", hint)
        self.assertNotIn("--follow-up", hint)
        self.assertNotIn("Continue with", hint)


class CommandLine(Fixture):
    def test_discovered_run_preserves_evidence_inspection_requirements(self):
        run = self.run_in(self.project)
        before = tree_snapshot(self.project)
        args, _ = self.parse("--status", "--inspect-evidence")
        self.assertEqual(run, args.run_dir)
        self.assertTrue(args.inspect_evidence)
        self.assertIn("--inspect-evidence requires --run-dir and --status",
                      self.parse_error("--inspect-evidence"))
        self.assertEqual(before, tree_snapshot(self.project))

    def test_discovered_run_requires_explicit_recovery_with_an_expected_token(self):
        run = self.run_in(self.project, status="PAUSED_BUDGET")
        before = tree_snapshot(self.project)
        for action in (["--resume-paused"], ["--abandon-stage", "001/terra-01"]):
            with self.subTest(action=action):
                args, _ = self.parse(*action, "--expected-recovery-token", "exact-view-token")
                self.assertEqual((run, "exact-view-token"), (args.run_dir, args.expected_recovery_token))
        self.assertIn("--expected-recovery-token requires a saved run and an explicit resume or abandon action",
                      self.parse_error("--expected-recovery-token", "exact-view-token"))
        self.assertEqual(before, tree_snapshot(self.project))

    def test_a_task_or_an_explicit_run_dir_turns_finding_off(self):
        first = self.worktree_run("One")[1]
        self.worktree_run("Two")
        args, notice = self.parse("Build something")
        self.assertIsNone(args.run_dir)
        self.assertEqual("", notice)
        args, notice = self.parse("--run-dir", str(first), "--status")
        self.assertEqual((first, first.parent.parent.parent), (args.run_dir, args.workspace))
        self.assertEqual("", notice)

    def test_new_run_inputs_turn_finding_off(self):
        self.run_in(self.project)
        handoff = self.root / "handoff.json"
        for flags in (["--in-place"], ["--figma-file", "https://www.figma.com/design/KEY/Name"],
                      ["--ui-run", str(self.root)], ["--figma-manifest", str(handoff)],
                      ["--figma-review", "human"], ["--builder-strong-model", "openai/gpt-6-sol"],
                      ["--conversation-handoff", str(handoff)]):
            with self.subTest(flags=flags):
                self.assertIsNone(self.parse(*flags)[0].run_dir)
        for flags in (["--engine", "codex"], ["--dry-run"], ["--workflow", "build"], ["--max-seconds", "60"],
                      ["--task-preflight", str(handoff)]):
            with self.subTest(flags=flags):
                self.assertIsNotNone(self.parse(*flags)[0].run_dir, "not a new-run input")

    def test_run_dir_without_workspace_selects_the_runs_own_checkout(self):
        tree, run = self.worktree_run()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        for argv in (["--run-dir", str(run), "--status"], ["--run-dir=" + str(run)], ["--run", str(run)]):
            with self.subTest(argv=argv):
                self.assertEqual(tree, self.parse(*argv, cwd=elsewhere)[0].workspace)
        args, _ = self.parse("--workspace", str(self.project), "--run-dir", str(run), cwd=elsewhere)
        self.assertEqual(self.project, args.workspace, "an explicit --workspace is kept")
        args, _ = self.parse("--works", str(self.project), "--run-dir", str(run), cwd=elsewhere)
        self.assertEqual(self.project, args.workspace, "so is an abbreviation")
        args, _ = self.parse("--run-dir", str(elsewhere), cwd=elsewhere)
        self.assertEqual(elsewhere, args.workspace, "not a run directory: left for the usual errors")

    def test_a_builder_worker_run_named_alone_keeps_the_usual_workspace(self):
        tree, run = self.worktree_run()
        builder = tree / ".autocode" / "builders" / "b1" / "1"
        builder.parent.mkdir(parents=True)
        git(self.project, "worktree", "add", "-q", "-b", "autocode/builder-b1-1", str(builder), "HEAD")
        worker = self.run_in(builder, "builder-b1-1", status="PAUSED_PROVIDER_UNCERTAIN", parent_run=str(run))
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        args, notice = self.parse("--run-dir", str(worker), "--resume-paused", "--no-chat", cwd=elsewhere)
        self.assertEqual((worker, elsewhere, ""), (args.run_dir, args.workspace, notice),
                         "not the Builder's worktree: the usual workspace errors stay")
        self.assertEqual(tree, self.parse("--run-dir", str(run), cwd=elsewhere)[0].workspace)

    def test_resume_and_status_words(self):
        self.assertFalse({"resume", "status"} & set(autocode_subcommands.SUBCOMMANDS))
        tree, run = self.worktree_run()
        args, notice = self.parse("resume", "--no-chat")
        self.assertEqual((run, tree, None), (args.run_dir, args.workspace, args.task))
        self.assertIn(str(run), notice)
        args, _ = self.parse("status")
        self.assertTrue(args.status)
        self.assertEqual(run, args.run_dir)
        for argv, cwd in ((["--no-chat", "resume"], self.project), (["--workspace", str(self.project), "resume"], self.root),
                          (["--feedback", "resume", "resume"], self.project)):
            with self.subTest(argv=argv):
                args, _ = self.parse(*argv, cwd=cwd)
                self.assertEqual((run, None), (args.run_dir, args.task), "the word after options is the command")
        for argv in (["--verbose", "status", "--workspace", str(self.project)], ["--no-chat", "resume", "--feedback", "resume"]):
            with self.subTest(argv=argv):
                args, _ = self.parse(*argv)
                self.assertEqual((run, None), (args.run_dir, args.task), "an option's value may be the same word")
        self.assertEqual("resume", self.parse("--no-chat", "resume", "--feedback", "resume")[0].feedback)
        args, _ = self.parse("--no-chat", "status")
        self.assertEqual((True, run, None), (args.status, args.run_dir, args.task))
        args, notice = self.parse("--", "status")
        self.assertEqual(("status", None, ""), (args.task, args.run_dir, notice), "after -- it is a task")
        self.assertIn("autocode resume continues a saved run", self.parse_error("resume", "Build another thing"))
        self.assertIn("--in-place only applies to a new one", self.parse_error("resume", "--in-place"))
        self.assertEqual(run, self.parse("resume", "--run-dir", str(run))[0].run_dir)

    def test_resume_acknowledges_a_pause_by_itself(self):
        tree, run = self.worktree_run(status="PAUSED_RATE_LIMIT")
        for argv in (["resume"], ["--no-chat", "resume"], ["resume", "--run-dir", str(run)],
                     ["resume", "--retry-failed-stage"], ["resume", "--grant-recovery", "2"]):
            with self.subTest(argv=argv):
                args, _ = self.parse(*argv)
                self.assertEqual((run, True), (args.run_dir, args.resume_paused))
        for argv in ([], ["--no-chat"], ["--status"], ["resume", "--status"], ["resume", "--feedback", "smaller"],
                     ["resume", "--abandon-stage", "001/terra-01"]):
            with self.subTest(argv=argv):
                self.assertFalse(self.parse(*argv)[0].resume_paused, "only the resume command acknowledges")
        for status in ("RESOLVER_PENDING", "BLOCKED_HUMAN", "PLAN_REWORK_REQUIRED"):
            with self.subTest(status=status):
                # A plain relaunch only shows these too; --resume-paused continues them (#412).
                other = self.run_in(tree, status=status)
                for argv in (["resume"], ["resume", "--retry-failed-stage"], ["resume", "--max-seconds", "0"]):
                    self.assertTrue(self.parse(*argv, "--run-dir", str(other))[0].resume_paused)
                for argv in ([], ["resume", "--feedback", "smaller"],
                             ["resume", "--resolver-response", "leave_paused", "--resolver-request", "R",
                              "--resolver-token", "T"]):
                    self.assertFalse(self.parse(*argv, "--run-dir", str(other))[0].resume_paused,
                                     "never with a user action or a resolver response")
        for status in ("WAITING_FOR_USER", "RUNNING", "PAUSED_DESIGN_CONFLICT", "BLOCKED", "WAITING_FOR_DEPENDENCY"):
            with self.subTest(status=status):
                other = self.run_in(tree, status=status)
                self.assertFalse(self.parse("resume", "--run-dir", str(other))[0].resume_paused, "nothing to acknowledge")

    def test_the_hint_after_a_user_action_at_a_pause_is_a_command_that_acknowledges_it(self):
        run = self.worktree_run(status="PAUSED_PLANNING_BUDGET")[1]
        hint = finder.continue_hint(run, json.loads((run / "state.json").read_text()))
        command = shlex.split(hint.removeprefix("Continue with: ").split(" (or ")[0])
        self.assertEqual("autocode", command[0])
        args, _ = self.parse(*command[1:], cwd=self.root)
        self.assertEqual((run, True), (args.run_dir, args.resume_paused))

    def test_resume_refuses_when_every_run_has_finished(self):
        self.run_in(self.project, status="TASK_COMPLETE")
        for argv in (["resume"], ["resume", "--no-chat"], []):
            with self.subTest(argv=argv):
                self.assertIn("nothing to resume", self.parse_error(*argv))

    def test_resume_does_not_acknowledge_an_unverified_operational_request(self):
        self.run_in(self.project, status="WAITING_FOR_USER",
                    resolver_human_request={"scope": "operational_exhaustion"})
        self.assertFalse(self.parse("resume")[0].resume_paused)
        self.assertIn("--grant-recovery requires --resume-paused",
                      self.parse_error("resume", "--grant-recovery", "1"))

    def test_the_notice_names_the_run_on_one_stderr_line_that_is_never_read_as_a_rejection(self):
        run = self.run_in(self.project)
        _, notice = self.parse("--status")
        self.assertEqual(1, notice.count("\n"))
        self.assertTrue(notice.startswith("Using the saved run "))
        self.assertIn(f"{run} (WAITING_FOR_USER, the only unfinished run)", notice)
        self.assertFalse(notice.startswith(("autocode:", "Input rejected:", "Run: ")))

    def test_each_action_flag_takes_its_own_rule(self):
        program_tree, program_run = self.worktree_run("Program workstream")
        state = self.project / ".autocode" / "programs" / "p1" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"workstreams": {"api": {"workspace": str(program_tree)}}}))
        complete = self.run_in(self.project, status="TASK_COMPLETE")
        for argv in (["--status"], ["--dry-run"], ["--show-goal"], ["--approve-goal", "r1:x"],
                     ["--answer", "Q1=yes"], ["--feedback", "smaller"], ["--close-finding", "F1", "--close-reason", "dup"],
                     ["--accept-completion"], ["--abandon-stage", "001/terra-01"],
                     ["--resolver-response", "leave_paused", "--resolver-request", "R", "--resolver-token", "T"]):
            with self.subTest(argv=argv):
                self.assertEqual(program_run, self.parse(*argv)[0].run_dir)
        self.assertEqual(complete, self.parse("--follow-up", "Fix them")[0].run_dir)
        for argv in ([], ["--resume-paused"], ["--no-chat"], ["--unit", "autoplanner"],
                     ["--resume-paused", "--retry-failed-stage"]):
            with self.subTest(argv=argv):
                self.assertIn("driven by `autocode program`", self.parse_error(*argv))
        self.assertIn("Choose one action per invocation", self.parse_error("--status", "--approve-goal", "r1:x"))

    def test_only_status_and_dry_run_fall_back_to_a_finished_run(self):
        complete = self.run_in(self.project, status="TASK_COMPLETE")
        for flag in ("--status", "--dry-run", "status"):
            with self.subTest(flag=flag):
                self.assertEqual(complete, self.parse(flag)[0].run_dir)
        for argv in (["--show-goal"], ["--answer", "Q1=x"], ["--migrate-only"]):
            with self.subTest(argv=argv):
                self.assertIn("No unfinished AutoCode run", self.parse_error(*argv))
        tree, program_run = self.worktree_run("Program workstream")
        self.owned_by_program(tree, program_run)
        mine = self.worktree_run("Mine")[1]
        for flag in ("--status", "--dry-run"):
            with self.subTest(flag=flag):
                self.assertTrue(self.parse_error(flag).startswith("autocode: 2 unfinished AutoCode runs"))
        for argv in (["--show-goal"], ["--answer", "Q1=x"]):
            with self.subTest(argv=argv):
                self.assertEqual(mine, self.parse(*argv)[0].run_dir)

    def test_a_unit_entry_point_lists_commands_that_keep_its_unit(self):
        runs = [self.worktree_run(task)[1] for task in ("One", "Two")]
        for unit, argv in (("autocode", ["--no-chat"]), ("autoplanner", ["--no-chat"]),
                           (None, ["--unit", "autoplanner", "--no-chat"]), (None, ["--unit=autoplanner", "--no-chat"]),
                           (None, ["--no-chat", "--uni", "autoplanner"])):
            with self.subTest(unit=unit, argv=argv):
                listing = self.parse_error(*argv, unit=unit).split("unfinished AutoCode runs", 1)[1]
                expected = unit or "autoplanner"
                for run in runs:
                    self.assertIn(f"autocode --run-dir {run} --unit {expected} --no-chat\n", listing + "\n")
                self.assertEqual(len(runs), listing.count("--un"), "the unit once per command")

    def test_resume_companions_name_only_what_is_missing(self):
        self.worktree_run()
        for argv in (["--retry-builder", "M2"], ["--accept-transport-change"], ["--retry-report", "X"],
                     ["--retry-failed-stage"], ["--diagnose-failed-stage"], ["--grant-recovery", "2"]):
            with self.subTest(argv=argv):
                message = self.parse_error(*argv).split("error: ", 1)[1]   # after argparse's usage lines
                self.assertIn(f"{argv[0]} requires --resume-paused", message)
                self.assertNotIn("--run-dir", message)
                self.assertIn(f"{argv[0]} requires --run-dir and --resume-paused",
                              self.parse_error("Some task", *argv), "no run named or found")
        self.assertIn("--job-retry-token requires --resume-paused --retry-failed-stage",
                      self.parse_error("--job-retry-token", "T"))
        # The token also binds a stopped job's model answer, alone (#463); never another answer.
        args, _ = self.parse("--answer", "route-sol=gpt-6-luna", "--job-retry-token", "T")
        self.assertEqual(("T", ["route-sol=gpt-6-luna"]), (args.job_retry_token, args.answer))
        for argv in (["--answer", "Q1=yes"], ["--answer", "route-sol=m", "--answer", "Q1=yes"],
                     ["--answer", "route-sol=m", "--resume-paused"]):
            with self.subTest(argv=argv):
                self.assertIn("--job-retry-token requires --resume-paused --retry-failed-stage",
                              self.parse_error(*argv, "--job-retry-token", "T"))
        # The answer issues a new token, so it never retries in the same command (nor saves a setting with it).
        for argv in (["--answer", "route-sol=m"], ["--delegate", "route-sol"]):
            with self.subTest(argv=argv):
                self.assertIn("names a stopped job's model on its own and issues a new token",
                              self.parse_error(*argv, "--resume-paused", "--retry-failed-stage",
                                               "--max-stage-seconds", "60", "--job-retry-token", "T"))
        message = self.parse_error("--resolver-response", "leave_paused").split("error: ", 1)[1]
        self.assertIn("--resolver-response requires --resolver-request and --resolver-token", message)
        self.assertNotIn("--run-dir", message)

    def test_inside_a_run_whose_state_cannot_be_used_nothing_else_is_chosen(self):
        self.run_in(self.project)
        truncated = self.project / ".autocode" / "runs" / "20261004-230000-bad-bbbbbbbb"
        truncated.mkdir(parents=True)
        (truncated / "state.json").write_text("{trunc")
        for argv in ([], ["--status"], ["--answer", "Q1=x"]):
            with self.subTest(argv=argv):
                self.assertIn(f"The saved run {truncated} you are in cannot be used",
                              self.parse_error(*argv, cwd=truncated))

    def test_an_ambiguous_command_lists_each_run_with_the_users_own_flags(self):
        runs = [self.worktree_run(task)[1] for task in ("One", "Two")]
        message = self.parse_error("--answer", "Q1=CLI only", "--workspace", str(self.project))
        for run in runs:
            self.assertIn(f"autocode --run-dir {run} --answer 'Q1=CLI only'", message)
        self.assertNotIn("--workspace", message.split("unfinished AutoCode runs", 1)[1])

    def test_ambiguous_resume_suggestions_preserve_the_command_and_companions(self):
        runs = {self.run_in(self.project, name=name, status="PAUSED_TIMEOUT_RECOVERY")
                for name in ("first paused run", "second paused run")}
        for argv in (["resume"], ["--no-chat", "resume"], ["resume", "--retry-failed-stage"],
                     ["resume", "--grant-recovery", "2"], ["resume", "--unit", "autoplanner"],
                     ["resume", "--status"], ["resume", "--feedback", "resume"]):
            with self.subTest(argv=argv):
                message = self.parse_error(*argv)
                commands = [shlex.split(line.strip())[1:] for line in message.splitlines()
                            if line.strip().startswith("autocode --run-dir ")]
                self.assertEqual(2, len(commands))
                chosen = []
                for command in commands:
                    args, _ = self.parse(*command)
                    chosen.append(args.run_dir)
                    self.assertEqual(not any(flag in argv for flag in ("--status", "--feedback")),
                                     args.resume_paused)
                    self.assertEqual("--retry-failed-stage" in argv, args.retry_failed_stage)
                    self.assertEqual(2 if "--grant-recovery" in argv else None, args.grant_recovery)
                    self.assertEqual("autoplanner" if "--unit" in argv else None, args.unit)
                self.assertEqual(runs, set(chosen))


class InProcessCli(Fixture):
    """autocode.main() as a user runs it, from the project root without --run-dir."""

    def main(self, *argv, cwd=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["autocode", *argv]), \
             patch.object(Path, "cwd", return_value=cwd or self.project), \
             patch.object(autocode, "run_role", side_effect=AssertionError("No provider may launch")), \
             patch.object(opencode_provider, "local_settings", return_value={"engine": "opencode"}), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = autocode.main()
            except SystemExit as exit_:
                code = exit_.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_status_from_the_project_root_shows_the_worktree_run_without_changing_it(self):
        tree, run = self.worktree_run()
        before = (run / "state.json").read_bytes()
        code, out, err = self.main("--status")
        self.assertEqual(0, code, err)
        view = json.loads(out)
        self.assertEqual((str(run), str(tree)), (view["run_dir"], view["workspace"]))
        self.assertIn(f"Using the saved run {run}", err)
        self.assertNotIn("Using the saved run", out)
        self.assertEqual(before, (run / "state.json").read_bytes())
        code, explicit, _ = self.main("--workspace", str(self.project), "--run-dir", str(run), "--status",
                                      cwd=self.root)
        self.assertEqual(0, code)
        self.assertEqual(view, json.loads(explicit), "the same view as naming the run")
        self.assertEqual(before, (run / "state.json").read_bytes())

    def test_show_goal_never_reaches_a_finished_run_and_status_leaves_it_as_it_is(self):
        # A legacy version-2 checkpoint: --show-goal would migrate it back to RUNNING.
        tree, run = self.worktree_run(status="TASK_COMPLETE", version=2,
                                      completed_at="2026-10-04T20:00:00+00:00")
        before = tree_snapshot(self.project)
        code, out, err = self.main("--show-goal")
        self.assertEqual(2, code, err)
        self.assertEqual("", out)
        self.assertIn("No unfinished AutoCode run", err)
        self.assertNotIn("Using the saved run", err)
        self.assertEqual(before, tree_snapshot(self.project))
        state = (run / "state.json").read_bytes()
        code, out, err = self.main("--status")
        self.assertEqual(0, code, err)
        self.assertEqual(str(run), json.loads(out)["run_dir"])
        self.assertIn("(TASK_COMPLETE, the latest finished run)", err)
        self.assertEqual(state, (run / "state.json").read_bytes())
        self.assertFalse((run / "state.pre-v3.json").exists())
        self.assertIn("nothing to resume", self.main()[2], "and a bare autocode still refuses it")

    def test_a_user_action_on_a_program_plan_run_says_to_continue_with_the_planning_unit(self):
        plan = self.run_in(self.project, task=autocode_program.PLAN_PREAMBLE + "Build an order system")
        code, out, err = self.main("--show-goal")
        self.assertEqual(0, code, err)
        self.assertIn(f"Using the saved run {plan}", err)
        self.assertIn(f"Continue planning with: autocode --run-dir {plan} --unit autoplanner", out)
        self.assertIn(f"autocode program derive --run-dir {plan} --output program.json", out)
        self.assertNotIn("plain autocode", out)

    def test_an_ambiguous_bare_command_exits_2_and_changes_nothing(self):
        self.worktree_run("One")
        self.worktree_run("Two")
        before = tree_snapshot(self.project)
        for argv in ([], ["--status"], ["resume", "--no-chat"], ["status"], ["--approve-goal", "r1:x"]):
            with self.subTest(argv=argv):
                code, out, err = self.main(*argv)
                self.assertEqual(2, code)
                self.assertEqual("", out)
                self.assertIn("2 unfinished AutoCode runs", err)
        self.assertEqual(before, tree_snapshot(self.project))


if __name__ == "__main__":
    unittest.main()
