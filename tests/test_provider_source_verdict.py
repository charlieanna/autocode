"""Offline providers honor source fields on fresh reports and saved-report repairs."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_goals as goals
from goal_fixtures import body

TOOLS = Path(__file__).resolve().parents[1] / "tools"
PROVIDERS = (
    "fake_codex.py",
    "live_fixture_provider.py",
    "fixtures/multicomponent_fake.py",
    "blackbox_build_provider.py",
    "fake_parallel_builder.py",
)
SOURCE_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "source_verdict": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "uncovered": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["verdict", "uncovered", "summary"],
        }
    },
    "required": ["source_verdict"],
}


class ProviderSourceVerdictTests(unittest.TestCase):
    def invoke(self, provider, *, stage="astra_discovery", schema=None, original=None, contract=None):
        with tempfile.TemporaryDirectory(prefix="source-report-") as directory:
            root = Path(directory)
            output, schema_path = root / "report.json", root / "schema.json"
            schema_path.write_text(json.dumps(schema or {"properties": {}}))
            draft = body(human=False) if contract is None else copy.deepcopy(contract)
            packet = {
                "stage": stage,
                "goal_contract": {"revision": 1, "hash": "fixture-contract", "body": draft},
                "acceptance_criteria": [{"id": "C1", "criterion": "Greeting holds"}],
                "current_task": {"id": "T1", "milestone_id": "M1"},
                "saved_answers": {"Q1": {"text": "CLI"}},
                "brief_feedback": [],
            }
            if original is not None:
                saved = root / "original.json"
                saved.write_text(json.dumps(original))
                packet = {
                    "report_repair": True,
                    "original": {"stage": stage, "output": str(saved)},
                    "rejected_report": {"path": str(saved), "content": original},
                }
            spec = root / "spec.json"
            spec.write_text(json.dumps({"contract": draft, "checks": {"M1": "pass"}}))
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {"alpha": {"description": "Greeting works", "file": "greet.py", "content": "", "check": "true"}}
                )
            )
            result = subprocess.run(
                [sys.executable, str(TOOLS / provider), "--output-schema", str(schema_path), "-o", str(output)],
                input="Implement the alpha component\nCURRENT HANDOFF DATA\n" + json.dumps(packet),
                text=True,
                capture_output=True,
                cwd=root,
                env={
                    **{
                        key: value
                        for key, value in os.environ.items()
                        if key not in ("BUILD_AUDIT_LIVE_CODEX", "REVIEW_AUDIT_LIVE_CODEX")
                    },
                    "FAKE_MANIFEST": str(manifest),
                    "BUILD_AUDIT_SPEC": str(spec),
                    "BUILD_AUDIT_LOG": str(root / "provider.log"),
                },
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(output.read_text())

    def test_scripted_legacy_plans_declare_empty_sources(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                report = self.invoke(provider)
                self.assertTrue(report["contract"]["acceptance_criteria"])
                for criterion in report["contract"]["acceptance_criteria"]:
                    self.assertEqual([], criterion["sources"])

    def test_repair_uses_requested_source_verdict_schema_without_goal_packet(self):
        original = {"status": "COMPLETE", "summary": "Saved report"}
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                report = self.invoke(provider, stage="astra_review", original=original, schema=SOURCE_VERDICT_SCHEMA)
                self.assertEqual("PASS", report["source_verdict"]["verdict"])
                self.assertEqual([], report["source_verdict"]["uncovered"])
                self.assertTrue(report["source_verdict"]["summary"])
                plain = self.invoke(provider, stage="astra_review", original=original)
                self.assertNotIn("source_verdict", plain)

    def test_repair_preserves_explicit_failed_verdict(self):
        verdict = {"verdict": "FAIL", "uncovered": ["task:0"], "summary": "Source requirement remains uncovered"}
        original = {"status": "CONTINUE", "summary": "Saved rejection", "source_verdict": verdict}
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                report = self.invoke(provider, stage="astra_review", original=original, schema=SOURCE_VERDICT_SCHEMA)
                self.assertEqual(verdict, report["source_verdict"])

    def test_parallel_planning_repair_keeps_explicit_source_waivers(self):
        schema = {"properties": {"source_trace": {"type": "object"}}, "required": ["source_trace"]}
        original = {"summary": "Saved plan review", "concerns": []}
        report = self.invoke("fake_parallel_builder.py", stage="astra_challenge", original=original, schema=schema)
        self.assertEqual({"out_of_scope": [], "negation_waivers": []}, report["source_trace"])
        trace = {
            "out_of_scope": [{"source_id": "task:0", "quote": "No deployment", "reason": "User scope"}],
            "negation_waivers": [],
        }
        original["source_trace"] = trace
        report = self.invoke("fake_parallel_builder.py", stage="astra_challenge", original=original, schema=schema)
        self.assertEqual(trace, report["source_trace"])

    def test_live_fixture_planning_repair_preserves_saved_citations(self):
        citations = [{"source_id": "task:0", "quote": "Build the greeting CLI"}]
        original = {"contract": {"acceptance_criteria": [{"id": "C1", "sources": citations}]}}
        report = self.invoke("live_fixture_provider.py", original=original)
        self.assertEqual(citations, report["contract"]["acceptance_criteria"][0]["sources"])

    def test_live_fixture_requirements_are_covered_by_its_real_plan(self):
        requirements = self.invoke("live_fixture_provider.py", stage="requirements_gather")
        state = {"task": "", "requirements_handoff": {"report": requirements}}
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                report = self.invoke("live_fixture_provider.py", stage=stage)
                goals.check_requirement_trace(state, report, report["contract"])

    def test_inherited_plan_keeps_citations_and_omits_runner_provenance(self):
        contract = body(human=False)
        citations = [{"source_id": "task:0", "quote": "Build the greeting CLI"}]
        contract["acceptance_criteria"][0]["sources"] = citations
        for field in ("source_trace", "brief_acceptance", "risk_acceptance"):
            contract[field] = {"runner_provenance": "sealed"}
        for stage in ("glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                report = self.invoke("fake_codex.py", stage=stage, contract=contract)
                self.assertEqual(citations, report["contract"]["acceptance_criteria"][0]["sources"])
                for field in ("source_trace", "brief_acceptance", "risk_acceptance"):
                    self.assertNotIn(field, report["contract"])


if __name__ == "__main__":
    unittest.main()
