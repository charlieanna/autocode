"""Review IDs bind to saved criteria without asking providers to echo text."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode as runner
from autocode_report_schema import hydrate_review_report, review_generation_schema, review_validation_schema
from autocode_util import validate_schema


class ReviewReportSchemaTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/astra-decision.schema.json"
        self.schema = json.loads(path.read_text())
        self.state = {"acceptance_criteria": [{"id": "G1", "criterion": "Same wording"},
                                              {"id": "G2", "criterion": "Same wording"}]}
        self.report = {"status": "TASK_COMPLETE", "acceptance_criteria": [
            {"id": cid, "status": "verified", "evidence": "event:check"} for cid in ("G1", "G2")],
            "evidence": ["event:check"], "next_objective": "", "blocker": "", "plan": [], "affected_paths": []}

    def test_strict_generation_requires_ids_without_text_and_preserves_inputs(self):
        before = copy.deepcopy((self.schema, self.state))
        for stage in ("astra_review",):
            schema = review_generation_schema(self.schema, self.state, stage)
            validate_schema(self.report, schema)
            item = schema["properties"]["acceptance_criteria"]["items"]
            self.assertNotIn("criterion", item["properties"])
            self.assertEqual(set(item["properties"]), set(item["required"]))
            self.assertEqual(["G1", "G2"], item["properties"]["id"]["enum"])
        self.assertEqual(before, (self.schema, self.state))

    def test_loader_saves_canonical_report_and_preserves_original_response(self):
        for stage in ("astra_review", "astra_review_report_repair"):
            for legacy in (False, True):
                with self.subTest(stage=stage, legacy=legacy), tempfile.TemporaryDirectory() as directory:
                    raw = copy.deepcopy(self.report)
                    if legacy:
                        for row in raw["acceptance_criteria"]:
                            row["criterion"] = "Same wording"
                    output, schema_path = Path(directory)/"out.json", Path(directory)/"schema.json"
                    output.write_text(json.dumps(raw))
                    schema_path.write_text(json.dumps(review_generation_schema(self.schema, self.state, "astra_review")))
                    record = {"stage": stage, "output": str(output), "schema": str(schema_path)}
                    if stage.endswith("_report_repair"):
                        record["original_stage"] = "astra_review"
                    result = runner.load_stage_report(record, state=self.state)
                    self.assertEqual(["Same wording"]*2, [row["criterion"] for row in result["acceptance_criteria"]])
                    self.assertEqual(result, json.loads(output.read_text()))
                    if not legacy:
                        self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
                    self.assertEqual(result, runner.load_stage_report(record, state=self.state))
                    self.assertNotIn("criteria_hydrated", record)

    def test_bad_identity_text_or_mixed_shape_is_invalid_output_before_hydration(self):
        for case in ("unknown", "duplicate", "missing", "reordered", "mixed", "conflicting"):
            value = copy.deepcopy(self.report)
            rows = value["acceptance_criteria"]
            if case == "unknown": rows[0]["id"] = "UNKNOWN"
            if case == "duplicate": rows[1]["id"] = "G1"
            if case == "missing": rows.pop()
            if case == "reordered": rows.reverse()
            if case == "mixed": rows[0]["criterion"] = "Same wording"
            if case == "conflicting":
                for row in rows: row["criterion"] = "A different requirement"
            with self.subTest(case=case), self.assertRaises(ValueError):
                review_validation_schema(self.schema, self.state, {"stage": "astra_review"}, value)

    def test_hydration_preserves_verdicts_evidence_and_inputs(self):
        before = copy.deepcopy(self.report)
        result = hydrate_review_report(self.report, self.state, {"stage": "astra_review"})
        self.assertEqual(before, self.report)
        for row, raw in zip(result["acceptance_criteria"], before["acceptance_criteria"], strict=False):
            self.assertEqual(raw, {key: value for key, value in row.items() if key != "criterion"})

    def test_other_stages_do_not_get_an_id_only_shape(self):
        for stage in ("astra_plan", "sol", "terra"):
            schema = review_generation_schema(self.schema, self.state, stage)
            self.assertIn("criterion", schema["properties"]["acceptance_criteria"]["items"]["required"])

    def test_production_checkpoint_keeps_its_nested_full_text_schema(self):
        import autocode_workflow as workflow
        schema = workflow.checkpoint_schema(runner.SCHEMA_DIR)
        bound = review_generation_schema(schema, self.state, "astra_checkpoint")
        self.assertEqual(schema, bound)
        item = bound["properties"]["decision"]["properties"]["acceptance_criteria"]["items"]
        self.assertIn("criterion", item["required"])
        report = {"decision": {"acceptance_criteria": [{"id": "G1"}]}}
        self.assertEqual(report, hydrate_review_report(report, self.state, {"stage": "astra_checkpoint"}))
