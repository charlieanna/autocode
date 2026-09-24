"""Parallel orchestration through real subprocesses/worktrees and offline models."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from . import test_goals
from . import test_subprocess
import autocode as runner
import autocode_dispatch as d
import autocode_goals as g
import autocode_milestones as m
import autocode_support as s
from goal_fixtures import body, envelope


class DispatchTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp

    def prepare(self, *, paths=None, human=False):
        draft = body(human=human)
        draft["acceptance_criteria"] = [
            {"id": f"C{i}", "criterion": f"Output {i} works", "verification_method": f"Read output {i}", "human_review": human}
            for i in (1, 2, 3)]
        draft["milestones"] = [
            {"id": "M1", "objective": "First output", "depends_on": [], "acceptance_criteria": ["C1"],
             "affected_paths": (paths or {}).get("M1", ["a.txt"])},
            {"id": "M2", "objective": "Second output", "depends_on": [], "acceptance_criteria": ["C2"],
             "affected_paths": (paths or {}).get("M2", ["b.txt"])},
            {"id": "M3", "objective": "Combine outputs", "depends_on": ["M1", "M2"], "acceptance_criteria": ["C3"],
             "affected_paths": ["combined.txt"]}]
        g.install_draft(self.state, draft, origin="test")
        g.present(self.state)
        g.approve(self.state, self.state["displayed_goal"])
        self.state["settings"].update(orchestration=copy.deepcopy(d.DEFAULTS),
                                      milestone_checkpoints=copy.deepcopy(m.DEFAULTS), engine="codex", report_repair={"max_attempts": 2})
        decision = {"status": "CONTINUE", "next_objective": "First output", "affected_paths": draft["milestones"][0]["affected_paths"],
                    "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["Output 1 works"],
                                  "acceptance_criteria": ["C1"], "validation_plan": ["Read output 1"]}}
        g.assign_task(self.state, decision, s.snapshot(self.root))
        self.state["next_stage"] = "orchestrator"
        self.state["affected_paths"] = decision["affected_paths"]
        fixture_bin = self.root / ".autocode/fixture-bin"
        fixture_bin.mkdir()
        shutil.copy2(Path(__file__).with_name("fake_parallel_builder.py"), fixture_bin / "codex")
        (fixture_bin / "codex").chmod(0o755)
        self.environment = patch.dict(os.environ, {"PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
                                                   "AUTOCODE_BUILDER_BARRIER": str(self.root / ".autocode/barrier")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        return draft

    def build(self):
        return d.dispatch(self.state, self.root, self.run)

    def validate(self, *, missing_member=False, flow_status="PASS"):
        evidence = self.run / "checks.jsonl"
        evidence.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": "read-both-outputs", "exit_code": 0, "aggregated_output": "M1 M2"}}))
        value = {**envelope(self.state), "verdict": "PASS", "findings": [], "unverified_criteria": ["C3"],
                 "checks_run": ["read-both-outputs"], "checks": [{"command": "read-both-outputs", "exit_code": 0, "evidence_ref": "event:check"}],
                 "criterion_results": [{"id": cid, "status": "PASS", "evidence_refs": ["event:check"]} for cid in ("C1", "C2")],
                 "end_to_end_result": {"status": flow_status, "summary": "Dependent combined output remains to be built" if flow_status == "NOT_VERIFIED" else "Both outputs work", "evidence_refs": ["event:check"]},
                 "milestone_results": [{"milestone_id": mid, "status": "PASS", "summary": "Executed output", "evidence_refs": ["event:check"]}
                                       for mid in (["M1"] if missing_member else ["M1", "M2"])]}
        runner.apply_result(self.state, "sol", value, {"role": "sol", "events": str(evidence), "output": str(evidence),
                                                     "source_revision": s.snapshot(self.root)["revision"]}, self.root, self.run)

    def advance(self, mid="M3"):
        cid = {"M1": "C1", "M2": "C2", "M3": "C3"}[mid]
        return g.assign_task(self.state, {"status": "CONTINUE", "next_objective": "Finish " + mid,
              "affected_paths": ["combined.txt"], "next_task": {"kind": "implement", "milestone_id": mid,
              "requirements": ["Keep outputs working"], "acceptance_criteria": [cid], "validation_plan": ["Read output"]}}, s.snapshot(self.root))

    def test_ready_selection_respects_dependency_ownership_and_limits(self):
        self.prepare()
        self.assertEqual(["M1", "M2"], [r["id"] for r in d.select(self.state)])
        for paths in (["a.txt"], ["../escape"], ["."] , ["*.txt"], [], [".git/config"]):
            with self.subTest(paths=paths):
                candidate = copy.deepcopy(self.state)
                candidate["goal_contract"]["body"]["milestones"][1]["affected_paths"] = paths
                # Validate selection mechanics separately from the approval digest guard.
                with patch.object(g, "execution_guard"):
                    self.assertEqual([], d.select(candidate))
        self.state["settings"]["orchestration"]["max_parallel"] = 1
        self.assertEqual([], d.select(self.state))
        self.assertFalse(d.disjoint(["src"], ["src/module.py"]))
        self.assertTrue(d.disjoint(["src/api"], ["src/api2"]))

    def test_unapproved_or_stale_contract_cannot_launch(self):
        self.prepare()
        self.state["goal_contract"]["body"]["constraints"].append("Changed")
        with self.assertRaises(s.Paused):
            self.build()
        self.assertFalse((self.run / "orchestration").exists())

    def test_real_parallel_builders_integrate_then_require_combined_review(self):
        self.prepare()
        head = d.git(self.root, "rev-parse", "HEAD")
        self.build()
        self.assertEqual("M1\n", (self.root / "a.txt").read_text())
        self.assertEqual("M2\n", (self.root / "b.txt").read_text())
        self.assertEqual(head, d.git(self.root, "rev-parse", "HEAD"))
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual([], sorted(m.accepted_ids(self.state)))
        batch = self.state["orchestration_history"][0]
        children = [s.read(Path(w["run_dir"]) / "state.json") for w in batch["workers"]]
        self.assertNotEqual(children[0]["sessions"]["terra"], children[1]["sessions"]["terra"])
        self.assertNotEqual(children[0]["workspace"], children[1]["workspace"])
        self.assertNotEqual(children[0]["current_task"]["id"], children[1]["current_task"]["id"])
        self.assertTrue(all(w["exit_code"] == 0 for w in batch["workers"]))
        with self.assertRaises(s.Paused):
            self.advance()
        with self.assertRaisesRegex(ValueError, "every batch milestone"):
            self.validate(missing_member=True)
        self.validate(flow_status="NOT_VERIFIED")
        self.assertEqual(set(), m.accepted_ids(self.state))
        for failed_part in ("flow", "member", "criterion", "stale"):
            candidate = copy.deepcopy(self.state)
            validation = candidate["validation"]
            if failed_part == "flow": validation["end_to_end_result"]["status"] = "FAIL"
            if failed_part == "member": validation["milestone_results"][1]["status"] = "NOT_VERIFIED"
            if failed_part == "criterion": validation["criterion_results"][1]["status"] = "NOT_VERIFIED"
            if failed_part == "stale": validation["source_revision"] = "stale"
            self.assertFalse(m.evidence_ready(candidate, s.snapshot(self.root)), failed_part)
        self.advance()
        self.assertEqual({"M1", "M2"}, m.accepted_ids(self.state))
        self.assertEqual("M3", self.state["current_task"]["milestone_id"])

    def test_snapshot_includes_dirty_and_new_files_without_touching_index(self):
        self.prepare(paths={"M1": ["a.txt", "raw.bin", "tool.sh", "new.link", "old.remove"], "M2": ["b.txt"]})
        (self.root / "staged.txt").write_text("staged\n")
        d.git(self.root, "add", "staged.txt")
        (self.root / "staged.txt").write_text("unstaged\n")
        (self.root / "old.remove").write_text("untracked baseline\n")
        index_before = d.git(self.root, "ls-files", "--stage")
        self.build()
        self.assertEqual(index_before, d.git(self.root, "ls-files", "--stage"))
        self.assertEqual("unstaged\n", (self.root / "staged.txt").read_text())
        self.assertEqual(b"\x00\xffbinary\x00", (self.root / "raw.bin").read_bytes())
        self.assertTrue((self.root / "tool.sh").stat().st_mode & 0o111)
        self.assertEqual("a.txt", os.readlink(self.root / "new.link"))
        self.assertFalse((self.root / "old.remove").exists())

    def test_failed_worker_retains_success_without_relaunch_or_integration(self):
        self.prepare()
        with patch.dict(os.environ, {"AUTOCODE_BUILDER_FAIL": "M2"}):
            with self.assertRaisesRegex(s.Paused, "Builder M2"):
                self.build()
        self.assertFalse((self.root / "a.txt").exists())
        before = copy.deepcopy(self.state["stages"])
        with patch.object(d, "run_workers"):
            with self.assertRaisesRegex(s.Paused, "Builder M2"):
                self.build()
        self.assertEqual(before, self.state["stages"])
        self.assertGreater(self.state["active_seconds"], 0)

    def test_scope_violation_blocks_integration(self):
        self.prepare()
        with patch.dict(os.environ, {"AUTOCODE_BUILDER_ESCAPE": "M2"}):
            with self.assertRaisesRegex(s.Paused, "exceed declared"):
                self.build()
        self.assertFalse((self.root / "outside.txt").exists())
        self.assertFalse((self.root / "a.txt").exists())

    def test_complete_patch_ownership_includes_changes_before_builder(self):
        self.prepare()
        batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
        (Path(batch["workers"][0]["workspace"]) / "outside.txt").write_text("pre-provider change")
        d.run_workers(self.state, self.run, batch)
        with self.assertRaisesRegex(s.Paused, "exceed declared"):
            d.collect(self.state, self.root, self.run, batch)
        self.assertFalse((self.root / "outside.txt").exists())

    def test_explicit_retry_only_restarts_failed_member_and_counts_once(self):
        self.prepare()
        with patch.dict(os.environ, {"AUTOCODE_BUILDER_FAIL": "M2"}):
            with self.assertRaises(s.Paused):
                self.build()
        first = (self.root / ".autocode/barrier/M1").read_bytes()
        d.request_retry(self.state, self.run, ["M2"])
        self.build()
        self.assertEqual(first, (self.root / ".autocode/barrier/M1").read_bytes())
        self.assertEqual("sol", self.state["next_stage"])
        attempts = [r for r in self.state["stages"] if r.get("worker_attempt")]
        self.assertEqual(3, len(attempts))
        self.assertAlmostEqual(sum(r["duration_seconds"] for r in attempts), self.state["active_seconds"])
        self.assertTrue(all(Path(r["events"]).exists() for r in attempts))

    def test_report_repair_never_replays_builder(self):
        self.prepare()
        with patch.dict(os.environ, {"AUTOCODE_BUILDER_BAD_REPORT": "M2"}):
            self.build()
        self.assertEqual("sol", self.state["next_stage"])
        attempts = [r for r in self.state["stages"] if r.get("worker_milestone") == "M2"]
        self.assertEqual(["terra", "terra_report_repair"], [r["stage"] for r in attempts])

    def test_completed_worker_missing_receipt_is_reconciled_without_rebuild(self):
        self.prepare()
        batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
        d.run_workers(self.state, self.run, batch)
        first = (self.root / ".autocode/barrier/M1").read_bytes()
        (Path(batch["workers"][0]["run_dir"]) / "result.json").unlink()
        with patch.dict(os.environ, {"AUTOCODE_BUILDER_FAIL": "M1"}):
            self.build()
        self.assertEqual(first, (self.root / ".autocode/barrier/M1").read_bytes())
        self.assertEqual("sol", self.state["next_stage"])

    def test_new_approved_plan_archives_old_stopped_batch(self):
        draft = self.prepare()
        old = d.prepare(self.state, self.root, self.run, d.select(self.state))
        d.run_workers(self.state, self.run, old)
        draft["constraints"].append("Updated requirement")
        g.install_draft(self.state, draft, origin="test")
        g.present(self.state)
        g.approve(self.state, self.state["displayed_goal"])
        self.advance("M1")
        self.state["current_task"]["affected_paths"] = ["a.txt"]
        self.state["next_stage"] = "orchestrator"
        self.build()
        self.assertEqual(["SUPERSEDED", "INTEGRATED"], [b["status"] for b in self.state["orchestration_history"]])
        self.assertEqual("sol", self.state["next_stage"])

    def test_interrupted_worktree_setup_resumes_same_batch(self):
        self.prepare()
        original = d.git
        def interrupt(workspace, *args, **kwargs):
            result = original(workspace, *args, **kwargs)
            if args[:2] == ("worktree", "add"):
                raise s.Paused("PAUSED_INTERRUPTED", "After worktree creation")
            return result
        with patch.object(d, "git", side_effect=interrupt):
            with self.assertRaisesRegex(s.Paused, "After worktree"):
                self.build()
        self.state = s.read(self.run / "state.json")
        batch = self.state["orchestration_batch"]
        self.assertEqual(2, len(batch["workers"]))
        self.assertEqual("PREPARING", batch["status"])
        first_workspace = batch["workers"][0]["workspace"]
        self.build()
        self.assertEqual(batch["id"], self.state["orchestration_history"][0]["id"])
        self.assertEqual(first_workspace, self.state["orchestration_history"][0]["workers"][0]["workspace"])
        self.assertEqual(3, d.git(self.root, "worktree", "list", "--porcelain").count(b"worktree "))

    def test_unrelated_builder_does_not_block_but_same_builder_does(self):
        self.prepare()
        batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
        row = batch["workers"][0]
        def ps(command):
            return subprocess.CompletedProcess([], 0, stdout=f"999999 python autocode_builder_worker.py {command}\n")
        with patch.object(s.subprocess, "run", return_value=ps("/unrelated/.autocode/runs/builder-other-1")):
            s.assert_no_legacy_process(Path(row["run_dir"]), Path(row["workspace"]))
        for command in (row["run_dir"], os.path.relpath(row["run_dir"], row["workspace"])):
            with patch.object(s.subprocess, "run", return_value=ps(command)):
                with self.assertRaisesRegex(s.Paused, "Existing run process"):
                    s.assert_no_legacy_process(Path(row["run_dir"]), Path(row["workspace"]))

    def test_incomplete_checkout_cannot_launch_or_integrate_deletions(self):
        self.prepare()
        (self.root / "keep.txt").write_text("baseline\n")
        original = d.git
        def interrupt(workspace, *args, **kwargs):
            if args[:2] == ("worktree", "add"):
                original(workspace, "worktree", "add", "--no-checkout", *args[2:], **kwargs)
                raise s.Paused("PAUSED_INTERRUPTED", "Before checkout")
            return original(workspace, *args, **kwargs)
        with patch.object(d, "git", side_effect=interrupt):
            with self.assertRaisesRegex(s.Paused, "Before checkout"):
                self.build()
        self.state = s.read(self.run / "state.json")
        with patch.object(d, "run_workers", side_effect=AssertionError("Must not launch")):
            with self.assertRaisesRegex(s.Paused, "checkout is incomplete"):
                self.build()
        self.assertEqual("PREPARING", self.state["orchestration_batch"]["workers"][0]["status"])
        self.assertEqual("baseline\n", (self.root / "keep.txt").read_text())

    def test_resume_after_patch_applied_does_not_reapply_or_rerun(self):
        self.prepare()
        original = d.git
        def interrupted(workspace, *args, **kwargs):
            result = original(workspace, *args, **kwargs)
            if args[:2] == ("apply", "--binary"):
                raise s.Paused("PAUSED_INTERRUPTED", "Injected after patch apply")
            return result
        with patch.object(d, "git", side_effect=interrupted):
            with self.assertRaisesRegex(s.Paused, "Injected"):
                self.build()
        self.assertEqual("INTEGRATING", self.state["orchestration_batch"]["status"])
        self.state = s.read(self.run / "state.json")
        with patch.object(d, "run_workers", side_effect=AssertionError("Must not replay")):
            self.build()
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual(2, sum(r.get("role") == "terra" for r in self.state["stages"]))

    def test_parent_drift_and_pending_pause_block_integration(self):
        self.prepare()
        batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
        d.run_workers(self.state, self.run, batch)
        d.collect(self.state, self.root, self.run, batch)
        (self.run / "pause-requested").write_text("pause")
        with self.assertRaisesRegex(s.Paused, "Pause requested"):
            d.integrate(self.state, self.root, self.run, batch)
        (self.run / "pause-requested").unlink()
        (self.root / "manual.txt").write_text("user edit")
        with self.assertRaisesRegex(s.Paused, "workspace changed"):
            d.integrate(self.state, self.root, self.run, batch)
        self.assertFalse((self.root / "a.txt").exists())

    def test_rework_preserves_batch_scope_and_budget(self):
        self.prepare()
        self.build()
        key = m.key(self.state)
        seconds = m.progress(self.state)["seconds"]
        self.advance("M2")
        self.assertEqual(key, m.key(self.state))
        self.assertEqual(["C1", "C2"], m.scope(self.state)["acceptance_criteria"])
        self.assertEqual(seconds, m.progress(self.state)["seconds"])
        self.assertEqual([], d.select(self.state))

    def test_human_review_not_bypassed_by_combined_pass(self):
        self.prepare(human=True)
        self.build()
        self.validate()
        with self.assertRaises(s.Paused) as error:
            self.advance()
        self.assertEqual("PAUSED_MILESTONE_HUMAN_REVIEW", error.exception.status)
        self.assertEqual(set(), m.accepted_ids(self.state))


class DispatchCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def fixture(self):
        shutil.copy2(Path(__file__).with_name("fake_parallel_builder.py"), self.root / "fixture-bin/codex")
        (self.root / "fixture-bin/codex").chmod(0o755)
        self.env["AUTOCODE_BUILDER_BARRIER"] = str(self.root / "barrier")

    def test_full_cli_parallel_wave_then_dependency_then_completion(self):
        self.fixture()
        self.launch(["Produce two outputs and combine", "--max-parallel-builders", "2", "--chat"], 0, answers="yes\n")
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual({"M1", "M2", "M3"}, m.accepted_ids(state))
        self.assertEqual(["M1", "M2"], [w["milestone_id"] for w in state["orchestration_history"][0]["workers"]])
        stages = [r["stage"] for r in state["stages"]]
        self.assertEqual(["astra_discovery", "astra_plan", "terra", "terra", "orchestrator", "sol", "astra_review",
                          "orchestrator", "terra", "sol", "astra_review"], stages)
        self.assertEqual("M3\n", (self.project / "combined.txt").read_text())
        unchanged = (run / "state.json").read_bytes()
        self.launch(["--run-dir", str(run)], 0)
        self.assertEqual(unchanged, (run / "state.json").read_bytes())

    def test_cli_retry_preserves_successful_builder(self):
        self.fixture()
        self.env["AUTOCODE_BUILDER_FAIL"] = "M2"
        self.launch(["Produce two outputs and combine", "--max-parallel-builders", "2", "--chat"], 2, answers="yes\n")
        run, state = self.saved()
        self.assertEqual("PAUSED_ORCHESTRATOR_WORKER", state["status"])
        first = (self.root / "barrier/M1").read_bytes()
        self.env.pop("AUTOCODE_BUILDER_FAIL")
        self.launch(["--run-dir", str(run), "--resume-paused", "--retry-builder", "M2", "--no-chat"], 0)
        self.assertEqual("TASK_COMPLETE", self.saved()[1]["status"])
        self.assertEqual(first, (self.root / "barrier/M1").read_bytes())
