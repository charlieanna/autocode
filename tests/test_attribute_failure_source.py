"""Incomplete suite evidence must never cause a guessed base probe or an empty report."""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import attribute_failure
import run_suite


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
        self.assertIn("      - name: Prepare suite evidence directory", workflow)
        preparation = workflow.split("      - name: Prepare suite evidence directory", 1)[1].split(
            "      - name: Record the checkout the suite will test", 1
        )[0]
        self.assertIn("id: suite_evidence", preparation)
        self.assertIn("if: needs.scope.outputs.suite == 'true'", preparation)
        suite = workflow.split("      - name: Run the suite gate", 1)[1].split(
            "      - name: Attribute red to base or head", 1
        )[0]
        attribution = workflow.split("      - name: Attribute red to base or head", 1)[1].split(
            "\n  scenario-harness:", 1
        )[0]
        self.assertIn("id: suite_gate", suite)
        self.assertIn("steps.suite_gate.outcome == 'failure'", attribution)
        self.assertIn("github.event_name == 'pull_request'", attribution)
        self.assertIn(
            'tools/run_suite.py --verbosity 2 --changed "$BASE" --all-fast --shard "$SHARD" '
            '--failed-out "$SUITE_EVIDENCE_DIR/.suite-failed.json"',
            suite,
        )
        self.assertIn(
            'tools/run_suite.py --verbosity 2 --shard "$SHARD" --failed-out "$SUITE_EVIDENCE_DIR/.suite-failed.json"',
            suite,
        )
        capture = workflow.split("      - name: Record the checkout the suite will test", 1)[1].split(
            "      - name: Run the suite gate", 1
        )[0]
        self.assertIn('tools/ci_tested_source.py --expected-head "$PR_HEAD" --event-base "$BASE"', capture)
        self.assertIn('if [ -s "$SUITE_EVIDENCE_DIR/.attribution.md" ]; then', attribution)
        self.assertLess(
            attribution.index('if [ -s "$SUITE_EVIDENCE_DIR/.attribution.md" ]; then'),
            attribution.index("gh pr comment"),
        )
        self.assertIn('cat "$SUITE_EVIDENCE_DIR/.attribution.md" >> "$GITHUB_STEP_SUMMARY"', attribution)
        self.assertIn('gh pr comment "$PR" --body-file "$SUITE_EVIDENCE_DIR/.attribution.md"', attribution)
        self.assertIn("actions/upload-artifact@v4", attribution)
        self.assertIn("include-hidden-files: true", attribution)
        self.assertIn("retention-days: 7", attribution)


@unittest.skipUnless(os.name == "posix" and shutil.which("git") and shutil.which("bash"), "requires Git and bash")
class WorkflowEvidenceSelectionTests(unittest.TestCase):
    """Run only workflow preparation/capture and Git selection, never a suite or provider."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        self.runner_temp = self.root / "runner temp"
        self.runner_temp.mkdir()
        home = self.root / "home"
        home.mkdir()
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "PATH": os.defpath,
                    "HOME": str(home),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_COUNT": "0",
                    "RUNNER_TEMP": str(self.runner_temp),
                    "GITHUB_OUTPUT": str(self.root / "step-output"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                clear=True,
            )
        )
        self.workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml").read_text()
        self.git("-c", "init.templateDir=", "init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.git("config", "maintenance.auto", "false")
        self.git("config", "gc.auto", "0")
        (self.repo / ".gitignore").write_text(".venv/\n")
        self.sources = {
            name: "# Selection-only fixture; never executed.\n"
            for name in (
                "tests.test_architecture",
                "tests.test_focus",
                *run_suite.ci_test_selection.CORE_CONSUMERS,
                *run_suite.ci_test_selection.PUBLIC_FLOW_CONSUMERS,
            )
        }
        for name, content in self.sources.items():
            path = self.repo / (name.replace(".", "/") + ".py")
            path.parent.mkdir(exist_ok=True)
            path.write_text(content)
        tools = self.repo / "tools"
        tools.mkdir()
        for name in ("ci_tested_source.py", "autocode_util.py"):
            shutil.copyfile(Path(run_suite.__file__).parent / name, tools / name)
        python = self.repo / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        self.git("add", ".")
        self.git("commit", "-qm", "selection base")
        self.base = self.git("rev-parse", "HEAD")
        (self.repo / "tests/test_focus.py").write_text("# Intended change; never executed.\n")
        self.git("add", "tests/test_focus.py")
        self.git("commit", "-qm", "one changed test")
        self.head = self.git("rev-parse", "HEAD")
        os.environ.update(BASE=self.base, PR_HEAD=self.head)
        self.enterContext(patch.object(run_suite, "REPO_ROOT", self.repo))

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()

    def step(self, name):
        marker = f"      - name: {name}\n"
        if marker not in self.workflow:
            return ""
        return self.workflow.split(marker, 1)[1].split("\n      - ", 1)[0]

    def script(self, name):
        return textwrap.dedent(self.step(name).split("        run: |\n", 1)[1])

    def prepare_evidence(self):
        if self.step("Prepare suite evidence directory"):
            self.run_script("Prepare suite evidence directory")
            outputs = dict(line.split("=", 1) for line in Path(os.environ["GITHUB_OUTPUT"]).read_text().splitlines())
            os.environ["SUITE_EVIDENCE_DIR"] = outputs["directory"]

    def run_script(self, name):
        # Only the exact reviewed preparation/capture scripts are executed. The
        # source helper makes two read-only Git observations, never a test run.
        self.assertIn(name, ("Prepare suite evidence directory", "Record the checkout the suite will test"))
        subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", self.script(name)],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def output_path(self, step, option):
        token = re.search(rf"{re.escape(option)}\s+(\"[^\"]+\"|[^\s]+)", self.script(step)).group(1)
        path = Path(os.path.expandvars(token.strip('"')))
        return path if path.is_absolute() else self.repo / path

    def selection(self):
        return set(run_suite.select_tests(run_suite.changed_paths(self.base), self.sources))

    def test_pre_suite_source_capture_preserves_the_covered_jobs_selection(self):
        covered = self.selection()
        self.assertEqual({"tests.test_architecture", "tests.test_focus"}, covered)
        self.prepare_evidence()
        self.run_script("Record the checkout the suite will test")
        captured = json.loads(self.output_path("Record the checkout the suite will test", "--out").read_text())
        self.assertEqual(self.head, captured["tested_revision"])
        self.assertEqual(self.base, captured["comparison_base"])
        self.assertTrue(captured["tracked_clean"])
        self.assertEqual(covered, self.selection(), "informational capture must not change ordinary suite selection")

    def test_all_four_outputs_and_their_consumers_share_external_evidence(self):
        covered = self.selection()
        self.prepare_evidence()
        paths = {
            ".suite-source.json": self.output_path("Record the checkout the suite will test", "--out"),
            ".suite-failed.json": self.output_path("Run the suite gate", "--failed-out"),
            ".attribution.json": self.output_path("Attribute red to base or head", "--json"),
            ".attribution.md": self.output_path("Attribute red to base or head", "tee"),
        }
        for name, path in paths.items():
            with self.subTest(output=name):
                path.write_text("{}\n")
                self.assertEqual(covered, self.selection(), "later reports must not become changed repository inputs")
                self.assertEqual(self.runner_temp / "autocode-suite-evidence" / name, path)
        attribution = "Attribute red to base or head"
        self.assertEqual(paths[".suite-source.json"], self.output_path(attribution, "--tested-source"))
        self.assertEqual(paths[".suite-failed.json"], self.output_path(attribution, "--failed"))
        self.assertEqual(paths[".attribution.md"], self.output_path(attribution, "--body-file"))
        for step in ("Record the checkout the suite will test", "Run the suite gate", attribution):
            self.assertIn("SUITE_EVIDENCE_DIR: ${{ steps.suite_evidence.outputs.directory }}", self.step(step))
        upload = self.step("Preserve suite attribution receipts")
        for name in paths:
            self.assertIn("${{ steps.suite_evidence.outputs.directory }}/" + name, upload)

    def test_genuine_unknown_repository_inputs_still_select_public_consumers(self):
        covered = self.selection()
        self.prepare_evidence()
        path = self.repo / "genuine-project-input.json"
        path.write_text("{}\n")
        self.assertEqual(
            covered
            | set(run_suite.ci_test_selection.CORE_CONSUMERS)
            | set(run_suite.ci_test_selection.PUBLIC_FLOW_CONSUMERS),
            self.selection(),
        )
