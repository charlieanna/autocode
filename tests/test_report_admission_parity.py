"""report-check and admission give one answer, and a rejection names every fault (#3865, #3866, #3891)."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import autocode_code_citations as code_citations
import autocode_report_check as report_check
import autocode_requirements_handoff as requirements_handoff
import autocode_requirements_triage as requirements_triage
import autopilot
from units import autoplanner

from . import test_autocode as base

TASK = "Add a --json flag to app.py"
R4 = "ambiguity_audit.error_and_failure_behavior entry r4 is not a file in the workspace"
LINE_40 = "source_refs entry app.py:40 points past the end of the file (3 lines)"
QUOTE_R2 = "Requirement R2 source_quote is not in the task or a saved user event"
MISSING = "source_refs entry missing.py is not a file in the workspace"
UNREAD = "source_refs entry README.md cites README.md, which this stage never read"
UNVERIFIED = "cites evidence the runner did not verify"


def entry(status, *refs):
    return {"status": status, "refs": list(refs)}


def audit(error_and_failure_behavior):
    return {
        "outcome_scope": entry("specified_in_source", "task"),
        "inputs_and_edge_cases": entry("not_applicable"),
        "error_and_failure_behavior": error_and_failure_behavior,
        "compatibility_and_callers": entry("not_applicable"),
        "ux_and_output_contract": entry("not_applicable"),
        "state_and_side_effects": entry("not_applicable"),
    }


def requirements_report(*, error_refs, source_refs, requirements=None):
    """A saved-shape requirements_gather report, valid against its schema."""
    return {
        "summary": "Add a --json flag to app.py",
        "intended_outcome": "app.py prints its output as JSON when given --json",
        "required_behaviors": ["app.py --json prints JSON"],
        "constraints": [],
        "acceptance_tests": ["Running app.py --json prints valid JSON"],
        "source_refs": list(source_refs),
        "proposed_assumptions": [],
        "open_questions": [],
        "requirements": requirements or [{"id": "r4", "text": "Add --json to app.py", "source_quote": TASK}],
        "ignored_statements": [],
        "conflicts": [],
        "ambiguity_audit": audit(entry("repo_convention", *error_refs)),
    }


def ac1_report():
    return requirements_report(error_refs=["r4"], source_refs=["app.py:40"])


def ac3_report():
    return requirements_report(
        error_refs=["app.py:2"],
        source_refs=["missing.py", "app.py:40"],
        requirements=[
            {"id": "R1", "text": "Add --json to app.py", "source_quote": TASK},
            {"id": "R2", "text": "Support XML", "source_quote": "Support XML output."},
        ],
    )


def ac7_requirements_report():
    return requirements_report(error_refs=["app.py:2"], source_refs=["app.py:2"])


def validator_schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "checks", "criterion_results"],
        "properties": {
            "verdict": {"type": "string"},
            "checks": {"type": "array"},
            "criterion_results": {"type": "array"},
        },
    }


def validator_report(checks, **criteria):
    return {
        "verdict": "PASS",
        "checks": checks,
        "criterion_results": [
            {"id": criterion, "status": "PASS", "evidence_refs": refs} for criterion, refs in criteria.items()
        ],
    }


TYPED_CHECK = {"command": "python -m unittest", "exit_code": 0, "evidence_ref": "event:item_3"}
BARE_CHECK = {"command": "python -m unittest", "exit_code": None, "evidence_ref": "event:"}


def command_event(identity, command, output="", code=0):
    return {
        "type": "item.completed",
        "item": {
            "id": identity,
            "type": "command_execution",
            "command": command,
            "exit_code": code,
            "aggregated_output": output,
        },
    }


class RequirementsParityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / "app.py").write_text("one\ntwo\nthree\n")
        (self.workspace / "README.md").write_text("# Project\n")
        self.run = self.workspace / ".autocode" / "runs" / "current"
        self.run.mkdir(parents=True)
        self.state = {"workspace": str(self.workspace), "run_dir": str(self.run), "task": TASK, "stages": []}
        self.queued = []

    def read_record(self):
        """The requirements_gather stage record, whose events show a completed `cat app.py`."""
        events = self.run / "requirements-events.jsonl"
        events.write_text(json.dumps(command_event("item_1", "cat app.py", "one\ntwo\nthree\n")) + "\n")
        output = self.run / "requirements-01.json"
        output.write_text("{}")
        return {"stage": "requirements_gather", "events": str(events), "output": str(output)}

    def spec_file(self):
        spec = report_check.build_spec(
            stage="requirements_gather",
            workspace=self.workspace,
            schema=autoplanner.SCHEMAS["requirements_gather"],
            state=self.state,
        )
        path = self.root / "requirements-spec.json"
        path.write_text(json.dumps(spec))
        return spec, path

    def cli(self, spec_path, report):
        candidate = self.root / "candidate.json"
        candidate.write_text(json.dumps(report))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = report_check.cli(["--spec", str(spec_path), str(candidate)])
        return code, out.getvalue(), err.getvalue()

    def accept(self, report, record=None):
        def queue(state, kind, payload, **fields):
            self.queued.append((kind, payload, fields))

        return autoplanner.accept_handoff(
            self.state,
            "requirements_gather",
            report,
            record or self.read_record(),
            autopilot._check_code_refs,
            requirements_handoff.accept,
            queue,
        )

    def rejection(self, report, record=None):
        with self.assertRaises(ValueError) as raised:
            self.accept(report, record)
        return raised.exception

    def test_ac1_report_check_lists_bad_dimension_ref_and_out_of_range_line(self):
        spec, path = self.spec_file()
        errors = report_check.check_report(json.dumps(ac1_report()), spec=spec)
        self.assertTrue(any(R4 in error for error in errors), errors)
        self.assertTrue(any(LINE_40 in error for error in errors), errors)
        code, _out, err = self.cli(path, ac1_report())
        self.assertEqual(1, code)
        self.assertIn(R4, err)
        self.assertIn(LINE_40, err)

    def test_ac2_admission_names_bad_dimension_ref_and_out_of_range_line_together(self):
        error = self.rejection(ac1_report())
        self.assertIs(ValueError, type(error))
        self.assertIn(R4, str(error))
        self.assertIn(LINE_40, str(error))
        self.assertTrue(str(error).startswith("ambiguity_audit."), "the original rejection comes first")

    def test_ac3_admission_names_quote_missing_file_and_line_faults_together(self):
        message = str(self.rejection(ac3_report()))
        self.assertIn(QUOTE_R2, message)
        self.assertIn(MISSING, message)
        self.assertIn(LINE_40, message)
        self.assertTrue(message.startswith(QUOTE_R2), "the original rejection comes first")
        self.assertEqual(1, message.count(QUOTE_R2), "a fault the message names is not repeated")

    def test_ac4_report_check_lists_quote_missing_file_and_line_faults(self):
        spec, path = self.spec_file()
        errors = report_check.check_report(json.dumps(ac3_report()), spec=spec)
        for expected in (QUOTE_R2, MISSING, LINE_40):
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in error for error in errors), errors)
        code, _out, _err = self.cli(path, ac3_report())
        self.assertEqual(1, code)

    def test_ac5_require_names_every_bad_citation(self):
        with self.assertRaises(ValueError) as raised:
            code_citations.require(self.state, ["missing.py", "app.py:40"], "code_refs", planning_unit=autoplanner)
        self.assertIn("code_refs entry missing.py is not a file in the workspace", str(raised.exception))
        self.assertIn("code_refs entry app.py:40 points past the end of the file (3 lines)", str(raised.exception))

    def test_ac7_valid_reports_still_pass_report_check(self):
        spec, path = self.spec_file()  # no stage events: app.py was never read
        self.assertEqual([], report_check.check_report(json.dumps(ac7_requirements_report()), spec=spec))
        code, out, _err = self.cli(path, ac7_requirements_report())
        self.assertEqual(0, code)
        self.assertIn("OK (requirements_gather)", out)

        tester_spec = report_check.build_spec(stage="tester", workspace=self.workspace, schema=validator_schema())
        tester_path = self.root / "tester-spec.json"
        tester_path.write_text(json.dumps(tester_spec))
        report = validator_report([TYPED_CHECK], AC1=["check:1"], AC2=["event:item_3"])
        self.assertEqual([], report_check.check_report(json.dumps(report), spec=tester_spec))
        code, out, _err = self.cli(tester_path, report)
        self.assertEqual(0, code)
        self.assertIn("OK (tester)", out)

    def test_ac8_valid_requirements_report_still_admitted(self):
        self.accept(ac7_requirements_report())
        self.assertEqual(["app.py:2"], self.state["requirements_handoff"]["report"]["source_refs"])

    def test_ac21_admission_names_bad_dimension_ref_and_unread_source_together(self):
        report = ac1_report()
        report["source_refs"] = ["README.md"]
        error = self.rejection(report)
        self.assertIs(ValueError, type(error), "never ExecutionEvidenceRequired: the stage read a file")
        self.assertIn(R4, str(error))
        self.assertIn(UNREAD, str(error))

    def test_a_source_ref_line_explanation_spanning_lines_gets_requires_error_everywhere(self):
        ref = "app.py:2 why\nmore words"
        with self.assertRaises(ValueError) as required:
            code_citations.require(self.state, [ref], "source_refs", planning_unit=autoplanner)
        expected = f"source_refs entry {ref} has no line number"
        self.assertEqual(expected, str(required.exception))
        report = ac1_report()
        report["source_refs"] = [ref]
        spec, _path = self.spec_file()
        errors = report_check.check_report(json.dumps(report), spec=spec)
        self.assertEqual(1, sum(expected in error for error in errors), errors)
        message = str(self.rejection(report))
        self.assertTrue(message.startswith(R4), message)
        self.assertEqual(1, message.count(expected), message)

    def test_an_older_spec_without_the_fault_context_keeps_only_the_quote_check(self):
        spec, _path = self.spec_file()
        spec.pop("requirements_faults")
        errors = report_check.check_report(json.dumps(ac3_report()), spec=spec)
        self.assertTrue(any(QUOTE_R2 in error for error in errors), errors)
        self.assertFalse(any("source_refs entry" in error for error in errors), errors)
        self.assertEqual([], report_check.check_report(json.dumps(ac1_report()), spec=spec))

    def test_a_triage_answer_keeps_its_rejection_message(self):
        report = {**ac1_report(), "ready": True}
        record = self.read_record()
        with code_citations.applying(record):
            with self.assertRaises(ValueError) as direct:
                autoplanner.ambiguity_audit.validate(self.state, copy.deepcopy(report))
        with mock.patch.object(requirements_triage, "due", return_value=True):
            error = self.rejection(report, record)
        self.assertEqual(str(direct.exception), str(error))
        self.assertNotIn(LINE_40, str(error))

    def test_a_failure_listing_the_other_faults_re_raises_the_original_rejection(self):
        import autocode_requirements_faults as requirements_faults

        report = ac1_report()
        record = self.read_record()
        with code_citations.applying(record):
            with self.assertRaises(ValueError) as direct:
                autoplanner.ambiguity_audit.validate(self.state, copy.deepcopy(report))
        with mock.patch.object(requirements_faults, "faults", side_effect=RuntimeError("broken")):
            error = self.rejection(report, record)
        self.assertEqual(str(direct.exception), str(error))
        self.assertIsNone(error.__cause__)
        self.assertNotIn(LINE_40, str(error))


class TesterParityTests(unittest.TestCase):
    setUp = base.RetrofitTest.setUp

    def spec_for_tester(self):
        return report_check.build_spec(stage="tester", workspace=self.root, schema=validator_schema())

    def cli(self, report):
        spec_path = self.run / "tester-spec.json"
        spec_path.write_text(json.dumps(self.spec_for_tester()))
        candidate = self.run / "candidate.json"
        candidate.write_text(json.dumps(report))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            return report_check.cli(["--spec", str(spec_path), str(candidate)])

    def events(self):
        events = self.run / "tester-events.jsonl"
        events.write_text(json.dumps(command_event("item_3", "python -m unittest")) + "\n")
        return events

    def apply(self, report):
        return autopilot.apply_review_result(
            base.runner,
            self.state,
            "tester",
            copy.deepcopy(report),
            {"events": str(self.events()), "source_revision": "r", "output": "tester-01.json"},
            self.root,
            self.run,
        )

    def test_ac6_report_check_flags_unbound_tester_event_refs(self):
        report = validator_report([TYPED_CHECK], AC1=["event:item_9"], AC2=["check:1", "event:"])
        errors = report_check.check_report(json.dumps(report), spec=self.spec_for_tester())
        self.assertIn("Criterion AC1 cites evidence the runner did not verify ('event:item_9')", "\n".join(errors))
        self.assertIn("Criterion AC2 cites evidence the runner did not verify ('event:')", "\n".join(errors))
        self.assertEqual(1, self.cli(report))
        with self.assertRaisesRegex(ValueError, "Criterion AC1 cites evidence the runner did not verify"):
            self.apply(report)

    def test_the_binding_flag_reads_a_checkpoint_validation_and_only_the_gated_stages(self):
        report = validator_report([TYPED_CHECK], AC1=["event:item_9"])
        checkpoint = report_check.build_spec(stage="review_checkpoint", workspace=self.root, schema={})
        errors = report_check.check_report(json.dumps({"validation": report}), spec=checkpoint)
        self.assertIn("Criterion AC1 cites evidence the runner did not verify ('event:item_9')", "\n".join(errors))
        completion = report_check.build_spec(stage="review_completion", workspace=self.root, schema={})
        self.assertFalse(
            [error for error in report_check.check_report(json.dumps(report), spec=completion) if UNVERIFIED in error]
        )

    def test_ac19_report_check_bare_event_check_flags_only_bare_criterion_event(self):
        cases = {"check": (["check:1"], False), "concrete": (["event:item_9"], False), "bare": (["event:"], True)}
        for name, (refs, flagged) in cases.items():
            with self.subTest(case=name):
                errors = report_check.check_report(
                    json.dumps(validator_report([BARE_CHECK], AC1=refs)), spec=self.spec_for_tester()
                )
                unverified = [error for error in errors if UNVERIFIED in error]
                if flagged:
                    self.assertIn(
                        "Criterion AC1 cites evidence the runner did not verify ('event:')", "\n".join(errors)
                    )
                else:
                    self.assertEqual([], unverified)

    def test_ac20_valid_tester_reports_pass_the_evidence_binding_gate(self):
        seen = []
        original = autopilot.check_refs.unbound_refs

        def unbound_refs(report, allowed):
            seen.append(original(report, allowed))
            return seen[-1]

        reports = {
            "typed": validator_report([TYPED_CHECK], AC1=["check:1"], AC2=["event:item_3"]),
            "bare": validator_report([BARE_CHECK], AC1=["check:1"]),
        }
        for name, report in reports.items():
            with self.subTest(report=name), mock.patch.object(autopilot.check_refs, "unbound_refs", unbound_refs):
                seen.clear()
                try:
                    self.apply(report)
                except Exception as error:  # a later gate on this minimal fixture is not the binding gate
                    self.assertNotIn(UNVERIFIED, str(error))
                self.assertEqual([[]], seen, "the evidence-binding gate ran and found every citation bound")


if __name__ == "__main__":
    unittest.main()
