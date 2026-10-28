"""`autocode runs`: every row is the run's own status view, never a second reading of it."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import autocode_registry as registry
import autocode_run_view as run_view
import autocode_runs_command as runs


def project(**written: dict) -> Path:
    """A workspace whose .autocode/runs holds exactly these checkpoints."""
    root = Path(tempfile.mkdtemp()).resolve()
    (root / ".git").mkdir()
    for name, state in written.items():
        run_dir = root / ".autocode" / "runs" / name
        run_dir.mkdir(parents=True)
        saved = {"version": 3, "workspace": str(root), "task": f"build {name}", "status": "RUNNING", **state}
        (run_dir / "state.json").write_text(json.dumps(saved))
    return root


def capture(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = runs.cli(argv)
    return code, out.getvalue(), err.getvalue()


class FleetRowsTests(unittest.TestCase):
    def setUp(self):
        self.root = project(
            waiting={"status": "PAUSED_REQUESTED", "stop_reason": "paused on request", "iteration": 2},
            working={"status": "RUNNING", "iteration": 1, "phase": "BUILDING", "next_stage": "terra"},
            finished={"status": "TASK_COMPLETE", "iteration": 3},
        )
        broken = self.root / ".autocode" / "runs" / "broken"
        broken.mkdir(parents=True)
        (broken / "state.json").write_text("{not json")

    def test_a_run_awaiting_a_person_leads_the_table_with_its_own_command(self):
        rows = runs.rows(self.root)
        self.assertEqual(runs.NEEDS, rows[0]["bucket"])
        self.assertEqual("resume", rows[0]["needs"])
        self.assertIn("--resume-paused", rows[0]["next_command"])
        self.assertIn(str(rows[0]["run_dir"]), rows[0]["next_command"])

    def test_working_settled_and_unreadable_runs_stay_apart(self):
        self.assertEqual(
            {"waiting": runs.NEEDS, "working": runs.WORKING, "finished": runs.SETTLED, "broken": runs.UNREADABLE},
            {item["name"]: item["bucket"] for item in runs.rows(self.root)},
        )
        self.assertEqual(
            {"total": 4, runs.NEEDS: 1, runs.WORKING: 1, runs.SETTLED: 1, runs.UNREADABLE: 1},
            runs.counts(runs.rows(self.root)),
        )

    def test_a_run_saved_for_the_person_to_continue_needs_them(self):
        """#2332: a ready controller receipt means nothing runs the RUNNING checkpoint until the named
        command is run; the row sat under "working"."""
        import autocode_controller_owner as owners
        import autocode_util as util

        run_dir = self.root / ".autocode" / "runs" / "working"
        state = json.loads((run_dir / "state.json").read_text())

        def persist(path, saved):
            util.atomic_json(path, saved)
            owners.checkpoint(path, saved)

        with util.run_lock(run_dir), owners.Owner(state, run_dir, persist) as owner:
            owner.result(0, ready=True)
        row = next(item for item in runs.rows(self.root) if item["name"] == "working")
        self.assertEqual((runs.NEEDS, True, "RUNNING"), (row["bucket"], row["ready_to_continue"], row["status"]))
        self.assertTrue(row["next_command"])
        rendered = runs.render(runs.rows(self.root), str(self.root))
        self.assertIn(
            "Ready to continue", rendered.split("  working (", 1)[0] if "  working (" in rendered else rendered
        )
        self.assertEqual(2, runs.counts(runs.rows(self.root))[runs.NEEDS])
        # The checkpoint moved on: the receipt no longer vouches for it.
        (run_dir / "state.json").write_text(json.dumps({**state, "iteration": 2}))
        row = next(item for item in runs.rows(self.root) if item["name"] == "working")
        self.assertEqual((runs.WORKING, False), (row["bucket"], row["ready_to_continue"]))

    def test_a_run_that_cannot_be_read_is_shown_with_its_reason(self):
        broken = next(item for item in runs.rows(self.root) if item["name"] == "broken")
        self.assertEqual("checkpoint_malformed", broken["availability"])
        self.assertTrue(broken["reason"])
        self.assertIn("broken", runs.render(runs.rows(self.root), str(self.root)))

    def test_a_command_that_could_discard_an_uncertain_attempt_is_not_offered(self):
        """Only the run itself can tell a repairable report from an attempt to set aside."""
        root = project(
            uncertain={
                "status": "PAUSED_INVALID_OUTPUT",
                "iteration": 1,
                "active_stage": {"stage": "terra", "output": "001/terra-01.json", "iteration": 1},
            }
        )
        row = next(item for item in runs.rows(root) if item["name"] == "uncertain")
        self.assertNotIn("--abandon-stage", row["next_command"])
        self.assertIn("--status", row["next_command"])
        self.assertIn("001/terra-01", row["reason"])

    def test_the_table_groups_rows_and_prints_the_action_under_each(self):
        text = runs.render(runs.rows(self.root), str(self.root), now=time.time())
        self.assertIn(f"{runs.NEEDS} (1)", text)
        self.assertIn("-> autocode --run-dir", text)
        self.assertIn("working (1)", text)


class IssueWorktreeRunsTests(unittest.TestCase):
    """Chat everywhere (#1731): a project's chat lists the runs of its autocode-issue worktrees too."""

    def test_an_issue_worktrees_run_is_one_of_the_projects_runs_in_its_own_workspace(self):
        root = project(local={"status": "RUNNING"})
        worktree = root / runs.ISSUE_WORKTREES / "acme-widgets-7"
        run_dir = worktree / ".autocode" / "runs" / "issue-run"
        run_dir.mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: elsewhere\n")
        saved = {"version": 3, "workspace": str(worktree), "task": "Resolve GitHub issue", "status": "RUNNING"}
        (run_dir / "state.json").write_text(json.dumps(saved))
        listed = {Path(row["run_dir"]).name: Path(row["workspace"]) for row in runs.rows(root)}
        self.assertEqual({"local": root, "issue-run": worktree}, listed)

    def test_a_task_worktrees_run_is_one_of_the_projects_runs(self):
        """A task started without --in-place runs in .autocode/worktrees/<lane>; --status and resume find it,
        so `autocode runs` must not answer "No runs found" for it."""
        root = project(local={"status": "RUNNING"}).resolve()
        lane = root / ".autocode" / "worktrees" / "add-a-hello-script-0123456789"
        run_dir = lane / ".autocode" / "runs" / "lane-run"
        run_dir.mkdir(parents=True)
        (lane / ".git").write_text("gitdir: elsewhere\n")
        (lane / ".autocode" / "task-workspace.json").write_text(
            json.dumps({"workspace": str(lane), "project_workspace": str(root)})
        )
        saved = {"version": 3, "workspace": str(lane), "task": "Add a hello script", "status": "WAITING_FOR_USER"}
        (run_dir / "state.json").write_text(json.dumps(saved))
        listed = {Path(row["run_dir"]).name: Path(row["workspace"]) for row in runs.rows(root)}
        self.assertEqual({"local": root, "lane-run": lane}, listed)
        # Another project's lane, or one whose metadata names a different project, is not this project's run.
        (lane / ".autocode" / "task-workspace.json").write_text(
            json.dumps({"workspace": str(lane), "project_workspace": str(root / "elsewhere")})
        )
        self.assertEqual({"local"}, {Path(row["run_dir"]).name for row in runs.rows(root)})

    def test_the_issue_store_is_the_one_autocode_issue_writes(self):
        import autocode_issue

        self.assertEqual(autocode_issue.STORE, runs.ISSUE_WORKTREES)


class StatusViewProjectionTests(unittest.TestCase):
    """The fleet row is the status view's own need and command, so it cannot name a refused one."""

    def test_the_action_projection_matches_the_full_view(self):
        for status in (
            "PAUSED_REQUESTED",
            "PAUSED_INVALID_OUTPUT",
            "PAUSED_NO_PROGRESS",
            "RUNNING",
            "AWAITING_GOAL_APPROVAL",
            "TASK_COMPLETE",
        ):
            with self.subTest(status=status):
                state = {"status": status, "settings": {}, "iteration": 1, "stop_reason": "because"}
                if status.startswith("PAUSED"):
                    state["active_stage"] = {"stage": "terra", "output": "002/terra-02.json", "iteration": 2}
                shown = run_view.view(state, run_dir=Path("/tmp/run"), workspace=Path("/tmp/project"))
                action = run_view.action(state, run_dir=Path("/tmp/run"), workspace=Path("/tmp/project"))
                self.assertEqual(shown["needs"], action["needs"])
                self.assertEqual(shown["pause_category"], action["pause_category"])
                self.assertEqual(shown["pause_category_label"], action["pause_category_label"])
                self.assertEqual(shown["next_command"], action["next_command"])


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.root = project(
            waiting={"status": "PAUSED_REQUESTED", "stop_reason": "paused on request"},
            finished={"status": "TASK_COMPLETE"},
        )

    def test_json_rows_report_the_scope_and_the_counts(self):
        code, out, _ = capture(["--workspace", str(self.root), "--json"])
        self.assertEqual(0, code)
        document = json.loads(out)
        self.assertEqual(runs.SCHEMA, document["schema"])
        self.assertEqual("workspace", document["scope"])
        self.assertEqual(str(self.root), document["workspace"])
        self.assertEqual(2, document["counts"]["total"])
        self.assertEqual(["waiting", "finished"], [item["name"] for item in document["runs"]])

    def test_each_row_is_titled_with_the_first_line_of_its_task(self):
        root = project(
            short={"task": "\n  Fix the typo in README\nMore detail"},
            long={"task": "x" * 200},
            blank={"task": "  "},
            heading={"task": "# Hierarchical planning\nThe body"},
        )
        titles = {row["name"]: row["title"] for row in runs.rows(root)}
        self.assertEqual("Fix the typo in README", titles["short"])
        self.assertEqual(runs.TITLE_LIMIT, len(titles["long"]))
        self.assertTrue(titles["long"].endswith("…"))
        self.assertIsNone(titles["blank"])
        self.assertEqual("Hierarchical planning", titles["heading"], "a Markdown heading's marks are not the title")

    def test_needs_shows_only_the_runs_waiting_on_a_person(self):
        code, out, _ = capture(["--workspace", str(self.root), "--needs"])
        self.assertEqual(0, code)
        self.assertIn("waiting", out)
        self.assertNotIn("finished", out)

    def test_a_project_with_no_runs_is_a_refusal_not_an_empty_table(self):
        empty = Path(tempfile.mkdtemp()).resolve()
        code, _, err = capture(["--workspace", str(empty)])
        self.assertEqual(2, code)
        self.assertIn("No runs found", err)

    def test_all_and_an_explicit_workspace_refuse_each_other(self):
        with self.assertRaises(SystemExit) as stopped:
            capture(["--all", "--workspace", "/tmp"])
        self.assertEqual(2, stopped.exception.code)

    def test_empty_json_inventory_is_a_successful_read_not_a_prose_error(self):
        with tempfile.TemporaryDirectory() as empty:
            code, out, err = capture(["--workspace", empty, "--json"])
        self.assertEqual(0, code)
        self.assertEqual([], json.loads(out)["runs"])
        self.assertEqual(0, json.loads(out)["counts"]["total"])
        self.assertEqual("", err)

        with (
            tempfile.TemporaryDirectory() as registry_home,
            mock.patch.dict(os.environ, {"AUTOCODE_HOME": registry_home}),
        ):
            code, out, err = capture(["--all", "--json"])
            self.assertEqual(0, code)
            self.assertEqual([], json.loads(out)["runs"])
            self.assertEqual("", err)
            self.assertEqual(
                [], list(Path(registry_home).iterdir()), "inventory does not create a run or registry entry"
            )

    def test_a_registered_run_whose_directory_is_gone_names_the_forget_command(self):
        """#2321: the row said only "Run state.json is unavailable", and nothing named a way out."""
        with (
            tempfile.TemporaryDirectory() as registry_home,
            mock.patch.dict(os.environ, {"AUTOCODE_HOME": registry_home}),
        ):
            run = self.root / ".autocode" / "runs" / "finished"
            registry.register_run(self.root, run, json.loads((run / "state.json").read_text()))
            shutil.rmtree(run)
            code, out, _ = capture(["--all", "--json"])
            self.assertEqual(0, code)
            (row,) = json.loads(out)["runs"]
            self.assertEqual(runs.UNREADABLE, row["bucket"])
            self.assertIn("run directory no longer exists", row["reason"])
            self.assertIn(f"autocode registry forget-deleted --workspace {self.root} --run-dir {run}", row["reason"])
            self.assertNotIn("next_command", row)
            code, _, _ = capture(["--all"])
            self.assertEqual(0, code)

    def test_the_root_command_dispatches_runs(self):
        """`autocode runs` reaches the command through the CLI's own dispatch table."""
        import autocode

        argv = ["autocode", "runs", "--workspace", str(self.root), "--json"]
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"AUTOCODE_HOME": home}), mock.patch.object(sys, "argv", argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = autocode.main()
            self.assertEqual([], list(Path(home).iterdir()))
        self.assertEqual(0, code)
        document = json.loads(out.getvalue())
        self.assertEqual("workspace", document["scope"])
        self.assertEqual(2, document["counts"]["total"])


class SharedCheckpointReaderTests(unittest.TestCase):
    """One reader decides when a run's file may be trusted, for the registry and the fleet table alike."""

    def setUp(self):
        self.root = project(one={"status": "RUNNING"})
        self.run_dir = self.root / ".autocode" / "runs" / "one"

    def test_an_available_checkpoint_carries_its_state_and_summary(self):
        availability, payload = registry.read_checkpoint(self.run_dir / "state.json", self.root)
        self.assertEqual("available", availability)
        self.assertEqual("RUNNING", payload["status"])
        self.assertEqual("build one", payload["state"]["task"])

    def test_a_symlinked_checkpoint_is_never_read(self):
        linked = self.root / ".autocode" / "runs" / "linked"
        linked.mkdir()
        (linked / "state.json").symlink_to(self.run_dir / "state.json")
        availability, payload = registry.read_checkpoint(linked / "state.json", self.root)
        self.assertEqual("checkpoint_missing", availability)
        self.assertNotIn("state", payload)

    def test_the_registry_listing_still_keeps_the_full_state_out(self):
        state = json.loads((self.run_dir / "state.json").read_text())
        with mock.patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")}):
            registry.register_run(self.root, self.run_dir, state)
            listed = registry.listing()
        record = next(item for item in listed["runs"] if item["run_dir"] == str(self.run_dir))
        self.assertEqual("available", record["availability"])
        self.assertNotIn("state", record["diagnostic"])
        self.assertEqual("RUNNING", record["diagnostic"]["status"])


if __name__ == "__main__":
    unittest.main()
