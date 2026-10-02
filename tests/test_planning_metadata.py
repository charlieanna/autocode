"""Mechanical metadata recovery preserves wire evidence and contract protections."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_goals as goals
from autocode_planning_metadata import normalize_planning_metadata


class PlanningMetadataTests(unittest.TestCase):
    def inputs(self):
        body = {"acceptance_criteria": [{"id": "AC1", "criterion": "Print the greeting exactly",
                  "verification_method": "test: test_ac1_greeting", "human_review": False}],
                "required_behaviors": ["Print the greeting exactly"], "constraints": ["Use stdlib only"],
                "scope_exclusions": [], "important_failure_cases": ["Reject empty names"],
                "permission_boundaries": ["No network"]}
        state = {"goal_contract": {"body": body, "approval_status": "draft", "approval_event": None},
                 "requirements_handoff": {"report": {"requirements": [
                     {"id": "R1", "text": "Print the greeting exactly"},
                     {"id": "R2", "text": "Reject empty names"}]}}}
        report = {"contract": copy.deepcopy(body), "contract_changes": [], "requirement_trace": [
            {"requirement_id": rid, "disposition": "covered", "evidence": "AC1"} for rid in ("R2", "R1")]}
        return state, report

    def addition(self, report):
        report["contract"]["acceptance_criteria"].append({"id": "AC2", "criterion": "Reject empty names",
                  "verification_method": "test: test_ac2_empty_name", "human_review": False})
        report["contract_changes"] = [{"item": "AC2", "change": "reworded", "basis": "agent_proposed",
                                       "answer_id": "", "replacement": "AC2 added by review"}]

    def test_single_omitted_id_is_recovered_by_elimination_not_order(self):
        state, report = self.inputs()
        report["requirement_trace"][0].pop("requirement_id")
        before = copy.deepcopy((state, report))
        result = normalize_planning_metadata(report, state, {"stage": "astra_discovery"})
        self.assertEqual("R2", result["requirement_trace"][0]["requirement_id"])
        self.assertEqual(before, (state, report))
        goals.check_requirement_trace(state, result, result["contract"])
        self.assertEqual(result, normalize_planning_metadata(result, state, {"stage": "astra_discovery"}))

    def test_ambiguous_missing_ids_are_all_reported_without_guessing(self):
        state, report = self.inputs()
        for row in report["requirement_trace"]:
            row.pop("requirement_id")
        with self.assertRaises(ValueError) as caught:
            normalize_planning_metadata(report, state, {"stage": "glm_revise"})
        for expected in ("requirement_trace[0].requirement_id", "requirement_trace[1].requirement_id", "R1", "R2"):
            self.assertIn(expected, str(caught.exception))
        self.assertTrue(all("requirement_id" not in row for row in report["requirement_trace"]))

    def test_unknown_duplicate_or_missing_rows_do_not_get_recovered(self):
        for kind in ("unknown", "duplicate", "missing_row", "extra_row", "explicit_empty"):
            state, report = self.inputs()
            rows = report["requirement_trace"]
            rows[0].pop("requirement_id")
            if kind == "unknown": rows[1]["requirement_id"] = "R999"
            if kind == "duplicate": rows.append(copy.deepcopy(rows[1]))
            if kind == "missing_row": rows.pop()
            if kind == "extra_row": rows.append({"requirement_id": "R3", "disposition": "covered", "evidence": "AC1"})
            if kind == "explicit_empty": rows[0]["requirement_id"] = ""
            before = copy.deepcopy(report)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                result = normalize_planning_metadata(report, state, {"stage": "astra_discovery"})
                goals.check_requirement_trace(state, result, result["contract"])
            self.assertEqual(before, report)

    def test_recovered_identity_still_requires_real_coverage(self):
        state, report = self.inputs()
        row = report["requirement_trace"][0]
        row.pop("requirement_id")
        row["evidence"] = "Use stdlib only"
        result = normalize_planning_metadata(report, state, {"stage": "astra_discovery"})
        with self.assertRaisesRegex(ValueError, "Requirement R2 is not covered"):
            goals.check_requirement_trace(state, result, result["contract"])

    def test_redundant_addition_declarations_leave_the_contract_intact(self):
        state, report = self.inputs()
        self.addition(report)
        report["contract"]["important_failure_cases"].append("Database failure rolls back")
        report["contract_changes"].append({"item": "important_failure_cases", "change": "reworded",
                                           "basis": "agent_proposed", "answer_id": "", "replacement": "Added a failure case"})
        before = copy.deepcopy((state, report))
        result = normalize_planning_metadata(report, state, {"stage": "glm_revise"})
        self.assertEqual([], result["contract_changes"])
        self.assertEqual(report["contract"], result["contract"])
        self.assertEqual(before, (state, report))
        goals.revision_guard(state, result["contract"], result["contract_changes"], "glm_revise")

    def test_addition_receipts_cannot_hide_changes_to_existing_obligations(self):
        for kind in ("criterion", "proof", "permission", "behavior", "failure_case", "review"):
            state, report = self.inputs()
            self.addition(report)
            body = report["contract"]
            if kind == "criterion": body["acceptance_criteria"][0]["criterion"] = "Accept any output"
            if kind == "proof": body["acceptance_criteria"][0]["verification_method"] = "Inspect it"
            if kind == "permission": body["permission_boundaries"] = ["Network allowed"]
            if kind == "behavior": body["required_behaviors"] = []
            if kind == "failure_case": body["important_failure_cases"] = []
            if kind == "review":
                state["goal_contract"]["approval_status"] = "approved"
                state["goal_contract"]["body"]["acceptance_criteria"][0]["human_review"] = True
            result = normalize_planning_metadata(report, state, {"stage": "glm_revise"})
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                goals.revision_guard(state, result["contract"], result["contract_changes"], "glm_revise")

    def test_real_or_malformed_receipts_are_retained_for_validation(self):
        valid = {"item": "AC2", "change": "reworded", "basis": "agent_proposed", "answer_id": "", "replacement": "Addition"}
        for changes in ([{"item": "AC1", "change": "reworded", "basis": "agent_proposed", "answer_id": ""}],
                        [{"item": "AC2", "change": "removed", "basis": "agent_proposed", "answer_id": ""}],
                        [{"item": "AC2", "change": "reworded", "basis": "user_answer", "answer_id": "invented"}],
                        [{"item": "AC2", "change": "reworded", "basis": "agent_proposed", "example_correction": []}],
                        [dict(valid, answer_id=0)], [dict(valid, extra=False)],
                        [dict(valid, example_correction=None)], [dict(valid, example_correction={})],
                        [{key: value for key, value in valid.items() if key != "replacement"}],
                        ["invalid"]):
            state, report = self.inputs()
            self.addition(report)
            report["contract_changes"] = changes
            result = normalize_planning_metadata(report, state, {"stage": "glm_revise"})
            self.assertEqual(changes, result["contract_changes"])

    def test_other_roles_are_not_normalized(self):
        state, report = self.inputs()
        self.addition(report)
        report["requirement_trace"][0].pop("requirement_id")
        for stage in ("requirements_gather", "astra_plan", "terra", "sol", "astra_review"):
            self.assertEqual(report, normalize_planning_metadata(report, state, {"stage": stage}))

    def test_loader_saves_canonical_report_and_preserves_raw_provider_output(self):
        state, raw = self.inputs()
        self.addition(raw)
        raw["requirement_trace"][0].pop("requirement_id")
        schema = {"type": "object", "properties": {"requirement_trace": {"type": "array", "items": {
            "type": "object", "required": ["requirement_id", "disposition", "evidence"], "properties": {
                "requirement_id": {"type": "string"}, "disposition": {"type": "string"}, "evidence": {"type": "string"}}}}}}
        for engine in ("codex", "opencode"):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                output, schema_file = Path(directory)/"report.json", Path(directory)/"schema.json"
                output.write_text(json.dumps(raw)); schema_file.write_text(json.dumps(schema))
                record = {"stage": "glm_revise_report_repair", "original_stage": "glm_revise", "engine": engine,
                          "output": str(output), "schema": str(schema_file), "events": "fixture-events"}
                with patch.object(runner.opencode, "final_report", return_value=copy.deepcopy(raw)):
                    result = runner.load_stage_report(record, state=state)
                    self.assertEqual([], result["contract_changes"])
                    self.assertEqual("R2", result["requirement_trace"][0]["requirement_id"])
                    self.assertEqual(result, json.loads(output.read_text()))
                    self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
                    self.assertEqual(result, runner.load_stage_report(record, state=state))

    def test_all_uncovered_requirements_are_reported_in_one_failure(self):
        state, report = self.inputs()
        for row in report["requirement_trace"]:
            row["evidence"] = "Use stdlib only"
        with self.assertRaises(ValueError) as caught:
            goals.check_requirement_trace(state, report, report["contract"])
        self.assertIn("Requirement R1 is not covered", str(caught.exception))
        self.assertIn("Requirement R2 is not covered", str(caught.exception))
        report["requirement_trace"][0]["evidence"] = "AC1 and AC99"
        with self.assertRaisesRegex(ValueError, "Requirement R2 is not covered"):
            goals.check_requirement_trace(state, report, report["contract"])
