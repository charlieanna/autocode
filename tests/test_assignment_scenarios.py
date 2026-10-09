"""Bounded-assignment scenarios against the real runner, worktrees and completion gates."""

import copy
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_artifacts as artifacts
import autocode_dispatch as d
import autocode_goal_lifecycle as lifecycle
import autocode_goals as g
import autocode_milestones as m
import autocode_stage_context as stage_context
import autocode_support as s
from goal_fixtures import body, envelope

from . import test_goals

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
        files = {
            "src/greeting.py": HELLO,
            "tests/test_greeting.py": TEST_SOURCE,
            "config/app.txt": CONFIG,
            "notes/unrelated.txt": NOTE,
            "docs/other.txt": "baseline\n",
        }
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        subprocess.run(
            ["git", "-C", str(self.root), "add", "--", "src", "tests", "config", "notes", "docs"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "baseline",
            ],
            check=True,
        )

    def install_builder(self, scenario):
        fixture_bin = self.root / ".autocode/fixture-bin"
        fixture_bin.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).resolve().parents[1] / "tools" / "scenario_builder.py", fixture_bin / "codex")
        (fixture_bin / "codex").chmod(0o755)
        self.environment = patch.dict(
            os.environ,
            {
                "PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
                "AUTOCODE_SCENARIO": scenario,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def approve(self, milestones, criteria):
        draft = body()
        draft["acceptance_criteria"] = criteria
        draft["milestones"] = milestones
        draft["scope_exclusions"] = ["tests/", "config/", "notes/unrelated.txt"]
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        self.state["settings"].update(
            orchestration=copy.deepcopy(d.DEFAULTS),
            milestone_checkpoints=copy.deepcopy(m.DEFAULTS),
            engine="codex",
            report_repair={"max_attempts": 2},
            limits={
                "iteration_ceiling": 5,
                "max_seconds": None,
                "no_progress_batches": 3,
                "stage_timeout_seconds": 3,
                "idle_timeout_seconds": 2,
                "tool_timeout_seconds": 2,
            },
        )
        first = milestones[0]
        decision = {
            "status": "CONTINUE",
            "next_objective": first["objective"],
            "affected_paths": first["affected_paths"],
            "next_task": {
                "kind": "implement",
                "milestone_id": first["id"],
                "requirements": [criteria[0]["criterion"]],
                "acceptance_criteria": first["acceptance_criteria"],
                "validation_plan": [criteria[0]["verification_method"]],
            },
        }
        lifecycle.assign_task(self.state, decision, s.snapshot(self.root))
        self.state["next_stage"] = "orchestrator"
        self.state["affected_paths"] = decision["affected_paths"]

    def greeting_contract(self, extra=None):
        criteria = [
            {
                "id": "C1",
                "criterion": "Greeting text is Welcome",
                "verification_method": "Read src/greeting.py",
                "human_review": False,
            }
        ]
        milestones = [
            {
                "id": "M1",
                "objective": "Change greeting from Hello to Welcome",
                "depends_on": [],
                "acceptance_criteria": ["C1"],
                "affected_paths": ["src/greeting.py"],
            }
        ]
        if extra:
            criteria.append(
                {
                    "id": "C2",
                    "criterion": "Independent note is updated",
                    "verification_method": "Read docs/other.txt",
                    "human_review": False,
                }
            )
            milestones.append(extra)
        self.approve(milestones, criteria)

    def partner(self):
        return {
            "id": "M2",
            "objective": "Update the independent note",
            "depends_on": [],
            "acceptance_criteria": ["C2"],
            "affected_paths": ["docs/other.txt"],
        }

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
        decision = {
            **envelope(self.state),
            "status": "COMPLETE",
            "next_objective": "",
            "acceptance_criteria": [
                {**c, "status": "verified", "evidence": "event:check"} for c in self.state["acceptance_criteria"]
            ],
            "next_task": {
                "kind": "none",
                "milestone_id": "",
                "requirements": [],
                "acceptance_criteria": [],
                "validation_plan": [],
            },
            "plan": ["done"],
            "affected_paths": [],
            "evidence": ["event:check"],
            "blocker": "",
            "agreed_limitations": [],
            "findings": [],
        }
        with self.assertRaises(s.Paused) as caught:
            runner.apply_result(
                self.state,
                "astra_review",
                decision,
                {"output": str(self.run / "claim.json"), "source_revision": s.snapshot(self.root)["revision"]},
                self.root,
                self.run,
            )
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
        self.assertTrue(
            all((tree / "tests/test_greeting.py").read_text() == TEST_SOURCE for tree in self.worker_trees())
        )

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
        with self.assertRaisesRegex(s.Paused, "without source changes"):
            self.build()
        self.assertEqual(HELLO, (self.root / "src/greeting.py").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        workers = self.state["orchestration_batch"]["workers"]
        greeting = next(row for row in workers if row["milestone_id"] == "M1")
        child = s.read(Path(greeting["run_dir"]) / "state.json")
        self.assertGreaterEqual(child["no_progress_batches"], 1)
        self.assertNotIn("implementation", child)
        self.assertNotEqual("BUILT", greeting.get("status"))
        self.parent_untouched()
        self.assertEqual("baseline\n", (self.root / "docs/other.txt").read_text())
        sibling = next(row for row in workers if row["milestone_id"] == "M2")
        self.assertEqual("BUILT", sibling["status"])
        self.assertEqual("independent note\n", (Path(sibling["workspace"]) / "docs/other.txt").read_text())

        # Public stage artifacts show one accepted classification, not a malformed
        # report repaired twice, and no further Builder was admitted for this member.
        run = Path(greeting["run_dir"])
        result = json.loads((run / "result.json").read_text())
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", result["status"])
        self.assertIn("Investigator:", result["reason"])
        self.assertNotIn("output was rejected", result["reason"])
        stem = artifacts.slug("investigate_stuck")
        reports = [
            path
            for path in (run / "iterations").glob(f"*/{stem}-*.json")
            if re.fullmatch(re.escape(stem) + r"-\d{2}\.json", path.name)
        ]
        self.assertEqual(1, len(reports))
        report = json.loads(reports[0].read_text())
        packet = json.loads(reports[0].with_suffix(".prompt.md").read_text().split("CURRENT HANDOFF DATA\n", 1)[1])
        failure = packet["builder_failure"]
        self.assertEqual(failure["failure_id"], report["failure_id"])
        self.assertEqual(failure["evidence_refs"], report["evidence_refs"])
        self.assertEqual(
            ("unknown", "pause", "", ""),
            (report["failure_class"], report["recommendation"], report["guidance"], report["probe"]),
        )
        self.assertTrue(report["untestable"])
        events = [json.loads(line) for line in (run / "activity.jsonl").read_text().splitlines()]
        finished = [event["stage"] for event in events if event.get("event") == "stage_finished"]
        self.assertEqual(1, finished.count("terra"))
        self.assertEqual(1, finished.count("investigate_stuck"))
        self.assertNotIn("investigate_stuck_report_repair", finished)

    def test_compile_failure_is_not_completion(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("compile_fail")
        self.build()
        compiled = subprocess.run(
            ["python3", "-m", "py_compile", "src/greeting.py"], cwd=self.root, capture_output=True
        )
        self.assertNotEqual(0, compiled.returncode)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.claim_complete()

    def test_test_failure_is_not_completion(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("correct")
        self.build()
        checked = subprocess.run(
            ["python3", "-c", TEST_SOURCE + "\ntest_greeting()\n"], cwd=self.root, capture_output=True, text=True
        )
        self.assertNotEqual(0, checked.returncode)
        evidence = self.run / "sol.jsonl"
        evidence.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "check",
                        "type": "command_execution",
                        "command": "python3 -m unittest",
                        "exit_code": 1,
                        "aggregated_output": checked.stderr,
                    },
                }
            )
        )
        value = {
            **envelope(self.state),
            "verdict": "FAIL",
            "findings": [],
            "unverified_criteria": ["C1", "C2"],
            "checks_run": ["python3 -m unittest"],
            "checks": [{"command": "python3 -m unittest", "exit_code": 1, "evidence_ref": "event:check"}],
            "criterion_results": [
                {"id": cid, "status": "FAIL", "evidence_refs": ["event:check"]} for cid in ("C1", "C2")
            ],
            "end_to_end_result": {
                "status": "FAIL",
                "summary": "Greeting tests still expect Hello",
                "evidence_refs": ["event:check"],
            },
            "milestone_results": [
                {"milestone_id": mid, "status": "FAIL", "summary": "Checks failed", "evidence_refs": ["event:check"]}
                for mid in ("M1", "M2")
            ],
        }
        runner.apply_result(
            self.state,
            "sol",
            value,
            {
                "role": "sol",
                "events": str(evidence),
                "output": str(evidence),
                "source_revision": s.snapshot(self.root)["revision"],
            },
            self.root,
            self.run,
        )
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
        self.assertEqual(
            ["terra", "terra_report_repair"],
            [row["stage"] for row in child["stages"] if row["stage"].startswith("terra")],
        )
        self.assertEqual(WELCOME, (self.root / "src/greeting.py").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    def test_hung_builder_is_stopped_and_not_completed(self):
        self.seed()
        self.greeting_contract(self.partner())
        self.install_builder("hang")
        with self.assertRaises(s.Paused):
            self.build()
        self.parent_untouched()
        self.assertFalse(
            any(
                row.get("status") == "BUILT" and row["milestone_id"] == "M1"
                for row in self.state["orchestration_batch"]["workers"]
            )
        )

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
        criteria = [
            {"id": f"C{i}", "criterion": f"Output {i}", "verification_method": "Read greeting", "human_review": False}
            for i in (1, 2)
        ]
        milestones = [
            {
                "id": "M1",
                "objective": "Edit greeting",
                "depends_on": [],
                "acceptance_criteria": ["C1"],
                "affected_paths": ["src/greeting.py"],
            },
            {
                "id": "M2",
                "objective": "Edit greeting again",
                "depends_on": [],
                "acceptance_criteria": ["C2"],
                "affected_paths": ["src/greeting.py"],
            },
        ]
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
        criteria = [
            {
                "id": "C1",
                "criterion": "Parts A through D exist",
                "verification_method": "Read the four part files",
                "human_review": False,
            },
            {
                "id": "C2",
                "criterion": "Independent note is updated",
                "verification_method": "Read docs/other.txt",
                "human_review": False,
            },
        ]
        milestones = [
            {
                "id": "M1",
                "objective": "Write parts A B C and D",
                "depends_on": [],
                "acceptance_criteria": ["C1"],
                "affected_paths": ["src/part_a.py", "src/part_b.py", "src/part_c.py", "src/part_d.py"],
            },
            self.partner(),
        ]
        self.approve(milestones, criteria)
        self.install_builder("partial")
        self.build()
        self.assertEqual("A = True\n", (self.root / "src/part_a.py").read_text())
        self.assertEqual("B = True\n", (self.root / "src/part_b.py").read_text())
        self.assertFalse((self.root / "src/part_c.py").exists())
        self.assertFalse((self.root / "src/part_d.py").exists())
        reports = [
            json.loads(Path(path).read_text())["report"] for path in self.state["implementation"]["builder_reports"]
        ]
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
        s.atomic_json(
            schema,
            s.model_output_schema(g.role_schema(s.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "terra")),
        )
        prompt, metrics = stage_context.context_packet(self.state, "terra", self.run / "state.json")
        self.state["pending_context_metrics"] = metrics
        value, record = runner.run_role(
            role="terra",
            prompt=prompt,
            sandbox="workspace-write",
            workspace=self.root,
            run_dir=self.run,
            state=self.state,
            schema=schema,
            model="terra",
            allow_write=True,
            dry_run=False,
        )
        self.assertEqual(["src/greeting.py", "tests/test_greeting.py"], sorted(record["changed_files"]))
        with self.assertRaises(s.Paused) as caught:
            runner.apply_result(self.state, "terra", value, record, self.root, self.run)
        self.assertEqual("PAUSED_ASSIGNMENT_SCOPE", caught.exception.status)
        self.assertIn("tests/test_greeting.py", str(caught.exception))
        self.assertNotIn("implementation", self.state)
        self.assertNotIn("tests/test_greeting.py", self.state.get("changed_files", []))
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        # The rejected stage records its delta; clean committed source is restored.
        self.assertEqual(TEST_SOURCE, (self.root / "tests/test_greeting.py").read_text())
        self.assertEqual(WELCOME, (self.root / "src/greeting.py").read_text())
        self.assertIn("restored pre-existing files", str(caught.exception))

    def test_serial_assignment_accepts_edits_inside_its_paths(self):
        self.seed()
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.state["next_stage"] = "terra"
        self.install_builder("correct")
        schema = self.run / "schema.json"
        s.atomic_json(
            schema,
            s.model_output_schema(g.role_schema(s.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "terra")),
        )
        prompt, metrics = stage_context.context_packet(self.state, "terra", self.run / "state.json")
        self.state["pending_context_metrics"] = metrics
        value, record = runner.run_role(
            role="terra",
            prompt=prompt,
            sandbox="workspace-write",
            workspace=self.root,
            run_dir=self.run,
            state=self.state,
            schema=schema,
            model="terra",
            allow_write=True,
            dry_run=False,
        )
        runner.apply_result(self.state, "terra", value, record, self.root, self.run)
        self.assertEqual(["src/greeting.py"], self.state["changed_files"])
        self.assertEqual(WELCOME, (self.root / "src/greeting.py").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    # A rejected attempt restores provably clean files. A retry still checks the
    # whole assignment, so unprovably clean changes cannot ride into validation.
    def serial_attempt(self, scenario):
        """One Builder attempt through Autopilot's own stage path, as a run makes it."""
        if not hasattr(self, "environment"):
            self.install_builder(scenario)
        os.environ["AUTOCODE_SCENARIO"] = scenario
        self.state.update(status="RUNNING", phase="EXECUTING", next_stage="terra")
        self.state.pop("stop_reason", None)
        try:
            runner.autopilot.dispatch_unit(runner, self.state, "terra", self.root, self.run)
        except s.Paused as caught:
            return str(caught)
        return None

    def serial_greeting(self, first):
        self.seed()
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.assertRegex(self.serial_attempt(first), "outside the assigned paths")

    def test_serial_retry_without_edits_proceeds_after_clean_escape_is_restored(self):
        self.serial_greeting("escape_tests")
        self.assertEqual(TEST_SOURCE, (self.root / "tests/test_greeting.py").read_text())
        self.assertIsNone(self.serial_attempt("no_change"))
        self.assertEqual(["src/greeting.py"], self.state["changed_files"])
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual(TEST_SOURCE, (self.root / "tests/test_greeting.py").read_text())
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    def test_serial_retry_with_in_scope_edits_proceeds_after_clean_escape_is_restored(self):
        self.serial_greeting("escape_tests")
        (self.root / "src/greeting.py").write_text(HELLO)
        self.assertIsNone(self.serial_attempt("correct"))
        self.assertEqual(["src/greeting.py"], self.state["changed_files"])
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual(TEST_SOURCE, (self.root / "tests/test_greeting.py").read_text())

    def test_serial_retry_proceeds_after_deleted_committed_file_is_restored(self):
        self.serial_greeting("delete_unrelated")
        self.assertEqual(NOTE, (self.root / "notes/unrelated.txt").read_text())
        self.assertIsNone(self.serial_attempt("correct"))
        self.assertEqual(["src/greeting.py"], self.state["changed_files"])
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual(NOTE, (self.root / "notes/unrelated.txt").read_text())

    def test_serial_retry_stays_blocked_when_escape_was_not_provably_clean_at_start(self):
        self.seed()
        (self.root / "tests/test_greeting.py").write_text(TEST_SOURCE + "# user work before assignment\n")
        self.greeting_contract()
        self.state["settings"]["orchestration"]["enabled"] = False
        self.assertRegex(self.serial_attempt("escape_tests"), "edits retained for inspection.*tests/test_greeting.py")
        retained = (self.root / "tests/test_greeting.py").read_text()
        self.assertNotEqual(TEST_SOURCE, retained)
        self.assertRegex(self.serial_attempt("correct"), "outside the assigned paths.*tests/test_greeting.py")
        self.assertEqual(retained, (self.root / "tests/test_greeting.py").read_text())
        self.assertNotIn("implementation", self.state)
        self.assertNotEqual("sol", self.state["next_stage"])

    def test_serial_retry_proceeds_once_the_escape_is_restored(self):
        self.serial_greeting("escape_tests")
        (self.root / "tests/test_greeting.py").write_text(TEST_SOURCE)
        (self.root / "src/greeting.py").write_text(HELLO)
        self.assertIsNone(self.serial_attempt("correct"))
        self.assertEqual(["src/greeting.py"], self.state["changed_files"])
        self.assertEqual("sol", self.state["next_stage"])

    def test_empty_starting_tree_retains_new_file_for_validation(self):
        # This scenario requires a genuinely empty starting tree, unlike the
        # shared contract fixture used by tests that replay validation.
        for name in ("greet.py", "test_greeting.py"):
            (self.root / name).unlink()
        subprocess.run(["git", "-C", str(self.root), "add", "-u"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "empty fixture",
            ],
            check=True,
        )
        criteria = [
            {
                "id": "C1",
                "criterion": "Create the new source file",
                "verification_method": "Read src/new.py",
                "human_review": False,
            }
        ]
        milestones = [
            {
                "id": "M1",
                "objective": "Create src/new.py",
                "depends_on": [],
                "acceptance_criteria": ["C1"],
                "affected_paths": ["src/new.py"],
            }
        ]
        self.approve(milestones, criteria)
        self.state["settings"]["orchestration"]["enabled"] = False
        self.assertEqual({}, s.snapshot(self.root)["files"])
        # The runner removes the out-of-scope file the attempt created, so the retry is not refused for it.
        self.assertRegex(
            self.serial_attempt("new_file_escape"),
            "outside the assigned paths; the runner removed the files they created.*unrelated.txt",
        )
        self.assertFalse((self.root / "unrelated.txt").exists())
        self.assertIsNone(self.serial_attempt("no_change"))
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual(["src/new.py"], self.state["changed_files"])
        self.assertEqual(["src/new.py"], self.state["implementation"]["changed_files"])
        self.assertFalse(self.state["retained_candidate_handoffs"][-1]["previously_validated"])
        self.assertTrue((self.root / "src/new.py").is_file())

    def test_serial_retry_pauses_when_the_starting_snapshot_is_missing(self):
        self.serial_greeting("escape_tests")
        first = next(row for row in self.state["stages"] if row.get("stage") == "terra")
        Path(first["before_ref"]).unlink()
        (self.root / "tests/test_greeting.py").write_text(TEST_SOURCE)
        (self.root / "src/greeting.py").write_text(HELLO)
        self.assertRegex(self.serial_attempt("correct"), "starting snapshot is missing")
        self.assertNotEqual("sol", self.state["next_stage"])
