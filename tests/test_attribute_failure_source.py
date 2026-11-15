"""Incomplete suite evidence must never cause a guessed base probe or an empty report."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import attribute_failure


class IncompleteHeadReportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.failed = self.root / "failed.json"
        self.result = self.root / "attribution.json"
        self.probe = self.enterContext(
            patch.object(attribute_failure.subprocess, "run", side_effect=AssertionError("no base probe"))
        )

    def assert_incomplete(self, module=None):
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                result = attribute_failure.main(
                    ["--base", "explicit-base", "--failed", str(self.failed), "--json", str(self.result)]
                )
        except (OSError, ValueError, TypeError, KeyError, AssertionError) as error:
            self.fail(f"Incomplete head evidence must produce an informational refusal: {type(error).__name__}")
        self.assertEqual(0, result)
        self.assertIn(attribute_failure.HEAD_INCOMPLETE, output.getvalue())
        self.assertTrue(output.getvalue().strip())
        self.assertNotIn("All observed head failures reproduced", output.getvalue())
        self.probe.assert_not_called()
        document = json.loads(self.result.read_text())
        self.assertEqual(attribute_failure.HEAD_INCOMPLETE, document[module or "status"])

    def test_missing_head_report_produces_an_explicit_incomplete_observation_without_a_probe(self):
        self.assert_incomplete()

    def test_unreadable_and_malformed_head_reports_do_not_probe_or_crash(self):
        self.failed.mkdir()
        self.assert_incomplete()
        self.failed.rmdir()
        self.failed.write_text("{not complete")
        self.assert_incomplete()

    def test_invalid_report_and_selector_shapes_do_not_invent_failed_modules(self):
        row = {"module": "tests.test_observed", "timed_out": False, "failures": []}
        for document in (
            None,
            True,
            {"version": 1},
            {"version": 1, "failures": "tests.test_observed"},
            {"version": 1, "failures": [None]},
            {"version": 1, "failures": [{"module": ""}]},
            {"version": 1, "failures": [{**row, "module": "tests.bad\nselector"}]},
            {"version": 1, "failures": [{**row, "timed_out": "false"}]},
            {"version": 1, "failures": [{**row, "failures": [{"test": "missing identity"}]}]},
            {"version": 1, "failures": [row, row]},
        ):
            with self.subTest(document=document):
                self.failed.write_text(json.dumps(document))
                self.assert_incomplete()

    def test_a_timed_out_head_requires_no_base_clone_even_when_it_kept_partial_failures(self):
        self.failed.write_text(
            json.dumps(
                {
                    "version": 1,
                    "failures": [
                        {"module": "tests.test_observed", "timed_out": True, "failures": [{"test": "partial"}]}
                    ],
                }
            )
        )
        self.assert_incomplete("tests.test_observed")

    def test_the_existing_explicit_base_empty_report_contract_remains_valid(self):
        self.failed.write_text("[]")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, attribute_failure.main(["--base", "explicit-base", "--failed", str(self.failed)]))
        self.assertIn("No failing module to attribute", output.getvalue())
        self.probe.assert_not_called()

    def test_invalid_versions_and_failure_payloads_do_not_start_a_base_probe(self):
        for document in (
            {"version": True, "failures": []},
            {"version": 0, "failures": []},
            {"version": 1, "failures": [{"module": "tests.test_observed", "failures": None}]},
            {"version": 1, "failures": [{"module": "tests.test_observed", "failures": [None]}]},
        ):
            with self.subTest(document=document):
                self.failed.write_text(json.dumps(document))
                self.assert_incomplete()

    def test_contextual_invalid_or_empty_report_refuses_before_source_observation(self):
        for failures in (["tests.test_observed"], []):
            with self.subTest(failures=failures):
                self.failed.write_text(json.dumps({"version": 1, "failures": failures}))
                with (
                    patch.object(
                        attribute_failure.tested_source.SourceContext,
                        "load",
                        side_effect=AssertionError("invalid head evidence must not inspect source"),
                    ) as source,
                    contextlib.redirect_stdout(io.StringIO()) as shown,
                ):
                    self.assertEqual(
                        0,
                        attribute_failure.main(
                            [
                                "--base",
                                "b" * 40,
                                "--expected-head",
                                "a" * 40,
                                "--tested-source",
                                str(self.root / "missing-source.json"),
                                "--failed",
                                str(self.failed),
                                "--json",
                                str(self.result),
                            ]
                        ),
                    )
                result = json.loads(self.result.read_text())
                self.assertEqual(attribute_failure.HEAD_INCOMPLETE, result["status"])
                self.assertEqual(2, result["version"])
                self.assertEqual({}, result["modules"])
                self.assertIsNone(result["source"])
                self.assertIn("No base probe was run", shown.getvalue())
                self.assertNotIn("tests.test_observed", shown.getvalue())
                source.assert_not_called()
                self.probe.assert_not_called()

    def test_unwritable_attribution_json_preserves_the_incomplete_head_observation(self):
        self.result.mkdir()
        with contextlib.redirect_stdout(io.StringIO()) as shown, contextlib.redirect_stderr(io.StringIO()) as errors:
            try:
                outcome = attribute_failure.main(
                    ["--base", "explicit-base", "--failed", str(self.failed), "--json", str(self.result)]
                )
            except OSError as error:
                self.fail(f"An unwritable informational receipt must not escape: {type(error).__name__}")
        self.assertEqual(0, outcome)
        self.assertIn(attribute_failure.HEAD_INCOMPLETE, shown.getvalue())
        self.assertIn("No base probe was run", shown.getvalue())
        self.assertEqual("Could not save the informational attribution JSON.\n", errors.getvalue())
        self.assertNotIn(str(self.root), shown.getvalue() + errors.getvalue())
        self.assertTrue(self.result.is_dir())
        self.probe.assert_not_called()

    def test_context_arguments_are_paired_before_reading_head_evidence(self):
        for extra in (["--expected-head", "a" * 40], ["--tested-source", str(self.root / "source.json")]):
            with (
                self.subTest(extra=extra),
                patch.object(
                    attribute_failure, "head_rows", side_effect=AssertionError("arguments are unbound")
                ) as head,
                contextlib.redirect_stderr(io.StringIO()) as errors,
                self.assertRaises(SystemExit) as raised,
            ):
                attribute_failure.main(["--base", "explicit-base", "--failed", str(self.failed), *extra])
            self.assertEqual(2, raised.exception.code)
            self.assertIn("--expected-head and --tested-source must be supplied together", errors.getvalue())
            head.assert_not_called()
            self.probe.assert_not_called()


class AttributionWorkflowTests(unittest.TestCase):
    def test_attribution_requires_failure_of_the_executed_suite_step(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml").read_text()
        suite = workflow.split("      - name: Run the suite gate", 1)[1].split(
            "      - name: Attribute red to base or head", 1
        )[0]
        attribution = workflow.split("      - name: Attribute red to base or head", 1)[1].split(
            "\n  scenario-harness:", 1
        )[0]
        self.assertIn("id: suite_gate", suite)
        self.assertIn("steps.suite_gate.outcome == 'failure'", attribution)
        self.assertIn("github.event_name == 'pull_request'", attribution)
        self.assertIn('tools/run_suite.py --verbosity 2 --changed "$BASE" --failed-out .suite-failed.json', suite)
        self.assertIn("tools/run_suite.py --verbosity 2 --failed-out .suite-failed.json", suite)
        capture = workflow.split("      - name: Record the checkout the suite will test", 1)[1].split(
            "      - name: Run the suite gate", 1
        )[0]
        self.assertIn('tools/ci_tested_source.py --expected-head "$PR_HEAD" --event-base "$BASE"', capture)
        self.assertIn("if [ -s .attribution.md ]; then", attribution)
        self.assertLess(attribution.index("if [ -s .attribution.md ]; then"), attribution.index("gh pr comment"))
        self.assertIn("actions/upload-artifact@v4", attribution)
        self.assertIn("include-hidden-files: true", attribution)
        self.assertIn("retention-days: 7", attribution)
