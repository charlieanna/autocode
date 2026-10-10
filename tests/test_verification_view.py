"""Coverage is recorded before checking; a saved PASS is not current proof."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_verification_inspection as inspection
import autocode_verification_view as view
from autocode_util import file_hash


def fixture():
    criteria = [
        {"id": "C1", "criterion": "Send message", "verification_method": "Run the send flow", "human_review": False},
        {
            "id": "C2",
            "criterion": "Readable error",
            "verification_method": "Inspect failed send",
            "human_review": False,
        },
        {"id": "C3", "criterion": "Keep history", "verification_method": "Reload the chat", "human_review": False},
    ]
    return {
        "status": "PAUSED_REQUESTED",
        "current_task": {"id": "task-one"},
        "criteria_revision": 3,
        "goal_contract": {"revision": 2, "hash": "plan", "body": {"acceptance_criteria": criteria}},
        "acceptance_criteria": copy.deepcopy(criteria),
        "validation": {
            "contract_revision": 2,
            "contract_hash": "plan",
            "task_id": "task-one",
            "source_revision": "source-one",
            "criteria_revision": 3,
            "criterion_results": [
                {"id": "C1", "status": "PASS", "evidence_refs": ["proof.txt"]},
                {"id": "C2", "status": "FAIL", "evidence_refs": ["proof.txt"]},
            ],
        },
    }


class VerificationViewTests(unittest.TestCase):
    def test_plan_methods_are_visible_before_any_check_and_are_not_passes(self):
        state = fixture()
        del state["validation"]
        result = view.project(state)
        self.assertEqual("not_recorded", result["freshness"])
        self.assertEqual(["unchecked"] * 3, [row["state"] for row in result["coverage"]])
        self.assertEqual("Reload the chat", result["coverage"][2]["verification_method"])

    def test_saved_pass_is_unchecked_until_actual_inspection(self):
        result = view.project(fixture())
        self.assertEqual("not_inspected", result["freshness"])
        self.assertEqual(["unchecked"] * 3, [row["state"] for row in result["coverage"]])
        self.assertEqual("PASS", result["coverage"][0]["recorded_status"])

    def test_failed_and_unchecked_are_both_visible_on_current_report(self):
        state = fixture()
        before = copy.deepcopy(state)
        result = view.project(state, current_revision="source-one", evidence_matches=True)
        self.assertEqual("current", result["freshness"])
        self.assertEqual(["checked", "failed", "unchecked"], [row["state"] for row in result["coverage"]])
        result["coverage"][0]["evidence_refs"].append("invented")
        self.assertEqual(before, state)

    def test_source_plan_task_checklist_pins_and_live_attempt_invalidate_current_passes(self):
        changes = [
            lambda s: s["validation"].update(source_revision="old"),
            lambda s: s["validation"].pop("source_revision"),
            lambda s: s["validation"].update(contract_hash="old"),
            lambda s: s["validation"].update(contract_revision=1),
            lambda s: s["validation"].update(task_id="other"),
            lambda s: s.update(criteria_revision=4),
            lambda s: s.update(active_stage={"stage": "terra"}),
            lambda s: s.update(active_runner_check={"stage": "regression_proof"}),
        ]
        for change in changes:
            state = fixture()
            change(state)
            with self.subTest(state=state):
                result = view.project(state, current_revision="source-one", evidence_matches=True)
                self.assertEqual("stale_or_unverified", result["freshness"])
                self.assertEqual(["unchecked"] * 3, [row["state"] for row in result["coverage"]])
        for intact in (False, None):
            result = view.project(fixture(), current_revision="source-one", evidence_matches=intact)
            self.assertEqual("unchecked", result["coverage"][0]["state"])

    def test_duplicate_or_evidenceless_results_never_turn_green(self):
        state = fixture()
        state["validation"]["criterion_results"].append(copy.deepcopy(state["validation"]["criterion_results"][0]))
        state["validation"]["criterion_results"][1]["evidence_refs"] = []
        result = view.project(state, current_revision="source-one", evidence_matches=True)
        self.assertEqual(["unchecked"] * 3, [row["state"] for row in result["coverage"]])

    def test_human_acceptance_is_distinct_from_a_model_pass(self):
        state = fixture()
        state["goal_contract"]["body"]["acceptance_criteria"][0]["human_review"] = True
        args = {"current_revision": "source-one", "evidence_matches": True}
        self.assertEqual("unchecked", view.project(state, **args)["coverage"][0]["state"])
        accepted = view.project(state, **args, accepted_human_ids=["C1"])
        self.assertEqual("checked", accepted["coverage"][0]["state"])
        self.assertFalse(accepted["coverage"][0]["human_acceptance_pending"])

    def test_validation_job_can_be_done_by_the_plan_reviewer(self):
        state = fixture()
        state["validation"]["reviewer_role"] = "astra"
        result = view.project(state, current_revision="source-one", evidence_matches=True)
        self.assertEqual("checked", result["coverage"][0]["state"])


class InspectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.proof = self.root / "proof.txt"
        self.proof.write_text("command output")
        self.state = fixture()
        self.state["validation"]["evidence_hashes"] = {str(self.proof): file_hash(self.proof)}

    def inspect(self, **kwargs):
        return inspection.inspect(
            self.state,
            self.root,
            snapshot=kwargs.get("snapshot", lambda _: {"revision": "source-one"}),
            read_state=kwargs.get("read_state", lambda: copy.deepcopy(self.state)),
        )

    def test_completion_snapshot_is_rechecked_and_changed_acceptance_is_not_current(self):
        calls = []

        def snapshot(_):
            calls.append("source")
            return {"revision": "source-one"}

        result = inspection.inspect(
            self.state,
            self.root,
            snapshot=snapshot,
            read_state=lambda: self.state,
            initial_snapshot={"revision": "source-one"},
        )
        self.assertEqual("current", result["freshness"])
        self.assertEqual(["source"], calls)
        latest = copy.deepcopy(self.state)
        latest["answers"] = {"acceptance": {"text": "Changed after inspection began"}}
        result = inspection.inspect(
            self.state,
            self.root,
            snapshot=snapshot,
            read_state=lambda: latest,
            initial_snapshot={"revision": "source-one"},
        )
        self.assertEqual("unavailable", result["freshness"])

    def test_actual_evidence_bytes_are_checked_without_writes(self):
        before = json.dumps(self.state, sort_keys=True)
        self.assertEqual("current", self.inspect()["freshness"])
        self.proof.write_text("different command output")
        self.assertEqual("stale_or_unverified", self.inspect()["freshness"])
        self.assertEqual(before, json.dumps(self.state, sort_keys=True))
        self.proof.unlink()
        self.assertEqual("unavailable", self.inspect()["freshness"])

    def test_source_or_record_changing_during_inspection_is_not_current(self):
        values = iter(["source-one", "source-two"])
        self.assertEqual("unavailable", self.inspect(snapshot=lambda _: {"revision": next(values)})["freshness"])
        latest = copy.deepcopy(self.state)
        latest["current_task"]["id"] = "next-task"
        self.assertEqual("unavailable", self.inspect(read_state=lambda: latest)["freshness"])

    def test_only_polling_timestamp_changes_leave_the_same_identity(self):
        latest = {**self.state, "updated_at": "later"}
        self.assertEqual("current", self.inspect(read_state=lambda: latest)["freshness"])

    def test_git_read_error_is_unavailable_not_a_status_crash(self):
        def broken(_):
            raise subprocess.CalledProcessError(1, ["git", "ls-files"])

        self.assertEqual("unavailable", self.inspect(snapshot=broken)["freshness"])

    def test_outside_symlink_and_missing_legacy_pins_are_unverified(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "private.txt"
            outside.write_text("outside the task")
            self.state["validation"]["evidence_hashes"] = {str(outside): file_hash(outside)}
            self.assertEqual("stale_or_unverified", self.inspect()["freshness"])
            link = self.root / "link"
            link.symlink_to(outside)
            self.state["validation"]["evidence_hashes"] = {str(link): file_hash(outside)}
            self.assertEqual("stale_or_unverified", self.inspect()["freshness"])
        self.state["validation"].pop("evidence_hashes")
        self.assertEqual("stale_or_unverified", self.inspect()["freshness"])

    def test_evidence_changed_during_source_walk_is_not_current(self):
        calls = []

        def source(_):
            calls.append(1)
            if len(calls) == 2:
                self.proof.write_text("replaced during inspection")
            return {"revision": "source-one"}

        self.assertEqual("stale_or_unverified", self.inspect(snapshot=source)["freshness"])
