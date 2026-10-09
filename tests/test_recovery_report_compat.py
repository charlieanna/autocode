"""Omitting an optional recovery proposal must never purchase report repair."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

import tests  # noqa: F401 - runtime import path
import autocode
import autocode_recovery_novelty as novelty
import autocode_util as util
import live_fixture_provider


class RecoveryReportCompatibility(unittest.TestCase):
    def load(self, report, *, with_proposal=True):
        root = Path(self.addCleanupDirectory())
        properties = {"diagnosis": {"type": "string"}}
        if with_proposal:
            properties["recovery_change"] = novelty.CHANGE_SCHEMA
        schema = util.model_output_schema({"type": "object", "properties": properties,
                                          "required": ["diagnosis"], "additionalProperties": False})
        util.atomic_json(root / "schema.json", schema)
        util.atomic_json(root / "report.json", report)
        record = {"stage": "astra_diagnose", "engine": "codex", "output": str(root / "report.json"),
                  "schema": str(root / "schema.json")}
        return autocode.load_stage_report(record), record, report

    def addCleanupDirectory(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name

    def test_strict_generation_does_not_require_a_semantic_proposal(self):
        value, record, original = self.load({"diagnosis": "Needs an independent investigation."})
        self.assertIsNone(value["recovery_change"])
        self.assertEqual(original, json.loads(Path(record["reported_output"]).read_text()))

    def test_legacy_schema_and_report_stay_unchanged(self):
        value, record, original = self.load({"diagnosis": "Original saved diagnosis."}, with_proposal=False)
        self.assertEqual(original, value)
        self.assertNotIn("reported_output", record)

    def test_explicit_null_has_no_grant_or_normalization(self):
        value, record, original = self.load({"diagnosis": "No bounded change proposed.", "recovery_change": None})
        self.assertEqual(original, value)
        self.assertNotIn("reported_output", record)

    def test_supplied_incomplete_or_invalid_proposal_is_not_defaulted(self):
        for proposal in ({}, {"hypothesis": "Try again"}, False, "retry"):
            with self.subTest(proposal=proposal), self.assertRaises(ValueError):
                self.load({"diagnosis": "No verified cause.", "recovery_change": proposal})

    def test_offline_repair_fixture_uses_bound_identity_without_top_level_task(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            identity = {"contract_revision": 7, "contract_hash": "approved", "task_id": "repair-task"}
            packet = {"report_repair": True, "original": {"stage": "sol"}, "report_identity": identity}
            with mock.patch("sys.argv", ["codex", "-o", str(output)]), \
                    mock.patch("sys.stdin", io.StringIO("CURRENT HANDOFF DATA\n" + json.dumps(packet))), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, live_fixture_provider.main())
            report = json.loads(output.read_text())
            self.assertEqual(identity, {key: report[key] for key in identity})

    def test_scenario_default_is_null_but_explicit_object_is_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            util.atomic_json(config, {"check": "python3 -m unittest", "paths": []})
            with mock.patch.dict("os.environ", {"SCENARIO_FAKE_CONFIG": str(config)}):
                fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scenarios/harness/fake_codex.py"))
            schema = {"type": ["object", "null"], "properties": {"answer": {"type": "string"}},
                      "required": ["answer"], "additionalProperties": False}
            self.assertIsNone(fixture["empty"](schema))
            self.assertEqual({"answer": ""}, fixture["complete"]({}, schema))


if __name__ == "__main__":
    unittest.main()
