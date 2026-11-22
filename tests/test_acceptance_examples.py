"""JSON acceptance examples must discriminate a wrong decoder (#3912).

A Requirements card can propose a JSON example a plausible-wrong implementation still satisfies:
a duplicate-key rejection fixture missing a required field, or a build-metadata case checked only by
value equality. The mechanical adequacy check (autocode_acceptance_examples) flags those through the
existing Requirements rewrite route (admission) and ``autocode report-check``, from one answer.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import autocode_acceptance_examples as acceptance_examples
import autocode_report_check as report_check
import autocode_requirements_faults as requirements_faults
import autocode_requirements_handoff as requirements_handoff
import autopilot
from units import autoplanner

TASK = "Add a --json flag to app.py"

VALID_OBJECT = '{"major":1,"minor":2,"patch":3}'
# Missing ``minor``: a duplicate-ACCEPTING decoder still raises for the missing field, not the duplicate.
INADEQUATE_DUPLICATE = '{"major":1,"major":2,"patch":3}'
# Every required field present, so only the repeated key is wrong.
ADEQUATE_DUPLICATE = '{"major":1,"minor":2,"patch":3,"major":2}'
BUILD_VERSION = "1.2.3-rc.1+007"


def entry(status, *refs):
    return {"status": status, "refs": list(refs)}


def audit():
    return {
        "outcome_scope": entry("specified_in_source", "task"),
        "inputs_and_edge_cases": entry("not_applicable"),
        "error_and_failure_behavior": entry("repo_convention", "app.py:2"),
        "compatibility_and_callers": entry("not_applicable"),
        "ux_and_output_contract": entry("not_applicable"),
        "state_and_side_effects": entry("not_applicable"),
    }


def requirements_report(acceptance_tests):
    """An otherwise-valid requirements_gather report (the parity suite's ac7 shape)."""
    return {
        "summary": TASK,
        "intended_outcome": "app.py prints its output as JSON when given --json",
        "required_behaviors": ["app.py --json prints JSON"],
        "constraints": [],
        "acceptance_tests": list(acceptance_tests),
        "source_refs": ["app.py:2"],
        "proposed_assumptions": [],
        "open_questions": [],
        "requirements": [{"id": "r4", "text": "Add --json to app.py", "source_quote": TASK}],
        "ignored_statements": [],
        "conflicts": [],
        "ambiguity_audit": audit(),
    }


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


class ProblemsTests(unittest.TestCase):
    """The pure mechanical check, independent of the gate."""

    def test_duplicate_fixture_missing_a_required_field_is_flagged(self):
        examples = [
            f"Example 1: {VALID_OBJECT} parses to version 1.2.3.",
            f"Example 5: {INADEQUATE_DUPLICATE} must raise ValueError for the duplicate key.",
        ]
        faults = acceptance_examples.problems(examples)
        self.assertEqual(1, len(faults), faults)
        self.assertIn(INADEQUATE_DUPLICATE, faults[0])
        self.assertIn("minor", faults[0])

    def test_duplicate_fixture_with_every_field_is_accepted(self):
        examples = [
            f"Example 1: {VALID_OBJECT} parses to version 1.2.3.",
            f"Example 5: {ADEQUATE_DUPLICATE} must raise ValueError for the duplicate key.",
        ]
        self.assertEqual([], acceptance_examples.problems(examples))

    def test_duplicate_fixture_without_a_valid_companion_is_not_decidable(self):
        # No valid object to compare fields against: the check guesses no domain and says nothing.
        examples = [f"Example 5: {INADEQUATE_DUPLICATE} must raise ValueError for the duplicate key."]
        self.assertEqual([], acceptance_examples.problems(examples))

    def test_build_metadata_checked_only_by_equality_is_flagged(self):
        examples = [f'Example 3: the round-trip result equals Version.parse("{BUILD_VERSION}").']
        faults = acceptance_examples.problems(examples)
        self.assertEqual(1, len(faults), faults)
        self.assertIn(BUILD_VERSION, faults[0])
        self.assertIn("build", faults[0])

    def test_build_metadata_asserted_as_the_full_string_is_accepted(self):
        examples = [f'Example 3: str(result) == "{BUILD_VERSION}" preserves the full version string.']
        self.assertEqual([], acceptance_examples.problems(examples))

    def test_ordinary_prose_examples_are_untouched(self):
        self.assertEqual([], acceptance_examples.problems(["Running app.py --json prints valid JSON", "run"]))
        self.assertEqual([], acceptance_examples.problems([]))
        self.assertEqual([], acceptance_examples.problems(None))


class GateTests(unittest.TestCase):
    """Admission and report-check read the one answer and send inadequate examples back."""

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
        events = self.run / "requirements-events.jsonl"
        events.write_text(json.dumps(command_event("item_1", "cat app.py", "one\ntwo\nthree\n")) + "\n")
        output = self.run / "requirements-01.json"
        output.write_text("{}")
        return {"stage": "requirements_gather", "events": str(events), "output": str(output)}

    def spec(self):
        return report_check.build_spec(
            stage="requirements_gather",
            workspace=self.workspace,
            schema=autoplanner.SCHEMAS["requirements_gather"],
            state=self.state,
        )

    def accept(self, report):
        def queue(state, kind, payload, **fields):
            self.queued.append((kind, payload, fields))

        return autoplanner.accept_handoff(
            self.state,
            "requirements_gather",
            report,
            self.read_record(),
            autopilot._check_code_refs,
            requirements_handoff.accept,
            queue,
        )

    def inadequate_report(self):
        return requirements_report(
            [
                f"Example 1: {VALID_OBJECT} parses to version 1.2.3.",
                f"Example 5: {INADEQUATE_DUPLICATE} must raise ValueError for the duplicate key.",
                f'Example 3: the round-trip result equals Version.parse("{BUILD_VERSION}").',
            ]
        )

    def adequate_report(self):
        return requirements_report(
            [
                f"Example 1: {VALID_OBJECT} parses to version 1.2.3.",
                f"Example 5: {ADEQUATE_DUPLICATE} must raise ValueError for the duplicate key.",
                f'Example 3: str(result) == "{BUILD_VERSION}" preserves the full version string.',
            ]
        )

    def test_faults_list_names_the_inadequate_examples(self):
        report = self.inadequate_report()
        faults = requirements_faults.faults(report, requirements_faults.context(self.state))
        self.assertTrue(any(INADEQUATE_DUPLICATE in fault for fault in faults), faults)
        self.assertTrue(any(BUILD_VERSION in fault for fault in faults), faults)

    def test_report_check_flags_inadequate_examples(self):
        errors = report_check.check_report(json.dumps(self.inadequate_report()), spec=self.spec())
        self.assertTrue(any(INADEQUATE_DUPLICATE in error for error in errors), errors)
        self.assertTrue(any(BUILD_VERSION in error for error in errors), errors)

    def test_report_check_accepts_adequate_examples(self):
        self.assertEqual([], report_check.check_report(json.dumps(self.adequate_report()), spec=self.spec()))

    def test_admission_sends_inadequate_examples_back_to_the_writer(self):
        with self.assertRaises(ValueError) as raised:
            self.accept(self.inadequate_report())
        self.assertIs(ValueError, type(raised.exception))
        self.assertIn(INADEQUATE_DUPLICATE, str(raised.exception))
        self.assertIn(BUILD_VERSION, str(raised.exception))
        self.assertNotIn("requirements_handoff", self.state, "an inadequate handoff is not installed")

    def test_admission_accepts_adequate_examples(self):
        self.accept(self.adequate_report())
        self.assertIn("requirements_handoff", self.state)

    def test_report_check_cli_reports_the_inadequate_example(self):
        spec_path = self.root / "requirements-spec.json"
        spec_path.write_text(json.dumps(self.spec()))
        candidate = self.root / "candidate.json"
        candidate.write_text(json.dumps(self.inadequate_report()))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = report_check.cli(["--spec", str(spec_path), str(candidate)])
        self.assertEqual(1, code)
        self.assertIn(INADEQUATE_DUPLICATE, err.getvalue())


if __name__ == "__main__":
    unittest.main()
