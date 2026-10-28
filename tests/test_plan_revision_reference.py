"""Sparse engineering revisions preserve exact obligations and all normal guards."""

import copy
import json
import unittest

import autocode_contract_revision as revision
import autocode_plan_retention as retention
import autocode_plan_revision_reference as references
import autocode_trace_coverage as coverage
from units import autoplanner as planner

from . import test_plan_retention as fixtures


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
        result = retention.expand(self.state, "glm_revise", report)
        expected = {**self.state["goal_contract"]["body"], "technical_approach": ["Check before writing"]}
        self.assertEqual(expected, result["contract"])
        self.assertEqual(retention.validated_trace(self.state), result["requirement_trace"])
        self.assertEqual(report["responses"], result["responses"])
        revision.revision_guard(self.state, result["contract"], [], "glm_revise")
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
                retention.expand(self.state, "glm_revise", {**self.report(), "contract": bad})
        for stage in ("astra_finalize", "astra_discovery", "plan_revise", "terra"):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                retention.expand(self.state, stage, self.report())

    def test_trace_reuse_is_explicit_and_requires_the_exact_validated_predecessor(self):
        report = self.report()
        report["contract"].pop("retain_requirement_trace")
        self.assertEqual([], retention.expand(self.state, "glm_revise", report)["requirement_trace"])
        for change in (False, "true", 1):
            bad = self.report()
            bad["contract"]["retain_requirement_trace"] = change
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "validated trace"):
                retention.expand(self.state, "glm_revise", bad)
        report = self.report()
        report["requirement_trace"] = [{"requirement_id": "invented"}]
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(self.state, "glm_revise", report)
        self.state["planning"]["reports"]["glm_revise"]["report"]["contract"] = {}
        with self.assertRaisesRegex(ValueError, "validated trace"):
            retention.expand(self.state, "glm_revise", self.report())

    def test_criteria_delta_still_requires_authority_and_current_trace_coverage(self):
        report = self.report()
        report["contract"]["acceptance_criteria"] = {
            "retain_from": retention.token(self.state),
            "upsert": [],
            "remove": ["AC8"],
        }
        result = retention.expand(self.state, "glm_revise", report)
        with self.assertRaises(ValueError):
            revision.revision_guard(self.state, result["contract"], [], "glm_revise")
        errors = coverage.coverage_errors(
            [{"id": "R1"}],
            {row["requirement_id"]: row for row in result["requirement_trace"]},
            result["contract"],
            covered=True,
            cites_user_event=lambda text: False,
        )
        self.assertEqual(["Requirement R1 is not covered by a behavior or criterion"], errors)

    def test_new_schema_and_repairs_offer_reference_but_old_pins_do_not(self):
        old = copy.deepcopy(planner.SCHEMAS["glm_revise"])
        schema = retention.model_schema(old, self.state, "glm_revise")
        self.assertTrue(references.offered(schema))
        self.assertFalse(references.offered(old))
        self.assertIn("REVISE ONLY CHANGED FIELDS", planner.repair_rules("glm_revise", schema))
        self.assertNotIn("REVISE ONLY CHANGED FIELDS", planner.repair_rules("glm_revise", old))
        reference = schema["properties"]["contract"]["anyOf"][0]
        self.assertEqual([retention.token(self.state)], reference["properties"]["revise_contract"]["enum"])
        self.assertFalse(reference["properties"]["changes"]["additionalProperties"])
        self.assertEqual(
            references.FIELDS & old["properties"]["contract"]["properties"].keys(),
            set(reference["properties"]["changes"]["properties"]),
        )
        self.assertEqual(old, planner.SCHEMAS["glm_revise"])
