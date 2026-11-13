"""Sparse engineering revisions preserve exact obligations and all normal guards."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode_contract_revision as revision
import autocode_plan_retention as retention
import autocode_plan_revision_reference as references
import autocode_trace_coverage as coverage
from units import autoplanner as planner

from . import test_plan_retention as fixtures


def validate_wire(value, schema):
    """Check selected anyOf branches too; the core's small validator does not."""
    from autocode_util import validate_schema

    if "anyOf" in schema:
        failures = []
        for branch in schema["anyOf"]:
            try:
                validate_wire(value, branch)
                return
            except ValueError as error:
                failures.append(str(error))
        raise ValueError("No wire branch accepts this value: " + "; ".join(failures))
    validate_schema(value, schema)
    if isinstance(value, dict):
        for key, child in value.items():
            if key in schema.get("properties", {}):
                validate_wire(child, schema["properties"][key])
    elif isinstance(value, list):
        for child in value:
            validate_wire(child, schema.get("items", {}))


class StrictRevisionReferenceTests(unittest.TestCase):
    def setUp(self):
        from autocode_util import model_output_schema
        from goal_fixtures import body

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        saved = body()
        self.state = {
            "goal_contract": {
                "revision": 1,
                "hash": "fixture-hash",
                "body": saved,
                "approval_status": "draft",
                "origin": "revise_plan",
            },
            "planning": {
                "reports": {"revise_plan": {"report": {"contract": copy.deepcopy(saved), "requirement_trace": []}}}
            },
        }
        canonical = copy.deepcopy(planner.SCHEMAS["revise_plan"])
        # The live planning schema offers the first assignment too.
        from autocode_goals import INITIAL_TASK

        canonical["properties"]["contract"]["properties"]["initial_task"] = copy.deepcopy(INITIAL_TASK)
        offered = retention.model_schema(canonical, self.state, "revise_plan")
        self.reference = offered["properties"]["contract"]["anyOf"][0]
        self.schema = model_output_schema(
            {
                "type": "object",
                "properties": {
                    "contract": offered["properties"]["contract"],
                    "contract_changes": {"type": "array"},
                    "requirement_trace": {"type": "array"},
                },
                "required": ["contract", "contract_changes", "requirement_trace"],
                "additionalProperties": False,
            }
        )

    def report(self):
        return {
            "contract": {
                "revise_contract": retention.token(self.state),
                "changes": {field: None for field in references.FIELDS},
                "acceptance_criteria": None,
                "retain_requirement_trace": None,
            },
            "contract_changes": [],
            "requirement_trace": [],
        }

    def load(self, value, *, schema=None, stage="revise_plan"):
        import autocode as runner

        attempt = self.root / str(len(list(self.root.iterdir())))
        attempt.mkdir()
        schema_file, output = attempt / "pin.json", attempt / "report.json"
        schema_file.write_text(json.dumps(self.schema if schema is None else schema))
        output.write_text(json.dumps(value))
        record = {"schema": str(schema_file), "output": str(output), "stage": stage, "engine": "codex"}
        loaded = runner.load_stage_report(record, state=self.state)
        if loaded != value:
            self.assertEqual(value, json.loads(Path(record["reported_output"]).read_text()))
        return loaded

    def test_strict_wire_can_retain_every_engineering_field_without_copying_it(self):
        from autocode_goal_lifecycle import validate_body

        report = self.report()
        before = copy.deepcopy((self.state, report, self.schema))
        validate_wire(report, self.schema)
        loaded = self.load(report)
        self.assertEqual({"revise_contract": retention.token(self.state), "changes": {}}, loaded["contract"])
        expanded = retention.expand(self.state, "revise_plan", loaded)
        self.assertEqual(self.state["goal_contract"]["body"], expanded["contract"])
        validate_body(self.state, expanded["contract"], origin="revise_plan")
        self.assertEqual(before, (self.state, report, self.schema))

    def test_nullable_objects_remain_strict_through_every_wire_branch(self):
        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertEqual(set(node.get("properties", {})), set(node.get("required", [])))
                    self.assertIs(False, node.get("additionalProperties"))
                for child in node.values():
                    check(child)
            elif isinstance(node, list):
                for child in node:
                    check(child)

        check(self.schema)
        criteria = self.schema["properties"]["contract"]["anyOf"][0]["properties"]["acceptance_criteria"]
        delta = next(branch for branch in criteria["anyOf"] if branch.get("type") == "object")
        self.assertIn("proofs", delta["required"])

    def test_strict_wire_can_change_one_field_and_retain_the_other_four(self):
        report = self.report()
        report["contract"]["changes"]["technical_approach"] = ["Inspect before editing"]
        validate_wire(report, self.schema)
        loaded = self.load(report)
        expected = {**self.state["goal_contract"]["body"], "technical_approach": ["Inspect before editing"]}
        self.assertEqual(expected, retention.expand(self.state, "revise_plan", loaded)["contract"])
        self.assertEqual({"technical_approach": ["Inspect before editing"]}, loaded["contract"]["changes"])

    def test_loader_normalizes_before_expanding_a_real_criteria_delta(self):
        report = self.report()
        delta = {
            "retain_from": retention.token(self.state),
            "upsert": [],
            "remove": [],
            "proofs": [{"id": "C1", "verification_method": "python3 -m unittest test_greeting.py"}],
        }
        report["contract"]["acceptance_criteria"] = delta
        report["requirement_trace"] = [{"requirement_id": "R1", "disposition": "covered", "evidence": "C1"}]
        validate_wire(report, self.schema)
        loaded = self.load(report)
        self.assertEqual(delta, loaded["contract"]["acceptance_criteria"])
        self.assertNotIn("retain_requirement_trace", loaded["contract"])
        expanded = retention.expand(self.state, "revise_plan", loaded)
        self.assertEqual(report["requirement_trace"], expanded["requirement_trace"])
        self.assertEqual(
            delta["proofs"][0]["verification_method"],
            expanded["contract"]["acceptance_criteria"][0]["verification_method"],
        )

    def test_nulls_are_not_a_permission_to_clear_required_fields_or_change_the_reference(self):
        from autocode_goal_lifecycle import validate_body

        for field, message in (
            ("deliverables", "Contract is missing deliverables"),
            ("end_to_end_flow", "Build brief is missing end_to_end_flow"),
        ):
            with self.subTest(field=field):
                report = self.report()
                report["contract"]["changes"][field] = []
                loaded = self.load(report)
                self.assertEqual([], loaded["contract"]["changes"][field])
                expanded = retention.expand(self.state, "revise_plan", loaded)
                with self.assertRaisesRegex(ValueError, message):
                    validate_body(self.state, expanded["contract"], origin="revise_plan")
        stale = self.report()
        stale["contract"]["revise_contract"] = "r0:stale"
        with self.assertRaisesRegex(ValueError, "reference_mismatch"):
            retention.expand(self.state, "revise_plan", self.load(stale))

    def test_nonnull_trace_reuse_keeps_its_existing_availability_guard(self):
        report = self.report()
        report["contract"]["retain_requirement_trace"] = True
        loaded = self.load(report)
        self.assertIs(True, loaded["contract"]["retain_requirement_trace"])
        self.assertEqual([], retention.expand(self.state, "revise_plan", loaded)["requirement_trace"])
        self.state["planning"]["reports"] = {}
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(self.state, "revise_plan", loaded)

    def test_schema_guidance_is_bound_to_nullable_offers_including_legacy_reference_pins(self):
        legacy = copy.deepcopy(self.schema)
        reference = legacy["properties"]["contract"]["anyOf"][0]
        for field in ("acceptance_criteria", "retain_requirement_trace"):
            prop = reference["properties"][field]
            if "anyOf" in prop:
                reference["properties"][field] = next(item for item in prop["anyOf"] if item != {"type": "null"})
        for field, prop in reference["properties"]["changes"]["properties"].items():
            if "anyOf" in prop:
                reference["properties"]["changes"]["properties"][field] = next(
                    item for item in prop["anyOf"] if item != {"type": "null"}
                )
        new_rule = planner.repair_rules("revise_plan", self.schema)
        old_rule = planner.repair_rules("revise_plan", legacy)
        new_reference_rule = new_rule.split("REVISE ONLY CHANGED FIELDS.", 1)[1].split("CRITERIA DELTA.", 1)[0]
        old_reference_rule = old_rule.split("REVISE ONLY CHANGED FIELDS.", 1)[1].split("CRITERIA DELTA.", 1)[0]
        self.assertIn("null", new_reference_rule)
        self.assertNotIn("null", old_reference_rule)
        legacy_report = self.report()
        loaded = self.load(legacy_report, schema=legacy)
        self.assertEqual(legacy_report["contract"], loaded["contract"])
        with self.assertRaises(ValueError):
            retention.expand(self.state, "revise_plan", loaded)

    def test_unknown_or_protected_null_changes_remain_refused(self):
        before = copy.deepcopy(self.state)
        for field in ("permission_boundaries", "required_behaviors", "unknown_field"):
            with self.subTest(field=field):
                report = self.report()
                report["contract"]["changes"][field] = None
                loaded = self.load(report)
                self.assertIn(field, loaded["contract"]["changes"])
                with self.assertRaisesRegex(ValueError, "unexpected_change_fields"):
                    retention.expand(self.state, "revise_plan", loaded)
        self.assertEqual(before, self.state)

    def test_full_contract_nulls_keep_their_existing_type_errors(self):
        from autocode_goal_lifecycle import validate_body

        for field, message in (("deliverables", "expected array"), ("initial_task", "expected object")):
            with self.subTest(field=field):
                report = self.report()
                report["contract"] = copy.deepcopy(self.state["goal_contract"]["body"])
                report["contract"][field] = None
                loaded = self.load(report)
                self.assertIsNone(loaded["contract"][field])
                with self.assertRaisesRegex(ValueError, message):
                    validate_body(self.state, loaded["contract"], origin="revise_plan")

    def test_nullable_meaning_requires_the_actual_pin_and_correct_stage(self):
        report = self.report()
        foreign = copy.deepcopy(self.schema)
        foreign["properties"]["contract"]["anyOf"][0]["properties"]["revise_contract"]["enum"] = ["r9:foreign"]
        unoffered = {"type": "object", "properties": {"contract": {"type": "object"}}}
        for kwargs in ({"schema": foreign}, {"stage": "builder"}, {"schema": unoffered}):
            with self.subTest(kwargs=kwargs):
                loaded = self.load(report, **kwargs)
                self.assertEqual(report["contract"], loaded["contract"])
        report["contract"]["retain_requirement_trace"] = False
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(self.state, "revise_plan", self.load(report))


class RevisionReferenceTests(unittest.TestCase):
    def setUp(self):
        self.state = fixtures.PlanRetentionTests().state()
        self.state["goal_contract"]["body"]["technical_approach"] = ["Original approach"]

    def report(self, **changes):
        return {
            "contract": {
                "revise_contract": retention.token(self.state),
                "changes": changes,
                "retain_requirement_trace": True,
            },
            "requirement_trace": [],
            "responses": [{"concern_id": "P1", "change": "Update engineering proposal"}],
            "contract_changes": [],
        }

    def test_sparse_revision_keeps_exact_criteria_trace_and_other_fields(self):
        original = copy.deepcopy(self.state)
        report = self.report(technical_approach=["Check before writing"])
        result = retention.expand(self.state, "revise_plan", report)
        expected = {**self.state["goal_contract"]["body"], "technical_approach": ["Check before writing"]}
        self.assertEqual(expected, result["contract"])
        self.assertEqual(retention.validated_trace(self.state), result["requirement_trace"])
        self.assertEqual(report["responses"], result["responses"])
        revision.revision_guard(self.state, result["contract"], [], "revise_plan")
        self.assertEqual(original, self.state)
        self.assertLess(len(json.dumps(report)), len(json.dumps(result)))
        result["contract"]["acceptance_criteria"][0]["criterion"] = "mutated"
        self.assertEqual(original, self.state)

    def test_stale_foreign_extra_and_protected_edits_are_refused(self):
        good = self.report()["contract"]
        for bad in (
            {**good, "revise_contract": "r3:old"},
            {**good, "unexpected": True},
            {**good, "changes": []},
            {**good, "changes": {"permission_boundaries": ["Network allowed"]}},
            {**good, "changes": {"source_trace": []}},
            {**good, "changes": {"required_behaviors": []}},
            {**good, "acceptance_criteria": []},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                retention.expand(self.state, "revise_plan", {**self.report(), "contract": bad})
        for stage in ("finalize_plan", "draft_plan", "plan_revise", "builder"):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                retention.expand(self.state, stage, self.report())

    def test_each_refusal_reports_only_its_fixed_first_failing_reason(self):
        token = retention.token(self.state)
        saved = self.state["goal_contract"]["body"]
        report = self.report()
        canary = "private-model-text-/private/path-r99-" + "a" * 64
        cases = (
            ("wrong_stage", canary, token, saved, report),
            ("current_reference_unavailable", "revise_plan", None, saved, report),
            (
                "reference_mismatch",
                "revise_plan",
                token,
                saved,
                {**report, "contract": {**report["contract"], "revise_contract": canary}},
            ),
            ("saved_contract_unavailable", "revise_plan", token, canary, report),
            (
                "unexpected_reference_fields",
                "revise_plan",
                token,
                saved,
                {**report, "contract": {**report["contract"], canary: canary}},
            ),
            (
                "changes_not_object",
                "revise_plan",
                token,
                saved,
                {**report, "contract": {**report["contract"], "changes": [canary]}},
            ),
            (
                "unexpected_change_fields",
                "revise_plan",
                token,
                saved,
                {**report, "contract": {**report["contract"], "changes": {canary: canary}}},
            ),
        )
        for reason, stage, current, predecessor, value in cases:
            with self.subTest(reason=reason):
                before = copy.deepcopy((self.state, value))
                with self.assertRaises(ValueError) as raised:
                    references.expand(value, stage, current, predecessor, [])
                message = str(raised.exception)
                self.assertEqual(
                    "Plan revision reference is stale, malformed or not a Planner revision "
                    f"[plan_revision_reference:{reason}]",
                    message,
                )
                self.assertNotIn(canary, message)
                self.assertNotIn(token, message)
                self.assertEqual(before, (self.state, value))

    def test_refusal_order_preserves_short_circuiting_and_never_rebinds_a_reference(self):
        report = {"contract": {"revise_contract": "r0:stale", "changes": None, "extra": True}}
        for stage, current, saved, reason in (
            ("finalize_plan", None, None, "wrong_stage"),
            ("revise_plan", None, None, "current_reference_unavailable"),
            ("revise_plan", "r1:current", None, "reference_mismatch"),
            ("revise_plan", "r0:stale", None, "saved_contract_unavailable"),
            ("revise_plan", "r0:stale", {}, "unexpected_reference_fields"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                references.expand(report, stage, current, saved, None)
        current = retention.token(self.state)
        for foreign in ("r0:stale", "r1:another-task", "current", ""):
            value = self.report()
            value["contract"]["revise_contract"] = foreign
            with self.subTest(foreign=foreign), self.assertRaisesRegex(ValueError, "reference_mismatch"):
                references.expand(value, "revise_plan", current, {}, [])
        # The same current reference still expands to the unchanged predecessor.
        self.assertEqual(
            self.state["goal_contract"]["body"],
            references.expand(self.report(), "revise_plan", current, self.state["goal_contract"]["body"], [])[
                "contract"
            ],
        )

    def test_trace_reuse_is_explicit_and_requires_the_exact_validated_predecessor(self):
        report = self.report()
        report["contract"].pop("retain_requirement_trace")
        self.assertEqual([], retention.expand(self.state, "revise_plan", report)["requirement_trace"])
        for change in (False, "true", 1):
            bad = self.report()
            bad["contract"]["retain_requirement_trace"] = change
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "validated trace"):
                retention.expand(self.state, "revise_plan", bad)
        report = self.report()
        report["requirement_trace"] = [{"requirement_id": "invented"}]
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(self.state, "revise_plan", report)
        self.state["planning"]["reports"]["revise_plan"]["report"]["contract"] = {}
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(self.state, "revise_plan", self.report())

    def test_criteria_delta_still_requires_authority_and_current_trace_coverage(self):
        report = self.report()
        report["contract"]["acceptance_criteria"] = {
            "retain_from": retention.token(self.state),
            "upsert": [],
            "remove": ["AC8"],
        }
        result = retention.expand(self.state, "revise_plan", report)
        with self.assertRaises(ValueError):
            revision.revision_guard(self.state, result["contract"], [], "revise_plan")
        errors = coverage.coverage_errors(
            [{"id": "R1"}],
            {row["requirement_id"]: row for row in result["requirement_trace"]},
            result["contract"],
            covered=True,
            cites_user_event=lambda text: False,
        )
        self.assertEqual(1, len(errors))
        self.assertTrue(errors[0].startswith("Requirement R1 is not covered by a behavior or criterion"))
        self.assertIn("required_behaviors entry copied exactly", errors[0])
        for criterion in result["contract"]["acceptance_criteria"]:
            self.assertIn(criterion["id"], errors[0])

    def test_requirement_own_text_is_refused_and_the_error_names_the_accepted_forms(self):
        # Issue #2592 shape (report-repair residual): R11's evidence paraphrased
        # its source and R12's was the requirement's own text copied exactly.
        # The gate refused both; its error must name the accepted forms and the
        # contract's criterion IDs because the repair turn sees only that text.
        contract = {
            "required_behaviors": [],
            "acceptance_criteria": [{"id": "AC1", "sources": []}],
        }
        errors = coverage.coverage_errors(
            [{"id": "R11"}, {"id": "R12"}],
            {
                "R11": {
                    "requirement_id": "R11",
                    "disposition": "covered",
                    "evidence": "The saved correction revises the proposal only and grants no implementation approval.",
                },
                "R12": {
                    "requirement_id": "R12",
                    "disposition": "covered",
                    "evidence": "Retain completed Requirements and existing evidence while revising the affected proposal.",
                },
            },
            contract,
            covered=True,
            cites_user_event=lambda text: False,
        )
        self.assertEqual(2, len(errors))
        for error in errors:
            self.assertIn("required_behaviors entry copied exactly", error)
            self.assertIn("AC1", error)
            self.assertIn("requirement's own text is not evidence, even copied exactly", error)

    def test_new_schema_and_repairs_offer_reference_but_old_pins_do_not(self):
        old = copy.deepcopy(planner.SCHEMAS["revise_plan"])
        schema = retention.model_schema(old, self.state, "revise_plan")
        self.assertTrue(references.offered(schema))
        self.assertFalse(references.offered(old))
        self.assertIn("REVISE ONLY CHANGED FIELDS", planner.repair_rules("revise_plan", schema))
        self.assertNotIn("REVISE ONLY CHANGED FIELDS", planner.repair_rules("revise_plan", old))
        reference = schema["properties"]["contract"]["anyOf"][0]
        self.assertEqual([retention.token(self.state)], reference["properties"]["revise_contract"]["enum"])
        self.assertFalse(reference["properties"]["changes"]["additionalProperties"])
        self.assertEqual(
            references.FIELDS & old["properties"]["contract"]["properties"].keys(),
            set(reference["properties"]["changes"]["properties"]),
        )
        self.assertEqual(old, planner.SCHEMAS["revise_plan"])
