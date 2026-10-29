"""An exact saved correction refreshes proof, never silently changes its obligations."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_bug_job as bug_job
import autocode_diagnosis_refresh as refresh
import autocode_goal_lifecycle as lifecycle
import autocode_run_view as run_view
import autocode_source_log as source_log
import autocode_test_cases as cases
import autocode_util as util

from .test_bug_job import diagnosis, state_for


class DiagnosisRefreshTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Path(folder.name)
        self.report = diagnosis(
            test_cases=[
                {"id": "T1", "given": "11 items, size 5", "when": "count pages", "then": "3 pages", "kind": "restore"},
                {
                    "id": "T4",
                    "given": "items=[], size=5",
                    "when": "page([], 1, 5)",
                    "then": "IndexError",
                    "kind": "restore",
                },
            ]
        )
        output = self.workspace / "investigation.json"
        output.write_text(json.dumps(self.report))
        self.snapshot = {"head": "initial", "files": {"epp/client.py": "abc"}, "revision": "source-1"}
        snapshot = self.workspace / "after.json"
        snapshot.write_text(json.dumps(self.snapshot))
        self.state = state_for(str(self.workspace))
        self.state["task_id"] = "task"
        bug_job.apply(self.state, self.report, {"output": str(output), "source_revision": "source-1"}, self.workspace)
        self.state["stages"] = [
            {
                "stage": "investigate_bug",
                "output": str(output),
                "after_ref": str(snapshot),
                "source_revision": "source-1",
                "finished_at": "2026-10-29T01:00:00+00:00",
            }
        ]
        source_log.seed(self.state)
        self.feedback = {
            "id": "feedback-correction",
            "actor": "user_cli",
            "kind": "brief_feedback",
            "at": "2026-10-29T02:00:00+00:00",
            "text": "Preserve page([],1,5)==[].",
        }
        source_log.save_feedback(self.state, self.feedback)
        self.criterion = {
            "id": "AC4",
            "criterion": "page([],1,5) returns []",
            "human_review": False,
            "verification_method": "guard: test_t4_empty_page",
            "sources": [{"source_id": "feedback:feedback-correction", "quote": self.feedback["text"]}],
        }
        contract = {
            "task_id": "task",
            "revision": 2,
            "body": {"task_kind": "bugfix", "acceptance_criteria": [self.criterion], "open_blocking_questions": []},
            "approval_status": "draft",
            "origin": "glm_draft",
            "declared_changes": [
                {
                    "item": "AC4",
                    "change": "reworded",
                    "basis": "user_feedback",
                    "answer_id": self.feedback["id"],
                    "replacement": "page([],1,5) returns []",
                }
            ],
        }
        self.seal(contract)
        self.state["contract_history"] = [copy.deepcopy(contract)]
        current = copy.deepcopy(contract)
        current.update(revision=3, declared_changes=[])
        current["body"]["acceptance_criteria"][0]["current_behavior"] = "Runner-derived prior observation"
        self.seal(current)
        self.state["goal_contract"] = current
        self.state["requirements_handoff"] = {"report": {"old_expected_behavior": "IndexError"}}
        self.state["planning"] = {"final_token": "old-reviewed-token"}
        self.state.update(status="AWAITING_GOAL_APPROVAL", next_stage="astra_plan")
        patcher = mock.patch.object(refresh.util, "snapshot", return_value=self.snapshot)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def seal(contract):
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})

    def corrected(self):
        value = copy.deepcopy(self.report)
        value["test_cases"][1].update(kind="preserve", then="[]")
        value.update(
            invariant="Ceiling division; preserve empty page 1",
            fix_plan=["Change page_count only"],
            root_cause="Floor division drops the tail; empty page 1 is not a bug",
        )
        return value

    def queued(self):
        pending = refresh.prepare(self.state, self.workspace)
        refresh.queue(self.state, pending)
        return pending

    def test_history_receipt_pins_newer_feedback_without_rewriting_old_requirements_or_diagnosis(self):
        previous = copy.deepcopy(self.state["investigation"])
        self.assertTrue(lifecycle.refresh_diagnosis(self.state, self.workspace))
        packet = bug_job.packet(self.state)
        correction = packet["diagnosis_correction"]
        self.assertEqual(self.feedback, correction["feedback"])
        self.assertEqual("IndexError", correction["historical_requirements"]["old_expected_behavior"])
        self.assertEqual("T4", correction["cases"][0]["case_id"])
        self.assertEqual("preserve", correction["cases"][0]["kind"])
        self.assertEqual(previous, self.state["investigation"])
        self.assertEqual("restore", cases.proof_cases(self.state)[1]["kind"])
        self.assertEqual("investigate_bug", self.state["next_stage"])
        self.assertNotIn("final_token", self.state["planning"])
        self.assertEqual(1, len(self.state[refresh.KEY]["attempts"]))
        self.assertFalse(lifecycle.refresh_diagnosis(self.state, self.workspace))
        self.assertEqual(correction, bug_job.repair_context(self.state)["diagnosis_correction"])
        self.assertEqual(self.report, packet["prior_investigation"])
        self.assertEqual(
            {"feedback", "source", "contract_token", "corrected_contract", "historical_requirements", "cases"},
            set(correction),
        )
        self.assertNotIn("source_snapshot", json.dumps(correction))
        self.assertNotIn("output_hash", packet["prior_investigation"])
        self.assertEqual(self.report, bug_job.repair_context(self.state)["prior_investigation"])

    def test_admission_keeps_raw_history_then_replaces_effective_cases(self):
        pending = self.queued()
        original = Path(pending["diagnosis"]["output"]).read_bytes()
        value = self.corrected()
        output = self.workspace / "refreshed.json"
        output.write_text(json.dumps(value))
        bug_job.apply(self.state, value, {"output": str(output), "source_revision": "source-1"}, self.workspace)
        self.assertEqual(original, Path(pending["diagnosis"]["output"]).read_bytes())
        self.assertEqual(value["test_cases"], cases.diagnosis_cases(self.state))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertNotIn("pending", self.state[refresh.KEY])
        self.assertEqual(pending["diagnosis"], self.state[refresh.KEY]["history"][0]["diagnosis"])
        self.assertEqual(
            value["test_cases"], json.loads((self.workspace / value["note_path"]).read_text())["test_cases"]
        )

    def test_missing_authority_holds_without_exposing_approval_or_refresh(self):
        self.state["contract_history"][0]["declared_changes"] = []
        self.assertTrue(lifecycle.refresh_diagnosis(self.state, self.workspace))
        self.assertEqual("PAUSED_STALE_DIAGNOSIS", self.state["status"])
        self.assertNotIn(refresh.KEY, self.state)
        self.assertNotIn("final_token", self.state["planning"])
        self.state["status"] = "AWAITING_GOAL_APPROVAL"
        with self.assertRaisesRegex(ValueError, "resume to refresh"):
            lifecycle.approve(self.state, "arbitrary")

    def test_saved_conflicting_approval_offers_existing_resume_without_mutation_or_source_io(self):
        self.state["displayed_goal"] = "shown-token"
        original = copy.deepcopy(self.state)
        with mock.patch.object(refresh, "prepare", side_effect=AssertionError("read side must not prepare")):
            action = run_view.action(self.state, run_dir=self.workspace / "run", workspace=self.workspace)
        self.assertEqual("resume", action["needs"]["kind"])
        self.assertIn("--resume-paused", action["next_command"])
        self.assertNotIn("--approve-goal", action["next_command"])
        self.assertEqual(original, self.state)

    def test_authority_and_identity_negatives(self):
        original = copy.deepcopy(self.state)
        for defect in (
            "older-feedback",
            "missing-ledger",
            "missing-source",
            "missing-receipt",
            "different-definition",
            "ambiguous-name",
            "approved",
            "wrong-turn",
        ):
            with self.subTest(defect=defect):
                self.state = copy.deepcopy(original)
                if defect == "older-feedback":
                    self.state["brief_feedback"][0]["at"] = "2026-10-28T01:00:00+00:00"
                    self.state["user_events"][0] = copy.deepcopy(self.state["brief_feedback"][0])
                elif defect == "missing-ledger":
                    self.state["user_events"] = []
                elif defect == "missing-source":
                    self.state["source_log"] = []
                elif defect == "missing-receipt":
                    self.state["contract_history"][0]["declared_changes"] = []
                elif defect == "different-definition":
                    self.state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"] += " with extra scope"
                    self.seal(self.state["goal_contract"])
                elif defect == "ambiguous-name":
                    self.state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = (
                        "guard: test_t1_t4_combined"
                    )
                    self.seal(self.state["goal_contract"])
                elif defect == "approved":
                    self.state["goal_contract"]["approval_status"] = "approved"
                else:
                    self.state["turns"] = [{"stage_index": 1}]
                    self.assertIsNone(refresh.proposal(self.state))
                    continue
                with self.assertRaises(ValueError):
                    refresh.prepare(self.state, self.workspace)

    def test_harmless_feedback_and_technical_prose_do_not_request_refresh(self):
        self.state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = (
            "test: test_t4_empty_page"
        )
        self.state["goal_contract"]["body"]["technical_approach"] = ["The old diagnosis is superseded"]
        self.seal(self.state["goal_contract"])
        self.assertIsNone(refresh.prepare(self.state, self.workspace))
        self.assertFalse(lifecycle.refresh_diagnosis(self.state, self.workspace))

    def test_stale_source_and_report_refuse_before_queue(self):
        self.snapshot["files"]["epp/client.py"] = "edited"
        with self.assertRaisesRegex(ValueError, "Source changed"):
            refresh.prepare(self.state, self.workspace)
        self.snapshot["files"]["epp/client.py"] = "abc"
        Path(self.state["investigation"]["output"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "report changed"):
            refresh.prepare(self.state, self.workspace)

    def test_runner_note_is_the_only_source_exception(self):
        self.snapshot["files"][self.report["note_path"]] = "runner-note"
        self.assertIsNotNone(refresh.prepare(self.state, self.workspace))
        self.snapshot["files"]["docs/bugs/other.json"] = "unexpected"
        with self.assertRaisesRegex(ValueError, "Source changed"):
            refresh.prepare(self.state, self.workspace)

    def test_changed_note_and_same_content_reencoding_after_queue_are_refused(self):
        note = self.workspace / self.report["note_path"]
        original = note.read_bytes()
        tampered = json.loads(original)
        tampered["fix_plan"] = ["Change an unrelated behavior"]
        note.write_text(json.dumps(tampered))
        with self.assertRaisesRegex(ValueError, "note changed"):
            refresh.prepare(self.state, self.workspace)
        note.write_bytes(original)
        self.queued()
        note.write_text(json.dumps(json.loads(original), sort_keys=True))
        with self.assertRaisesRegex(ValueError, "stale"):
            refresh.check(self.state, self.corrected(), self.workspace)

    def test_multiple_criteria_for_one_case_are_not_a_unique_correction(self):
        self.state["goal_contract"]["body"]["acceptance_criteria"].append(
            {**self.criterion, "id": "AC5", "verification_method": "test: test_t4_still_restore"}
        )
        self.seal(self.state["goal_contract"])
        with self.assertRaisesRegex(ValueError, "unambiguous"):
            refresh.prepare(self.state, self.workspace)

    def test_pending_contract_or_feedback_drift_cannot_admit_report(self):
        self.queued()
        self.state["goal_contract"]["revision"] += 1
        self.seal(self.state["goal_contract"])
        with self.assertRaisesRegex(ValueError, "stale"):
            refresh.check(self.state, self.corrected(), self.workspace)

    def test_same_feedback_is_consumed_before_dispatch_even_if_pending_is_later_retired(self):
        pending = self.queued()
        self.state[refresh.KEY].pop("pending")
        self.state["goal_contract"]["revision"] += 1
        self.seal(self.state["goal_contract"])
        with self.assertRaisesRegex(ValueError, "already requested"):
            refresh.prepare(self.state, self.workspace)
        self.assertEqual(pending["id"], self.state[refresh.KEY]["attempts"][0]["refresh_id"])

    def test_fresh_report_cannot_drop_or_weaken_other_obligations(self):
        self.queued()
        for defect in ("drop", "unaffected", "case-input", "wrong-kind", "scope", "not-reproduced"):
            with self.subTest(defect=defect):
                value = self.corrected()
                if defect == "drop":
                    value["test_cases"].pop(0)
                elif defect == "unaffected":
                    value["test_cases"][0]["then"] = "any number"
                elif defect == "case-input":
                    value["test_cases"][1]["given"] = "nonempty input"
                elif defect == "wrong-kind":
                    value["test_cases"][1]["kind"] = "restore"
                elif defect == "scope":
                    value["affected_paths"].append("unrelated.py")
                else:
                    value["outcome"] = "not_reproduced"
                with self.assertRaises(ValueError):
                    refresh.check(self.state, value, self.workspace)
                self.assertEqual(self.report["test_cases"], cases.diagnosis_cases(self.state))

    def test_existing_reproduction_and_counterexample_are_still_called(self):
        self.report.update(
            probe="python reproduce.py",
            untestable="",
            counterexample={"file": "epp/client.py", "find": "bad", "replace": "good"},
        )
        output = Path(self.state["investigation"]["output"])
        output.write_text(json.dumps(self.report))
        self.state["investigation"].update(
            self.report, output_hash=util.file_hash(output), probe_result={"exit_code": 0}
        )
        (self.workspace / self.report["note_path"]).write_text(
            json.dumps({**bug_job.note(self.report), "proven_by": self.report["probe"]})
        )
        self.queued()
        value = self.corrected()
        value.update(probe=self.report["probe"], untestable="", counterexample=self.report["counterexample"])
        target = self.workspace / "refreshed.json"
        target.write_text(json.dumps(value))
        probe = mock.Mock(return_value={"exit_code": 0, "stdout": "shown", "stderr": ""})
        opposite = mock.Mock(return_value={"exit_code": 1, "stdout": "gone", "stderr": ""})
        bug_job.apply(
            self.state, value, {"output": str(target)}, self.workspace, run_probe=probe, run_counterexample=opposite
        )
        probe.assert_called_once_with("python reproduce.py")
        opposite.assert_called_once_with("python reproduce.py", self.report["counterexample"])

    def test_source_drift_during_reproduction_cannot_replace_the_accepted_note(self):
        self.report.update(
            probe="python reproduce.py",
            untestable="",
            counterexample={"file": "epp/client.py", "find": "bad", "replace": "good"},
        )
        output = Path(self.state["investigation"]["output"])
        output.write_text(json.dumps(self.report))
        self.state["investigation"].update(
            self.report, output_hash=util.file_hash(output), probe_result={"exit_code": 0}
        )
        note = self.workspace / self.report["note_path"]
        note.write_text(json.dumps({**bug_job.note(self.report), "proven_by": self.report["probe"]}))
        original = note.read_bytes()
        self.queued()
        value = self.corrected()

        def probe(command):
            self.snapshot["files"]["epp/client.py"] = "changed while probing"
            return {"exit_code": 0, "stdout": "shown", "stderr": ""}

        with self.assertRaisesRegex(ValueError, "Source changed"):
            bug_job.apply(
                self.state,
                value,
                {},
                self.workspace,
                run_probe=probe,
                run_counterexample=lambda command, edit: {"exit_code": 1, "stdout": "gone", "stderr": ""},
            )
        self.assertEqual(original, note.read_bytes())
        self.assertEqual(self.report["test_cases"], cases.diagnosis_cases(self.state))


if __name__ == "__main__":
    unittest.main()
