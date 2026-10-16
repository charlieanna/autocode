"""CI admission selects affected jobs and fails closed on unreadable event refs."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_scope

ROOT = Path(__file__).resolve().parents[1]


class ScopeTests(unittest.TestCase):
    def enabled(self, *paths):
        return {job for job, enabled in ci_scope.select(list(paths)).items() if enabled}

    def test_prompt_fix_uses_python_and_installed_resources_without_unrelated_heavy_jobs(self):
        self.assertEqual(
            {"suite", "wheel", "docs"},
            self.enabled(
                "tools/prompts/fragments/milestones/policy.md",
                "tests/test_milestone_policy.py",
                "tests/prompt_rendering_fixtures.json",
                "CHANGELOG.md",
            ),
        )

    def test_docs_only_never_dispatches_provider_or_native_jobs(self):
        self.assertEqual({"docs"}, self.enabled("docs/testing.md"))

    def test_dashboard_and_visual_changes_keep_their_real_browser_jobs(self):
        self.assertEqual({"suite", "dashboard", "wheel"}, self.enabled("tools/dashboard/dashboard_app.js"))
        self.assertTrue({"suite", "visual", "wheel"} <= self.enabled("tools/autocode_visual_browser.cjs"))
        self.assertIn("dashboard", self.enabled("tests/test_preview_browser_ui.py"))
        self.assertIn("dashboard", self.enabled("tests/test_catalogue_t12.py"))
        self.assertIn("dashboard", self.enabled("tests/test_autocode_stop.py"))

    def test_process_provider_and_boundary_changes_keep_native_refusal_consumers(self):
        for path in (
            "tools/autocode_process.py",
            "tools/autocode_session_boundary.py",
            "tools/autocode_supervision_keeper.py",
            "tools/autocode_provider_launch.py",
            "tools/providers/command.py",
        ):
            with self.subTest(path=path):
                self.assertTrue({"suite", "scenarios", "macos", "linux", "windows", "wheel"} <= self.enabled(path))

    def test_native_check_changes_keep_the_macos_job_that_runs_their_seatbelt_tests(self):
        for path in (
            "tools/autocode_native_check_execution.py",
            "tools/autocode_native_check_runtime.py",
            "tools/autocode_native_launch.py",
            "tools/autocode_verification_copy.py",
            "tools/autocode_visual_protection.py",
            "tests/test_native_check_execution.py",
            "tests/test_native_initial_callers.py",
        ):
            with self.subTest(path=path):
                enabled = self.enabled(path)
                self.assertTrue({"suite", "macos"} <= enabled)
                self.assertFalse({"linux", "windows"} & enabled)

    def test_contained_fake_cli_changes_keep_the_linux_job_that_runs_that_flow_without_skips(self):
        for path in (
            "tools/fake_opencode.py",
            "tests/opencode_fixture_cli.py",
            "tests/test_contained_fake_cli.py",
            "tests/test_containment_policy_cli.py",
        ):
            with self.subTest(path=path):
                enabled = self.enabled(path)
                self.assertTrue({"suite", "linux"} <= enabled)
                self.assertFalse({"macos", "windows"} & enabled)

    def test_container_changes_keep_real_teardown_job(self):
        self.assertIn("containers", self.enabled("tests/test_local_compose_scenario.py"))
        self.assertIn("containers", self.enabled("tools/autocode_compose_file.py"))
        self.assertIn("containers", self.enabled("scenarios/harness/component_services.py"))
        self.assertEqual({"containers"}, self.enabled(".github/workflows/local-compose.yml"))

    def test_suite_machinery_exercises_selection_without_unrelated_platform_jobs(self):
        self.assertEqual({"suite", "scenarios", "wheel"}, self.enabled("tools/ci_scope.py"))

    def test_workflow_edit_exercises_its_job_wiring_and_master_always_keeps_every_job(self):
        self.assertEqual(set(ci_scope.JOBS), self.enabled(".github/workflows/tests.yml"))
        for paths in ([], ["docs/testing.md"], ["tools/prompts/fragments/milestones/policy.md"]):
            with self.subTest(paths=paths):
                self.assertTrue(all(ci_scope.select(paths, full=True).values()))

    def test_unknown_input_retains_the_python_gate(self):
        self.assertIn("suite", self.enabled("new-runtime-input.json"))

    def test_full_output_is_explicit_and_preserves_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as printed:
            output = Path(directory) / "outputs"
            output.write_text("existing=value\n")
            self.assertEqual(0, ci_scope.main(["--full", "--github-output", str(output)]))
            self.assertTrue(all(json.loads(printed.getvalue()).values()))
            self.assertEqual(
                ["existing=value", *(f"{job}=true" for job in ci_scope.JOBS)], output.read_text().splitlines()
            )

    def test_missing_or_invalid_refs_fail_instead_of_skipping_checks(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            ci_scope.main([])
        self.assertEqual(2, error.exception.code)
        with self.assertRaises(ValueError):
            ci_scope.changed_paths("master", "a" * 40)

    def test_event_diff_includes_both_sides_of_a_rename_and_excludes_unrelated_base_work(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):

            def git(*args):
                return subprocess.check_output(["git", *args], text=True).strip()

            git("init", "-q")
            git("config", "user.name", "CI fixture")
            git("config", "user.email", "ci@example.invalid")
            Path("tools").mkdir()
            Path("tools/autocode_local_compose.py").write_text("original\n")
            git("add", ".")
            git("commit", "-qm", "base")
            common = git("rev-parse", "HEAD")
            git("checkout", "-qb", "pr")
            git("mv", "tools/autocode_local_compose.py", "docs.md")
            git("commit", "-qm", "rename")
            head = git("rev-parse", "HEAD")
            git("checkout", "-qb", "base-moved", common)
            Path("unrelated.py").write_text("base only\n")
            git("add", ".")
            git("commit", "-qm", "base only")
            base = git("rev-parse", "HEAD")
            paths = ci_scope.changed_paths(base, head)
            self.assertEqual({"tools/autocode_local_compose.py", "docs.md"}, set(paths))
            self.assertTrue(ci_scope.select(paths)["containers"])


class WorkflowGateTests(unittest.TestCase):
    def script(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text()
        gate = workflow.split("\n  ci-required:\n", 1)[1]
        return textwrap.dedent(gate.split("python3 - <<'PYTHON'\n", 1)[1].split("\n          PYTHON", 1)[0])

    def results(self, enabled):
        flags = {flag: str(flag in enabled).lower() for flag in ci_scope.JOBS}
        jobs = {
            "suite": bool(enabled & {"suite", "scenarios", "dashboard"}),
            "macos-tool-containment": "macos" in enabled,
            "linux-tool-containment": "linux" in enabled,
            "windows-fast-suite": "windows" in enabled,
            "wheel-smoke": "wheel" in enabled,
            "visual-comparison": "visual" in enabled,
            "lint": True,
        }
        return {
            "scope": {"result": "success", "outputs": flags},
            **{job: {"result": "success" if required else "skipped"} for job, required in jobs.items()},
        }

    def run_gate(self, results):
        with patch.dict(os.environ, {"NEEDS": json.dumps(results)}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(self.script(), "ci-required", "exec"), {"__name__": "__main__"})

    def test_actual_required_gate_accepts_full_success_and_only_expected_skips(self):
        self.run_gate(self.results(set(ci_scope.JOBS)))
        self.run_gate(self.results({"suite", "wheel"}))
        self.run_gate(self.results({"docs"}))

    def test_selected_jobs_cannot_pass_by_failing_cancelling_or_skipping(self):
        for outcome in ("failure", "cancelled", "skipped"):
            with self.subTest(outcome=outcome):
                results = self.results({"suite", "linux"})
                results["linux-tool-containment"]["result"] = outcome
                with self.assertRaises(SystemExit):
                    self.run_gate(results)

    def test_failed_or_incomplete_scope_cannot_turn_all_jobs_into_safe_skips(self):
        results = self.results(set())
        results["scope"]["result"] = "failure"
        with self.assertRaises(SystemExit):
            self.run_gate(results)
        results = self.results(set())
        del results["scope"]["outputs"]["suite"]
        with self.assertRaises(SystemExit):
            self.run_gate(results)

    def test_workflows_report_scope_for_prs_and_use_full_coverage_for_other_existing_events(self):
        for name in ("tests.yml", "documentation.yml", "local-compose.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text()
                self.assertIn('--base "$BASE" --head "$HEAD"', workflow)
                self.assertIn("--full --github-output", workflow)
                self.assertIn("github.event.pull_request.base.sha", workflow)
                self.assertNotIn("\n    paths:", workflow)


if __name__ == "__main__":
    unittest.main()
