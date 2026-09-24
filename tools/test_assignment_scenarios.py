"""Bounded-assignment scenarios against the real runner, worktrees and completion gates."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from . import test_goals
import autocode as runner
import autocode_dispatch as d
import autocode_goals as g
import autocode_milestones as m
import autocode_support as s
from goal_fixtures import body, envelope


HELLO = 'GREETING = "Hello"\n'
WELCOME = 'GREETING = "Welcome"\n'
TEST_SOURCE = "def test_greeting():\n    assert 'Hello' in open('src/greeting.py').read()\n"
CONFIG = "stable\n"
NOTE = "keep\n"


class AssignmentScenarios(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp

    def tearDown(self):
        subprocess.run(["git", "-C", str(self.root), "worktree", "prune"], check=False, capture_output=True)

    def seed(self):
        files = {"src/greeting.py": HELLO, "tests/test_greeting.py": TEST_SOURCE,
                 "config/app.txt": CONFIG, "notes/unrelated.txt": NOTE, "docs/other.txt": "baseline\n"}
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        subprocess.run(["git", "-C", str(self.root), "add", "--", "src", "tests", "config", "notes", "docs"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "-qm", "baseline"], check=True)

    def install_builder(self, scenario):
        fixture_bin = self.root / ".autocode/fixture-bin"
        fixture_bin.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).with_name("scenario_builder.py"), fixture_bin / "codex")
        (fixture_bin / "codex").chmod(0o755)
        self.environment = patch.dict(os.environ, {
            "PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
            "AUTOCODE_SCENARIO": scenario, "PYTHONDONTWRITEBYTECODE": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def approve(self, milestones, criteria):
        draft = body()
        draft["acceptance_criteria"] = criteria
        draft["milestones"] = milestones
        draft["scope_exclusions"] = ["tests/", "config/", "notes/unrelated.txt"]
        g.install_draft(self.state, draft, origin="test")
        g.present(self.state)
        g.approve(self.state, self.state["displayed_goal"])
        self.state["settings"].update(
            orchestration=copy.deepcopy(d.DEFAULTS), milestone_checkpoints=copy.deepcopy(m.DEFAULTS),
            engine="codex", report_repair={"max_attempts": 2},
            limits={"iteration_ceiling": 5, "max_seconds": None, "max_reported_tokens": None,
                    "no_progress_batches": 3, "stage_timeout_seconds": 3, "idle_timeout_seconds": 2,
                    "tool_timeout_seconds": 2})
        first = milestones[0]
        decision = {"status": "CONTINUE", "next_objective": first["objective"],
                    "affected_paths": first["affected_paths"],
                    "next_task": {"kind": "implement", "milestone_id": first["id"],
                                  "requirements": [criteria[0]["criterion"]],
                                  "acceptance_criteria": first["acceptance_criteria"],
                                  "validation_plan": [criteria[0]["verification_method"]]}}
        g.assign_task(self.state, decision, s.snapshot(self.root))
        self.state["next_stage"] = "orchestrator"
        self.state["affected_paths"] = decision["affected_paths"]

    def greeting_contract(self, extra=None):
        criteria = [{"id": "C1", "criterion": "Greeting text is Welcome",
                     "verification_method": "Read src/greeting.py", "human_review": False}]
        milestones = [{"id": "M1", "objective": "Change greeting from Hello to Welcome", "depends_on": [],
                       "acceptance_criteria": ["C1"], "affected_paths": ["src/greeting.py"]}]
        if extra:
            criteria.append({"id": "C2", "criterion": "Independent note is updated",
                             "verification_method": "Read docs/other.txt", "human_review": False})
            milestones.append(extra)
        self.approve(milestones, criteria)

    def partner(self):
        return {"id": "M2", "objective": "Update the independent note", "depends_on": [],
                "acceptance_criteria": ["C2"], "affected_paths": ["docs/other.txt"]}

    def build(self):
        return d.dispatch(self.state, self.root, self.run)

    def parent_untouched(self):
        self.assertEqual(HELLO, (self.root / "src/greeting.py").read_text())
        self.assertEqual(TEST_SOURCE, (self.root / "tests/test_greeting.py").read_text())
        self.assertEqual(CONFIG, (self.root / "config/app.txt").read_text())
        self.assertEqual(NOTE, (self.root / "notes/unrelated.txt").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    # Either gate is a valid rejection: the worker's own assignment check or
    # the orchestrator's ownership check on the integrated delta.
    REJECTED = "outside the assigned paths|exceed declared"

    def worker_trees(self):
        batch = self.state.get("orchestration_batch") or self.state["orchestration_history"][-1]
        return [Path(row["workspace"]) for row in batch["workers"]]

    def claim_complete(self):
        decision = {**envelope(self.state), "status": "COMPLETE", "next_objective": "",
                    "acceptance_criteria": [{**c, "status": "verified", "evidence": "event:check"}
                                            for c in self.state["acceptance_criteria"]],
                    "next_task": {"kind": "none", "milestone_id": "", "requirements": [],
                                  "acceptance_criteria": [], "validation_plan": []},
                    "plan": ["done"], "affected_paths": [], "evidence": ["event:check"], "blocker": "",
                    "agreed_limitations": [], "findings": []}
        with self.assertRaises(s.Paused) as caught:
            runner.apply_result(self.state, "astra_review", decision, {"output": str(self.run / "claim.json"),
                                "source_revision": s.snapshot(self.root)["revision"]}, self.root, self.run)
        self.assertEqual("PAUSED_COMPLETION_GATE", caught.exception.status)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    def test_correct_implementation_produces_a_candidate(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("correct")
        self.build()
        self.assertEqual(WELCOME, (self.root / "src/greeting.py").read_text())
        self.assertEqual(TEST_SOURCE, (self.root / "tests/test_greeting.py").read_text())
        self.assertEqual(CONFIG, (self.root / "config/app.txt").read_text())
        self.assertEqual("sol", self.state["next_stage"])
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.assertTrue(self.state["implementation"]["builder_reports"])

    def test_test_edits_outside_the_assignment_are_rejected(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("escape_tests")
        with self.assertRaisesRegex(s.Paused, self.REJECTED):
            self.build()
        self.parent_untouched()
        self.assertTrue(any((tree / "tests/test_greeting.py").read_text() != TEST_SOURCE for tree in self.worker_trees()))

    def test_config_edits_outside_allowed_paths_are_rejected(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("escape_config")
        with self.assertRaisesRegex(s.Paused, self.REJECTED):
            self.build()
        self.parent_untouched()

    def test_deleting_an_unrelated_file_is_rejected(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("delete_unrelated")
        with self.assertRaisesRegex(s.Paused, self.REJECTED):
            self.build()
        self.parent_untouched()

    def test_no_source_changes_are_recorded_as_no_progress(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("no_change")
        self.build()
        self.assertEqual(HELLO, (self.root / "src/greeting.py").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        workers = self.state["orchestration_history"][-1]["workers"]
        greeting = next(row for row in workers if row["milestone_id"] == "M1")
        child = s.read(Path(greeting["run_dir"]) / "state.json")
        self.assertGreaterEqual(child["no_progress_batches"], 1)
        self.assertEqual([], greeting["changed_files"])

    def test_compile_failure_is_not_completion(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("compile_fail")
        self.build()
        compiled = subprocess.run(["python3", "-m", "py_compile", "src/greeting.py"], cwd=self.root, capture_output=True)
        self.assertNotEqual(0, compiled.returncode)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.claim_complete()

    def test_test_failure_is_not_completion(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("correct")
        self.build()
        checked = subprocess.run(["python3", "-c", TEST_SOURCE + "\ntest_greeting()\n"], cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(0, checked.returncode)
        evidence = self.run / "sol.jsonl"
        evidence.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": "python3 -m unittest", "exit_code": 1, "aggregated_output": checked.stderr}}))
        value = {**envelope(self.state), "verdict": "FAIL", "findings": [], "unverified_criteria": ["C1", "C2"],
                 "checks_run": ["python3 -m unittest"], "checks": [{"command": "python3 -m unittest", "exit_code": 1,
                    "evidence_ref": "event:check"}],
                 "criterion_results": [{"id": cid, "status": "FAIL", "evidence_refs": ["event:check"]} for cid in ("C1", "C2")],
                 "end_to_end_result": {"status": "FAIL", "summary": "Greeting tests still expect Hello", "evidence_refs": ["event:check"]},
                 "milestone_results": [{"milestone_id": mid, "status": "FAIL", "summary": "Checks failed", "evidence_refs": ["event:check"]}
                                       for mid in ("M1", "M2")]}
        runner.apply_result(self.state, "sol", value, {"role": "sol", "events": str(evidence), "output": str(evidence),
            "source_revision": s.snapshot(self.root)["revision"]}, self.root, self.run)
        self.claim_complete()

    def test_crash_after_writing_files_keeps_the_worktree(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("crash")
        with self.assertRaises(s.Paused):
            self.build()
        self.parent_untouched()
        trees = self.worker_trees()
        self.assertTrue(any((tree / "src/greeting.py").read_text() == WELCOME for tree in trees))

    def test_malformed_report_is_repaired_without_replaying_the_edit(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("malformed")
        self.build()
        workers = self.state["orchestration_history"][-1]["workers"]
        greeting = next(row for row in workers if row["milestone_id"] == "M1")
        child = s.read(Path(greeting["run_dir"]) / "state.json")
        self.assertEqual(["terra", "terra_report_repair"], [row["stage"] for row in child["stages"] if row["stage"].startswith("terra")])
        self.assertEqual(WELCOME, (self.root / "src/greeting.py").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    def test_hung_builder_is_stopped_and_not_completed(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("hang")
        with self.assertRaises(s.Paused):
            self.build()
        self.parent_untouched()
        self.assertFalse(any(row.get("status") == "BUILT" and row["milestone_id"] == "M1"
                             for row in self.state["orchestration_batch"]["workers"]))

    def test_permission_request_pauses_for_a_user_decision(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("permission")
        with self.assertRaisesRegex(s.Paused, "Choose whether to replan or stop"):
            self.build()
        self.parent_untouched()
        child = s.read(Path(self.state["orchestration_batch"]["workers"][0]["run_dir"]) / "state.json")
        self.assertEqual("permission", child["implementation"]["user_request"]["kind"])

    def test_architecture_change_is_not_improvised(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("architecture")
        with self.assertRaisesRegex(s.Paused, "Choose whether to replan or stop"):
            self.build()
        self.parent_untouched()
        child = s.read(Path(self.state["orchestration_batch"]["workers"][0]["run_dir"]) / "state.json")
        self.assertEqual("infeasible", child["implementation"]["user_request"]["kind"])
        self.assertFalse((self.root / "src/architecture.py").exists())
        self.assertFalse(any((tree / "src/architecture.py").exists() for tree in self.worker_trees()))

    def test_independent_tasks_use_isolated_worktrees(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("correct")
        self.build()
        batch = self.state["orchestration_history"][-1]
        workspaces = [row["workspace"] for row in batch["workers"]]
        self.assertEqual(2, len(set(workspaces)))
        children = [s.read(Path(row["run_dir"]) / "state.json") for row in batch["workers"]]
        self.assertNotEqual(children[0]["sessions"]["terra"], children[1]["sessions"]["terra"])
        self.assertEqual(WELCOME, (self.root / "src/greeting.py").read_text())
        self.assertEqual("independent note\n", (self.root / "docs/other.txt").read_text())

    def test_overlapping_tasks_are_not_parallelized_or_merged(self):
        self.seed()
        criteria = [{"id": f"C{i}", "criterion": f"Output {i}", "verification_method": "Read greeting", "human_review": False} for i in (1, 2)]
        milestones = [{"id": "M1", "objective": "Edit greeting", "depends_on": [], "acceptance_criteria": ["C1"],
                       "affected_paths": ["src/greeting.py"]},
                      {"id": "M2", "objective": "Edit greeting again", "depends_on": [], "acceptance_criteria": ["C2"],
                       "affected_paths": ["src/greeting.py"]}]
        self.approve(milestones, criteria)
        self.assertEqual([], d.select(self.state))
        self.install_builder("overlap_write")
        self.build()
        self.assertEqual("terra", self.state["next_stage"])
        self.assertIsNone(self.state.get("orchestration_batch"))
        self.assertEqual(HELLO, (self.root / "src/greeting.py").read_text())
        # Even a forced batch cannot merge overlapping writes blindly.
        batch = d.prepare(self.state, self.root, self.run, milestones)
        d.run_workers(self.state, self.run, batch)
        with self.assertRaisesRegex(s.Paused, "overlap or exceed"):
            d.collect(self.state, self.root, self.run, batch)
        self.assertEqual(HELLO, (self.root / "src/greeting.py").read_text())

    def test_partial_success_prose_is_stored_and_not_completion(self):
        self.seed()
        criteria = [{"id": "C1", "criterion": "Parts A through D exist", "verification_method": "Read the four part files", "human_review": False},
                    {"id": "C2", "criterion": "Independent note is updated", "verification_method": "Read docs/other.txt", "human_review": False}]
        milestones = [{"id": "M1", "objective": "Write parts A B C and D", "depends_on": [], "acceptance_criteria": ["C1"],
                       "affected_paths": ["src/part_a.py", "src/part_b.py", "src/part_c.py", "src/part_d.py"]},
                      self.partner()]
        self.approve(milestones, criteria)
        self.install_builder("partial")
        self.build()
        self.assertEqual("A = True\n", (self.root / "src/part_a.py").read_text())
        self.assertEqual("B = True\n", (self.root / "src/part_b.py").read_text())
        self.assertFalse((self.root / "src/part_c.py").exists())
        self.assertFalse((self.root / "src/part_d.py").exists())
        reports = [json.loads(Path(path).read_text())["report"] for path in self.state["implementation"]["builder_reports"]]
        prose = " ".join(Path(path).read_text() for path in reports)
        self.assertIn("Implementation substantially complete.", prose)
        self.assertEqual("sol", self.state["next_stage"])
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.claim_complete()

    def test_serial_assignment_rejects_hidden_test_edits(self):
        self.seed()
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.state["next_stage"] = "terra"
        self.install_builder("escape_tests")
        schema = self.run / "schema.json"
        s.atomic_json(schema, s.model_output_schema(g.role_schema(s.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "terra")))
        prompt, metrics = s.context_packet(self.state, "terra", self.run / "state.json")
        self.state["pending_context_metrics"] = metrics
        value, record = runner.run_role(role="terra", prompt=prompt, sandbox="workspace-write", workspace=self.root,
            run_dir=self.run, state=self.state, schema=schema, model="terra", allow_write=True, dry_run=False)
        self.assertEqual(["src/greeting.py", "tests/test_greeting.py"], sorted(record["changed_files"]))
        with self.assertRaises(s.Paused) as caught:
            runner.apply_result(self.state, "terra", value, record, self.root, self.run)
        self.assertEqual("PAUSED_ASSIGNMENT_SCOPE", caught.exception.status)
        self.assertIn("tests/test_greeting.py", str(caught.exception))
        self.assertNotIn("implementation", self.state)
        self.assertNotIn("tests/test_greeting.py", self.state.get("changed_files", []))
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        # The escaped edit is retained in the tree for inspection, never promoted.
        self.assertNotEqual(TEST_SOURCE, (self.root / "tests/test_greeting.py").read_text())

    def test_serial_assignment_accepts_edits_inside_its_paths(self):
        self.seed()
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.state["next_stage"] = "terra"
        self.install_builder("correct")
        schema = self.run / "schema.json"
        s.atomic_json(schema, s.model_output_schema(g.role_schema(s.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "terra")))
        prompt, metrics = s.context_packet(self.state, "terra", self.run / "state.json")
        self.state["pending_context_metrics"] = metrics
        value, record = runner.run_role(role="terra", prompt=prompt, sandbox="workspace-write", workspace=self.root,
            run_dir=self.run, state=self.state, schema=schema, model="terra", allow_write=True, dry_run=False)
        runner.apply_result(self.state, "terra", value, record, self.root, self.run)
        self.assertEqual(["src/greeting.py"], self.state["changed_files"])
        self.assertEqual(WELCOME, (self.root / "src/greeting.py").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
