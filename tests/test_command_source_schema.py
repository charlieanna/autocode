"""The offline configured tool honors the actual current source schema."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from goal_fixtures import body
from providers.command import CommandProvider

TOOLS = Path(__file__).resolve().parents[1] / "tools"
PLANNING_SCHEMA = {
    "type": "object",
    "properties": {
        "contract": {
            "properties": {
                "acceptance_criteria": {
                    "items": {"properties": {"sources": {"type": "array"}}},
                }
            }
        },
        "source_trace": {"type": "object"},
    },
}
VERDICT_SCHEMA = {"type": "object", "properties": {"source_verdict": {"type": "object"}}}


class CommandSourceSchemaTests(unittest.TestCase):
    def invoke(
        self, stage, schema, *, events=False, retained=None, raw_retained=None, contract=None, expected_error=None
    ):
        with tempfile.TemporaryDirectory(prefix="command-source-schema-") as directory:
            root = Path(directory)
            output = root / "report.json"
            draft = body(questions=False, human=False) if contract is None else copy.deepcopy(contract)
            packet = {
                "stage": stage,
                "execution_engine": "opencode",
                "goal_contract": {"revision": 1, "hash": "approved", "body": draft},
                "saved_answers": {"Q1": {"text": "CLI"}},
                "acceptance_criteria": [{"id": "C1", "criterion": "Contract holds"}],
                "current_task": {"id": "T1"},
                "source_inventory": [{"id": "task:0", "kind": "task", "text": "Build a greeting CLI."}],
            }
            if retained is not None or raw_retained is not None:
                original = root / "original.json"
                original.write_text(raw_retained if raw_retained is not None else json.dumps(retained))
                packet = {"report_repair": True, "original": {"stage": stage, "output": str(original)}}
            config = {
                "name": "fixture",
                "roles": {},
                "command": ["fixture-tool", "--report", "{report}"],
                "output": "opencode_events" if events else "report_file",
            }
            adapter = CommandProvider(config, root / "provider.toml")
            prompt = adapter.prompt_for_schema(
                "Task\nCURRENT HANDOFF DATA\n" + json.dumps(packet), schema, output.with_suffix(".jsonl")
            )
            if not events:
                self.assertIn("\nFINAL REPORT DELIVERY\n", prompt)
            command = [sys.executable, str(TOOLS / "fake_command_tool.py"), "--report", str(output)]
            if events:
                command.append("--events")
            env = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_FIXTURE_")}
            result = subprocess.run(
                command, input=prompt, text=True, capture_output=True, cwd=root, env=env, timeout=10
            )
            if expected_error is not None:
                self.assertNotEqual(0, result.returncode)
                self.assertIn(expected_error, result.stderr)
                self.assertFalse(output.exists())
                return None
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse((root / "greet.py").exists(), "Planning and report repairs never execute Builder work")
            if events:
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                return json.loads(next(row["part"]["text"] for row in rows if row["type"] == "text"))
            return json.loads(output.read_text())

    def test_modern_planning_cites_the_visible_source_and_supplies_waivers(self):
        report = self.invoke("astra_discovery", PLANNING_SCHEMA)
        self.assertEqual(
            [{"source_id": "task:0", "quote": "Build a greeting CLI."}],
            report["contract"]["acceptance_criteria"][0]["sources"],
        )
        self.assertEqual({"out_of_scope": [], "negation_waivers": []}, report["source_trace"])

    def test_inherited_plan_keeps_citations_and_omits_runner_provenance(self):
        draft = body(questions=False, human=False)
        citations = [{"source_id": "task:0", "quote": "Earlier approved source"}]
        draft["acceptance_criteria"][0]["sources"] = citations
        for field in ("source_trace", "brief_acceptance", "risk_acceptance"):
            draft[field] = {"runner_provenance": "sealed"}
        report = self.invoke("glm_revise", PLANNING_SCHEMA, contract=draft)
        self.assertEqual(citations, report["contract"]["acceptance_criteria"][0]["sources"])
        for field in ("source_trace", "brief_acceptance", "risk_acceptance"):
            self.assertNotIn(field, report["contract"])

    def test_context_free_completion_repair_keeps_failed_verdict_and_execution_history(self):
        for events in (False, True):
            for verdict in (None, {"verdict": "FAIL", "uncovered": ["task:0"], "summary": "Uncovered source"}):
                with self.subTest(events=events, verdict=verdict):
                    retained = {
                        "status": "CONTINUE",
                        "summary": "Saved report",
                        "checks": [{"command": "false", "exit_code": 1, "evidence_ref": "receipt.json"}],
                    }
                    if verdict is not None:
                        retained["source_verdict"] = verdict
                    report = self.invoke("astra_review", VERDICT_SCHEMA, events=events, retained=retained)
                    self.assertEqual(retained["status"], report["status"])
                    self.assertEqual(retained["checks"], report["checks"])
                    if verdict is not None:
                        self.assertEqual(verdict, report["source_verdict"])
                    else:
                        self.assertEqual("PASS", report["source_verdict"]["verdict"])

    def test_planning_repair_preserves_citations_and_explicit_source_waivers(self):
        draft = body(questions=False, human=False)
        citations = [{"source_id": "task:0", "quote": "Original source"}]
        draft["acceptance_criteria"][0]["sources"] = citations
        waiver = {
            "out_of_scope": [{"source_id": "task:1", "quote": "No deployment", "reason": "User scope"}],
            "negation_waivers": [],
        }
        retained = {"summary": "Original planning report", "contract": draft, "source_trace": waiver}
        self.assertEqual(retained, self.invoke("astra_finalize", PLANNING_SCHEMA, retained=retained))

    def test_legacy_schema_does_not_invent_source_fields(self):
        report = self.invoke("astra_discovery", {"type": "object", "properties": {}})
        self.assertNotIn("source_trace", report)
        self.assertNotIn("sources", report["contract"]["acceptance_criteria"][0])
        retained = {"summary": "Saved completion", "checks": []}
        self.assertEqual(retained, self.invoke("astra_review", {"properties": {}}, retained=retained))

    def test_builder_repair_adds_only_missing_required_summary(self):
        retained = {
            "changed_files": ["greet.py"],
            "checks": [{"command": "false", "exit_code": 1, "evidence_ref": "retained.json"}],
        }
        schema = {"properties": {"summary": {"type": "string"}}, "required": ["summary"]}
        repaired = self.invoke("terra", schema, retained=retained)
        self.assertTrue(repaired["summary"])
        self.assertEqual(retained, {key: value for key, value in repaired.items() if key != "summary"})
        retained["summary"] = "Original Builder summary"
        self.assertEqual(retained, self.invoke("terra", schema, retained=retained))
        self.assertEqual(retained, self.invoke("terra", {"properties": {}}, retained=retained))

    def test_unparsable_legacy_report_keeps_its_synthetic_repair_fallback(self):
        report = self.invoke("terra", {"properties": {}}, raw_retained="not JSON")
        self.assertEqual("Repaired report", report["summary"])
        self.assertEqual([], report["commands_run"])

    def test_nonobject_retained_json_is_refused(self):
        for retained in ([], ["retained check"], "retained text", 17):
            with self.subTest(retained=retained):
                self.invoke(
                    "terra",
                    {"properties": {}},
                    retained=retained,
                    expected_error="report-only repair needs an object report",
                )


if __name__ == "__main__":
    unittest.main()
