"""The scripted provider follows the offered repair wire schema, like a real model."""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_stuck_job as stuck
import autocode_util as util


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

    def test_unclassified_builder_failure_keeps_its_pinned_identity_without_inventing_a_cause(self):
        failure = {"failure_id": "pinned-failure", "evidence_refs": ["/run/review.json", "/run/after.json"]}
        for context in (
            {"builder_failure": failure},
            {"stuck_investigation": {"failure_evidence": failure}},
            {"investigation_context": {"builder_failure": failure}},
        ):
            with self.subTest(context=context):
                report = self.provider.report_for("investigate_stuck", context)
                self.assertEqual("unknown", report["failure_class"])
                self.assertEqual("pinned-failure", report["failure_id"])
                self.assertEqual(["/run/review.json"], report["evidence_refs"])
                self.assertEqual("pause", report["recommendation"])
                self.assertEqual("", report["probe"])
                self.assertTrue(report["untestable"])
                self.assertTrue(report["example"])
                self.assertEqual("", report["user_question"])
        ordinary = self.provider.report_for("investigate_stuck", {})
        self.assertNotIn("failure_id", ordinary)
        self.assertEqual([], ordinary["evidence_refs"])

    def test_lifecycle_classification_uses_only_the_exact_pinned_rework_report(self):
        report = {
            "status": "REWORK",
            "task_id": "task",
            "contract_hash": "contract",
            "contract_revision": 3,
            "evidence": ["F-runner"],
            "next_objective": "Correct the product so the runner's failed lifecycle observation passes: "
            "Hard-kill lost the unacknowledged event",
        }
        output = self.root / "review.json"
        output.write_text(json.dumps(report))
        binding = {key: report[key] for key in ("task_id", "contract_hash", "contract_revision")}
        binding["evidence_hashes"] = {str(output): hashlib.sha256(output.read_bytes()).hexdigest()}
        failure = {
            "failure_id": "pinned-failure",
            "record": {"output": str(output)},
            "evidence_refs": [str(output)],
            "binding": binding,
        }
        data = {"builder_failure": failure}
        expected = [
            {
                "id": "F-runner",
                "source": "runner",
                "blocking": True,
                "finding": "Hard-kill lost the unacknowledged event",
            }
        ]
        self.assertEqual(expected, self.provider.runner_findings(data))
        self.provider.CONFIG.update(brief="Fix the scripted lifecycle", criteria={"C1": "Lifecycle holds"})
        classified = self.provider.report_for("investigate_stuck", data)
        self.assertEqual("execution", classified["failure_class"])
        self.assertEqual("pinned-failure", classified["failure_id"])
        self.assertEqual([str(output)], classified["evidence_refs"])
        self.assertEqual("retry", classified["recommendation"])
        util.validate_schema(classified, stuck.schema({"stuck_investigation": {"mode": "builder_failure"}}))
        for change in (
            {"status": "PASS"},
            {"task_id": "different-task"},
            {"contract_hash": "different-contract"},
            {"contract_revision": 4},
            {"next_objective": "Unrelated rework"},
            {"evidence": []},
            {"evidence": ["F-one", "F-two"]},
            {"evidence": ["not-a-finding"]},
        ):
            with self.subTest(change=change):
                output.write_text(json.dumps({**report, **change}))
                binding["evidence_hashes"][str(output)] = hashlib.sha256(output.read_bytes()).hexdigest()
                self.assertEqual([], self.provider.runner_findings(data))
        output.write_text(json.dumps(report))
        self.assertEqual([], self.provider.runner_findings(data), "stale hash is refused")
        binding["evidence_hashes"][str(output)] = hashlib.sha256(output.read_bytes()).hexdigest()
        for field, value in (("failure_id", ""), ("evidence_refs", []), ("record", {})):
            with self.subTest(field=field):
                self.assertEqual([], self.provider.runner_findings({"builder_failure": {**failure, field: value}}))
        output.unlink()
        self.assertEqual([], self.provider.runner_findings(data))

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
