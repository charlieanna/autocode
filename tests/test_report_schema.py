"""Review IDs bind to saved criteria without asking providers to echo text."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_resolver_recommendation as recommendation
import autocode_role_schema as role_schema
from autocode_report_schema import hydrate_review_report, review_generation_schema, review_validation_schema
from autocode_util import model_output_schema, validate_schema
from units import autoplanner


class ReviewReportSchemaTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/astra-decision.schema.json"
        self.schema = json.loads(path.read_text())
        self.state = {
            "acceptance_criteria": [
                {"id": "G1", "criterion": "Same wording"},
                {"id": "G2", "criterion": "Same wording"},
            ]
        }
        self.report = {
            "status": "TASK_COMPLETE",
            "acceptance_criteria": [
                {"id": cid, "status": "verified", "evidence": "event:check"} for cid in ("G1", "G2")
            ],
            "evidence": ["event:check"],
            "next_objective": "",
            "blocker": "",
            "plan": [],
            "affected_paths": [],
        }

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
                    output, schema_path = Path(directory) / "out.json", Path(directory) / "schema.json"
                    output.write_text(json.dumps(raw))
                    schema_path.write_text(
                        json.dumps(review_generation_schema(self.schema, self.state, "astra_review"))
                    )
                    record = {"stage": stage, "output": str(output), "schema": str(schema_path)}
                    if stage.endswith("_report_repair"):
                        record["original_stage"] = "astra_review"
                    result = runner.load_stage_report(record, state=self.state)
                    self.assertEqual(["Same wording"] * 2, [row["criterion"] for row in result["acceptance_criteria"]])
                    self.assertEqual(result, json.loads(output.read_text()))
                    if not legacy:
                        self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
                    self.assertEqual(result, runner.load_stage_report(record, state=self.state))
                    self.assertNotIn("criteria_hydrated", record)

    def test_bad_identity_text_or_mixed_shape_is_invalid_output_before_hydration(self):
        for case in ("unknown", "duplicate", "missing", "reordered", "mixed", "conflicting"):
            value = copy.deepcopy(self.report)
            rows = value["acceptance_criteria"]
            if case == "unknown":
                rows[0]["id"] = "UNKNOWN"
            if case == "duplicate":
                rows[1]["id"] = "G1"
            if case == "missing":
                rows.pop()
            if case == "reordered":
                rows.reverse()
            if case == "mixed":
                rows[0]["criterion"] = "Same wording"
            if case == "conflicting":
                for row in rows:
                    row["criterion"] = "A different requirement"
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


class ReportAuthorityCompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.file_count = 0
        self.raw = {
            "user_request": {
                "kind": "none",
                "discovered": "",
                "impact": "",
                "decision_needed": "",
                "options": [],
                "proposed_delta": "",
            }
        }
        request = copy.deepcopy(role_schema.USER_REQUEST)
        request["properties"]["recommended_revision"] = copy.deepcopy(recommendation.SCHEMA)
        self.schema = model_output_schema(role_schema.obj({"user_request": request}))

    def files(self, schema, raw, stage="astra_resolve", original_stage=None):
        self.file_count += 1
        directory = self.root / str(self.file_count)
        directory.mkdir()
        output, schema_path = directory / "out.json", directory / "schema.json"
        output.write_text(json.dumps(raw))
        schema_path.write_text(json.dumps(schema))
        record = {"stage": stage, "output": str(output), "schema": str(schema_path)}
        if original_stage:
            record["original_stage"] = original_stage
        return record, output

    def test_missing_resolver_recommendation_defaults_to_null_and_preserves_raw_report(self):
        for stage, original in (
            ("astra_resolve", None),
            ("astra_resolve_report_repair", None),
            ("astra_resolve_report_repair", "astra_resolve"),
        ):
            with self.subTest(stage=stage, original_stage=original):
                record, output = self.files(self.schema, self.raw, stage, original)
                expected = copy.deepcopy(self.raw)
                expected["user_request"]["recommended_revision"] = None
                result = runner.load_stage_report(record)
                self.assertEqual(expected, result)
                self.assertEqual(expected, json.loads(output.read_text()))
                preserved = Path(record["reported_output"])
                self.assertEqual(self.raw, json.loads(preserved.read_text()))
                self.assertEqual(expected, runner.load_stage_report(record))
                self.assertEqual(self.raw, json.loads(preserved.read_text()))
                self.assertEqual(self.schema, json.loads(Path(record["schema"]).read_text()))

    def test_old_saved_schema_and_report_remain_unchanged(self):
        schema = model_output_schema(role_schema.obj({"user_request": copy.deepcopy(role_schema.USER_REQUEST)}))
        for stage in ("astra_resolve", "astra_resolve_report_repair"):
            with self.subTest(stage=stage):
                record, output = self.files(schema, self.raw, stage)
                before = output.read_bytes()
                self.assertEqual(self.raw, runner.load_stage_report(record))
                self.assertEqual(before, output.read_bytes())
                self.assertFalse(output.with_suffix(".reported.json").exists())
                self.assertEqual(schema, json.loads(Path(record["schema"]).read_text()))

    def test_supplied_recommendations_are_never_replaced_by_null(self):
        valid = {"acceptance_criteria": [{"id": "C1", "criterion": "Corrected wording"}]}
        for supplied in (None, valid, "accept", {}, {"acceptance_criteria": []}, {**valid, "permission": True}):
            with self.subTest(supplied=supplied):
                raw = copy.deepcopy(self.raw)
                raw["user_request"]["recommended_revision"] = supplied
                record, output = self.files(self.schema, raw)
                before = output.read_bytes()
                if supplied is None or supplied == valid:
                    self.assertEqual(raw, runner.load_stage_report(record))
                else:
                    with self.assertRaisesRegex(ValueError, "recommended_revision"):
                        runner.load_stage_report(record)
                self.assertEqual(before, output.read_bytes())
                self.assertFalse(output.with_suffix(".reported.json").exists())

    def test_other_stages_do_not_receive_recommendation_defaults(self):
        for stage in ("terra", "sol", "astra_review", "astra_review_report_repair"):
            with self.subTest(stage=stage):
                record, output = self.files(self.schema, self.raw, stage)
                with self.assertRaisesRegex(ValueError, "missing recommended_revision"):
                    runner.load_stage_report(record)
                self.assertEqual(self.raw, json.loads(output.read_text()))
                self.assertFalse(output.with_suffix(".reported.json").exists())

    def test_nonnullable_schema_does_not_receive_a_null_default(self):
        schema = copy.deepcopy(self.schema)
        schema["properties"]["user_request"]["properties"]["recommended_revision"]["type"] = "object"
        record, output = self.files(schema, self.raw)
        with self.assertRaisesRegex(ValueError, "missing recommended_revision"):
            runner.load_stage_report(record)
        self.assertEqual(self.raw, json.loads(output.read_text()))
        self.assertFalse(output.with_suffix(".reported.json").exists())

    def test_missing_or_malformed_user_request_is_not_invented(self):
        for raw in ({}, {"user_request": None}, {"user_request": "none"}):
            with self.subTest(raw=raw):
                record, output = self.files(self.schema, raw)
                with self.assertRaisesRegex(ValueError, "user_request"):
                    runner.load_stage_report(record)
                self.assertEqual(raw, json.loads(output.read_text()))
                self.assertFalse(output.with_suffix(".reported.json").exists())

    def test_existing_recovery_change_default_is_retained(self):
        schema = role_schema.obj({"recovery_change": {"type": ["object", "null"], "properties": {}}})
        for stage in ("astra_resolve", "astra_resolve_report_repair", "terra"):
            with self.subTest(stage=stage):
                record, output = self.files(schema, {}, stage)
                self.assertEqual({"recovery_change": None}, runner.load_stage_report(record))
                self.assertEqual({"recovery_change": None}, json.loads(output.read_text()))
                self.assertEqual({}, json.loads(Path(record["reported_output"]).read_text()))
        record, output = self.files(schema, {"recovery_change": "expand permissions"})
        with self.assertRaisesRegex(ValueError, "recovery_change"):
            runner.load_stage_report(record)
        self.assertEqual({"recovery_change": "expand permissions"}, json.loads(output.read_text()))


class ProjectConventionsReportCompatibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.file_count = 0
        self.schema = model_output_schema(
            role_schema.obj(
                {
                    "summary": {"type": "string"},
                    "project_conventions": copy.deepcopy(
                        autoplanner.SCHEMAS["requirements_gather"]["properties"]["project_conventions"]
                    ),
                }
            )
        )

    def files(self, schema, raw, stage, original_stage=None):
        self.file_count += 1
        directory = self.root / str(self.file_count)
        directory.mkdir()
        output, schema_path = directory / "out.json", directory / "schema.json"
        output.write_text(json.dumps(raw))
        schema_path.write_text(json.dumps(schema))
        record = {"stage": stage, "output": str(output), "schema": str(schema_path)}
        if original_stage:
            record["original_stage"] = original_stage
        return record, output

    def test_absent_tags_default_to_empty_for_requirements_and_repair_preserving_raw(self):
        raw = {"summary": "No reusable human conventions were tagged."}
        before = copy.deepcopy((self.schema, raw))
        for stage in ("requirements_gather", "requirements"):
            for recorded, original in (
                (stage, None),
                (stage + "_report_repair", None),
                (stage + "_report_repair", stage),
            ):
                with self.subTest(stage=recorded, original_stage=original):
                    record, output = self.files(self.schema, raw, recorded, original)
                    expected = {**raw, "project_conventions": []}
                    self.assertEqual(expected, runner.load_stage_report(record))
                    self.assertEqual(expected, json.loads(output.read_text()))
                    preserved = Path(record["reported_output"])
                    self.assertEqual(raw, json.loads(preserved.read_text()))
                    self.assertEqual(expected, runner.load_stage_report(record))
                    self.assertEqual(raw, json.loads(preserved.read_text()))
                    self.assertEqual(self.schema, json.loads(Path(record["schema"]).read_text()))
        self.assertEqual(before, (self.schema, raw))

    def test_supplied_tags_are_never_replaced_and_malformed_values_still_fail(self):
        for supplied in ([], ["PROJECT_TEST_NAMES"], None, "PROJECT_TEST_NAMES", False, {}, [1], ["Q"] * 33):
            with self.subTest(supplied=supplied):
                raw = {"summary": "Current report", "project_conventions": supplied}
                record, output = self.files(self.schema, raw, "requirements")
                before = output.read_bytes()
                if supplied in ([], ["PROJECT_TEST_NAMES"]):
                    self.assertEqual(raw, runner.load_stage_report(record))
                else:
                    with self.assertRaisesRegex(ValueError, "project_conventions"):
                        runner.load_stage_report(record)
                self.assertEqual(before, output.read_bytes())
                self.assertNotIn("reported_output", record)

    def test_saved_schema_without_tags_does_not_gain_a_field(self):
        schema = model_output_schema(role_schema.obj({"summary": {"type": "string"}}))
        raw = {"summary": "Existing report"}
        for stage in ("requirements_gather", "requirements_report_repair"):
            with self.subTest(stage=stage):
                record, output = self.files(schema, raw, stage)
                before = output.read_bytes()
                self.assertEqual(raw, runner.load_stage_report(record))
                self.assertEqual(before, output.read_bytes())
                self.assertNotIn("reported_output", record)

    def test_unrelated_stages_do_not_gain_tags_and_original_stage_takes_precedence(self):
        raw = {"summary": "No tags supplied"}
        for stage, original in (
            ("astra_discovery", None),
            ("glm_revise", None),
            ("terra", None),
            ("requirements_gather_report_repair", "terra"),
        ):
            with self.subTest(stage=stage, original_stage=original):
                record, output = self.files(self.schema, raw, stage, original)
                with self.assertRaisesRegex(ValueError, "missing project_conventions"):
                    runner.load_stage_report(record)
                self.assertEqual(raw, json.loads(output.read_text()))
                self.assertNotIn("reported_output", record)

    def test_missing_other_required_fields_are_not_invented(self):
        record, output = self.files(self.schema, {}, "requirements")
        with self.assertRaisesRegex(ValueError, "missing summary"):
            runner.load_stage_report(record)
        self.assertEqual({}, json.loads(output.read_text()))
        self.assertNotIn("reported_output", record)


class GitDeliveryReportCompatibilityTests(unittest.TestCase):
    def test_loader_defaults_only_absent_nullable_delivery_and_preserves_raw(self):
        import autocode_git_delivery_policy as delivery

        for nested in (False, True):
            for stage in ("astra_discovery", "astra_plan", "glm_revise_report_repair"):
                with self.subTest(nested=nested, stage=stage), tempfile.TemporaryDirectory() as tmp:
                    body = role_schema.obj({"git_delivery": copy.deepcopy(delivery.SCHEMA)})
                    schema = model_output_schema(role_schema.obj({"contract": body}) if nested else body)
                    raw = {"contract": {}} if nested else {}
                    expected = {"contract": {"git_delivery": None}} if nested else {"git_delivery": None}
                    output, schema_path = Path(tmp) / "out.json", Path(tmp) / "schema.json"
                    output.write_text(json.dumps(raw))
                    schema_path.write_text(json.dumps(schema))
                    record = {"stage": stage, "output": str(output), "schema": str(schema_path)}
                    result = runner.load_stage_report(record)
                    self.assertEqual(expected, result)
                    self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
                    self.assertEqual(expected, runner.load_stage_report(record))

    def test_normalizer_preserves_supplied_delivery_and_nonnullable_or_older_schemas(self):
        import autocode_git_delivery_policy as delivery
        from autocode_report_schema import normalize_absent_authority

        nullable = copy.deepcopy(delivery.SCHEMA)
        nullable["type"] = ["object", "null"]
        schema = role_schema.obj({"contract": role_schema.obj({"git_delivery": nullable})})
        for supplied in (None, {"kind": "atomic_commit", "message": "Deliver"}, {}, "commit"):
            raw = {"contract": {"git_delivery": supplied}}
            before = copy.deepcopy(raw)
            self.assertEqual(before, normalize_absent_authority(raw, schema, {"stage": "astra_plan"}))
            self.assertEqual(before, raw)
        for old in ({}, {"properties": {"git_delivery": {"type": "object"}}}):
            raw = {"contract": {}}
            self.assertEqual(raw, normalize_absent_authority(raw, old, {"stage": "astra_plan"}))
        for raw in ({}, {"contract": None}, {"contract": "invalid"}):
            self.assertEqual(raw, normalize_absent_authority(raw, schema, {"stage": "astra_plan"}))


class ReviewSeveritySerializationTests(unittest.TestCase):
    def setUp(self):
        import autocode_review_job as review

        self.schema = copy.deepcopy(review.SCHEMA)
        self.raw = {
            "verdict": "request_changes",
            "summary": "Retained live severity spelling error",
            "change_under_review": "HEAD^..HEAD",
            "change_patch": "",
            "findings": [
                {
                    "id": "F2",
                    "severity": "advisary",
                    "file": "validator.py",
                    "lines": [17],
                    "summary": "A nonblocking observation",
                    "evidence": "Original event remains exact",
                    "example": "Given the reviewed code, observe the reported behavior",
                    "untestable": "",
                }
            ],
            "tests_run": ["python -m unittest tests.test_validator"],
            "delivered_tests": [],
        }

    def load(self, root, raw, stage="review_change", schema=None):
        output, schema_path = root / "out.json", root / "schema.json"
        output.write_text(json.dumps(raw))
        schema_path.write_text(json.dumps(schema or self.schema))
        record = {"stage": stage, "output": str(output), "schema": str(schema_path)}
        return record, runner.load_stage_report(record)

    def test_live_typo_preserves_original_report_and_every_semantic_field_without_a_repair(self):
        for stage in ("review_change", "review_change_report_repair"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                record, result = self.load(Path(d), self.raw, stage)
                expected = copy.deepcopy(self.raw)
                expected["findings"][0]["severity"] = "advisory"
                self.assertEqual(expected, result)
                self.assertEqual(self.raw, json.loads(Path(record["reported_output"]).read_text()))
                self.assertEqual(expected, json.loads(Path(record["output"]).read_text()))
                self.assertEqual(expected, runner.load_stage_report(record))
                self.assertNotIn("report_repaired", record)
                self.assertEqual("advisary", self.raw["findings"][0]["severity"])

    def test_single_edit_spellings_do_not_downgrade_blockers(self):
        for typo, label in (
            ("advisroy", "advisory"),
            ("advisor", "advisory"),
            ("advisoryy", "advisory"),
            ("blockign", "blocking"),
            ("blockin", "blocking"),
            ("blockingg", "blocking"),
        ):
            with self.subTest(typo=typo), tempfile.TemporaryDirectory() as d:
                raw = copy.deepcopy(self.raw)
                raw["findings"][0]["severity"] = typo
                _, result = self.load(Path(d), raw)
                self.assertEqual(label, result["findings"][0]["severity"])
                self.assertEqual("request_changes", result["verdict"])

    def test_unknown_severity_or_other_malformed_fields_still_reject_atomically(self):
        variants = []
        for label in ("informational", "nonblocking", "warning", "adviserri", "", None, 1, "аdvisary"):
            raw = copy.deepcopy(self.raw)
            raw["findings"][0]["severity"] = label
            variants.append(raw)
        for field, value in (("verdict", "approv"), ("tests_run", "old PASS"), ("delivered_tests", None)):
            raw = copy.deepcopy(self.raw)
            raw[field] = value
            variants.append(raw)
        raw = copy.deepcopy(self.raw)
        raw["findings"][0]["lines"] = ["17"]
        variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError):
                    self.load(Path(d), raw)
                self.assertEqual(raw, json.loads((Path(d) / "out.json").read_text()))
                self.assertFalse((Path(d) / "out.reported.json").exists())

    def test_other_stages_and_other_vocabularies_are_not_normalized(self):
        for stage, schema in (
            ("sol", self.schema),
            ("astra_challenge", self.schema),
            (
                "review_change",
                {
                    **self.schema,
                    "properties": {
                        **self.schema["properties"],
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"severity": {"type": "string", "enum": ["warning"]}},
                            },
                        },
                    },
                },
            ),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
                self.load(Path(d), self.raw, stage, schema)

    def test_valid_labels_remain_exact(self):
        for label in ("blocking", "advisory"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as d:
                raw = copy.deepcopy(self.raw)
                raw["findings"][0]["severity"] = label
                record, result = self.load(Path(d), raw)
                self.assertEqual(raw, result)
                self.assertNotIn("reported_output", record)
