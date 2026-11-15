"""Accepted writer changes are task work and still require independent review."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_builder_failure as failure
import autocode_recovery_history as recovery_history
import autocode_retained_work as retained
import autocode_util as util
import autopilot


class RetainedTestWriterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()

    def snapshot(self, name, files, revision):
        path = self.root / name
        util.atomic_json(path, {"head": "head", "files": files, "revision": revision})
        return str(path)

    def fixture(self, product="src/value.py", test="tests/test_value.py"):
        source = self.workspace / product
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("retained implementation\n")
        target = self.workspace / test
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("independently authored test_value\n")
        archive = self.root / "accepted-test"
        archive.write_bytes(target.read_bytes())
        files = {product: util.file_hash(source), test: util.file_hash(target)}
        before = self.snapshot("writer.before", {product: files[product]}, "assigned")
        after = self.snapshot("writer.after", files, "written")
        contract = {
            "task_id": "goal",
            "revision": 1,
            "body": {
                "open_blocking_questions": [],
                "acceptance_criteria": [
                    {"id": "C1", "criterion": "Value works", "verification_method": "test: test_value"}
                ],
            },
        }
        contract["hash"] = util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": "r1:" + contract["hash"]}
        contract.update(approval_status="approved", approval_event=approval)
        output = self.root / "writer.json"
        output.write_text(json.dumps({"tests": [{"case_id": "C1", "file": test, "test_name": "test_value"}]}))
        writer = {
            "stage": "write_acceptance_tests",
            "iteration": 1,
            "task_id": "task",
            "started_at": "writer",
            "contract_hash": contract["hash"],
            "contract_revision": 1,
            "before_ref": before,
            "after_ref": after,
            "source_revision": "written",
            "changed_files": [test],
            "output": str(output),
            "exit_code": 0,
            "accounted": True,
        }
        builder = {
            "stage": "builder",
            "iteration": 1,
            "task_id": "task",
            "started_at": "builder",
            "contract_hash": contract["hash"],
            "contract_revision": 1,
            "before_ref": after,
            "after_ref": self.snapshot("builder.after", files, "written"),
            "source_revision": "written",
            "changed_files": [],
            "output": str(self.root / "builder.json"),
            "exit_code": 0,
            "accounted": True,
        }
        Path(builder["output"]).write_text(json.dumps({"changed_files": [], "summary": "Checks pass"}))
        state = {
            "workspace": str(self.workspace),
            "goal_contract": contract,
            "user_events": [approval],
            "current_task": {
                "id": "task",
                "kind": "implement",
                "contract_hash": contract["hash"],
                "milestone_id": "M1",
                "source_revision": "assigned",
                "affected_paths": [product, test],
                "acceptance_criteria": ["C1"],
            },
            "stages": [writer],
            "acceptance_tests": {
                "receipts": {
                    "accepted": {
                        "contract_hash": contract["hash"],
                        "source_revision": "written",
                        "case_ids": ["C1"],
                        "output": str(output),
                        "files": {test: {"sha256": files[test], "archive": str(archive)}},
                        "results": {"C1": "passes candidate; fails original reference"},
                    }
                }
            },
        }
        return state, builder, writer

    def receipt(self, state):
        return state["acceptance_tests"]["receipts"]["accepted"]

    def test_an_accepted_writer_can_supply_the_current_tasks_only_source_change(self):
        for product, test in (
            ("src/value.py", "tests/test_value.py"),
            ("lib/value.js", "checks/value.spec.js"),
            ("service/value.go", "service/value_test.go"),
        ):
            with self.subTest(product=product):
                state, builder, writer = self.fixture(product, test)
                before = copy.deepcopy(state)
                result = retained.fresh_candidate(state, builder, "written")
                self.assertIsNotNone(result, "accepted independent source must reach fresh verification")
                self.assertEqual("written", result["source_revision"])
                self.assertEqual([test], result["retained_paths"])
                self.assertEqual(before, state, "classification alone grants no pass or retry")

    def test_red_tdd_input_and_missing_or_conflicting_observations_are_not_progress(self):
        for verdict in ("fails", "not run", "passes", None):
            with self.subTest(verdict=verdict):
                state, builder, _ = self.fixture()
                self.receipt(state)["results"]["C1"] = verdict
                self.assertIsNone(retained.fresh_candidate(state, builder, "written"))
        state, builder, _ = self.fixture()
        other = copy.deepcopy(self.receipt(state))
        other["results"]["C1"] = "fails"
        state["acceptance_tests"]["receipts"]["conflict"] = other
        self.assertIsNone(retained.fresh_candidate(state, builder, "written"))

    def test_writer_identity_completion_scope_and_approval_are_required(self):
        changes = (
            lambda s, b, w: w.update(task_id="foreign"),
            lambda s, b, w: w.update(contract_hash="stale"),
            lambda s, b, w: w.update(contract_revision=0),
            lambda s, b, w: w.update(exit_code=1),
            lambda s, b, w: w.update(accounted=False),
            lambda s, b, w: w.update(rejected=True),
            lambda s, b, w: w.update(report_only=True),
            lambda s, b, w: b.update(interrupted=True),
            lambda s, b, w: b.update(contract_hash="stale"),
            lambda s, b, w: s["current_task"].update(source_revision="other"),
            lambda s, b, w: s["current_task"].update(affected_paths=["src/value.py"]),
            lambda s, b, w: s["goal_contract"].update(approval_status="draft"),
            lambda s, b, w: s["acceptance_tests"].update(receipts={}),
            lambda s, b, w: self.receipt(s).update(source_revision="stale"),
            lambda s, b, w: self.receipt(s).update(output="other-writer.json"),
        )
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                state, builder, writer = self.fixture()
                change(state, builder, writer)
                self.assertIsNone(retained.fresh_candidate(state, builder, "written"))

    def test_current_and_archived_bytes_and_unchanged_head_are_required(self):
        for target in ("current", "archive", "snapshot", "head"):
            with self.subTest(target=target):
                state, builder, writer = self.fixture()
                entry = self.receipt(state)["files"]["tests/test_value.py"]
                if target in ("current", "archive"):
                    path = self.workspace / "tests/test_value.py" if target == "current" else Path(entry["archive"])
                    path.write_text("changed bytes")
                else:
                    start = util.read_object(writer["before_ref"])
                    start["head" if target == "head" else "revision"] = "changed"
                    util.atomic_json(writer["before_ref"], start)
                self.assertIsNone(retained.fresh_candidate(state, builder, "written"))

    def test_only_current_writer_cases_need_current_observations_without_borrowing_a_pass(self):
        state, builder, _ = self.fixture()
        # Other criteria already had receipts in previous revisions. No verdict
        # or proof is transferred: every criterion still faces fresh review.
        state["goal_contract"]["body"]["acceptance_criteria"].append(
            {"id": "C2", "criterion": "Other behavior", "verification_method": "test: test_other"}
        )
        contract = state["goal_contract"]
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        contract["approval_event"]["token"] = "r1:" + contract["hash"]
        state["current_task"]["contract_hash"] = contract["hash"]
        for record in (*state["stages"], builder):
            record["contract_hash"] = contract["hash"]
        self.receipt(state)["contract_hash"] = contract["hash"]
        self.assertIsNotNone(retained.fresh_candidate(state, builder, "written"))
        self.receipt(state)["case_ids"] = ["unknown"]
        self.assertIsNone(retained.fresh_candidate(state, builder, "written"))

    def test_multiple_writer_revisions_must_form_one_task_owned_source_chain(self):
        state, builder, writer = self.fixture()
        first = copy.deepcopy(writer)
        middle = util.read_object(writer["before_ref"])
        middle["files"]["tests/test_value.py"] = "first-test-version"
        middle["revision"] = "intermediate"
        middle_ref = str(self.root / "intermediate.json")
        util.atomic_json(middle_ref, middle)
        first.update(after_ref=middle_ref, source_revision="intermediate", output="older-writer.json")
        writer["before_ref"] = middle_ref
        state["stages"].insert(0, first)
        self.assertIsNotNone(retained.fresh_candidate(state, builder, "written"))
        broken = copy.deepcopy(middle)
        broken["files"]["src/value.py"] = "unowned-change-between-writers"
        writer["before_ref"] = str(self.root / "broken-chain.json")
        util.atomic_json(writer["before_ref"], broken)
        self.assertIsNone(retained.fresh_candidate(state, builder, "written"))

    def no_progress(self, *, held=False):
        state, builder, _ = self.fixture()
        state["stages"].append(builder)
        state.update(no_progress_reports=[util.read_object(builder["output"])], no_progress_batches=3)
        bound = failure.evidence(state, builder)
        reason = "Builder completed an implementation attempt without source changes"
        if held:
            failure.hold(state, bound, "PAUSED_BUILDER_CLASSIFICATION", reason)
        else:
            self.assertTrue(failure.queue(state, bound, reason, enabled=True))
        return state, builder

    def test_builder_acceptance_hands_writer_progress_to_review_once(self):
        state, builder, _ = self.fixture()
        events = self.root / "builder.jsonl"
        events.write_text("")
        builder["events"] = str(events)
        value = {
            "changed_files": [],
            "evidence_refs": ["tests/test_value.py"],
            "summary": "Existing implementation passes",
        }
        with (
            patch.object(autopilot.source_scope, "snapshot", return_value={"revision": "written"}),
            patch.object(autopilot, "route_builder_failure") as route,
        ):
            autopilot.apply_build_result(None, state, value, builder, self.workspace, self.root)
            route.assert_not_called()
            self.assertEqual("tester", state["next_stage"])
            self.assertEqual(["tests/test_value.py"], state["implementation"]["changed_files"])
            self.assertNotIn("validation", state)
            self.assertNotIn("regression_proof", state)
            # A failed fresh review cannot submit the same no-edit Builder again.
            autopilot.apply_build_result(None, state, value, builder, self.workspace, self.root)
            route.assert_called_once()
            self.assertEqual(1, state["no_progress_batches"])

    def test_saved_pending_or_held_incident_reaches_fresh_review_without_a_builder_or_pass(self):
        for held in (False, True):
            with self.subTest(held=held):
                state, builder = self.no_progress(held=held)
                before = copy.deepcopy(state)
                with patch.object(autopilot.source_scope, "snapshot", return_value={"revision": "written"}):
                    self.assertTrue(autopilot.recover_retained_candidate(state, self.workspace, bound_only=True))
                    self.assertFalse(autopilot.recover_retained_candidate(state, self.workspace, bound_only=True))
                self.assertEqual("tester", state["next_stage"])
                self.assertEqual(["tests/test_value.py"], state["changed_files"])
                self.assertEqual(0, state["no_progress_batches"])
                self.assertNotIn("validation", state)
                self.assertNotIn("regression_proof", state)
                self.assertNotIn("builder_failure_hold", state)
                self.assertNotIn("stuck_investigation", state)
                for key in ("stages", "goal_contract", "current_task", "no_progress_reports", "acceptance_tests"):
                    self.assertEqual(before[key], state[key], key)
                self.assertIsNone(retained.fresh_candidate(state, builder, "written"), "writer/source is consumed once")
                recovery = recovery_history.project(state)[-1]
                self.assertEqual("candidate_retained", recovery["kind"])
                self.assertIn("fresh independent verification", recovery["action"])
                self.assertEqual([], failure.classified(state))

    def test_saved_recovery_cannot_override_a_real_failure_or_unreconciled_work(self):
        for field in ("reason", "launched", "in_force", "active_stage", "pending_report_repair", "uncertain_artifacts"):
            with self.subTest(field=field):
                state, _ = self.no_progress()
                request = state["stuck_investigation"]
                if field == "reason":
                    request[field] = "Builder failed a required check"
                elif field == "launched":
                    state["stages"].append({"stage": "investigate_stuck", "started_at": request["requested_at"]})
                elif field == "in_force":
                    request[field] = True
                else:
                    state[field] = {"pending": True}
                before = copy.deepcopy(state)
                with patch.object(autopilot.source_scope, "snapshot", return_value={"revision": "written"}):
                    self.assertFalse(autopilot.recover_retained_candidate(state, self.workspace, bound_only=True))
                self.assertEqual(before, state)

    def test_saved_recovery_refuses_changed_failure_evidence(self):
        state, builder = self.no_progress()
        Path(builder["output"]).write_text("edited report")
        with patch.object(autopilot.source_scope, "snapshot", return_value={"revision": "written"}):
            with self.assertRaisesRegex(util.Paused, "evidence changed"):
                autopilot.recover_retained_candidate(state, self.workspace, bound_only=True)
