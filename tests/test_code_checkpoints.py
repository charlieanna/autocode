"""Public checkpoint CLI tests with real Git and owned temporary run fixtures."""

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import autocode_checkpoint_cli as operation
import autocode_code_checkpoints as checkpoints
import autocode_contract_identity as identity
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
            "next_stage": "sol",
            "sessions": {},
            "history": [],
            "stages": [],
            "active_seconds": 12,
            "settings": {
                "engine": "codex",
                "roles": {"terra": {"model": "gpt-6-sol", "reasoning_effort": "high", "pinned": True}},
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
        contract.update(approval_status="approved", approval_event=approval, origin="astra_discovery")
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
                "stage": "terra",
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
            ("active_stage", {"stage": "terra"}),
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


class RealCheckpointFlow(unittest.TestCase):
    def test_report_only_builder_repair_preserves_a_public_restorable_checkpoint(self):
        fixture = test_subprocess.SubprocessFlow()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.env.update(AUTOCODE_FIXTURE_MODE="no-human", AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE="terra")
        client = TaskRun.start(
            fixture.project,
            "Build greeting",
            command=(sys.executable, str(TOOLS / "autocode.py")),
            options=("--engine", "codex"),
            start_options=("--workflow", "build"),
            env=fixture.env,
            timeout=60,
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
        builder = next(row for row in attempts if row["stage"] == "terra")
        repair = next(row for row in attempts if row["stage"] == "terra_report_repair")
        self.assertTrue(builder["rejected"])
        self.assertFalse(repair["rejected"])
        self.assertTrue(repair["report_only"])
        self.assertEqual(builder["source_revision"], repair["source_revision"])
        rows = view["code_checkpoints"]["rows"]
        self.assertEqual(1, len(rows), rows)
        self.assertTrue(rows[0]["available"], rows[0])
        self.assertEqual(["greet.py"], rows[0]["changed_files"])
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
        client = TaskRun(fixture.project, run, command=tuple(fixture.entry), env=fixture.env, timeout=60)
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
            "role": "terra",
            "iteration": 1,
            "schema": "builder-schema",
            "criteria_revision": "approved-criteria",
            "exit_code": 0,
        }
        self.original = {
            **common,
            "stage": "terra",
            "output": str(original_output),
            "events": str(events),
            "rejected": True,
            "changed_files": ["runner-observed.py"],
        }
        self.accepted = {
            **common,
            "stage": "terra_report_repair",
            "original_stage": "terra",
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
            {"original_stage": "sol"},
            {"stage": "terra"},
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
