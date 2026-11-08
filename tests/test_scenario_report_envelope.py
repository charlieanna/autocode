"""The scripted provider follows the offered repair wire schema, like a real model."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class ScenarioReportEnvelopeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        config = self.root / "config.json"
        config.write_text(json.dumps({"check": "python -m unittest", "paths": []}))
        source = Path(__file__).resolve().parents[1] / "scenarios/harness/fake_codex.py"
        spec = importlib.util.spec_from_file_location("scripted_report_provider", source)
        self.provider = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config)}):
            spec.loader.exec_module(self.provider)
        self.report_schema = {
            "type": "object",
            "properties": {"verdict": {"type": "string"}, "checks": {"type": "array"}},
            "required": ["verdict", "checks"],
        }
        self.wire_schema = {
            "type": "object",
            "properties": {"repair_report": {"anyOf": [{"type": "object"}, self.report_schema]}},
            "required": ["repair_report"],
        }

    def test_full_report_is_completed_before_wrapping_without_a_mixed_placeholder(self):
        self.provider.WIRE_SCHEMA = self.wire_schema
        report = {"verdict": "PASS"}
        self.provider.complete(report, self.provider.report_schema(self.wire_schema))
        # Scenario-specific witnesses may add their correction after completion.
        report["changed_code_exceptions"] = [{"gap_id": "L1", "decision": "accept"}]
        expected = dict(report)
        target = self.root / "answer.json"
        with patch.dict(os.environ, {}, clear=True):
            self.provider._report(report, str(target))
        self.assertEqual({"repair_report": expected}, json.loads(target.read_text()))
        self.assertNotIn("repair_report", report)
        self.assertEqual([], report["checks"])

    def test_plain_reports_and_explicit_edit_envelopes_keep_their_original_shape(self):
        for wire, report in (
            (self.report_schema, {"verdict": "PASS", "checks": []}),
            (self.wire_schema, {"repair_report": {"sha256": "a" * 64, "edits": []}}),
            (self.wire_schema, {"repair_report": {}, "verdict": "FAIL"}),
        ):
            with self.subTest(report=report), patch.dict(os.environ, {}, clear=True):
                self.provider.WIRE_SCHEMA = wire
                target = self.root / "answer.json"
                self.provider._report(report, str(target))
                self.assertEqual(report, json.loads(target.read_text()))
        self.assertEqual(self.report_schema, self.provider.report_schema(self.report_schema))

    def test_replay_emitter_receives_the_same_offered_wire_envelope(self):
        self.provider.WIRE_SCHEMA = self.wire_schema
        report = {"verdict": "PASS", "checks": []}
        with patch.dict(os.environ, {"AUTOCODE_REPLAY_EMITTER_COMMAND": "fixture"}):
            with patch.object(self.provider, "_stream") as stream:
                self.provider._report(report, "answer.json")
        stream.assert_called_once_with({"kind": "report", "output": "answer.json", "report": {"repair_report": report}})

    def test_changed_code_review_uses_the_scripted_builders_actual_repair_source(self):
        self.provider.CONFIG.update(reference="/fixtures/broken", paths=["product.py"], turn_paths=[])
        self.provider.OUTPUT_SCHEMA = {"properties": {"changed_code_exceptions": {}}}
        judge = Mock(return_value=[{"gap_id": "current", "decision": "accept"}])
        finding = {"source": "runner", "blocking": True}
        for findings, expected in (([], "/fixtures/broken"), ([finding], "/fixtures/correction")):
            with self.subTest(findings=findings):
                self.provider.DATA = {"open_findings": findings, "reference": "/model-asserted-source"}
                with (
                    patch.dict(os.environ, {"SCENARIO_FAKE_REWORK_SOLUTION": "/fixtures/correction"}),
                    patch.dict(sys.modules, {"fake_changed_code": SimpleNamespace(judgments=judge)}),
                ):
                    report = self.provider.lazy_citations({})
                    self.assertEqual(expected, self.provider.builder_copy_source(self.provider.DATA))
                self.assertEqual(expected, judge.call_args.kwargs["producer"]["reference"])
                self.assertEqual(["product.py"], judge.call_args.kwargs["producer"]["paths"])
                self.assertEqual([{"gap_id": "current", "decision": "accept"}], report["changed_code_exceptions"])
        for mode in ("turn_paths", "milestones"):
            self.provider.CONFIG["turn_paths"] = [["product.py"]] if mode == "turn_paths" else []
            self.provider.MILESTONES = [{"id": "M1"}] if mode == "milestones" else []
            with (
                self.subTest(copy_mode=mode),
                patch.dict(os.environ, {"SCENARIO_FAKE_REWORK_SOLUTION": "/fixtures/correction"}),
                patch.dict(sys.modules, {"fake_changed_code": SimpleNamespace(judgments=judge)}),
            ):
                self.provider.lazy_citations({})
                self.assertEqual("/fixtures/broken", self.provider.builder_copy_source(self.provider.DATA))
                self.assertEqual("/fixtures/broken", judge.call_args.kwargs["producer"]["reference"])
