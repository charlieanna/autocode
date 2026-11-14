"""Public report-check handles the compact planning reports offered to real stages."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_plan_retention as retention
import autocode_plan_revision_reference as references
import autocode_report_check as checker
import autocode_util as util
from autocode_goals import INITIAL_TASK
from goal_fixtures import body
from units import autoplanner as planner


class RetainedReportCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        saved = body()
        for row in saved["acceptance_criteria"]:
            row["sources"] = [{"source_id": "task:0", "quote": "Keep greetings working."}]
        contract = {"revision": 4, "hash": "saved-hash", "body": saved, "origin": "revise_plan"}
        self.state = {
            "task": "Keep greetings working.",
            "goal_contract": contract,
            "planning": {
                "reports": {"revise_plan": {"report": {"contract": copy.deepcopy(saved), "requirement_trace": []}}}
            },
        }
        canonical = copy.deepcopy(planner.SCHEMAS["revise_plan"])
        canonical["properties"]["contract"]["properties"]["initial_task"] = copy.deepcopy(INITIAL_TASK)
        offered = retention.model_schema(canonical, self.state, "revise_plan")
        # Use the real compact contract wire schema; unrelated report fields do not
        # affect the observed pre-check crash.
        self.schema = util.model_output_schema(
            {
                "type": "object",
                "properties": {"contract": offered["properties"]["contract"], "requirement_trace": {"type": "array"}},
                "required": ["contract", "requirement_trace"],
            }
        )
        self.spec = checker.build_spec(stage="revise_plan", workspace=self.root, schema=self.schema, state=self.state)

    def report(self):
        return {
            "contract": {
                "revise_contract": retention.token(self.state),
                "changes": {field: None for field in references.FIELDS},
                "acceptance_criteria": {"retain_from": retention.token(self.state), "upsert": [], "remove": []},
                "retain_requirement_trace": True,
            },
            "requirement_trace": [],
        }

    def check(self, report, spec=None):
        return checker.check_report(json.dumps(report), spec=spec or self.spec)

    def test_public_cli_checks_compact_revision_without_mutating_spec_report_or_workspace(self):
        spec_path, report_path = self.root / "spec.json", self.root / "report.json"
        spec_path.write_text(json.dumps(self.spec))
        report_path.write_text(json.dumps(self.report()))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = subprocess.run(
            [
                sys.executable,
                str(Path(checker.__file__).with_name("autocode.py")),
                "report-check",
                "--spec",
                str(spec_path),
                "--json",
                "-",
            ],
            input=report_path.read_text(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_empty_delta_and_schema_offered_null_noops_keep_existing_checks(self):
        report = self.report()
        before = copy.deepcopy((self.state, self.spec, report))
        self.assertEqual([], self.check(report))
        report["contract"]["acceptance_criteria"] = None
        self.assertEqual([], self.check(report))
        self.assertEqual(before[:2], (self.state, self.spec))

    def test_compact_upsert_checks_new_citations_and_inherited_milestone_graph(self):
        report = self.report()
        row = copy.deepcopy(self.state["goal_contract"]["body"]["acceptance_criteria"][0])
        row["sources"][0]["quote"] = "task:0#1"
        report["contract"]["acceptance_criteria"]["upsert"] = [row]
        self.assertEqual([], self.check(report))
        row["sources"][0]["quote"] = "Invented requirement."
        self.assertTrue(self.check(report))
        row["sources"][0]["quote"] = "task:0#99"
        self.assertTrue(self.check(report))
        row["sources"][0]["quote"] = "task:0#1"
        milestones = copy.deepcopy(self.state["goal_contract"]["body"]["milestones"])
        milestones[0]["depends_on"] = [milestones[0]["id"]]
        report["contract"]["changes"]["milestones"] = milestones
        self.assertTrue(any("cycle" in e.lower() or "itself" in e.lower() for e in self.check(report)))

    def test_stale_reference_missing_context_and_missing_trace_are_diagnostics(self):
        for field in ("revise_contract", "retain_from"):
            with self.subTest(field=field):
                report = self.report()
                target = report["contract"] if field == "revise_contract" else report["contract"]["acceptance_criteria"]
                target[field] = "r3:old-hash"
                self.assertTrue(self.check(report))
        for key in ("retained_contract", "retained_requirement_trace"):
            with self.subTest(missing=key):
                spec = copy.deepcopy(self.spec)
                spec.pop(key, None)
                self.assertTrue(self.check(self.report(), spec))
        self.assertTrue(self.check(self.report(), {**self.spec, "retained_contract": "invalid"}))

    def test_full_draft_delta_individual_references_and_final_reference(self):
        saved = self.state["goal_contract"]["body"]
        for stage in ("draft_plan", "finalize_plan"):
            spec = checker.build_spec(stage=stage, workspace=self.root, schema={"type": "object"}, state=self.state)
            contract = (
                copy.deepcopy(saved) if stage == "draft_plan" else {"retain_contract": retention.token(self.state)}
            )
            if stage == "draft_plan":
                contract["acceptance_criteria"] = retention.inventory(self.state)
            self.assertEqual([], self.check({"contract": contract}, spec))
            contract["acceptance_criteria"] = {"retain_from": retention.token(self.state), "upsert": [], "remove": []}
            self.assertEqual([], self.check({"contract": contract}, spec))
            contract["acceptance_criteria"]["retain_from"] = "stale"
            self.assertTrue(self.check({"contract": contract}, spec))
        malformed = {"contract": {"acceptance_criteria": ["not a criterion"]}}
        self.assertTrue(self.check(malformed, {"stage": "draft_plan", "schema": {"type": "object"}}))
        malformed = {"contract": {"initial_task": None}}
        self.assertTrue(self.check(malformed, {"stage": "draft_plan", "schema": {"type": "object"}}))
