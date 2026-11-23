"""Public checkpoint CLI tests with real Git and owned temporary run fixtures."""

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import autocode_checkpoint_cli as operation
import autocode_code_checkpoints as checkpoints
import autocode_contract_identity as identity
import autocode_run_view as run_view
import autocode_source_scope as source_scope
import autocode_util as util
from autocode_taskrun import TaskRun, TaskRunError
from goal_fixtures import body

from . import test_subprocess

TOOLS = Path(__file__).resolve().parents[1] / "tools"


class CodeCheckpoints(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.git("init", "-q")
        (self.workspace / "app.txt").write_text("baseline\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        self.run = self.workspace / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        (self.workspace / ".autocode/.gitignore").write_text("*\n")
        self.state = {
            "version": 3,
            "task_id": "run-task",
            "task": "Build fixture",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "created_at": util.now(),
            "status": "PAUSED_REQUESTED",
            "iteration": 3,
            "next_stage": "tester",
            "sessions": {},
            "history": [],
            "stages": [],
            "active_seconds": 12,
            "settings": {
                "engine": "codex",
                "roles": {"builder": {"model": "gpt-6-sol", "reasoning_effort": "high", "pinned": True}},
                "limits": {"max_seconds": 0},
            },
            "user_events": [],
            "findings_ledger": [{"id": "early", "status": "open"}],
            "workflow": {"kind": "build"},
            "current_task": {"id": "bounded-task", "milestone_id": "M1", "objective": "Fixture code"},
        }
        contract = {"task_id": self.state["task_id"], "revision": 1, "body": body()}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract), "at": util.now()}
        contract.update(approval_status="approved", approval_event=approval, origin="draft_plan")
        self.state["goal_contract"] = contract
        self.state["user_events"].append(approval)
        self.state["base_commit"] = self.git("rev-parse", "HEAD")
        (self.workspace / "app.txt").write_text("checkpoint code\n")
        (self.workspace / "new file.txt").write_text("new at checkpoint\n")
        (self.workspace / "run.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.workspace / "run.sh").chmod(0o755)
        (self.workspace / "link").symlink_to("app.txt")
        snap = util.snapshot(self.workspace)
        self.state["implementation"] = {
            "task_id": "bounded-task",
            "source_revision": snap["revision"],
            "contract_hash": contract["hash"],
        }
        self.state["stages"].append(
            {
                "stage": "builder",
                "task_id": "bounded-task",
                "source_revision": snap["revision"],
                "exit_code": 0,
                "finished_at": util.now(),
                "changed_files": ["app.txt", "new file.txt", "run.sh", "link"],
            }
        )
        checkpoints.update(self.state, self.run)
        self.assertTrue(self.state["code_checkpoints"][0]["available"], self.state["code_checkpoints"][0])
        self.ident = self.state["code_checkpoints"][0]["id"]
        self.save()
        self.client = TaskRun(
            self.workspace,
            self.run,
            command=(sys.executable, str(TOOLS / "autocode.py")),
            env={"AUTOCODE_HOME": str(self.root / "registry")},
            timeout=20,
        )

    def git(self, *args):
        return checkpoints.git(self.workspace, *args).decode().strip()

    def save(self):
        util.atomic_json(self.run / "state.json", self.state)

    def test_capture_leaves_head_index_and_uncommitted_content_unchanged(self):
        self.assertEqual(self.state["base_commit"], self.git("rev-parse", "HEAD"))
        self.assertEqual("app.txt", self.git("ls-files"))
        self.assertEqual("checkpoint code\n", (self.workspace / "app.txt").read_text())
        old = copy.deepcopy(self.state["code_checkpoints"])
        checkpoints.update(self.state, self.run)
        self.assertEqual(old, self.state["code_checkpoints"])
        comparison = self.client.compare_checkpoint(self.ident)
        self.assertEqual([], comparison["changed_files"])
        self.assertEqual("", comparison["patch"])

    def step(self, ident):
        """The named, display-only ``--step`` through the real CLI."""
        rows = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["rows"]
        [row] = [row for row in rows if row["id"] == ident]
        [action] = [action for action in row["actions"] if action["id"] == "step"]
        result = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), *action["next_command"]],
            capture_output=True,
            text=True,
            env={**os.environ, "AUTOCODE_HOME": str(self.root / "registry")},
            timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_a_step_shows_its_own_change_when_the_files_still_match_it(self):
        # compare of the newest step shows nothing on a finished run; --step shows what the step changed.
        self.assertEqual([], self.client.compare_checkpoint(self.ident)["changed_files"])
        first = self.step(self.ident)
        self.assertEqual("start", first["since"])
        self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], first["changed_files"])
        self.assertIn("-baseline\n+checkpoint code\n", first["patch"])
        self.assertIn("+new at checkpoint", first["patch"])
        # A later step is shown against the step before it, from the saved code, not the files on disk.
        (self.workspace / "app.txt").write_text("second step\n")
        snap = util.snapshot(self.workspace)
        self.state["iteration"] = 4
        self.state["implementation"]["source_revision"] = snap["revision"]
        self.state["stages"].append(
            {
                "stage": "builder",
                "task_id": "bounded-task",
                "source_revision": snap["revision"],
                "exit_code": 0,
                "finished_at": util.now(),
                "changed_files": ["app.txt"],
            }
        )
        checkpoints.update(self.state, self.run)
        self.save()
        later = self.state["code_checkpoints"][-1]["id"]
        self.assertNotEqual(self.ident, later)
        (self.workspace / "app.txt").write_text("not saved yet\n")
        second = self.step(later)
        self.assertEqual((self.ident, ["app.txt"]), (second["since"], second["changed_files"]))
        self.assertIn("-checkpoint code\n+second step\n", second["patch"])
        self.assertNotIn("not saved yet", second["patch"])
        self.assertEqual("not saved yet\n", (self.workspace / "app.txt").read_text(), "display only")

    def test_restore_preserves_original_dirty_index_and_marks_later_findings(self):
        checkpoint_bytes = (self.run / "code-checkpoints" / f"{self.ident}.json").read_bytes()
        (self.workspace / "app.txt").write_text("later work\n")
        self.git("add", "app.txt")
        self.state["validation"] = {
            "verdict": "PASS",
            "source_revision": "later",
            "contract_hash": self.state["goal_contract"]["hash"],
        }
        self.state["human_reviews"] = {"AC1": {"actor": "user_cli"}}
        self.state["final_decision"] = {"status": "COMPLETE"}
        self.state["findings_ledger"][0]["status"] = "resolved"  # Fixed only by later code; rollback must reopen it.
        self.state["findings_ledger"].append({"id": "later", "status": "open", "finding": "Later code problem"})
        self.state["milestone_progress"] = {
            "M1": {
                "id": "M1",
                "contract_hash": self.state["goal_contract"]["hash"],
                "accepted": True,
                "seconds": 12,
                "replans": 1,
                "acceptance_criteria": ["AC1"],
            }
        }
        self.save()
        before = (self.git("rev-parse", "HEAD"), self.git("write-tree"), self.git("diff", "--cached"))
        compared = self.client.compare_checkpoint(self.ident)
        self.assertIn("app.txt", compared["changed_files"])
        self.assertIn("+later work", compared["patch"])
        child = self.client.restore_checkpoint(self.ident, compared["expected_token"], "restore-owned-one")
        self.assertNotEqual(self.workspace, child.workspace)
        self.assertEqual(before, (self.git("rev-parse", "HEAD"), self.git("write-tree"), self.git("diff", "--cached")))
        self.assertEqual("later work\n", (self.workspace / "app.txt").read_text())
        self.assertEqual("checkpoint code\n", (child.workspace / "app.txt").read_text())
        self.assertTrue((child.workspace / "run.sh").stat().st_mode & 0o111)
        self.assertTrue((child.workspace / "link").is_symlink())
        view = child.status()
        self.assertEqual("PAUSED_REQUESTED", view["status"])
        self.assertFalse(view["done"])
        self.assertIsNone(view["evidence"]["check_replay"])
        self.assertFalse(any(x["human_reviewed"] for x in view["evidence"]["acceptance"]))
        statuses = {row["id"]: row["status"] for row in view["evidence"]["findings"]}
        self.assertEqual({"early": "open", "later": "rolled_back"}, statuses)
        saved = util.read(child.run_dir / "state.json")  # owned fixture only
        self.assertEqual(self.state["goal_contract"], saved["goal_contract"])
        self.assertTrue(identity.approved(saved))
        self.assertEqual(self.state["settings"], saved["settings"])
        self.assertEqual(12, saved["active_seconds"])
        self.assertFalse(saved["milestone_progress"]["M1"]["accepted"])
        self.assertEqual(12, saved["milestone_progress"]["M1"]["seconds"])
        self.assertNotIn("validation", saved)
        self.assertNotIn("final_decision", saved)
        # An in-place original: the new worktree's project is the checkout its Builder edited (autocode_regression.proof_dependencies).
        self.assertEqual(
            (str(self.workspace), True), (saved["project_workspace"], saved.get("project_worked_in_place"))
        )
        self.assertEqual(self.state, util.read(child.run_dir / "restoration-history.json"))
        self.assertEqual(checkpoint_bytes, (self.run / "code-checkpoints" / f"{self.ident}.json").read_bytes())
        again = self.client.restore_checkpoint(self.ident, compared["expected_token"], "restore-owned-one")
        self.assertEqual(child.run_dir, again.run_dir)
        self.assertEqual(1, len(list((self.workspace / ".autocode/worktrees").iterdir())))

    def test_stale_source_or_approval_live_worker_and_lock_refuse(self):
        comparison = self.client.compare_checkpoint(self.ident)
        (self.workspace / "app.txt").write_text("unsent later change")
        with self.assertRaisesRegex(TaskRunError, "source changed"):
            self.client.restore_checkpoint(self.ident, comparison["expected_token"], "stale-source-one")
        for field, value in [
            ("active_stage", {"stage": "builder"}),
            ("active_runner_check", {"stage": "regression_proof"}),
            ("pending_questions", [{"id": "question"}]),
            ("uncertain_artifacts", True),
        ]:
            with self.subTest(field=field):
                self.state[field] = value
                self.save()
                comparison = self.client.compare_checkpoint(self.ident)
                with self.assertRaisesRegex(TaskRunError, "Reconcile"):
                    self.client.restore_checkpoint(self.ident, comparison["expected_token"], "live-refusal-" + field)
                self.state.pop(field)
                self.save()
        with util.run_lock(self.run):
            with self.assertRaisesRegex(TaskRunError, "run lock"):
                self.client.restore_checkpoint(self.ident, comparison["expected_token"], "locked-refusal")
        self.state["goal_contract"]["approval_status"] = "draft"
        self.save()
        with self.assertRaisesRegex(TaskRunError, "approved plan"):
            self.client.restore_checkpoint(self.ident, comparison["expected_token"], "unapproved-refusal")
        self.assertFalse((self.workspace / ".autocode/worktrees").exists())

    def test_changed_receipt_legacy_step_pending_intervention_refuse(self):
        with self.assertRaisesRegex(TaskRunError, "no restorable"):
            self.client.compare_checkpoint("legacy-step")
        comparison = self.client.compare_checkpoint(self.ident)
        util.atomic_json(self.run / "interventions.json", {"requests": [{"id": "new-pause"}]})
        with self.assertRaisesRegex(TaskRunError, "pending intervention"):
            self.client.restore_checkpoint(self.ident, comparison["expected_token"], "inbox-refusal")
        (self.run / "interventions.json").unlink()
        path = self.run / "code-checkpoints" / f"{self.ident}.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(TaskRunError, "receipt changed"):
            self.client.compare_checkpoint(self.ident)

    def test_partial_failure_reconciles_same_candidate_without_reset(self):
        comparison = self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry, "register_run", side_effect=ValueError("registry unavailable")):
            with self.assertRaisesRegex(ValueError, "registry unavailable"):
                operation.restore(self.state, self.run, self.ident, comparison["expected_token"], "partial-owned-one")
        partial = util.read(self.run / "checkpoint-restores/partial-owned-one.json")
        workspace = Path(partial["workspace"])
        original = (workspace / "app.txt").read_bytes()
        child = self.client.restore_checkpoint(self.ident, comparison["expected_token"], "partial-owned-one")
        self.assertEqual(workspace, child.workspace)
        self.assertEqual(original, (workspace / "app.txt").read_bytes())
        with self.assertRaisesRegex(TaskRunError, "different checkpoint"):
            self.client.restore_checkpoint(self.ident, "wrong-token", "partial-owned-one")

    def test_changed_partial_candidate_is_preserved_and_refused(self):
        comparison = self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry, "register_run", side_effect=ValueError("registry unavailable")):
            with self.assertRaises(ValueError):
                operation.restore(self.state, self.run, self.ident, comparison["expected_token"], "partial-changed-one")
        partial = util.read(self.run / "checkpoint-restores/partial-changed-one.json")
        workspace = Path(partial["workspace"])
        (workspace / "app.txt").write_text("independent later edit")
        with self.assertRaisesRegex(TaskRunError, "source changed"):
            self.client.restore_checkpoint(self.ident, comparison["expected_token"], "partial-changed-one")
        self.assertEqual("independent later edit", (workspace / "app.txt").read_text())

    def test_partial_continuation_accounting_change_refuses_without_reset(self):
        comparison = self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry, "register_run", side_effect=ValueError("registry unavailable")):
            with self.assertRaises(ValueError):
                operation.restore(self.state, self.run, self.ident, comparison["expected_token"], "partial-budget-one")
        partial = util.read(self.run / "checkpoint-restores/partial-budget-one.json")
        run = next((Path(partial["workspace"]) / ".autocode/runs").iterdir())
        state_path = run / "state.json"
        changed = util.read(state_path)
        changed["active_seconds"] = 0
        util.atomic_json(state_path, changed)
        before = state_path.read_bytes()
        with self.assertRaisesRegex(TaskRunError, "continuation has changed"):
            self.client.restore_checkpoint(self.ident, comparison["expected_token"], "partial-budget-one")
        self.assertEqual(before, state_path.read_bytes())

    def test_completed_receipt_recovers_missing_chat_history_without_new_candidate(self):
        comparison = self.client.compare_checkpoint(self.ident)
        original_write = util.atomic_json

        def fail_status(path, data):
            if Path(path) == self.run / "state.json":
                raise OSError("status receipt interrupted")
            return original_write(path, data)

        with patch.object(util, "atomic_json", side_effect=fail_status):
            with self.assertRaisesRegex(OSError, "status receipt interrupted"):
                operation.restore(self.state, self.run, self.ident, comparison["expected_token"], "receipt-gap-owned")
        self.assertEqual([], self.client.status()["code_checkpoints"]["restores"])
        child = self.client.restore_checkpoint(self.ident, comparison["expected_token"], "receipt-gap-owned")
        rows = self.client.status()["code_checkpoints"]["restores"]
        self.assertEqual([str(child.run_dir)], [r["run_dir"] for r in rows])
        self.assertEqual(1, len(list((self.workspace / ".autocode/worktrees").iterdir())))

    def test_partial_staged_change_is_preserved_and_refused(self):
        comparison = self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry, "register_run", side_effect=ValueError("registry unavailable")):
            with self.assertRaises(ValueError):
                operation.restore(self.state, self.run, self.ident, comparison["expected_token"], "partial-index-owned")
        partial = util.read(self.run / "checkpoint-restores/partial-index-owned.json")
        workspace = Path(partial["workspace"])
        file = workspace / "app.txt"
        old = file.read_bytes()
        file.write_text("staged later edit")
        checkpoints.git(workspace, "add", "app.txt")
        file.write_bytes(old)
        staged = checkpoints.git(workspace, "write-tree")
        with self.assertRaisesRegex(TaskRunError, "source changed"):
            self.client.restore_checkpoint(self.ident, comparison["expected_token"], "partial-index-owned")
        self.assertEqual(staged, checkpoints.git(workspace, "write-tree"))
        self.assertEqual(old, file.read_bytes())

    def test_partial_redirected_storage_refuses_before_creating_locks(self):
        comparison = self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry, "register_run", side_effect=ValueError("registry unavailable")):
            with self.assertRaises(ValueError):
                operation.restore(self.state, self.run, self.ident, comparison["expected_token"], "partial-link-owned")
        partial = util.read(self.run / "checkpoint-restores/partial-link-owned.json")
        workspace = Path(partial["workspace"])
        runs = workspace / ".autocode/runs"
        runs.rename(workspace / ".autocode/preserved-runs")
        outside = self.root / "outside"
        outside.mkdir()
        runs.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(TaskRunError, "operational storage changed"):
            self.client.restore_checkpoint(self.ident, comparison["expected_token"], "partial-link-owned")
        self.assertEqual([], list(outside.iterdir()))
        self.assertTrue((workspace / ".autocode/preserved-runs").is_dir())

    def test_restore_does_not_replenish_consumed_recovery_or_extension_allowances(self):
        self.state.update(
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=2,
            milestone_active_seconds={"M1": 123},
            automatic_timeout_recoveries=[{"attempt": "old"}],
            resolver={
                "budget_extensions": [{"kind": "max_seconds", "idempotency_key": "used"}],
                "human_escalations": {"historical": {"status": "consumed"}},
            },
        )
        self.save()
        compared = self.client.compare_checkpoint(self.ident)
        child = self.client.restore_checkpoint(self.ident, compared["expected_token"], "spent-allowance-owned")
        restored = util.read(child.run_dir / "state.json")  # owned disposable fixture
        from autocode_recovery_limits import stop_reason
        from autocode_run_records import recovery_count

        self.assertEqual("PAUSED_TIMEOUT_RECOVERY", stop_reason(restored, recovery_count(restored), 3)[0])
        self.assertEqual({"M1": 123}, restored["milestone_active_seconds"])
        self.assertEqual([{"attempt": "old"}], restored["automatic_timeout_recoveries"])
        self.assertEqual(self.state["resolver"]["budget_extensions"], restored["resolver"]["budget_extensions"])
        self.assertNotIn("human_escalations", restored["resolver"])

    def test_checkpoint_cli_accepts_json_flag(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = operation.cli(
                [
                    "--workspace",
                    str(self.workspace),
                    "--run-dir",
                    str(self.run),
                    "--compare",
                    self.ident,
                    "--json",
                ]
            )
        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertEqual(self.ident, payload["checkpoint_id"])


class ProjectedRowActions(CodeCheckpoints):
    """The projection names the checkpoint actions a CLI client may run (#1532)."""

    def test_a_restorable_pause_names_compare_and_restore_with_real_paths(self):
        projected = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)
        [row] = projected["rows"]
        prefix = ["checkpoint", "--workspace", str(self.workspace), "--run-dir", str(self.run)]
        self.assertIsNone(projected["blocked_reason"])
        self.assertEqual(
            [
                {
                    "id": "compare",
                    "label": "Show what changed since here",
                    "next_command": [*prefix, "--compare", self.ident],
                },
                {
                    "id": "step",
                    "label": "Show what this step changed",
                    "next_command": [*prefix, "--step", self.ident],
                },
                {
                    "id": "restore",
                    "label": "Go back to here",
                    "next_command": [
                        *prefix,
                        "--restore",
                        self.ident,
                        "--expected-token",
                        "-",
                        "--request-id",
                        "REQUEST_ID",
                    ],
                },
            ],
            row["actions"],
        )

    def test_the_status_view_passes_the_runs_own_paths(self):
        view = run_view.view(self.state, run_dir=self.run, workspace=self.workspace)
        named = view["code_checkpoints"]["rows"][0]["actions"]
        self.assertTrue(named[0]["next_command"][2] == str(self.workspace))
        self.assertTrue(named[1]["next_command"][4] == str(self.run))

    def test_without_paths_the_named_actions_use_the_public_placeholders(self):
        [row] = checkpoints.project(self.state)["rows"]
        self.assertEqual("WORKSPACE", row["actions"][0]["next_command"][2])
        self.assertEqual("RUN_DIR", row["actions"][0]["next_command"][4])

    def test_an_unreconciled_pause_names_compare_only(self):
        self.state["status"] = "RUNNING"
        [row] = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["rows"]
        self.assertEqual(["compare", "step"], [action["id"] for action in row["actions"]])

    def test_a_finished_run_names_compare_only_and_says_why_without_asking_for_a_pause(self):
        # #1643: on a finished run the reason told the person to pause, though nothing was running.
        self.state["status"] = "TASK_COMPLETE"
        projected = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)
        self.assertEqual(["compare", "step"], [action["id"] for action in projected["rows"][0]["actions"]])
        self.assertIn("finished run", projected["blocked_reason"])
        self.assertNotIn("Pause", projected["blocked_reason"])

    def test_a_row_without_verified_source_names_no_actions(self):
        self.state["code_checkpoints"][0].update(available=False, reason="storage failed")
        [row] = checkpoints.project(self.state)["rows"]
        self.assertEqual([], row["actions"])

    def test_the_named_restore_grammar_round_trips_through_the_cli(self):
        comparison = self.client.compare_checkpoint(self.ident)
        restore = [
            action
            for action in checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["rows"][0][
                "actions"
            ]
            if action["id"] == "restore"
        ][0]["next_command"]
        # A client prepends the autocode executable to the named argv and passes
        # the inspected token through its environment variable, never argv.
        argv = [word if word != "REQUEST_ID" else "view-named-restore-1" for word in restore]
        import os

        done = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), *argv],
            capture_output=True,
            text=True,
            env={**os.environ, "AUTOCODE_CHECKPOINT_EXPECTED_TOKEN": comparison["expected_token"]},
        )
        self.assertEqual(0, done.returncode, done.stderr)
        payload = json.loads(done.stdout)
        self.assertEqual("view-named-restore-1", payload["operation"])
        self.assertEqual("PAUSED_REQUESTED", payload["status"])


class AllChanges(unittest.TestCase):
    """`checkpoint --all`: the run's whole change, the one diff a person reads (owner decision 2026-11-13)."""

    setUp = CodeCheckpoints.setUp
    git = CodeCheckpoints.git
    save = CodeCheckpoints.save

    def all_changes(self):
        named = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["all_changes"]
        self.assertEqual(
            ["checkpoint", "--workspace", str(self.workspace), "--run-dir", str(self.run), "--all"],
            named["next_command"],
        )
        self.assertEqual("Show all changes", named["label"])
        done = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), *named["next_command"]],
            capture_output=True,
            text=True,
            env={**os.environ, "AUTOCODE_HOME": str(self.root / "registry")},
            timeout=60,
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def save_step(self, changed):
        snap = util.snapshot(self.workspace)
        self.state["iteration"] += 1
        self.state["implementation"]["source_revision"] = snap["revision"]
        self.state["stages"].append(
            {
                "stage": "builder",
                "task_id": "bounded-task",
                "source_revision": snap["revision"],
                "exit_code": 0,
                "finished_at": util.now(),
                "changed_files": changed,
            }
        )
        checkpoints.update(self.state, self.run)
        self.save()
        return self.state["code_checkpoints"][-1]["id"]

    def test_it_spans_every_saved_step_from_the_start(self):
        (self.workspace / "app.txt").write_text("second step\n")
        (self.workspace / "new file.txt").unlink()
        (self.workspace / "later.txt").write_text("added later\n")
        last = self.save_step(["app.txt", "new file.txt", "later.txt"])
        (self.workspace / "app.txt").write_text("not saved yet\n")
        shown = self.all_changes()
        self.assertEqual(("start", last), (shown["since"], shown["until"]))
        # A file one step added and a later step removed is not part of the result.
        self.assertEqual(["app.txt", "later.txt", "link", "run.sh"], shown["changed_files"])
        self.assertIn("-baseline\n+second step\n", shown["patch"])
        self.assertIn("+added later", shown["patch"])
        self.assertNotIn("checkpoint code", shown["patch"], "the steps in between are not shown")
        self.assertNotIn("not saved yet", shown["patch"], "saved code, never the files on disk")
        self.assertNotIn("expected_token", shown, "display only: nothing here can restore")

    def test_a_file_the_run_removed_is_shown_as_removed(self):
        (self.workspace / "app.txt").unlink()
        self.save_step(["app.txt"])
        shown = self.all_changes()
        self.assertIn("app.txt", shown["changed_files"])
        self.assertIn("-baseline", shown["patch"])

    def test_a_finished_run_still_shows_its_result(self):
        self.state["status"] = "TASK_COMPLETE"
        self.save()
        shown = self.all_changes()
        self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])

    def test_it_is_named_only_once_a_step_is_saved(self):
        self.state["code_checkpoints"][0].update(available=False, reason="storage failed")
        self.assertIsNone(checkpoints.project(self.state)["all_changes"])
        self.state["code_checkpoints"] = []
        self.assertIsNone(checkpoints.project(self.state)["all_changes"])
        self.save()
        self.assertEqual(2, operation.cli(["--workspace", str(self.workspace), "--run-dir", str(self.run), "--all"]))

    def test_without_paths_it_uses_the_public_placeholders(self):
        named = checkpoints.project(self.state)["all_changes"]["next_command"]
        self.assertEqual(("WORKSPACE", "RUN_DIR"), (named[2], named[4]))

    # Work a run keeps without a new saved step (#3945, #3933): every durable save captures it.

    def record_stage(self, stage, **row):
        self.state["stages"].append(
            {"stage": stage, "task_id": "bounded-task", "exit_code": 0, "finished_at": util.now(), **row}
        )

    def update(self):
        """The durable save's checkpoint update, as `autocode_status.persist` runs it."""
        checkpoints.update(self.state, self.run)
        self.save()

    def builder_retry(self):
        """A Builder retry that kept its bytes and saved no step: its handoff lists no changed files."""
        (self.workspace / "docs").mkdir()
        (self.workspace / "docs/guide.md").write_text("guide\n")
        (self.workspace / "README.md").write_text("readme\n")
        revision = util.snapshot(self.workspace)["revision"]
        self.state["implementation"]["source_revision"] = revision
        self.record_stage("builder", source_revision=revision, changed_files=[])
        self.update()

    def inventory_fixture(self):
        """A workspace whose start commit also tracks `keep.txt` and `.vscode/settings.json`, which the
        source inventory leaves out, with one saved Builder step S1 that changed `app.txt`."""
        self.workspace = self.root / "inventory"
        self.workspace.mkdir()
        self.git("init", "-q")
        (self.workspace / "app.txt").write_text("baseline\n")
        (self.workspace / "keep.txt").write_text("keep\n")
        (self.workspace / ".vscode").mkdir()
        (self.workspace / ".vscode/settings.json").write_text("{}\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        self.run = self.workspace / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        (self.workspace / ".autocode/.gitignore").write_text("*\n")
        self.state.update(
            workspace=str(self.workspace),
            run_dir=str(self.run),
            stages=[],
            code_checkpoints=[],
            base_commit=self.git("rev-parse", "HEAD"),
        )
        self.state.pop("kept_code", None)
        (self.workspace / "app.txt").write_text("step one\n")
        return self.save_step(["app.txt"])

    def test_ac1_builder_retry_without_checkpoint_is_in_all_changes(self):
        self.builder_retry()
        self.assertEqual(1, len(self.state["code_checkpoints"]))
        kept = checkpoints.project(self.state)["kept"]
        self.assertTrue(kept["available"])
        shown = self.all_changes()
        self.assertEqual(
            ["README.md", "app.txt", "docs/guide.md", "link", "new file.txt", "run.sh"], shown["changed_files"]
        )
        self.assertEqual(kept["id"], shown["until"])
        self.assertIn("+guide", shown["patch"])
        self.assertIn("+readme", shown["patch"])
        self.assertIs(False, shown["truncated"])
        self.assertIs(True, shown["complete"])
        self.assertIsNone(shown["note"])

    def test_ac2_tester_retained_tests_are_in_all_changes(self):
        (self.workspace / "tests").mkdir()
        tests = self.workspace / "tests/test_json.py"
        tests.write_text("".join(f"line {number}\n" for number in range(1, 148)))
        second = self.save_step(["tests/test_json.py"])
        with tests.open("a") as handle:
            handle.write("".join(f"line {number}\n" for number in range(148, 155)))
        self.record_stage("tester")
        self.update()
        shown = self.all_changes()
        self.assertNotEqual(second, shown["until"])
        self.assertIn("tests/test_json.py", shown["changed_files"])
        self.assertIn("+line 147\n", shown["patch"])
        self.assertIn("+line 154\n", shown["patch"])
        self.assertIs(True, shown["complete"])

    def test_ac3_work_kept_by_any_stage_is_in_all_changes(self):
        cases = [
            ("review_checkpoint", "kept by review_checkpoint\n"),
            ("write_acceptance_tests", "kept by write_acceptance_tests\n"),
            (None, "kept by accepted edit\n"),  # a source edit accepted at a pause: no stage row
        ]
        for stage, text in cases:
            with self.subTest(stage=stage):
                (self.workspace / "extra.txt").write_text(text)
                if stage:
                    self.record_stage(stage)
                self.update()
                shown = self.all_changes()
                self.assertIn("extra.txt", shown["changed_files"])
                self.assertIn("+" + text.strip(), shown["patch"])
                self.assertEqual(1, len(self.state["code_checkpoints"]))

    def test_ac4_completed_run_shows_its_delivery(self):
        delivered = ["README.md", "app.txt", "docs/guide.md", "link", "new file.txt", "run.sh"]
        self.builder_retry()
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        shown = self.all_changes()
        self.assertEqual(delivered, shown["changed_files"])
        self.assertIs(False, shown["truncated"])
        self.assertIs(True, shown["complete"])
        # A person editing after delivery does not change the delivered diff; the result says so.
        (self.workspace / "after.txt").write_text("edited after delivery\n")
        self.update()
        shown = self.all_changes()
        self.assertEqual(delivered, shown["changed_files"])
        self.assertIs(False, shown["complete"])
        self.assertEqual(
            "Ends at saved code " + shown["until"] + "; changes made after it are not included.", shown["note"]
        )

    def test_ac5_unsaved_edits_are_named_not_included(self):
        (self.workspace / "app.txt").write_text("second step\n")
        second = self.save_step(["app.txt"])
        (self.workspace / "app.txt").write_text("still being written\n")
        self.state["active_stage"] = {"stage": "builder"}
        self.update()
        shown = self.all_changes()
        self.assertEqual(second, shown["until"])
        self.assertNotIn("still being written", shown["patch"])
        self.assertIs(False, shown["complete"])
        self.assertEqual("Ends at saved code " + second + "; changes made after it are not included.", shown["note"])
        self.state.pop("active_stage")
        (self.workspace / "app.txt").write_text("kept but unsaved\n")
        with patch.object(checkpoints, "capture_tree", side_effect=ValueError("storage gone")):
            self.update()
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual((False, "storage gone"), (kept["available"], kept["reason"]))
        shown = self.all_changes()
        self.assertEqual(second, shown["until"])
        self.assertIs(False, shown["complete"])
        self.assertEqual(
            "Ends at saved code "
            + second
            + "; changes made after it are not included. The newest kept code could not be saved: storage gone",
            shown["note"],
        )

    def test_an_unreadable_source_is_recorded_without_failing_the_save(self):
        (self.workspace / "app.txt").write_text("kept but unreadable\n")
        self.record_stage("tester")
        with patch.object(source_scope, "snapshot", side_effect=OSError("unreadable")):
            self.update()  # The durable save goes on; the gap is recorded instead of raised.
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual((False, "unreadable"), (kept["available"], kept["reason"]))
        self.assertIsNone(self.state["kept_code"]["source_revision"])
        self.assertIsNone(self.state["kept_code"]["commit"])
        self.assertEqual("kept-" + util.digest([str(self.run.resolve()), None])[:24], kept["id"])
        shown = self.all_changes()
        self.assertIs(False, shown["complete"])
        self.assertTrue(shown["note"].endswith("The newest kept code could not be saved: unreadable"), shown["note"])

    def test_ac6_kept_work_without_a_saved_step_is_offered(self):
        # The only Builder attempt timed out; its retry kept the bytes without saving a step.
        self.state["stages"][0]["changed_files"] = []
        self.state["code_checkpoints"] = []
        self.update()
        named = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["all_changes"]
        self.assertEqual(
            ["checkpoint", "--workspace", str(self.workspace), "--run-dir", str(self.run), "--all"],
            named["next_command"],
        )
        shown = self.all_changes()
        self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertIs(True, shown["complete"])

    def test_ac17_completed_run_without_kept_work_keeps_its_delivery(self):
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        self.assertEqual(1, len(self.state["code_checkpoints"]))
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual((self.ident, self.state["code_checkpoints"][0]["commit"]), (kept["id"], kept["commit"]))
        (self.workspace / "after.txt").write_text("edited after delivery\n")
        self.update()
        shown = self.all_changes()
        self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertEqual(self.ident, shown["until"])
        self.assertIs(False, shown["complete"])
        self.assertEqual(
            "Ends at saved code " + self.ident + "; changes made after it are not included.", shown["note"]
        )

    def test_ac18_files_removed_by_kept_work_are_accounted_for(self):
        # No saved step: the kept work deleted the tracked app.txt.
        self.state["stages"][0]["changed_files"] = []
        self.state["code_checkpoints"] = []
        (self.workspace / "app.txt").unlink()
        self.update()
        shown = self.all_changes()
        self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertIn("-baseline", shown["patch"])
        self.assertIs(True, shown["complete"])
        # One saved step, then the Tester's retained work removed a file the step added.
        self.setUp()
        (self.workspace / "new file.txt").unlink()
        self.record_stage("tester")
        self.update()
        shown = self.all_changes()
        self.assertEqual(["app.txt", "link", "run.sh"], shown["changed_files"])
        self.assertNotIn("new at checkpoint", shown["patch"])
        self.assertIs(True, shown["complete"])

    def test_ac19_follow_up_completion_delivers_its_new_steps(self):
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        self.state["status"] = "PAUSED_REQUESTED"
        (self.workspace / "later.txt").write_text("added later\n")
        second = self.save_step(["later.txt"])
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        shown = self.all_changes()
        self.assertEqual(second, shown["until"])
        self.assertEqual(["app.txt", "later.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertIn("+added later", shown["patch"])
        self.assertIs(True, shown["complete"])
        self.assertIsNone(shown["note"])
        self.assertEqual(second, checkpoints.project(self.state)["kept"]["id"])

    def test_ac20_kept_removals_stay_inside_the_source_inventory(self):
        self.inventory_fixture()
        (self.workspace / "keep.txt").unlink()
        (self.workspace / "notes.txt").write_text("kept note\n")
        self.record_stage("tester")
        self.update()
        self.assertEqual("{}\n", (self.workspace / ".vscode/settings.json").read_text())
        shown = self.all_changes()
        self.assertEqual(["app.txt", "keep.txt", "notes.txt"], shown["changed_files"])
        self.assertIn("-keep", shown["patch"])
        self.assertIn("+kept note", shown["patch"])
        self.assertNotIn("settings.json", shown["patch"])
        self.assertIs(True, shown["complete"])

    def test_ac21_kept_removal_survives_a_later_step_and_completion(self):
        self.inventory_fixture()
        (self.workspace / "keep.txt").unlink()
        self.record_stage("tester")
        self.update()
        (self.workspace / "later.txt").write_text("added later\n")
        second = self.save_step(["later.txt"])
        shown = self.all_changes()
        self.assertEqual(second, shown["until"])
        self.assertEqual(["app.txt", "keep.txt", "later.txt"], shown["changed_files"])
        self.assertIn("-keep", shown["patch"])
        self.assertIn("+added later", shown["patch"])
        self.assertNotIn("settings.json", shown["patch"])
        self.assertIs(True, shown["complete"])
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        shown = self.all_changes()
        self.assertEqual(second, shown["until"])
        self.assertEqual(["app.txt", "keep.txt", "later.txt"], shown["changed_files"])
        self.assertIs(True, shown["complete"])
        self.assertIsNone(shown["note"])

    def test_a_changed_kept_receipt_falls_back_to_the_newest_step_and_says_so(self):
        self.builder_retry()
        receipt = Path(self.state["kept_code"]["receipt"])
        receipt.write_text(receipt.read_text() + " ")
        shown = self.all_changes()
        self.assertEqual(self.ident, shown["until"])
        self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertIs(False, shown["complete"])
        self.assertEqual(
            "Ends at saved code "
            + self.ident
            + "; changes made after it are not included."
            + " The newest kept code could not be read: The saved kept code receipt changed",
            shown["note"],
        )
        # The next save cannot read where the kept code ended either: the gap is recorded, the save goes on.
        (self.workspace / "README.md").write_text("readme again\n")
        self.update()
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual((False, "The saved code checkpoint receipt changed"), (kept["available"], kept["reason"]))

    def test_ac22_index_removal_survives_a_second_kept_capture(self):
        self.inventory_fixture()
        self.git("rm", "-q", "keep.txt")
        self.record_stage("tester")
        self.update()
        (self.workspace / "notes.txt").write_text("kept note\n")
        self.update()
        shown = self.all_changes()
        self.assertEqual(["app.txt", "keep.txt", "notes.txt"], shown["changed_files"])
        self.assertIn("-keep", shown["patch"])
        self.assertIn("+kept note", shown["patch"])
        self.assertNotIn("settings.json", shown["patch"])
        self.assertIs(True, shown["complete"])

    def without_a_saved_step(self):
        """The only Builder attempt kept its bytes without saving a step (as in AC6)."""
        self.state["stages"][0]["changed_files"] = []
        self.state["code_checkpoints"] = []

    def kept_snapshot_revision(self):
        return source_scope.snapshot(str(self.workspace), self.state)["revision"]

    def test_with_no_saved_step_a_changed_kept_receipt_is_refused(self):
        # Without a step to fall back to, an unverifiable kept capture is refused, not shown as some older cutoff.
        self.without_a_saved_step()
        self.update()
        named = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["all_changes"]
        receipt = Path(self.state["kept_code"]["receipt"])
        receipt.write_text(receipt.read_text() + " ")
        done = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), *named["next_command"]],
            capture_output=True,
            text=True,
            env={**os.environ, "AUTOCODE_HOME": str(self.root / "registry")},
            timeout=60,
        )
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertEqual(
            {"code": "checkpoint_refused", "message": "The saved kept code receipt changed"},
            json.loads(done.stdout)["error"],
        )

    def test_a_save_outside_completion_releases_the_delivery_even_while_a_stage_runs(self):
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        # A follow-up starts: its first save comes while a stage is already running.
        self.state.update(status="RUNNING", active_stage={"stage": "builder"})
        (self.workspace / "extra.txt").write_text("follow-up edit\n")
        self.update()
        self.state.pop("active_stage")
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        shown = self.all_changes()
        self.assertEqual(["app.txt", "extra.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertIn("+follow-up edit", shown["patch"])
        self.assertIs(True, shown["complete"])
        self.assertIsNone(shown["note"])

    def test_an_approved_run_with_nothing_implemented_keeps_no_code(self):
        self.without_a_saved_step()
        self.state["implementation"].pop("source_revision")
        (self.workspace / "extra.txt").write_text("before any implementation\n")
        self.update()
        self.assertNotIn("kept_code", self.state)
        projected = checkpoints.project(self.state)
        self.assertIsNone(projected["kept"])
        self.assertIsNone(projected["all_changes"])

    def test_a_failed_capture_names_the_source_it_tried_to_save(self):
        (self.workspace / "app.txt").write_text("kept but unsaved\n")
        revision = self.kept_snapshot_revision()
        with patch.object(checkpoints, "capture_tree", side_effect=ValueError("storage gone")):
            self.update()
            failed = checkpoints.project(self.state)["kept"]
            self.update()
            again = checkpoints.project(self.state)["kept"]
        self.assertTrue(failed["id"].startswith("kept-"), failed)
        self.assertEqual(failed["id"], again["id"], "a repeated failure for the same source keeps its identity")
        self.assertEqual(revision, failed["source_revision"])
        self.assertIsNone(failed["commit"])
        self.assertIsInstance(failed["at"], str)
        self.assertTrue(failed["at"])
        self.assertEqual((False, "storage gone"), (failed["available"], failed["reason"]))
        # Once storage works, the same source is saved under the identity the failure named.
        self.update()
        saved = checkpoints.project(self.state)["kept"]
        self.assertEqual((failed["id"], revision), (saved["id"], saved["source_revision"]))
        self.assertEqual((True, None), (saved["available"], saved["reason"]))
        self.assertIsInstance(saved["commit"], str)
        self.assertIsInstance(saved["at"], str)
        self.assertTrue(saved["at"])
        self.assertEqual(saved["id"], self.all_changes()["until"])

    def test_kept_code_is_saved_under_the_runs_checkpoint_refs(self):
        (self.workspace / "extra.txt").write_text("kept\n")
        self.update()
        kept = checkpoints.project(self.state)["kept"]
        refs = dict(
            line.split(" ", 1)[::-1]
            for line in self.git(
                "for-each-ref", "--format=%(objectname) %(refname)", "refs/autocode/code-checkpoints/"
            ).splitlines()
        )
        [step_ref] = [ref for ref in refs if ref.endswith("/" + self.ident)]
        kept_ref = step_ref.rsplit("/", 1)[0] + "/" + kept["id"]
        self.assertEqual(kept["commit"], refs.get(kept_ref), refs)
        self.assertEqual("kept\n", self.git("show", kept["commit"] + ":extra.txt") + "\n")

    def test_a_step_marker_names_the_steps_source(self):
        self.state["status"] = "TASK_COMPLETE"
        self.update()
        kept = checkpoints.project(self.state)["kept"]
        step = self.state["code_checkpoints"][0]
        self.assertEqual(
            (step["id"], step["source_revision"], step["commit"]), (kept["id"], kept["source_revision"], kept["commit"])
        )
        self.assertEqual((True, None), (kept["available"], kept["reason"]))
        self.assertTrue(kept["at"])

    def test_with_no_saved_step_tracked_files_outside_the_inventory_are_not_removed(self):
        self.inventory_fixture()
        self.without_a_saved_step()
        self.state.pop("kept_code", None)
        (self.workspace / "notes.txt").write_text("kept note\n")
        self.update()
        shown = self.all_changes()
        self.assertEqual(["app.txt", "notes.txt"], shown["changed_files"])
        self.assertNotIn("settings.json", shown["patch"])
        self.assertIs(True, shown["complete"])

    def test_a_failed_capture_keeps_the_start_the_run_began_from(self):
        self.without_a_saved_step()
        self.update()
        # The person commits the run's app.txt; the run still started from the base commit.
        self.git("add", "app.txt")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "mine")
        (self.workspace / "README.md").write_text("readme\n")
        with patch.object(checkpoints, "capture_tree", side_effect=ValueError("storage gone")):
            self.update()
        self.assertIs(False, checkpoints.project(self.state)["kept"]["available"])
        self.update()
        shown = self.all_changes()
        self.assertEqual(["README.md", "app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertIn("-baseline", shown["patch"])
        self.assertIs(True, shown["complete"])

    def test_a_removal_kept_before_a_failed_capture_is_listed_after_it(self):
        self.inventory_fixture()
        self.git("rm", "-q", "keep.txt")
        self.record_stage("tester")
        self.update()
        (self.workspace / "later.txt").write_text("added later\n")
        self.save_step(["later.txt"])
        (self.workspace / "notes.txt").write_text("kept note\n")
        with patch.object(checkpoints, "capture_tree", side_effect=ValueError("storage gone")):
            self.update()
        self.update()
        self.assertIs(True, checkpoints.project(self.state)["kept"]["available"])
        shown = self.all_changes()
        self.assertEqual(["app.txt", "keep.txt", "later.txt", "notes.txt"], shown["changed_files"])
        self.assertIn("-keep", shown["patch"])
        self.assertIs(True, shown["complete"])

    def test_a_failed_capture_at_completion_is_retried_by_the_next_complete_save(self):
        (self.workspace / "README.md").write_text("delivered late\n")
        self.state["status"] = "TASK_COMPLETE"
        with patch.object(checkpoints, "capture_tree", side_effect=ValueError("storage gone")):
            self.update()
        self.assertIs(False, checkpoints.project(self.state)["kept"]["available"])
        self.update()
        shown = self.all_changes()
        self.assertEqual(["README.md", "app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
        self.assertIn("+delivered late", shown["patch"])
        self.assertIs(True, shown["complete"])
        self.assertIsNone(shown["note"])

    def test_returning_to_kept_code_reuses_its_saved_capture(self):
        extra = self.workspace / "extra.txt"
        extra.write_text("first\n")
        self.update()
        first = checkpoints.project(self.state)["kept"]
        extra.write_text("second\n")
        self.update()
        self.assertNotEqual(first["id"], checkpoints.project(self.state)["kept"]["id"])
        extra.write_text("first\n")
        self.update()
        again = checkpoints.project(self.state)["kept"]
        self.assertEqual((first["id"], first["commit"], True), (again["id"], again["commit"], again["available"]))
        shown = self.all_changes()
        self.assertEqual(first["id"], shown["until"])
        self.assertIn("+first", shown["patch"])
        self.assertIs(True, shown["complete"])

    def fallback_note(self, reason):
        return (
            "Ends at saved code "
            + self.ident
            + "; changes made after it are not included. The newest kept code could not be read: "
            + reason
        )

    def rewrite_kept_receipt(self, change):
        """Rewrite the kept capture's receipt and re-pin its hash, so only its contents are wrong."""
        receipt = Path(self.state["kept_code"]["receipt"])
        saved = json.loads(receipt.read_text())
        change(saved)
        receipt.write_text(json.dumps(saved))
        self.state["kept_code"]["receipt_sha256"] = util.file_hash(receipt)
        self.save()

    def test_a_kept_receipt_naming_other_code_falls_back_to_the_newest_step(self):
        self.builder_retry()
        receipt = Path(self.state["kept_code"]["receipt"])
        original = (receipt.read_text(), self.state["kept_code"]["receipt_sha256"])
        other = {"id": "kept-other", "commit": self.state["code_checkpoints"][0]["commit"], "run_dir": "/elsewhere"}
        for key, value in other.items():
            with self.subTest(key=key):
                self.rewrite_kept_receipt(lambda saved, key=key, value=value: saved.update({key: value}))
                shown = self.all_changes()
                self.assertEqual(self.ident, shown["until"])
                self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])
                self.assertIs(False, shown["complete"])
                self.assertEqual(self.fallback_note("Kept code identity changed"), shown["note"])
                receipt.write_text(original[0])
                self.state["kept_code"]["receipt_sha256"] = original[1]
                self.save()

    def test_a_kept_receipt_whose_source_differs_from_its_commit_is_not_shown(self):
        self.builder_retry()
        self.rewrite_kept_receipt(lambda saved: saved["source"]["files"].update({"README.md": "0" * 64}))
        shown = self.all_changes()
        self.assertEqual(self.ident, shown["until"])
        self.assertNotIn("+readme", shown["patch"])
        self.assertIs(False, shown["complete"])
        self.assertEqual(self.fallback_note("Saved kept code contents cannot be verified"), shown["note"])

    def test_a_linked_kept_receipt_is_not_read_as_the_end(self):
        self.builder_retry()
        receipt = Path(self.state["kept_code"]["receipt"])
        copied = self.run / "copied-receipt.json"
        copied.write_bytes(receipt.read_bytes())
        receipt.unlink()
        receipt.symlink_to(copied)
        (self.workspace / "README.md").write_text("readme again\n")
        self.update()
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual(
            (False, "Saved code receipt must be a regular file inside its run directory"),
            (kept["available"], kept["reason"]),
        )

    def test_a_deletion_a_step_did_not_name_is_listed_once_kept_work_follows(self):
        self.inventory_fixture()
        # The step's record names only later.txt, though keep.txt was deleted from the disk too.
        (self.workspace / "keep.txt").unlink()
        (self.workspace / "later.txt").write_text("added later\n")
        self.save_step(["later.txt"])
        (self.workspace / "notes.txt").write_text("kept note\n")
        self.record_stage("tester")
        self.update()
        self.assertEqual(["keep.txt"], self.state["kept_code"]["removed"])
        shown = self.all_changes()
        self.assertEqual(["app.txt", "keep.txt", "later.txt", "notes.txt"], shown["changed_files"])
        self.assertIn("-keep", shown["patch"])
        self.assertIs(True, shown["complete"])

    def test_kept_code_is_saved_when_no_checkpoint_storage_exists_yet(self):
        self.without_a_saved_step()
        shutil.rmtree(self.run / "code-checkpoints")
        self.update()
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual((True, None), (kept["available"], kept["reason"]))
        shown = self.all_changes()
        self.assertEqual(kept["id"], shown["until"])
        self.assertEqual(["app.txt", "link", "new file.txt", "run.sh"], shown["changed_files"])

    def test_linked_checkpoint_storage_is_refused_for_kept_code(self):
        storage = self.run / "storage"
        (self.run / "code-checkpoints").rename(storage)
        (self.run / "code-checkpoints").symlink_to(storage)
        (self.workspace / "extra.txt").write_text("kept\n")
        self.update()
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual((False, "Checkpoint storage must not be a symlink"), (kept["available"], kept["reason"]))
        self.assertEqual([], [path.name for path in storage.iterdir() if path.name.startswith("kept-")])

    def test_a_conflicting_kept_receipt_is_not_reused(self):
        extra = self.workspace / "extra.txt"
        extra.write_text("first\n")
        self.update()
        first = Path(self.state["kept_code"]["receipt"])
        extra.write_text("second\n")
        self.update()
        saved = json.loads(first.read_text())
        saved["source"]["files"]["extra.txt"] = "0" * 64
        first.write_text(json.dumps(saved))
        extra.write_text("first\n")
        self.update()
        kept = checkpoints.project(self.state)["kept"]
        self.assertEqual(
            (False, "An existing kept code receipt conflicts with this source"), (kept["available"], kept["reason"])
        )

    def test_the_kept_code_receipt_records_what_it_was_saved_from(self):
        first = self.inventory_fixture()
        (self.workspace / "keep.txt").unlink()
        (self.workspace / "notes.txt").write_text("kept note\n")
        self.record_stage("tester")
        self.update()
        kept = self.state["kept_code"]
        saved = json.loads(Path(kept["receipt"]).read_text())
        self.assertEqual(
            {
                "id": kept["id"],
                "kind": "kept",
                "commit": kept["commit"],
                "start": self.state["base_commit"],
                "after": first,
                "removed": ["keep.txt"],
                "run_dir": str(self.run),
                "workspace": str(self.workspace),
                "at": kept["at"],
            },
            {key: value for key, value in saved.items() if key != "source"},
        )
        self.assertEqual(kept["source_revision"], saved["source"]["revision"])


class WorkingDiff(unittest.TestCase):
    """`checkpoint --working`: what the running Builder has changed so far, display only (#1643)."""

    setUp = CodeCheckpoints.setUp
    git = CodeCheckpoints.git
    save = CodeCheckpoints.save

    def working(self):
        done = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), "checkpoint", "--workspace", str(self.workspace)]
            + ["--run-dir", str(self.run), "--working"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)

    def test_against_the_newest_saved_step(self):
        self.assertEqual([], self.working()["changed_files"])
        (self.workspace / "app.txt").write_text("builder is editing\n")
        shown = self.working()
        self.assertEqual(self.ident, shown["since"])
        self.assertEqual(["app.txt"], shown["changed_files"])
        self.assertIn("+builder is editing", shown["patch"])
        self.assertNotIn("expected_token", shown, "display only: nothing here can restore")

    def test_a_follow_up_with_a_revised_plan_still_shows_the_working_diff(self):
        self.state["goal_contract"] = dict(self.state["goal_contract"], hash="revised-plan-hash")
        self.save()
        (self.workspace / "app.txt").write_text("follow-up edit\n")
        shown = self.working()
        self.assertEqual(self.ident, shown["since"])
        self.assertEqual(["app.txt"], shown["changed_files"])
        self.assertIn("+follow-up edit", shown["patch"])

    def test_before_any_step_is_saved_it_is_against_the_checked_out_commit(self):
        self.state["code_checkpoints"] = []
        self.save()
        shown = self.working()
        self.assertEqual("start", shown["since"])
        self.assertIn("app.txt", shown["changed_files"])
        self.assertIn("-baseline", shown["patch"])
        self.assertIn("+checkpoint code", shown["patch"])

    def test_the_view_names_it_only_while_a_builder_step_runs(self):
        self.assertIsNone(checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["working"])
        self.state["active_stage"] = {"stage": "builder"}
        named = checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["working"]
        self.assertEqual(
            ["checkpoint", "--workspace", str(self.workspace), "--run-dir", str(self.run), "--working"],
            named["next_command"],
        )
        self.state["active_stage"] = {"stage": "tester"}
        self.assertIsNone(checkpoints.project(self.state, run_dir=self.run, workspace=self.workspace)["working"])


class IntegratedBatchCheckpoint(unittest.TestCase):
    """An integrated parallel batch is one saved step whose members keep their own milestone and files."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve() / "project"
        self.workspace.mkdir()
        checkpoints.git(self.workspace, "init", "-q")
        (self.workspace / "base.txt").write_text("base\n")
        checkpoints.git(self.workspace, "add", ".")
        checkpoints.git(self.workspace, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        self.run = self.workspace / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        (self.workspace / ".autocode/.gitignore").write_text("*\n")
        contract = {"task_id": "run-task", "revision": 1, "body": body()}
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract), "at": util.now()}
        contract.update(approval_status="approved", approval_event=approval, origin="draft_plan")
        (self.workspace / "server.py").write_text("server\n")
        (self.workspace / "client.py").write_text("client\n")
        revision = util.snapshot(self.workspace)["revision"]
        self.state = {
            "task_id": "run-task",
            "workspace": str(self.workspace),
            "iteration": 4,
            "user_events": [approval],
            "goal_contract": contract,
            "findings_ledger": [],
            "stages": [
                {
                    "stage": "builder",
                    "task_id": "task-b",
                    "batch_id": "batch1",
                    "exit_code": 0,
                    "source_revision": "w1",
                },
                {
                    "stage": "builder",
                    "task_id": "task-c",
                    "batch_id": "batch1",
                    "exit_code": 0,
                    "source_revision": "w2",
                },
                {"stage": "orchestrator", "batch_id": "batch1"},
            ],
            "current_task": {
                "id": "task-batch1",
                "milestone_id": "B",
                "milestone_ids": ["B", "C"],
                "objective": "Integrate and verify: B; C",
            },
            "implementation": {
                "task_id": "task-batch1",
                "source_revision": revision,
                "contract_hash": contract["hash"],
            },
            "orchestration_history": [
                {
                    "id": "batch1",
                    "status": "INTEGRATED",
                    "contract_hash": contract["hash"],
                    "finished_at": "2026-10-17T00:00:00+00:00",
                    "expected": {"revision": revision},
                    "changed_files": ["client.py", "server.py"],
                    "workers": [
                        {"milestone_id": "B", "status": "BUILT", "changed_files": ["server.py"]},
                        {"milestone_id": "C", "status": "BUILT", "changed_files": ["client.py"]},
                    ],
                }
            ],
        }

    def test_one_restorable_row_names_each_member_milestone_and_its_files(self):
        checkpoints.update(self.state, self.run)
        [row] = self.state["code_checkpoints"]
        self.assertTrue(row["available"], row)
        self.assertEqual(
            {
                "task_id": "task-batch1",
                "milestone_id": None,
                "label": "Integrate and verify: B; C",
                "at": "2026-10-17T00:00:00+00:00",
                "changed_files": ["client.py", "server.py"],
                "members": [
                    {"milestone_id": "B", "changed_files": ["server.py"]},
                    {"milestone_id": "C", "changed_files": ["client.py"]},
                ],
            },
            {key: row[key] for key in ("task_id", "milestone_id", "label", "at", "changed_files", "members")},
        )
        self.assertEqual("server\n", checkpoints.git(self.workspace, "show", row["commit"] + ":server.py").decode())
        self.assertEqual(row["members"], checkpoints.project(self.state)["rows"][0]["members"])
        checkpoints.update(self.state, self.run)
        self.assertEqual(1, len(self.state["code_checkpoints"]))

    def test_a_batch_that_is_not_the_integrated_implementation_records_nothing(self):
        cases = {
            "not integrated": lambda batch: batch.update(status="READY_TO_INTEGRATE"),
            "other revision": lambda batch: batch["expected"].update(revision="other"),
            "other contract": lambda batch: batch.update(contract_hash="other"),
            "other batch": lambda batch: batch.update(id="batch2"),
            "no built member": lambda batch: [row.update(status="SERIAL") for row in batch["workers"]],
        }
        for name, change in cases.items():
            with self.subTest(name):
                state = copy.deepcopy(self.state)
                change(state["orchestration_history"][0])
                checkpoints.update(state, self.run)
                self.assertEqual([], state.get("code_checkpoints", []))


class RealCheckpointFlow(unittest.TestCase):
    # An advance or resume can include mutation checks and independent reviews.
    timeout_seconds = 300

    def test_report_only_builder_repair_preserves_a_public_restorable_checkpoint(self):
        fixture = test_subprocess.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.env.update(AUTOCODE_FIXTURE_MODE="no-human", AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE="builder")
        client = TaskRun.start(
            fixture.project,
            "Build greeting",
            command=(sys.executable, str(TOOLS / "autocode.py")),
            options=("--engine", "codex"),
            start_options=("--workflow", "build"),
            env=fixture.env,
            timeout=self.timeout_seconds,
            cwd=fixture.root,
        )
        view = client.status()
        self.assertEqual("answer", view["needs"]["kind"], view)
        client.answer("Q1", "CLI")
        view = client.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        client.approve_plan(view["needs"]["token"])
        view = client.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        attempts = view["usage"]["accounting"]["attempts"]
        builder = next(row for row in attempts if row["stage"] == "builder")
        repair = next(row for row in attempts if row["stage"] == "builder_report_repair")
        self.assertTrue(builder["rejected"])
        self.assertFalse(repair["rejected"])
        self.assertTrue(repair["report_only"])
        self.assertEqual(builder["source_revision"], repair["source_revision"])
        rows = view["code_checkpoints"]["rows"]
        self.assertEqual(1, len(rows), rows)
        self.assertTrue(rows[0]["available"], rows[0])
        self.assertEqual(["greet.py", "test_greeting.py"], rows[0]["changed_files"])
        compared = client.compare_checkpoint(rows[0]["id"])
        self.assertEqual([], compared["changed_files"])
        self.assertTrue(compared["expected_token"])

    def test_actual_builder_checkpoint_restores_then_requires_fresh_validation(self):
        fixture = test_subprocess.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        fixture.launch(["Build greeting", "--chat"], 2, answers="CLI\nno\n")
        run, state = fixture.saved()
        client = TaskRun(
            fixture.project, run, command=tuple(fixture.entry), env=fixture.env, timeout=self.timeout_seconds
        )
        client.approve_plan(state["displayed_goal"])
        for _ in range(3):
            fixture.launch(["--run-dir", str(run), "--resume-paused", "--pause-after-stage", "--no-chat"], 2)
            view = client.status()
            if view["code_checkpoints"]["rows"]:
                break
        rows = view["code_checkpoints"]["rows"]
        self.assertTrue(rows, view)
        self.assertTrue(rows[-1]["available"], rows[-1])
        old_head = checkpoints.git(fixture.project, "rev-parse", "HEAD")
        (fixture.project / "later.txt").write_text("keep later work")
        compared = client.compare_checkpoint(rows[-1]["id"])
        restored = client.restore_checkpoint(rows[-1]["id"], compared["expected_token"], "real-flow-restore")
        self.assertEqual(old_head, checkpoints.git(fixture.project, "rev-parse", "HEAD"))
        self.assertEqual("keep later work", (fixture.project / "later.txt").read_text())
        self.assertFalse((restored.workspace / "later.txt").exists())
        self.assertEqual("PAUSED_REQUESTED", restored.status()["status"])
        done = restored.resume_paused()
        self.assertEqual("TASK_COMPLETE", done["status"], done)
        self.assertTrue(done["evidence"]["check_replay"], done)
        proof = done["evidence"]["check_replay"]
        self.assertEqual("PASS", proof["verdict"])
        self.assertNotEqual(rows[-1]["source_revision"], proof["source_revision"])


class BuilderExecutionProvenance(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        original_output = self.root / "original.json"
        original_output.write_text(json.dumps({"changed_files": ["model-declared-only.py"]}))
        events = self.root / "original.jsonl"
        events.write_text("{}\n")
        repaired_output = self.root / "repair.json"
        repaired_output.write_text(json.dumps({"changed_files": ["model-declared-only.py"]}))
        self.binding = {
            "task_id": "owned-task",
            "contract_hash": "approved-contract",
            "source_revision": "current-source",
        }
        common = {
            **self.binding,
            "role": "builder",
            "iteration": 1,
            "schema": "builder-schema",
            "criteria_revision": "approved-criteria",
            "exit_code": 0,
        }
        self.original = {
            **common,
            "stage": "builder",
            "output": str(original_output),
            "events": str(events),
            "rejected": True,
            "changed_files": ["runner-observed.py"],
        }
        self.accepted = {
            **common,
            "stage": "builder_report_repair",
            "original_stage": "builder",
            "report_only": True,
            "output": str(repaired_output),
            "events": "repair-events",
            "applied_original_events": str(events),
            "changed_files": [],
            "rejected": False,
        }
        self.receipt = {
            "result": "accepted",
            "repair": copy.deepcopy(self.accepted),
            "original_output": str(original_output),
            "output_hash": util.file_hash(repaired_output),
        }
        self.state = {"stages": [self.original, self.accepted], "report_repair_history": [self.receipt]}

    def test_accepted_repair_retains_only_runner_observed_paths_without_mutation(self):
        before = copy.deepcopy(self.state)
        execution = checkpoints.builder_execution(self.state, self.accepted, self.binding)
        self.assertEqual(["runner-observed.py"], execution["changed_files"])
        self.assertEqual([], self.accepted["changed_files"])
        self.assertEqual(before, self.state)

    def test_failed_repair_history_cannot_replace_the_accepted_original(self):
        failed = copy.deepcopy(self.receipt)
        failed.update(result="rejected", original_output="unrelated-output")
        self.state["report_repair_history"].insert(0, failed)
        unrelated = {
            **self.original,
            "output": "unrelated-output",
            "events": "unrelated-events",
            "changed_files": ["unrelated.py"],
        }
        self.state["stages"].append(unrelated)
        self.assertIs(self.original, checkpoints.builder_execution(self.state, self.accepted, self.binding))

    def test_missing_duplicate_and_unlinked_provenance_is_rejected(self):
        cases = (
            "missing-receipt",
            "duplicate-receipt",
            "missing-original",
            "duplicate-original",
            "wrong-events",
            "wrong-original-output",
        )
        for case in cases:
            with self.subTest(case=case):
                state = copy.deepcopy(self.state)
                accepted = state["stages"][1]
                if case == "missing-receipt":
                    state["report_repair_history"] = []
                elif case == "duplicate-receipt":
                    state["report_repair_history"].append(copy.deepcopy(state["report_repair_history"][0]))
                elif case == "missing-original":
                    state["stages"].pop(0)
                elif case == "duplicate-original":
                    state["stages"].append(copy.deepcopy(state["stages"][0]))
                elif case == "wrong-events":
                    accepted["applied_original_events"] = "another-original-events"
                    state["report_repair_history"][0]["repair"] = copy.deepcopy(accepted)
                else:
                    state["report_repair_history"][0]["original_output"] = "another-original-output"
                with self.assertRaises(ValueError):
                    checkpoints.builder_execution(state, accepted, self.binding)

    def test_original_task_contract_source_and_stage_bindings_must_match(self):
        fields = (
            "task_id",
            "contract_hash",
            "source_revision",
            "role",
            "iteration",
            "schema",
            "criteria_revision",
            "stage",
            "exit_code",
            "report_only",
            "abandoned",
            "timed_out",
        )
        for field in fields:
            with self.subTest(field=field):
                state = copy.deepcopy(self.state)
                state["stages"][0][field] = True if field in ("report_only", "abandoned", "timed_out") else "other"
                with self.assertRaisesRegex(ValueError, "mismatched"):
                    checkpoints.builder_execution(state, state["stages"][1], self.binding)
        for field in self.binding:
            with self.subTest(implementation=field):
                binding = {**self.binding, field: "different-current-binding"}
                with self.assertRaisesRegex(ValueError, "mismatched"):
                    checkpoints.builder_execution(self.state, self.accepted, binding)

    def test_repair_source_changes_and_rejected_or_misrouted_repairs_are_rejected(self):
        changes = (
            {"changed_files": ["repair-wrote.py"]},
            {"rejected": True},
            {"abandoned": True},
            {"timed_out": True},
            {"exit_code": True},
            {"original_stage": "tester"},
            {"stage": "builder"},
            {"report_only": False},
            {"applied_original_events": ""},
        )
        for change in changes:
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                accepted = state["stages"][1]
                accepted.update(change)
                state["report_repair_history"][0]["repair"] = copy.deepcopy(accepted)
                with self.assertRaisesRegex(ValueError, "provenance"):
                    checkpoints.builder_execution(state, accepted, self.binding)

    def test_changed_repaired_output_and_missing_original_artifacts_are_rejected(self):
        output = Path(self.accepted["output"])
        saved = output.read_bytes()
        output.write_bytes(saved + b" ")
        with self.assertRaisesRegex(ValueError, "hash"):
            checkpoints.builder_execution(self.state, self.accepted, self.binding)
        output.write_bytes(saved)
        for field in ("output", "events"):
            with self.subTest(field=field):
                artifact = Path(self.original[field])
                artifact.unlink()
                with self.assertRaisesRegex(ValueError, "mismatched"):
                    checkpoints.builder_execution(self.state, self.accepted, self.binding)
                artifact.write_text("{}\n")

    def test_saved_provider_response_can_witness_an_original_without_a_json_output(self):
        Path(self.original["output"]).unlink()
        response = self.root / "original.response.txt"
        response.write_text("Completed provider response rejected before JSON output was saved.\n")
        self.original.update(engine="opencode", response_text=str(response))
        before = copy.deepcopy(self.state)
        self.assertEqual(
            ["runner-observed.py"],
            checkpoints.builder_execution(self.state, self.accepted, self.binding)["changed_files"],
        )
        self.assertEqual(before, self.state)
        self.original.pop("response_text")
        self.assertEqual(
            ["runner-observed.py"],
            checkpoints.builder_execution(self.state, self.accepted, self.binding)["changed_files"],
        )
        self.original["engine"] = "codex"
        with self.assertRaisesRegex(ValueError, "mismatched"):
            checkpoints.builder_execution(self.state, self.accepted, self.binding)
        self.original["engine"] = "opencode"
        response.unlink()
        with self.assertRaisesRegex(ValueError, "mismatched"):
            checkpoints.builder_execution(self.state, self.accepted, self.binding)

    def test_malformed_original_paths_and_binding_types_are_rejected(self):
        changes = (
            {"output": 1},
            {"events": 1},
            {"changed_files": "model-declared.py"},
            {"changed_files": [""]},
            {"changed_files": [1]},
            {"schema": ""},
            {"iteration": True},
        )
        for change in changes:
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                state["stages"][0].update(change)
                with self.assertRaises(ValueError):
                    checkpoints.builder_execution(state, state["stages"][1], self.binding)
        for value in (None, 1, ""):
            with self.subTest(events=value):
                state = copy.deepcopy(self.state)
                accepted = state["stages"][1]
                accepted["applied_original_events"] = value
                state["report_repair_history"][0]["repair"] = copy.deepcopy(accepted)
                with self.assertRaises(ValueError):
                    checkpoints.builder_execution(state, accepted, self.binding)


class TreeFiles(unittest.TestCase):
    def repository(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        checkpoints.git(root, "init", "-q")
        for name, data in files.items():
            (root / name).write_bytes(data)
        checkpoints.git(root, "add", ".")
        checkpoints.git(root, "-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        return root

    def test_one_batch_preserves_binary_empty_repeated_blobs_modes_links_and_literal_names(self):
        files = {f"repeated-{n}.txt": b"repeated\n" for n in range(128)}
        files.update({"binary\nfile\t.bin": bytes(range(256)) * 512, "--empty": b"", "script": b"#!/bin/sh\nexit 0\n"})
        root = self.repository(files)
        (root / "script").chmod(0o755)
        (root / "link").symlink_to("binary\nfile\t.bin")
        checkpoints.git(root, "add", ".")
        tree = checkpoints.git(root, "write-tree").decode().strip()
        before = (checkpoints.git(root, "rev-parse", "HEAD"), tree)
        expected = {
            name: ("executable:" if name == "script" else "") + hashlib.sha256(data).hexdigest()
            for name, data in files.items()
        }
        expected["link"] = "symlink:binary\nfile\t.bin"
        with patch.object(checkpoints.subprocess, "Popen", wraps=subprocess.Popen) as launches:
            actual = checkpoints.tree_files(root, tree)
        self.assertEqual(expected, actual)
        commands = [call.args[0] for call in launches.call_args_list]
        self.assertEqual(2, len(commands), commands)
        self.assertEqual(["cat-file", "--batch"], commands[1][-2:])
        self.assertIn("core.hooksPath=/dev/null", commands[1])
        self.assertIn("core.fsmonitor=false", commands[1])
        self.assertEqual(
            before, (checkpoints.git(root, "rev-parse", "HEAD"), checkpoints.git(root, "write-tree").decode().strip())
        )
        self.assertEqual(files["binary\nfile\t.bin"], (root / "binary\nfile\t.bin").read_bytes())

    def test_submodule_is_rejected_before_a_batch_is_started(self):
        root = self.repository({"file": b"base\n"})
        commit = checkpoints.git(root, "rev-parse", "HEAD").strip()
        tree = checkpoints.git(root, "mktree", data=b"160000 commit " + commit + b"\tnested\n").decode().strip()
        with patch.object(checkpoints.subprocess, "Popen", wraps=subprocess.Popen) as launches:
            with self.assertRaisesRegex(ValueError, "nested Git repositories"):
                checkpoints.tree_files(root, tree)
        self.assertEqual(1, launches.call_count)

    def test_malformed_reply_kills_and_reaps_the_owned_batch(self):
        oid = "a" * 40
        proc = MagicMock()
        proc.stdin = io.BytesIO()
        proc.stdout = io.BytesIO(b"not a blob header\n")
        proc.poll.return_value = None
        proc.wait.return_value = -9
        with (
            patch.object(checkpoints, "git", return_value=f"100644 blob {oid}\tfile\0".encode()),
            patch.object(checkpoints.subprocess, "Popen", return_value=proc),
        ):
            with self.assertRaisesRegex(ValueError, "invalid blob header"):
                checkpoints.tree_files("/unused-fixture", "tree")
        proc.kill.assert_called_once_with()
        proc.wait.assert_called_once_with()
        self.assertTrue(proc.stdin.closed)
        self.assertTrue(proc.stdout.closed)

    def test_native_batch_failure_cannot_return_a_tree_identity(self):
        oid = "a" * 40
        proc = MagicMock()
        proc.stdin = io.BytesIO()
        proc.stdout = io.BytesIO(f"{oid} blob 0\n\n".encode())
        proc.poll.return_value = 1
        proc.wait.return_value = 1

        def start(*args, stderr, **kwargs):
            stderr.write(b"fixture Git batch failure\n")
            return proc

        with (
            patch.object(checkpoints, "git", return_value=f"100644 blob {oid}\tfile\0".encode()),
            patch.object(checkpoints.subprocess, "Popen", side_effect=start),
        ):
            with self.assertRaisesRegex(ValueError, "fixture Git batch failure"):
                checkpoints.tree_files("/unused-fixture", "tree")
        proc.kill.assert_not_called()
        self.assertTrue(proc.stdin.closed)
        self.assertTrue(proc.stdout.closed)

    def test_unrequested_trailing_output_is_rejected(self):
        oid = "a" * 40
        proc = MagicMock()
        proc.stdin = io.BytesIO()
        proc.stdout = io.BytesIO(f"{oid} blob 0\n\nextra".encode())
        proc.poll.return_value = None
        proc.wait.return_value = -9
        with (
            patch.object(checkpoints, "git", return_value=f"100644 blob {oid}\tfile\0".encode()),
            patch.object(checkpoints.subprocess, "Popen", return_value=proc),
        ):
            with self.assertRaisesRegex(ValueError, "trailing output"):
                checkpoints.tree_files("/unused-fixture", "tree")
        proc.kill.assert_called_once_with()
        self.assertTrue(proc.stdin.closed)
        self.assertTrue(proc.stdout.closed)

    def test_empty_tree_does_not_launch_a_batch(self):
        with (
            patch.object(checkpoints, "git", return_value=b""),
            patch.object(checkpoints.subprocess, "Popen") as launch,
        ):
            self.assertEqual({}, checkpoints.tree_files("/unused-fixture", "tree"))
        launch.assert_not_called()


class BatchBlobReplies(unittest.TestCase):
    def test_empty_blob_and_unicode_symlink_with_sha256_object_ids(self):
        oid = "b" * 64
        self.assertEqual(
            hashlib.sha256(b"").hexdigest(),
            checkpoints._read_blob_identity(io.BytesIO(f"{oid} blob 0\n\n".encode()), oid, "100644"),
        )
        target = "dir/δ\nfile"
        data = target.encode()
        reply = f"{oid} blob {len(data)}\n".encode() + data + b"\n"
        self.assertEqual("symlink:" + target, checkpoints._read_blob_identity(io.BytesIO(reply), oid, "120000"))

    def test_regular_blob_reads_are_bounded_and_use_exact_binary_size(self):
        oid = "b" * 40
        data = bytes(range(256)) * 1024 + b"\nlooks like a header\n"

        class BoundedStream(io.BytesIO):
            def read(self, size=-1):
                if not 0 < size <= 64 * 1024:
                    raise AssertionError(f"Unbounded blob read: {size}")
                return super().read(size)

        stream = BoundedStream(f"{oid} blob {len(data)}\n".encode() + data + b"\n")
        self.assertEqual(
            "executable:" + hashlib.sha256(data).hexdigest(), checkpoints._read_blob_identity(stream, oid, "100755")
        )

    def test_malformed_missing_mismatched_and_truncated_replies_are_rejected(self):
        oid = "b" * 40
        replies = [
            b"",
            b"garbage\n",
            f"{oid} missing\n".encode(),
            f"{'a' * 40} blob 0\n\n".encode(),
            f"{oid} tree 0\n\n".encode(),
            f"{oid} blob -1\n".encode(),
            f"{oid} blob size\n".encode(),
            f"{oid} blob 3\nxy".encode(),
            f"{oid} blob 1\nx!".encode(),
            f"{oid} blob 0".encode(),
            b"a" * 257 + b"\n",
        ]
        for reply in replies:
            with self.subTest(reply=reply):
                with self.assertRaises(ValueError):
                    checkpoints._read_blob_identity(io.BytesIO(reply), oid, "100644")
