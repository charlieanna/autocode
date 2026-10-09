"""Parallel orchestration through real subprocesses/worktrees and offline models."""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_dispatch as d
import autocode_goal_lifecycle as lifecycle
import autocode_goals as g
import autocode_milestones as m
import autocode_support as s
from goal_fixtures import assert_operational_wait, body, envelope

from . import test_goals, test_subprocess


class TaskForTests(unittest.TestCase):
    def test_primary_handoff_is_copied_and_rebound_without_leaking_to_siblings(self):
        rows = [{"id": "M1", "objective": "First output", "affected_paths": ["first/"],
                 "acceptance_criteria": ["C1"]},
                {"id": "M2", "objective": "Second output", "affected_paths": ["second/"],
                 "acceptance_criteria": ["C2"]}]
        contract = {"revision": 2, "hash": "approved-contract", "body": {
            "milestones": rows, "acceptance_criteria": [
                {"id": f"C{i}", "criterion": f"Output {i} works", "verification_method": f"Read output {i}"}
                for i in (1, 2)]}}
        primary = {"id": "reviewed-task", "kind": "implement", "milestone_id": "M1",
                   "objective": "First output with an integrity checksum", "affected_paths": ["first/output.txt"],
                   "requirements": ["Output 1 works", "Append the SHA-256 digest of the milestone ID."],
                   "validation_plan": ["Read output 1", "Verify the output checksum."],
                   "acceptance_criteria": ["C1"], "findings": [], "decision": "CONTINUE",
                   "contract_revision": 2, "contract_hash": "approved-contract",
                   "source_revision": "reviewed-source", "assigned_at": "review-time"}
        state = {"goal_contract": contract, "current_task": primary}
        before = copy.deepcopy(state)
        with patch.object(s, "now", return_value="dispatch-time"):
            task = d.task_for(state, rows[0], {"revision": "dispatch-source"})
            sibling = d.task_for(state, rows[1], {"revision": "dispatch-source"})
        self.assertEqual({**primary, "id": task["id"], "source_revision": "dispatch-source",
                          "assigned_at": "dispatch-time"}, task)
        self.assertEqual({"id": sibling["id"], "kind": "implement", "milestone_id": "M2",
                          "objective": "Second output", "affected_paths": ["second/"],
                          "requirements": ["Output 2 works"], "validation_plan": ["Read output 2"],
                          "acceptance_criteria": ["C2"], "decision": "CONTINUE",
                          "contract_revision": 2, "contract_hash": "approved-contract",
                          "source_revision": "dispatch-source", "assigned_at": "dispatch-time"}, sibling)
        self.assertEqual(3, len({primary["id"], task["id"], sibling["id"]}))
        for field in ("requirements", "validation_plan", "affected_paths", "acceptance_criteria", "findings"):
            task[field].append("child-only")
        self.assertEqual(before, state)


class SnapshotCommitTests(unittest.TestCase):
    def test_snapshot_with_untracked_source_and_runner_directories(self):
        for tracked_seed in (False, True):
            for ignored_runner in (False, True):
                with self.subTest(tracked_seed=tracked_seed, ignored_runner=ignored_runner), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    d.git(root, "init", "-q")
                    if tracked_seed:
                        (root / "seed.txt").write_text("seed\n")
                        d.git(root, "add", "seed.txt")
                    d.git(root, "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                          "commit", "--allow-empty", "-qm", "fixture")
                    if ignored_runner:
                        (root / ".git/info/exclude").write_text("/.autocode/\n/.autocode-ui/\n__pycache__/\n*.pyc\n")
                    for name in (".autocode/runs/fixture/log", ".autocode-ui/log",
                                 "__pycache__/root.pyc", "contract/__pycache__/schema.pyc", "contract/other.pyc"):
                        path = root / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"not source")
                    empty = d.snapshot_commit(root, root / ".autocode/runs/fixture")
                    self.assertEqual(["seed.txt"] if tracked_seed else [],
                                     list(filter(None, d.git(root, "ls-tree", "-rz", "--name-only", empty).decode().split("\0"))))
                    (root / "contract/schema.json").write_text('{"version": 1}\n')
                    (root / "dependency_trace.json").write_text('{"edges": []}\n')
                    head = d.git(root, "rev-parse", "HEAD")
                    index = (root / ".git/index").read_bytes()
                    before = s.snapshot(root)
                    commit = d.snapshot_commit(root, root / ".autocode/runs/fixture")
                    self.assertEqual(sorted(before["files"]), d.git(root, "ls-tree", "-rz", "--name-only", commit).decode().strip("\0").split("\0"))
                    self.assertEqual(b'{"version": 1}\n', d.git(root, "show", f"{commit}:contract/schema.json"))
                    self.assertEqual(head, d.git(root, "rev-parse", "HEAD"))
                    self.assertEqual(index, (root / ".git/index").read_bytes())
                    self.assertEqual(before, s.snapshot(root))


class DispatchTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp

    def prepare(self, *, paths=None, human=False, decision=None):
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
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        self.state["settings"].update(orchestration=copy.deepcopy(d.DEFAULTS),
                                      milestone_checkpoints=copy.deepcopy(m.DEFAULTS), engine="codex", report_repair={"max_attempts": 2})
        decision = decision or {"status": "CONTINUE", "next_objective": "First output", "affected_paths": draft["milestones"][0]["affected_paths"],
                    "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["Output 1 works"],
                                  "acceptance_criteria": ["C1"], "validation_plan": ["Read output 1"]}}
        lifecycle.assign_task(self.state, decision, s.snapshot(self.root))
        self.state["next_stage"] = "orchestrator"
        self.state["affected_paths"] = decision["affected_paths"]
        fixture_bin = self.root / ".autocode/fixture-bin"
        fixture_bin.mkdir()
        shutil.copy2((Path(__file__).resolve().parents[1] / "tools" / ("fake_parallel_builder.py")), fixture_bin / "codex")
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
            "command": "cat a.txt b.txt", "exit_code": 0, "aggregated_output": "M1 M2"}}))
        value = {**envelope(self.state), "verdict": "PASS", "findings": [], "unverified_criteria": ["C3"],
                 "checks_run": ["cat a.txt b.txt"], "checks": [{"command": "cat a.txt b.txt", "exit_code": 0, "evidence_ref": "event:check"}],
                 "criterion_results": [{"id": cid, "status": "PASS", "evidence_refs": ["event:check"]} for cid in ("C1", "C2")],
                 "end_to_end_result": {"status": flow_status, "summary": "Dependent combined output remains to be built" if flow_status == "NOT_VERIFIED" else "Both outputs work", "evidence_refs": ["event:check"]},
                 "milestone_results": [{"milestone_id": mid, "status": "PASS", "summary": "Executed output", "evidence_refs": ["event:check"]}
                                       for mid in (["M1"] if missing_member else ["M1", "M2"])]}
        runner.apply_result(self.state, "sol", value, {"role": "sol", "events": str(evidence), "output": str(evidence),
                                                     "source_revision": s.snapshot(self.root)["revision"]}, self.root, self.run)

    def advance(self, mid="M3"):
        cid = {"M1": "C1", "M2": "C2", "M3": "C3"}[mid]
        return lifecycle.assign_task(self.state, {"status": "CONTINUE", "next_objective": "Finish " + mid,
              "affected_paths": ["combined.txt"], "next_task": {"kind": "implement", "milestone_id": mid,
              "requirements": ["Keep outputs working"], "acceptance_criteria": [cid], "validation_plan": ["Read output"]}}, s.snapshot(self.root))

    def test_validator_guidance_switches_from_batch_to_single_task_schema(self):
        from units import autoreview
        self.prepare()
        self.state["settings"]["milestone_checkpoints"] = {"enabled": True}
        schemas = Path(d.__file__).with_name("autocode-schemas")
        self.state["current_task"]["milestone_ids"] = ["M1", "M2"]
        batch = autoreview.prepare(self.state, "sol", self.run / "state.json", schemas)
        self.assertIn("milestone_results", batch.schema["required"])
        self.assertIn("must provide milestone_results", batch.prompt)
        self.assertNotIn("Do not include milestone_results", batch.prompt)

        self.state["current_task"].pop("milestone_ids")
        self.state["current_task"]["kind"] = "validate"
        single = autoreview.prepare(self.state, "sol", self.run / "state.json", schemas)
        self.assertNotIn("milestone_results", single.schema["properties"])
        self.assertFalse(single.schema["additionalProperties"])
        self.assertIn("Do not include milestone_results", single.prompt)
        self.assertIn("whole-product validation", single.prompt)
        self.assertNotIn("must provide milestone_results", single.prompt)
        self.assertIn("milestone_results", batch.schema["required"])

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
            # The worker's own assignment gate rejects the escape before integration.
            with self.assertRaisesRegex(s.Paused, "outside the assigned paths"):
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

    def test_empty_legacy_built_receipt_cannot_be_integrated(self):
        self.prepare()
        batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
        d.run_workers(self.state, self.run, batch)
        # Simulate a legacy result whose purported implementation has no tree
        # delta. The collector must reject independently of the worker's label.
        with patch.object(d, 'snapshot_commit', return_value=batch['base_commit']):
            with self.assertRaisesRegex(s.Paused, 'empty implementation candidate'):
                d.collect(self.state, self.root, self.run, batch)
        self.assertFalse((self.root / 'a.txt').exists())

    def test_snapshot_commit_ignores_generated_python_bytecode_at_any_depth(self):
        self.prepare()
        batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
        workspace = Path(batch["workers"][0]["workspace"])
        (workspace / "a.txt").write_text("owned change\n")
        for name in ("__pycache__/root.pyc", "pkg/__pycache__/module.pyc", "pkg/other.pyc"):
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"generated bytecode")
        revision = s.snapshot(workspace)["revision"]
        commit = d.snapshot_commit(workspace, Path(batch["workers"][0]["run_dir"]))
        paths = list(filter(None, d.git(self.root, "diff", "--name-only", "--no-renames", "-z",
                                        batch["base_commit"], commit).decode().split("\0")))
        self.assertEqual(["a.txt"], paths)
        self.assertEqual(revision, s.snapshot(workspace)["revision"])

    def test_snapshot_uses_literal_inventory_and_preserves_ignored_policy_and_deletions(self):
        self.prepare()
        # A staged deletion is absent from ls-files but still present in HEAD.
        d.git(self.root, "rm", "greet.py")
        (self.root / "new.txt").write_text("staged before ignore\n")
        d.git(self.root, "add", "new.txt")
        (self.root / "new.txt").write_text("working source after staging\n")
        (self.root / ".autocode/staged.log").write_text("runner output\n")
        (self.root / "__pycache__").mkdir(exist_ok=True)
        (self.root / "__pycache__/staged.pyc").write_bytes(b"generated")
        d.git(self.root, "add", ".autocode/staged.log", "__pycache__/staged.pyc")
        (self.root / "test_greeting.py").write_text("tracked despite ignore rule\n")
        with (self.root / ".git/info/exclude").open("a") as exclude:
            exclude.write("/test_greeting.py\n/new.txt\n/secret.env\n")
        (self.root / "secret.env").write_text("must not be captured\n")
        names = (":(glob)*.txt", "space\nname.txt", "--odd[1].txt")
        for name in names:
            (self.root / name).write_bytes(b"\x00\xffliteral source\n")
        head = d.git(self.root, "rev-parse", "HEAD")
        index = (self.root / ".git/index").read_bytes()
        before = s.snapshot(self.root)
        commit = d.snapshot_commit(self.root, self.run)
        paths = set(filter(None, d.git(self.root, "ls-tree", "-rz", "--name-only", commit).decode().split("\0")))
        self.assertEqual(set(before["files"]), paths)
        self.assertNotIn("greet.py", paths)
        self.assertNotIn("secret.env", paths)
        self.assertEqual(b"working source after staging\n", d.git(self.root, "show", f"{commit}:new.txt"))
        self.assertEqual(b"tracked despite ignore rule\n", d.git(self.root, "show", f"{commit}:test_greeting.py"))
        for name in names:
            self.assertEqual(b"\x00\xffliteral source\n", d.git(self.root, "show", f"{commit}:{name}"))
        self.assertEqual(head, d.git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(index, (self.root / ".git/index").read_bytes())
        self.assertEqual(before, s.snapshot(self.root))

    def test_snapshot_reads_working_contents_without_changing_user_index_flags(self):
        self.prepare()
        name = "test_greeting.py"
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                for reset in ("--no-assume-unchanged", "--no-skip-worktree"):
                    d.git(self.root, "update-index", reset, "--", name)
                d.git(self.root, "add", name)
                d.git(self.root, "update-index", flag, "--", name)
                expected = f"working contents under {flag}\n".encode()
                (self.root / name).write_bytes(expected)
                head = d.git(self.root, "rev-parse", "HEAD")
                index = (self.root / ".git/index").read_bytes()
                before = s.snapshot(self.root)
                commit = d.snapshot_commit(self.root, self.run)
                self.assertEqual(expected, d.git(self.root, "show", f"{commit}:{name}"))
                self.assertEqual(head, d.git(self.root, "rev-parse", "HEAD"))
                self.assertEqual(index, (self.root / ".git/index").read_bytes())
                self.assertEqual(before, s.snapshot(self.root))

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

    def test_reviewed_primary_output_and_check_survive_preparation_interrupt_and_retry(self):
        self.prepare(decision={
            "status": "CONTINUE", "next_objective": "First output with an integrity checksum",
            "affected_paths": ["a.txt"], "next_task": {
                "kind": "implement", "milestone_id": "M1", "acceptance_criteria": ["C1"],
                "requirements": ["Output 1 works", "Append the SHA-256 digest of the milestone ID."],
                "validation_plan": ["Read output 1", "Verify the output checksum."]}})
        reviewed = copy.deepcopy(self.state["current_task"])
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
        with patch.dict(os.environ, {"AUTOCODE_BUILDER_FAIL": "M1"}):
            with self.assertRaisesRegex(s.Paused, "Builder M1"):
                self.build()
        self.state = s.read(self.run / "state.json")
        sibling_attempt = (self.root / ".autocode/barrier/M2").read_bytes()
        d.request_retry(self.state, self.run, ["M1"])
        self.state = s.read(self.run / "state.json")
        self.build()

        self.assertEqual("M1\n" + hashlib.sha256(b"M1").hexdigest() + "\n", (self.root / "a.txt").read_text())
        self.assertEqual("M2\n", (self.root / "b.txt").read_text())
        self.assertEqual(sibling_attempt, (self.root / ".autocode/barrier/M2").read_bytes())
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual(set(), m.accepted_ids(self.state))
        batch = self.state["orchestration_history"][0]
        for row in batch["workers"]:
            directory = Path(row["run_dir"])
            prompt = next(directory.glob("iterations/*/builder-*.prompt.md")).read_text()
            handed = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])["current_task"]
            if row["milestone_id"] == "M1":
                for field in ("objective", "requirements", "validation_plan", "affected_paths"):
                    self.assertEqual(reviewed[field], handed[field])
            else:
                self.assertEqual(["Output 2 works"], handed["requirements"])
                self.assertEqual(["Read output 2"], handed["validation_plan"])
            child = s.read(directory / "state.json")
            events = [json.loads(line) for record in child["stages"] if record["stage"] == "terra"
                      for line in Path(record["events"]).read_text().splitlines()]
            checks = [e["item"] for e in events if e.get("item", {}).get("id") == "checksum"]
            self.assertEqual([0] if row["milestone_id"] == "M1" else [], [c["exit_code"] for c in checks])

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
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
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
        # Integrated: both Builder checkouts and branches are gone; their run records stay.
        self.assertEqual(1, d.git(self.root, "worktree", "list", "--porcelain").count(b"worktree "))
        self.assertEqual(b"", d.git(self.root, "branch", "--list", "autocode/builder-*").strip())
        for row in self.state["orchestration_history"][0]["workers"]:
            self.assertTrue(row["worktree_removed"])
            self.assertFalse((Path(row["workspace"]) / ".git").exists())
            self.assertTrue((Path(row["run_dir"]) / "state.json").is_file())

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
        shutil.copy2((Path(__file__).resolve().parents[1] / "tools" / ("fake_parallel_builder.py")), self.root / "fixture-bin/codex")
        (self.root / "fixture-bin/codex").chmod(0o755)
        self.env["AUTOCODE_BUILDER_BARRIER"] = str(self.root / "barrier")

    def test_full_cli_parallel_wave_then_dependency_then_completion(self):
        self.fixture()
        (self.project / ".git/info/exclude").write_text("/.autocode/\n/.autocode-ui/\n__pycache__/\n*.pyc\n")
        (self.project / ".autocode-ui").mkdir()
        (self.project / ".autocode-ui/log").write_text("runner only\n")
        self.launch(["Produce two outputs and combine", "--max-parallel-builders", "2", "--chat"], 0, answers="yes\n")
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual({"M1", "M2", "M3"}, m.accepted_ids(state))
        self.assertEqual(["M1", "M2"], [w["milestone_id"] for w in state["orchestration_history"][0]["workers"]])
        stages = [r["stage"] for r in state["stages"]]
        self.assertEqual(["recognize_workflow", "astra_discovery", "astra_plan", "terra", "terra", "orchestrator",
                          "sol", "astra_review", "orchestrator", "terra", "sol", "astra_review"], stages)
        self.assertEqual("M3\n", (self.project / "combined.txt").read_text())
        unchanged = (run / "state.json").read_bytes()
        self.launch(["--run-dir", str(run)], 0)
        self.assertEqual(unchanged, (run / "state.json").read_bytes())

    def test_cli_retry_preserves_successful_builder(self):
        self.fixture()
        self.env["AUTOCODE_BUILDER_FAIL"] = "M2"
        self.launch(["Produce two outputs and combine", "--max-parallel-builders", "2", "--chat"], 2, answers="yes\n")
        run, state = self.saved()
        # An exhausted operational pause is now surfaced as an AutoResolver operational request.
        assert_operational_wait(self, state, "PAUSED_ORCHESTRATOR_WORKER")
        first = (self.root / "barrier/M1").read_bytes()
        self.env.pop("AUTOCODE_BUILDER_FAIL")
        self.launch(["--run-dir", str(run), "--resume-paused", "--retry-builder", "M2", "--no-chat"], 0)
        self.assertEqual("TASK_COMPLETE", self.saved()[1]["status"])
        self.assertEqual(first, (self.root / "barrier/M1").read_bytes())


class StrayWriteTests(unittest.TestCase):
    """Parent writes cannot be attributed to Builders by path ownership alone."""
    setUp = test_goals.GoalTests.setUp
    prepare = DispatchTests.prepare

    def batch(self, **env):
        self.prepare(paths={"M1": ["a.txt"], "M2": ["pkg/b.txt"]})
        with patch.dict(os.environ, env):
            batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
            d.run_workers(self.state, self.run, batch)
        return batch

    def test_a_stray_parent_write_is_preserved_and_blocks_integration(self):
        batch = self.batch(AUTOCODE_BUILDER_STRAY="M2")
        with self.assertRaisesRegex(s.Paused, "Parent source changed"):
            d.collect(self.state, self.root, self.run, batch)
        self.assertEqual("stray M2\n", (self.root / "pkg/b.txt").read_text())
        self.assertFalse((self.root / "a.txt").exists())
        self.assertEqual("M2\n", (Path(batch["workers"][1]["workspace"]) / "pkg/b.txt").read_text())

    def test_anything_else_in_the_parent_still_stops_integration(self):
        batch = self.batch()
        d.collect(self.state, self.root, self.run, batch)
        for name, text in (("manual.txt", "user edit"), ("greet.py", "changed existing file")):
            with self.subTest(name=name):
                before = (self.root / name).read_text() if (self.root / name).exists() else None
                (self.root / name).write_text(text)
                with self.assertRaisesRegex(s.Paused, "workspace changed"):
                    d.integrate(self.state, self.root, self.run, batch)
                if before is None:
                    (self.root / name).unlink()
                else:
                    (self.root / name).write_text(before)
        # Even exact copies of the expected result are not proof that this batch applied its patch.
        (self.root / "a.txt").write_text("M1\n")
        (self.root / "pkg").mkdir()
        (self.root / "pkg/b.txt").write_text("M2\n")
        with self.assertRaisesRegex(s.Paused, "workspace changed"):
            d.integrate(self.state, self.root, self.run, batch)
        self.assertEqual("M1\n", (self.root / "a.txt").read_text())
        self.assertEqual("M2\n", (self.root / "pkg/b.txt").read_text())

    def test_the_builder_is_told_its_worktree_and_the_shared_root(self):
        self.prepare()
        batch = d.prepare(self.state, self.root, self.run, d.select(self.state))
        d.run_workers(self.state, self.run, batch)
        for row in batch["workers"]:
            prompts = list(Path(row["run_dir"]).glob("iterations/*/builder-*.prompt.md")) + list(Path(row["run_dir"]).glob("iterations/*/terra-*.prompt.md"))
            prompt = prompts[0].read_text()
            self.assertIn(f"Your worktree is {row['workspace']}", prompt)
            self.assertIn("never write there, and never cd there", prompt)
