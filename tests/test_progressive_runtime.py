"""Public stage/approval boundary tests with real source and replay evidence."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_progressive_completion as completion
import autocode_progressive_state as progressive
import autocode_resolver_human as human
import autocode_util as util
import goal_fixtures


class ProgressiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        goal_fixtures.seed_greeting_workspace(self.workspace)
        self.run_dir = self.workspace / ".autocode" / "runs" / "test"
        self.run_dir.mkdir(parents=True)
        self.state = {
            "version": 3,
            "task_id": "runtime-test",
            "task": "Build greeting and rejection",
            "workspace": str(self.workspace),
            "run_dir": str(self.run_dir),
            "iteration": 0,
            "answers": {},
            "user_events": [],
            "acceptance_criteria": [],
            "status": "RUNNING",
            "next_stage": "astra_discovery",
            "settings": {
                "joint_planning": True,
                "roles": {"glm": {"model": "fake-planner"}, "astra": {"model": "fake-reviewer"}},
                "milestone_checkpoints": {"enabled": True, "max_seconds": 5400},
                "limits": {"max_seconds": 43200},
            },
        }
        self.body = goal_fixtures.body()
        self.body["milestones"][0]["affected_paths"] = ["greet.py", "test_greeting.py"]
        self.first = {
            "objective": "Deliver greeting",
            "affected_paths": ["greet.py", "test_greeting.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Print greeting"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["python3 -m unittest test_greeting.py"],
        }
        self.proposal = {
            "version": 1,
            "needed_because": "Greeting and rejection are separately useful",
            "shared_decisions": ["Keep a local CLI"],
            "outstanding_criteria": [],
            "done_slices": [],
            "slices": [
                {
                    "id": "S1",
                    "intended_result": "Print the greeting",
                    "criterion_ids": ["C1"],
                    "paths": ["greet.py", "test_greeting.py"],
                    "depends_on": [],
                    "tentative": False,
                    "checks": [
                        {
                            "id": "A",
                            "method": "python3 -m unittest test_greeting.py",
                            "relation": "contributes_to",
                            "criterion_ids": ["C1"],
                        }
                    ],
                },
                {
                    "id": "S2",
                    "intended_result": "Reject invalid input too",
                    "criterion_ids": ["C1"],
                    "paths": ["greet.py", "test_greeting.py"],
                    "depends_on": ["S1"],
                    "tentative": True,
                    "checks": [
                        {
                            "id": "B",
                            "method": "python3 -m unittest test_greeting.py",
                            "relation": "fully_verify",
                            "criterion_ids": ["C1"],
                        }
                    ],
                },
            ],
        }

    def stage(self, stage, value, seconds=100):
        self.state["next_stage"] = stage
        output = self.run_dir / (stage + "-" + str(len(self.state.get("stages", []))) + ".json")
        output.write_text(json.dumps(value))
        record = {
            "stage": stage,
            "role": "glm" if stage in ("astra_discovery", "glm_revise") else "astra",
            "output": str(output),
            "iteration": 0,
            "exit_code": 0,
            "duration_seconds": seconds,
            "source_revision": util.snapshot(self.workspace)["revision"],
            "changed_files": [],
        }
        if stage == "sol":
            record["role"] = "sol"
            event = output.with_suffix(".jsonl")
            rows = []
            for check in value["checks"]:
                executed = subprocess.run(
                    check["command"], shell=True, cwd=self.workspace, capture_output=True, text=True, check=True
                )
                event_id = check["evidence_ref"].removeprefix("event:")
                rows.append(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": event_id,
                            "type": "command_execution",
                            "command": check["command"],
                            "exit_code": executed.returncode,
                            "aggregated_output": executed.stdout + executed.stderr,
                        },
                    }
                )
            event.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            record["events"] = str(event)
        if stage in ("astra_challenge", "astra_finalize"):
            runner.planning.charge(self.state, stage, record=record)
        if goals.approved(self.state) or progressive.view(self.state).get("budget"):
            if self.state.get("current_task"):
                record["task_id"] = self.state["current_task"]["id"]
            progressive.admit_attempt(self.state, record, util.snapshot(self.workspace))
            self.state["active_stage"] = record
        runner.account_stage(self.state, record)
        session = getattr(self, "sessions", {}).get(stage)
        if session:
            record.update(thread_id=session, supports_sessions=True)
            events = output.with_suffix(".jsonl")
            events.write_text(
                json.dumps({"type": "thread.started", "thread_id": session})
                + "\n"
                + json.dumps({"type": "turn.completed"})
                + "\n"
            )
            record["events"] = str(events)
        if stage in getattr(self, "repair_stages", ()):
            if not session:
                record["supports_sessions"] = False
                record["events"] = str(output.with_suffix(".jsonl"))
                Path(record["events"]).write_text("")
            output.write_text(json.dumps({"summary": "Rejected incomplete draft"}))
            runner.archive_rejected_stage(self.state, self.run_dir, record, "repair fixture")
            self.state["pending_report_repair"] = {
                "original": copy.deepcopy(record),
                "attempts": 1,
                "contract_hash": self.state["goal_contract"]["hash"],
                "pins": {record[key]: util.file_hash(record[key]) for key in ("output", "events")},
            }
            repair = {
                **copy.deepcopy(record),
                "stage": stage + "_report_repair",
                "original_stage": stage,
                "report_only": True,
                "output": str(output),
                "events": str(output.with_suffix(".repair.jsonl")),
            }
            for key in ("rejected", "rejection_reason", "thread_id"):
                repair.pop(key, None)
            Path(repair["events"]).write_text("")
            output.write_text(json.dumps(value))
            runner.accept_repaired_report(self.state, self.run_dir, self.workspace, copy.deepcopy(value), repair)
        else:
            runner.apply_result(self.state, stage, copy.deepcopy(value), record, self.workspace, self.run_dir)
        return record

    def plan(self):
        common = {
            "summary": "Plan useful slices",
            "contract_changes": [],
            "conflict_resolutions": [],
            "requirement_trace": [],
            "progressive_proposal": copy.deepcopy(self.proposal),
        }
        self.stage(
            "astra_discovery",
            {
                **common,
                "contract": copy.deepcopy(self.body),
                "code_refs": ["greet.py"],
                "alternatives": [],
                "uncertainties": [],
            },
        )
        self.stage("astra_challenge", {"summary": "No blocking concerns", "concerns": []})
        self.stage(
            "glm_revise", {**common, "contract": copy.deepcopy(self.body), "code_refs": ["greet.py"], "responses": []}
        )
        self.stage(
            "astra_finalize",
            {
                **common,
                "contract": {**copy.deepcopy(self.body), "initial_task": copy.deepcopy(self.first)},
                "decisions": [],
            },
        )
        human.evaluate(self.state)
        lifecycle.present(self.state)

    def approve(self):
        self.plan()
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))

    def test_first_task_outside_first_slice_is_rejected_during_planning(self):
        # The whole-product grant includes both files; only the concrete first slice owns greet.py.
        self.body["milestones"][0]["affected_paths"].append("future.py")
        self.first["affected_paths"] = ["future.py"]
        with self.assertRaisesRegex(ValueError, "exceeds active slice writable ownership"):
            self.plan()
        self.assertNotEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertFalse(self.state.get("current_task"))

    def test_reviewed_initial_slice_activates_before_joint_task_without_an_extra_call(self):
        self.approve()
        self.assertEqual("terra", self.state["next_stage"])
        self.assertEqual("S1", self.state["current_task"]["slice_id"])
        self.assertEqual(2, self.state["planning"]["astra_calls"])
        active = progressive.require_active(self.state)
        self.assertEqual("S1", active["definition"]["id"])
        self.assertTrue((self.run_dir / active["artifact"]["path"]).is_file())
        pool = self.state["progressive"]["budget"]["pools"][self.state["progressive"]["active_allowance"]["pool_id"]]
        self.assertEqual((2, 400, 5400), (pool["reviews_used"], pool["seconds_used"], pool["seconds_limit"]))
        self.assertEqual(43200, self.state["progressive"]["budget"]["run_limit"])
        old_planner = json.loads(json.dumps(self.state["stages"][0]))
        old_planner.pop("accounted")
        runner.account_stage(self.state, old_planner)
        self.assertEqual(400, self.state["active_seconds"])

    def test_final_review_artifact_disclosure_is_canonicalized_after_reload(self):
        self.plan()
        output = Path(self.state["planning"]["reports"]["astra_finalize"]["output"])
        saved = json.loads(output.read_text())
        generated = progressive.rules.disclosure(self.proposal, ["C1"], limits=progressive.initial_limits(self.state))
        saved["contract"]["constraints"] = generated["constraints"] + saved["contract"]["constraints"]
        output.write_text(json.dumps(saved))
        report_contract = self.state["planning"]["reports"]["astra_finalize"]["report"]["contract"]
        ordinary = [line for line in report_contract["constraints"] if line not in generated["constraints"]]
        report_contract["constraints"] = generated["constraints"] + ordinary
        loaded = json.loads(output.read_text())
        loaded_contract = progressive._canonical_contract(
            loaded["contract"], self.proposal, ["C1"], progressive.initial_limits(self.state)
        )
        reviewed_contract = progressive._canonical_contract(
            report_contract, self.proposal, ["C1"], progressive.initial_limits(self.state)
        )
        self.assertEqual(loaded_contract, reviewed_contract)
        tampered = copy.deepcopy(reviewed_contract)
        tampered["acceptance_criteria"][0]["description"] = "Changed after review"
        self.assertEqual(
            "$.acceptance_criteria[0].description", progressive._first_difference(loaded_contract, tampered)
        )

    def test_repaired_planner_and_reviewer_activate_from_original_sessions(self):
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.sessions = {"glm_revise": "planner-session", "astra_finalize": "reviewer-session"}
        self.approve()
        self.assertTrue(goals.approved(self.state))
        self.assertEqual("S1", progressive.require_active(self.state)["definition"]["id"])

    def test_sessionless_repaired_planner_and_reviewer_activate(self):
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.approve()
        self.assertTrue(goals.approved(self.state))

    def test_repaired_approval_pins_accepted_bytes_not_the_rejected_original(self):
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.sessions = {"glm_revise": "planner-session", "astra_finalize": "reviewer-session"}
        self.plan()
        self.state = json.loads(json.dumps(self.state))
        for receipt in self.state["report_repair_history"]:
            self.assertEqual(util.file_hash(receipt["repair"]["output"]), receipt["output_hash"])
            self.assertNotEqual(util.file_hash(receipt["original_output"]), receipt["output_hash"])
            original = next(row for row in self.state["stages"] if row["output"] == receipt["original_output"])
            self.assertTrue(original["rejected"])
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertTrue(goals.approved(self.state))
        self.assertEqual("S1", progressive.require_active(self.state)["definition"]["id"])

    def test_repaired_report_tampering_refuses_approval_without_mutating_state_or_source(self):
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.sessions = {"glm_revise": "planner-session", "astra_finalize": "reviewer-session"}
        self.plan()
        saved = copy.deepcopy(self.state)
        source = util.snapshot(self.workspace)
        for stage in self.repair_stages:
            output = Path(saved["planning"]["reports"][stage]["output"])
            accepted = output.read_bytes()
            for change in ("decisions", "constraint", "null", "list", "missing"):
                with self.subTest(stage=stage, change=change):
                    self.state = copy.deepcopy(saved)
                    value = json.loads(accepted)
                    if change == "decisions":
                        value["decisions"] = [
                            {
                                "concern_id": "unreviewed",
                                "decision": "blocked",
                                "rationale": "Changed after acceptance",
                                "acceptance_test": "Not verified",
                                "resolved": False,
                            }
                        ]
                    elif change == "constraint":
                        value["contract"]["constraints"].append("New unreviewed constraint")
                    elif change == "null":
                        value = None
                    elif change == "list":
                        value = []
                    if change == "missing":
                        output.unlink()
                    else:
                        output.write_text(json.dumps(value))
                    try:
                        with self.assertRaisesRegex(ValueError, "accepted repair.*hash"):
                            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
                        self.assertEqual(saved, self.state)
                        self.assertEqual(source, util.snapshot(self.workspace))
                    finally:
                        output.write_bytes(accepted)

    def test_repaired_approval_requires_a_saved_acceptance_hash_without_repinning(self):
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.plan()
        saved = copy.deepcopy(self.state)
        for index in range(len(saved["report_repair_history"])):
            for digest in (None, "", "0" * 64, {"sha256": "0" * 64}, "missing"):
                with self.subTest(index=index, digest=digest):
                    self.state = copy.deepcopy(saved)
                    receipt = self.state["report_repair_history"][index]
                    if digest == "missing":
                        receipt.pop("output_hash", None)
                    else:
                        receipt["output_hash"] = digest
                    before = copy.deepcopy(self.state)
                    with self.assertRaisesRegex(ValueError, "accepted repair.*hash"):
                        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
                    self.assertEqual(before, self.state)

    def test_ordinary_repaired_plan_checks_accepted_hash_before_approval(self):
        self.proposal = {
            "version": 0,
            "needed_because": "",
            "shared_decisions": [],
            "outstanding_criteria": [],
            "done_slices": [],
            "slices": [],
        }
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.plan()
        output = Path(self.state["planning"]["reports"]["astra_finalize"]["output"])
        accepted = output.read_bytes()
        value = json.loads(accepted)
        value["decisions"] = [{"resolved": False}]
        output.write_text(json.dumps(value))
        before = copy.deepcopy(self.state)
        source = util.snapshot(self.workspace)
        with self.assertRaisesRegex(ValueError, "accepted repair.*hash"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state)
        self.assertEqual(source, util.snapshot(self.workspace))
        output.write_bytes(accepted)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertTrue(goals.approved(self.state))

    def test_original_reviewer_session_cannot_be_the_planner_session(self):
        self.sessions = {"glm_revise": "shared-session", "astra_finalize": "shared-session"}
        self.plan()
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state)

    def test_repaired_approval_rejects_missing_or_changed_original_provenance(self):
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.sessions = {"glm_revise": "planner-session", "astra_finalize": "reviewer-session"}
        self.plan()
        saved = copy.deepcopy(self.state)
        for stage in self.repair_stages:
            for change in (
                "receipt",
                "events",
                "role",
                "source",
                "session",
                "output",
                "rejected",
                "stage",
                "expected_session",
                "iteration",
                "changed_files",
                "session_capability",
            ):
                with self.subTest(stage=stage, change=change):
                    self.state = copy.deepcopy(saved)
                    repair = next(row for row in self.state["stages"] if row.get("original_stage") == stage)
                    original = next(row for row in self.state["stages"] if row["stage"] == stage)
                    if change == "receipt":
                        self.state["report_repair_history"] = []
                    elif change == "events":
                        repair["applied_original_events"] = repair["events"]
                    elif change == "role":
                        original["role"] = "other"
                    elif change == "source":
                        original["source_revision"] = "other"
                    elif change == "session":
                        original["thread_id"] = "invented"
                    elif change == "output":
                        original["output"] = str(self.run_dir / "missing-original.json")
                    elif change == "rejected":
                        repair["rejected"] = True
                    elif change == "stage":
                        repair["original_stage"] = "astra_discovery"
                    elif change == "expected_session":
                        original["expected_session"] = "different-session"
                    elif change == "iteration":
                        original["iteration"] += 1
                    elif change == "changed_files":
                        original["changed_files"] = ["greet.py"]
                    else:
                        original.pop("thread_id")
                    before = copy.deepcopy(self.state)
                    with self.assertRaises(ValueError):
                        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
                    self.assertEqual(before, self.state)

    def test_repair_transport_session_cannot_substitute_for_original_reviewer(self):
        self.repair_stages = ("glm_revise", "astra_finalize")
        self.sessions = {"glm_revise": "shared-session", "astra_finalize": "shared-session"}
        self.plan()
        for row in self.state["stages"]:
            if row.get("report_only"):
                row["thread_id"] = row["stage"] + "-transport-session"
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "independent"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state)

    def test_non_review_attempt_time_is_bound_at_launch_and_accounted_once(self):
        self.approve()
        snapshot = util.snapshot(self.workspace)
        record = {
            "stage": "terra",
            "output": str(self.run_dir / "builder.json"),
            "duration_seconds": 300,
            "exit_code": 1,
            "source_revision": snapshot["revision"],
        }
        progressive.admit_attempt(self.state, record, snapshot)
        runner.account_stage(self.state, record)
        runner.account_stage(self.state, record)
        self.state = json.loads(json.dumps(self.state))
        recovered = json.loads(json.dumps(record))
        recovered.pop("accounted")
        runner.account_stage(self.state, recovered)
        self.assertEqual(700, self.state["active_seconds"])
        self.assertEqual(700, self.state["progressive"]["budget"]["run_seconds"])
        task = copy.deepcopy(self.state["current_task"])
        self.state["current_task"]["slice_id"] = "S9"
        with self.assertRaisesRegex(ValueError, "immutable"):
            progressive.check_result_binding(self.state, record, snapshot)
        self.state["current_task"] = task

    def test_source_changed_since_final_review_refuses_approval_atomically(self):
        self.plan()
        (self.workspace / "greet.py").write_text("print('changed after review')\n")
        before = copy.deepcopy(self.state["user_events"])
        with self.assertRaisesRegex(ValueError, "source changed"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state["user_events"])
        self.assertNotEqual("approved", self.state["goal_contract"]["approval_status"])

    def test_reviewed_initial_task_cannot_target_tentative_future_with_same_paths_and_criterion(self):
        future = self.proposal["slices"][1]
        future["checks"][0]["method"] = (
            "python3 -m unittest test_greeting.GreetingTests.test_rejects_empty_and_whitespace_names"
        )
        self.first.update(objective=future["intended_result"], validation_plan=[future["checks"][0]["method"]])
        source = util.snapshot(self.workspace)
        self.plan()
        before = copy.deepcopy(self.state["user_events"])
        with self.assertRaisesRegex(ValueError, "concrete first slice"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state["user_events"])
        self.assertEqual(source, util.snapshot(self.workspace))
        self.assertFalse(self.state.get("current_task"))

    def test_initial_approval_refuses_unreconciled_worker_before_adding_user_authority(self):
        self.plan()
        before = copy.deepcopy(self.state["user_events"])
        self.state["active_stage"] = {"stage": "glm_revise", "output": "uncertain-review", "exit_code": None}
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state["user_events"])
        self.assertFalse(self.state["progressive"].get("active"))

    def test_ordinary_admission_is_a_noop_and_malformed_delegation_fails_closed(self):
        state = {"settings": {}}
        progressive.admit_attempt(state, {}, {})
        self.assertEqual({"settings": {}}, state)
        self.approve()
        self.state["progressive"] = {"version": 999}
        with self.assertRaises(runner.support.Paused):
            progressive.guard_dispatch(self.state, "terra")

    def test_default_local_time_ceiling_stops_next_admission_without_resetting_usage(self):
        self.approve()
        snapshot = util.snapshot(self.workspace)
        record = {
            "stage": "terra",
            "output": str(self.run_dir / "slow-builder.json"),
            "duration_seconds": 5000,
            "exit_code": 1,
            "source_revision": snapshot["revision"],
        }
        progressive.admit_attempt(self.state, record, snapshot)
        runner.account_stage(self.state, record)
        with self.assertRaises(runner.support.Paused):
            progressive.admit_attempt(self.state, {**record, "output": str(self.run_dir / "retry.json")}, snapshot)
        self.assertEqual(5400, self.state["active_seconds"])
        self.assertEqual(5400, self.state["progressive"]["budget"]["run_seconds"])

    def test_configured_initial_limits_are_visibly_reviewed_and_frozen_at_approval(self):
        self.state["settings"]["planning_review_call_limit"] = 3
        self.state["settings"]["milestone_checkpoints"]["max_seconds"] = 6000
        self.state["settings"]["limits"]["max_seconds"] = 45000
        self.approve()
        disclosure = "\n".join(self.state["goal_contract"]["body"]["constraints"])
        self.assertIn("3 plan-review calls", disclosure)
        self.assertIn("6000 stage-seconds", disclosure)
        self.assertIn("45000 whole-run", disclosure)
        frozen = copy.deepcopy(self.state["progressive"]["delegation"])
        spent = self.state["progressive"]["budget"]["run_seconds"]
        progressive.set_explicit_limits(self.state, run_seconds=50000, slice_seconds=7000)
        self.assertEqual(frozen, self.state["progressive"]["delegation"])
        self.assertEqual(spent, self.state["progressive"]["budget"]["run_seconds"])
        self.assertEqual(50000, self.state["progressive"]["budget"]["run_limit"])

    def test_a_validate_task_naming_no_paths_checks_the_active_slice(self):
        # #615: a validate task that names no paths takes what it may check; in a progressive run that is
        # the active slice's paths, not the whole-product milestone's, which the slice guard would refuse.
        self.body["milestones"][0]["affected_paths"] = ["greet.py", "test_greeting.py", "README.md"]
        self.approve()
        decision = {
            "status": "CONTINUE",
            "next_objective": "Check the greeting",
            "affected_paths": [],
            "evidence": [],
            "next_task": {
                "kind": "validate",
                "milestone_id": "M1",
                "requirements": ["Print greeting"],
                "acceptance_criteria": ["C1"],
                "validation_plan": ["python3 -m unittest test_greeting.py"],
            },
        }
        self.assertEqual("validate", lifecycle.assign_task(self.state, decision, util.snapshot(self.workspace)))
        self.assertEqual(["greet.py", "test_greeting.py"], self.state["current_task"]["affected_paths"])

    def test_batch_bypass_and_missing_due_validator_check_fail_closed(self):
        self.approve()
        with self.assertRaises(runner.support.Paused):
            progressive.guard_dispatch(self.state, "orchestrator")
        self.assertNotIn("milestone_ids", self.state["current_task"])
        with self.assertRaisesRegex(ValueError, "omitted cumulative"):
            self.stage("sol", {**self.validation(), "checks": []})

    def test_validator_product_pass_without_fully_verify_is_rejected_not_rewritten(self):
        self.approve()
        report = self.validation(fully=True)
        with self.assertRaisesRegex(ValueError, "Product PASS is not established"):
            self.stage("sol", report)
        self.assertEqual("PASS", report["criterion_results"][0]["status"])
        self.assertFalse(self.state["progressive"].get("history"))
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))

    def test_slice_owner_cannot_label_contribution_only_product_criterion_verified(self):
        self.approve()
        self.stage("sol", self.validation())
        decision = self.checkpoint_decision()
        decision["acceptance_criteria"][0].update(status="verified", evidence="Only local contribution passed")
        with self.assertRaisesRegex(ValueError, "cannot claim verified original criteria"):
            self.stage("astra_review", decision)
        self.assertFalse(self.state["progressive"].get("history"))
        self.assertEqual("unverified", self.state["acceptance_criteria"][0]["status"])

    def test_next_slice_failed_review_refunds_call_once_but_not_time(self):
        self.approve()
        self.stage("sol", self.validation())
        self.stage("astra_review", self.checkpoint_decision())
        snapshot = util.snapshot(self.workspace)
        failed = {
            "stage": "astra_finalize",
            "output": str(self.run_dir / "failed-review.json"),
            "duration_seconds": 25,
            "exit_code": 1,
            "timed_out": True,
            "source_revision": snapshot["revision"],
        }
        progressive.admit_attempt(self.state, failed, snapshot)
        pool_id = self.state["progressive"]["pending_allowance"]["pool_id"]
        self.assertEqual(1, self.state["progressive"]["budget"]["pools"][pool_id]["reviews_used"])
        runner.account_stage(self.state, failed)
        runner.account_stage(self.state, failed)
        pool = self.state["progressive"]["budget"]["pools"][pool_id]
        self.assertEqual((0, 25), (pool["reviews_used"], pool["seconds_used"]))
        rejected = {**failed, "output": str(self.run_dir / "rejected-review.json"), "exit_code": 0, "timed_out": False}
        rejected.pop("accounted")
        progressive.admit_attempt(self.state, rejected, snapshot)
        runner.account_stage(self.state, rejected)
        self.assertEqual(
            (1, 50),
            (
                self.state["progressive"]["budget"]["pools"][pool_id]["reviews_used"],
                self.state["progressive"]["budget"]["pools"][pool_id]["seconds_used"],
            ),
        )

    def test_explicit_product_reapproval_renews_reviewed_authority_without_losing_checks_or_usage(self):
        self.approve()
        self.stage("sol", self.validation())
        self.stage("astra_review", self.checkpoint_decision())
        old_token = goals.token(self.state["goal_contract"])
        old_history = copy.deepcopy(self.state["progressive"]["history"])
        spent = self.state["active_seconds"]
        old_pools = copy.deepcopy(self.state["progressive"]["budget"]["pools"])
        self.body["intended_outcome"] = "Provide the revised greeting and rejection CLI"
        lifecycle.install_draft(self.state, copy.deepcopy(self.body), origin="user_cli_edit")
        self.proposal["slices"] = [copy.deepcopy(self.proposal["slices"][1]), copy.deepcopy(self.proposal["slices"][1])]
        self.proposal["slices"][0].update(tentative=False, depends_on=[])
        self.proposal["slices"][1].update(id="S3", depends_on=["S2"])
        self.proposal["slices"][1]["checks"][0]["id"] = "C"
        self.approve()
        self.assertNotEqual(old_token, goals.token(self.state["goal_contract"]))
        self.assertEqual("S2", progressive.require_active(self.state)["definition"]["id"])
        self.assertEqual(old_history, self.state["progressive"]["history"])
        self.assertEqual({"A", "B"}, {row["id"] for row in self.state["progressive"]["required_checks"]})
        self.assertGreater(self.state["active_seconds"], spent)
        for key, old in old_pools.items():
            new = self.state["progressive"]["budget"]["pools"][key]
            self.assertGreaterEqual(new["seconds_used"], old["seconds_used"])
            self.assertGreaterEqual(new["reviews_used"], old["reviews_used"])
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))

    def validation(self, fully=False):
        command = "python3 -m unittest test_greeting.py"
        return {
            **goal_fixtures.envelope(self.state),
            "verdict": "PASS",
            "findings": [],
            "checks_run": [command],
            "unverified_criteria": [] if fully else ["C1"],
            "checks": [{"command": command, "exit_code": 0, "evidence_ref": "event:check"}],
            "end_to_end_result": {
                "status": "PASS" if fully else "NOT_VERIFIED",
                "summary": "Executed CLI",
                "evidence_refs": ["event:check"] if fully else [],
            },
            "criterion_results": [
                {
                    "id": "C1",
                    "status": "PASS" if fully else "NOT_VERIFIED",
                    "evidence_refs": ["event:check"] if fully else [],
                }
            ],
            "finding_dispositions": [],
        }

    def checkpoint_decision(self):
        return {
            **goal_fixtures.envelope(self.state),
            "status": "CONTINUE",
            "progressive_checkpoint": True,
            "acceptance_criteria": copy.deepcopy(self.state["acceptance_criteria"]),
            "next_objective": "Review the next slice",
            "next_task": {
                "kind": "none",
                "milestone_id": "",
                "requirements": [],
                "acceptance_criteria": [],
                "validation_plan": [],
                "findings": [],
            },
            "findings": [],
            "finding_dispositions": [],
            "agreed_limitations": [],
            "evidence": [],
            "blocker": "",
            "plan": ["Continue the approved slices"],
            "affected_paths": [],
        }

    def test_intermediate_task_proof_does_not_make_unfinished_slice_checks_due(self):
        self.approve()
        command = "python3 -m unittest test_greeting.GreetingTests.test_greets_a_valid_name"
        next_task = {
            "kind": "validate",
            "milestone_id": "M1",
            "requirements": ["Inspect greeting unit"],
            "acceptance_criteria": ["C1"],
            "validation_plan": [command],
            "findings": [],
        }
        decision = {
            **self.checkpoint_decision(),
            "progressive_checkpoint": False,
            "next_objective": "Validate the intermediate greeting unit",
            "next_task": next_task,
            "affected_paths": ["greet.py", "test_greeting.py"],
        }
        self.stage("astra_review", decision)
        local = self.validation()
        local["checks"] = [{"command": command, "exit_code": 0, "evidence_ref": "event:check"}]
        local["checks_run"] = [command]
        self.stage("sol", local)
        self.assertEqual("PASS", self.state["validation"]["verdict"])
        self.assertEqual("NOT_VERIFIED", self.state["validation"]["criterion_results"][0]["status"])
        self.assertEqual("astra_review", self.state["next_stage"])
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))
        with self.assertRaisesRegex(ValueError, "not independently replayed"):
            self.stage("astra_review", self.checkpoint_decision())
        self.assertEqual("S1", progressive.require_active(self.state)["definition"]["id"])
        self.assertFalse(self.state["progressive"].get("history"))

    def begin_next_detail(self):
        self.approve()
        self.stage("sol", self.validation())
        self.stage("astra_review", self.checkpoint_decision())
        proposal = copy.deepcopy(self.proposal)
        proposal["done_slices"] = ["S1"]
        proposal["slices"] = [proposal["slices"][1]]
        proposal["slices"][0]["tentative"] = False
        self.stage(
            "glm_revise",
            {
                "summary": "Detail S2",
                "progressive_proposal": proposal,
                "initial_task": {**self.first, "objective": "Deliver complete CLI"},
            },
        )

    def test_rejected_technical_review_reuses_reserved_pool_without_report_repair(self):
        self.begin_next_detail()
        pool_id = self.state["progressive"]["pending_allowance"]["pool_id"]
        self.stage(
            "astra_finalize",
            {
                "summary": "Improve the concrete task detail",
                "accepted": False,
                "product_changes": False,
                "permission_changes": False,
                "unresolved_product_decisions": False,
            },
        )
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertEqual("detail", self.state["progressive"]["transition"]["phase"])
        self.assertEqual(pool_id, self.state["progressive"]["pending_allowance"]["pool_id"])
        self.assertEqual(1, self.state["progressive"]["budget"]["pools"][pool_id]["reviews_used"])
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual("S1", progressive.require_active(self.state)["definition"]["id"])

    def test_reviewed_material_change_uses_existing_resolver_human_boundary(self):
        self.begin_next_detail()
        token = goals.token(self.state["goal_contract"])
        task_id = self.state["current_task"]["id"]
        self.stage(
            "astra_finalize",
            {
                "summary": "Decide whether to add remote accounts to this local CLI",
                "accepted": False,
                "product_changes": True,
                "permission_changes": False,
                "unresolved_product_decisions": True,
            },
        )
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        human.evaluate(self.state)
        self.assertEqual("goal_change", human.current(self.state)["scope"])
        self.assertEqual(token, goals.token(self.state["goal_contract"]))
        self.assertEqual(task_id, self.state["current_task"]["id"])
        self.assertEqual("S1", progressive.require_active(self.state)["definition"]["id"])
        with self.assertRaises(runner.support.Paused):
            progressive.guard_dispatch(self.state, "terra")

    def test_reviewed_split_siblings_share_the_reserved_pool_and_cumulative_proof(self):
        self.approve()
        self.stage("sol", self.validation())
        self.stage("astra_review", self.checkpoint_decision())
        pool_id = self.state["progressive"]["pending_allowance"]["pool_id"]
        full = "python3 -m unittest test_greeting.py"
        unit = "python3 -m unittest test_greeting.GreetingTests.test_greets_a_valid_name"
        first = copy.deepcopy(self.proposal["slices"][1])
        first.update(id="S2a", tentative=False)
        first["checks"] = [{"id": "B1", "method": unit, "relation": "contributes_to", "criterion_ids": ["C1"]}]
        second = copy.deepcopy(self.proposal["slices"][1])
        second.update(id="S2b", depends_on=["S2a"])
        proposal = {**copy.deepcopy(self.proposal), "done_slices": ["S1"], "slices": [first, second]}
        review = {
            "summary": "Independent split preserves product coverage",
            "accepted": True,
            "product_changes": False,
            "permission_changes": False,
            "unresolved_product_decisions": False,
        }
        self.stage(
            "glm_revise",
            {
                "summary": "Split S2 without new capacity",
                "progressive_proposal": proposal,
                "initial_task": {**self.first, "validation_plan": [unit]},
            },
        )
        self.stage("astra_finalize", review)
        self.assertEqual("S2a", self.state["current_task"]["slice_id"])
        checks = [
            {"command": full, "exit_code": 0, "evidence_ref": "event:full"},
            {"command": unit, "exit_code": 0, "evidence_ref": "event:unit"},
        ]
        local = {**self.validation(), "checks": checks, "checks_run": [full, unit]}
        self.stage("sol", local)
        self.stage("astra_review", self.checkpoint_decision())
        self.assertEqual(pool_id, self.state["progressive"]["pending_allowance"]["pool_id"])
        next_proposal = {**proposal, "done_slices": ["S1", "S2a"], "slices": [{**second, "tentative": False}]}
        self.stage(
            "glm_revise",
            {
                "summary": "Detail retained sibling",
                "progressive_proposal": next_proposal,
                "initial_task": copy.deepcopy(self.first),
            },
        )
        self.stage("astra_finalize", review)
        self.assertEqual("S2b", self.state["current_task"]["slice_id"])
        self.assertEqual(pool_id, self.state["progressive"]["active_allowance"]["pool_id"])
        self.assertEqual(2, len(self.state["progressive"]["budget"]["pools"]))
        self.assertEqual(2, self.state["progressive"]["budget"]["pools"][pool_id]["reviews_used"])
        validated = {**self.validation(fully=True), "checks": checks, "checks_run": [full, unit]}
        validated["criterion_results"][0]["evidence_refs"] = ["event:full", "event:unit"]
        validated["end_to_end_result"]["evidence_refs"] = ["event:full", "event:unit"]
        self.stage("sol", validated)
        self.stage("astra_review", self.checkpoint_decision())
        self.assertTrue(completion.ready(self.state, util.snapshot(self.workspace)))

    def test_final_slice_persists_original_prescribed_product_command_as_current_proof(self):
        original = "python3 -m unittest test_greeting.GreetingTests.test_rejects_empty_and_whitespace_names"
        self.body["acceptance_criteria"][0]["verification_method"] = original
        self.begin_next_detail()
        self.assertNotIn(original, [row["command"] for row in self.state["validation"]["check_replay"]["checks"]])
        self.stage(
            "astra_finalize",
            {
                "summary": "Original product checks remain required",
                "accepted": True,
                "product_changes": False,
                "permission_changes": False,
                "unresolved_product_decisions": False,
            },
        )
        canonical = next(row for row in self.state["progressive"]["required_checks"] if row["method"] == original)
        self.assertTrue(canonical["id"].startswith("contract-"))
        with self.assertRaisesRegex(ValueError, "omitted cumulative"):
            self.stage("sol", self.validation(fully=True))
        report = self.validation(fully=True)
        report["checks"].append({"command": original, "exit_code": 0, "evidence_ref": "event:product"})
        report["checks_run"].append(original)
        report["criterion_results"][0]["evidence_refs"].append("event:product")
        report["end_to_end_result"]["evidence_refs"].append("event:product")
        self.stage("sol", report)
        self.stage("astra_review", self.checkpoint_decision())
        proof = self.state["progressive"]["completion_proof"]
        self.assertIn(canonical["id"], proof["results"])
        self.assertEqual(
            util.snapshot(self.workspace)["revision"], proof["results"][canonical["id"]]["source_revision"]
        )
        self.assertTrue(completion.ready(self.state, util.snapshot(self.workspace)))

    def test_initial_revise_can_replace_exact_generated_map_and_limits_without_rewriting_product(self):
        common = {
            "summary": "Initial plan",
            "contract_changes": [],
            "conflict_resolutions": [],
            "requirement_trace": [],
            "progressive_proposal": copy.deepcopy(self.proposal),
        }
        self.stage(
            "astra_discovery",
            {
                **common,
                "contract": copy.deepcopy(self.body),
                "code_refs": ["greet.py"],
                "alternatives": [],
                "uncertainties": [],
            },
        )
        original = copy.deepcopy(self.state["goal_contract"]["body"])
        self.stage("astra_challenge", {"summary": "Detail the next slice more concretely", "concerns": []})
        self.proposal["slices"][1]["intended_result"] = "Reject all invalid names and preserve greeting"
        self.state["settings"]["planning_review_call_limit"] = 3
        common["progressive_proposal"] = copy.deepcopy(self.proposal)
        self.stage("glm_revise", {**common, "contract": original, "code_refs": ["greet.py"], "responses": []})
        self.stage(
            "astra_finalize",
            {
                **common,
                "contract": {
                    **copy.deepcopy(self.state["goal_contract"]["body"]),
                    "initial_task": copy.deepcopy(self.first),
                },
                "decisions": [],
            },
        )
        human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(self.proposal, self.state["progressive"]["initial_plan"]["proposal"])
        self.assertEqual(3, self.state["progressive"]["delegation"]["limits"]["slice_review_calls"])
        self.assertEqual(self.body["required_behaviors"], self.state["goal_contract"]["body"]["required_behaviors"])

    def test_initial_revise_can_drop_only_known_runner_disclosure_and_return_to_ordinary(self):
        common = {"summary": "Plan", "contract_changes": [], "conflict_resolutions": [], "requirement_trace": []}
        self.stage(
            "astra_discovery",
            {
                **common,
                "contract": copy.deepcopy(self.body),
                "progressive_proposal": copy.deepcopy(self.proposal),
                "code_refs": ["greet.py"],
                "alternatives": [],
                "uncertainties": [],
            },
        )
        prior = copy.deepcopy(self.state["goal_contract"]["body"])
        self.stage("astra_challenge", {"summary": "An ordinary bounded plan suffices", "concerns": []})
        self.stage("glm_revise", {**common, "contract": prior, "code_refs": ["greet.py"], "responses": []})
        self.stage(
            "astra_finalize",
            {
                **common,
                "contract": {
                    **copy.deepcopy(self.state["goal_contract"]["body"]),
                    "initial_task": copy.deepcopy(self.first),
                },
                "decisions": [],
            },
        )
        human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertNotIn("progressive", self.state)
        self.assertNotIn("slice_id", self.state["current_task"])

    def test_empty_tail_with_only_contributions_details_full_original_proof_under_spent_lineage(self):
        self.proposal["slices"][1]["checks"][0]["relation"] = "contributes_to"
        self.begin_next_detail()
        token = goals.token(self.state["goal_contract"])
        self.stage(
            "astra_finalize",
            {
                "summary": "Second contribution is within the original goal",
                "accepted": True,
                "product_changes": False,
                "permission_changes": False,
                "unresolved_product_decisions": False,
            },
        )
        pool_id = self.state["progressive"]["active_allowance"]["pool_id"]
        self.stage("sol", self.validation())
        self.stage("astra_review", self.checkpoint_decision())
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertEqual(pool_id, self.state["progressive"]["pending_allowance"]["pool_id"])
        self.assertEqual(["C1"], self.state["progressive"]["transition"]["remaining_criteria"])
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))
        proposal = {
            **copy.deepcopy(self.proposal),
            "done_slices": ["S1", "S2"],
            "slices": [
                {
                    "id": "S3",
                    "intended_result": "Validate the original product end to end",
                    "criterion_ids": ["C1"],
                    "paths": ["greet.py", "test_greeting.py"],
                    "depends_on": ["S2"],
                    "tentative": False,
                    "checks": [
                        {
                            "id": "G",
                            "method": "python3 -m unittest test_greeting.py",
                            "relation": "fully_verify",
                            "criterion_ids": ["C1"],
                        }
                    ],
                }
            ],
        }
        self.stage(
            "glm_revise",
            {
                "summary": "Detail the still-open product proof",
                "progressive_proposal": proposal,
                "initial_task": {**self.first, "kind": "validate"},
            },
        )
        self.stage(
            "astra_finalize",
            {
                "summary": "Independently reviewed full proof within the existing grant",
                "accepted": True,
                "product_changes": False,
                "permission_changes": False,
                "unresolved_product_decisions": False,
            },
        )
        self.assertEqual(token, goals.token(self.state["goal_contract"]))
        self.assertEqual(pool_id, self.state["progressive"]["active_allowance"]["pool_id"])
        self.assertEqual(2, self.state["progressive"]["budget"]["pools"][pool_id]["reviews_used"])
        self.assertEqual(2, len(self.state["progressive"]["budget"]["pools"]))
        self.stage("sol", self.validation(fully=True))
        decision = self.checkpoint_decision()
        decision.update(
            status="COMPLETE",
            progressive_checkpoint=False,
            acceptance_criteria=[
                {**row, "status": "verified", "evidence": "Current full original product proof"}
                for row in self.state["acceptance_criteria"]
            ],
        )
        self.stage("astra_review", decision)
        self.assertEqual("TASK_COMPLETE", self.state["status"])

    def test_local_checkpoint_restart_and_reviewed_s2_retain_product_and_cumulative_proof(self):
        self.approve()
        self.stage("sol", self.validation(), seconds=3000)
        self.stage("astra_review", self.checkpoint_decision(), seconds=100)
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))
        self.assertNotEqual("verified", self.state["acceptance_criteria"][0]["status"])
        original_contract = copy.deepcopy(self.state["goal_contract"])
        path = self.run_dir / "state.json"
        path.write_text(json.dumps(self.state))
        self.state = json.loads(path.read_text())
        proposal = copy.deepcopy(self.proposal)
        proposal["done_slices"] = ["S1"]
        proposal["slices"] = [proposal["slices"][1]]
        proposal["slices"][0]["tentative"] = False
        self.stage(
            "glm_revise",
            {
                "summary": "Detail S2",
                "progressive_proposal": proposal,
                "initial_task": {**self.first, "objective": "Deliver complete CLI"},
            },
            seconds=100,
        )
        self.stage(
            "astra_finalize",
            {
                "summary": "Exact candidate accepted",
                "accepted": True,
                "product_changes": False,
                "permission_changes": False,
                "unresolved_product_decisions": False,
            },
            seconds=100,
        )
        self.assertEqual("S2", self.state["current_task"]["slice_id"])
        self.assertEqual(original_contract, self.state["goal_contract"])
        self.stage("sol", self.validation(fully=True), seconds=3000)
        self.stage("astra_review", self.checkpoint_decision(), seconds=100)
        self.assertTrue(completion.ready(self.state, util.snapshot(self.workspace)))
        self.assertGreater(self.state["active_seconds"], 5400)
        self.assertLess(self.state["active_seconds"], 43200)
        pools = list(self.state["progressive"]["budget"]["pools"].values())
        self.assertEqual(2, len(pools))
        self.assertTrue(all(pool["seconds_used"] < 5400 for pool in pools))
        self.assertEqual({"A", "B"}, set(self.state["progressive"]["completion_proof"]["results"]))
        decision = self.checkpoint_decision()
        decision.update(
            status="COMPLETE",
            progressive_checkpoint=False,
            acceptance_criteria=[
                {**row, "status": "verified", "evidence": "Current independent full CLI proof"}
                for row in self.state["acceptance_criteria"]
            ],
        )
        self.stage("astra_review", decision)
        self.assertEqual("TASK_COMPLETE", self.state["status"])
