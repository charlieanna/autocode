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

    def test_chat_contract_changes_select_the_extension_consumer(self):
        for path in (
            "vscode-extension/src/chat.ts",
            "tools/autocode_chat_view.py",
            "tools/autocode_run_view.py",
            "tools/autocode_quota_route.py",
            "tools/autocode_roles.py",
            "tools/autocode_usage.py",
            "tools/autocode_progress_view.py",
        ):
            self.assertIn("extension", self.enabled(path))

    def test_dashboard_and_visual_changes_keep_their_real_browser_jobs(self):
        self.assertEqual({"suite", "dashboard", "wheel"}, self.enabled("tools/dashboard/dashboard_app.js"))
        self.assertTrue({"suite", "visual", "wheel"} <= self.enabled("tools/autocode_visual_browser.cjs"))
        self.assertIn("dashboard", self.enabled("tests/test_preview_browser_ui.py"))
        self.assertIn("dashboard", self.enabled("tests/test_catalogue_t12.py"))
        self.assertIn("dashboard", self.enabled("tests/test_autocode_stop.py"))

    def test_legacy_guard_changes_keep_the_dashboard_approval_consumer(self):
        self.assertTrue({"suite", "dashboard"} <= self.enabled("tools/autocode_legacy_process.py"))

    def test_process_provider_and_boundary_changes_keep_native_refusal_consumers(self):
        for path in (
            "tools/autocode_process.py",
            "tools/autocode_session_boundary.py",
            "tools/autocode_supervision_keeper.py",
            "tools/autocode_provider_launch.py",
            "tools/autocode_python_entrypoints.py",
            "tools/providers/command.py",
        ):
            with self.subTest(path=path):
                self.assertTrue({"suite", "scenarios", "macos", "linux", "windows", "wheel"} <= self.enabled(path))

    def test_native_check_changes_keep_the_macos_job_that_runs_their_seatbelt_tests(self):
        for path, linux in (
            # The contained OpenCode flow loads these runtime modules, so linux runs too (#709).
            ("tools/autocode_native_check_execution.py", True),
            ("tools/autocode_native_check_runtime.py", True),
            ("tools/autocode_native_launch.py", True),
            ("tools/autocode_verification_copy.py", True),
            ("tools/autocode_visual_protection.py", True),
            # It does not load these test modules.
            ("tests/test_native_check_execution.py", False),
            ("tests/test_native_initial_callers.py", False),
        ):
            with self.subTest(path=path):
                enabled = self.enabled(path)
                self.assertTrue({"suite", "macos"} <= enabled)
                self.assertNotIn("windows", enabled)
                self.assertEqual(linux, "linux" in enabled)

    def test_contained_fake_cli_changes_keep_the_linux_job_that_runs_that_flow_without_skips(self):
        for path in (
            "tools/fake_opencode.py",
            "tests/opencode_fixture_cli.py",
            "tests/test_contained_fake_cli.py",
            "tests/test_containment_policy_cli.py",
            # Files the flow loads that no prefix named: the status view it reads, the setup
            # decision to contain, the delegate the fake runs and the CLI flow harness (#709).
            "tools/autocode_run_view.py",
            "tools/autocode_containment_policy.py",
            "tools/fake_codex.py",
            "tests/test_subprocess.py",
            # Workers the runtime starts only by file name, never by an import.
            "tools/autocode_builder_worker.py",
            "tools/autocode_verification_prepare_worker.py",
            # A new module in scenarios/, first on the tests' sys.path, would shadow the one the flow imports.
            "scenarios/autocode_tool_containment.py",
            # Schemas each stage's report is checked against.
            "tools/autocode-schemas/v2/terra-report.schema.json",
            "tools/autocode-schemas/sol-report.schema.json",
        ):
            with self.subTest(path=path):
                enabled = self.enabled(path)
                self.assertTrue({"suite", "linux"} <= enabled)
                self.assertFalse({"macos", "windows"} & enabled)

    def test_evidence_and_planning_policy_select_their_black_box_scenarios(self):
        for path in (
            "tools/autocode_report_identity.py",
            "tools/autocode_planning_advice.py",
            "tools/autocode_stuck_job.py",
            "tools/autocode_regression.py",
            "tools/autocode_verify.py",
        ):
            with self.subTest(path=path):
                self.assertIn("scenarios", self.enabled(path))

    def test_a_tools_fixture_a_scenario_copies_turns_the_scenario_harness_on(self):
        # scenarios/test_contained_opencode.py copies and runs the fake OpenCode client; no import names it (#709).
        self.assertEqual({"suite", "scenarios", "linux", "wheel"}, self.enabled("tools/fake_opencode.py"))
        # A tools/ fixture no scenario copies leaves the scenario harness off.
        self.assertNotIn("scenarios", self.enabled("tools/fake_codex.py"))

    def test_files_the_contained_flow_never_reads_leave_the_linux_job_off(self):
        # Runtime modules the flow neither imports nor starts by name, the dashboard,
        # the scenario runner, a fixture and a prompt.
        for path in (
            "tools/autocode_doctor.py",
            "tools/inprocess_cli.py",
            "tools/dashboard/agent_console.py",
            "scenarios/run.py",
            "tools/fixtures/fake_docker.py",
            "tools/prompts/fragments/milestones/policy.md",
        ):
            with self.subTest(path=path):
                self.assertNotIn("linux", self.enabled(path))

    def test_the_contained_flow_data_is_a_directory_a_flow_module_reads_by_name(self):
        # Renaming the schema directory fails here instead of quietly dropping the trigger.
        sources = [(ROOT / path).read_text() for path in ci_scope.contained_flow_files() if (ROOT / path).is_file()]
        for directory in ci_scope.CONTAINED_FLOW_DATA:
            with self.subTest(directory=directory):
                self.assertTrue(list((ROOT / directory).glob("*.json")))
                self.assertTrue(any(f'"{Path(directory).name}"' in source for source in sources))

    def test_the_contained_flow_trigger_starts_from_the_module_the_linux_job_runs(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text()
        job = workflow.split("\n  linux-tool-containment:\n", 1)[1].split("\n  windows-fast-suite:\n", 1)[0]
        module = ci_scope.CONTAINED_FLOW.removesuffix(".py").replace("/", ".")
        self.assertIn(f'"{module}.ContainedFakeOpenCodeFlow"', job)

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


class ContainedFlowFilesTests(unittest.TestCase):
    """The linux job's trigger follows what the contained flow loads, so it stays right as imports change."""

    FILES = {
        "tests/__init__.py": "",
        "tests/test_contained_fake_cli.py": "import autocode_tool_containment\nfrom . import harness\n",
        # A function-level import, the CLI and its fake started or copied by file name, and a
        # scenarios/ helper imported by its top-level name (tests/__init__.py puts scenarios/ on sys.path).
        "tests/harness.py": (
            'CLI = TOOLS / "cli.py"\nFAKE = "fake_client.py"\nfrom phases import guard\n'
            "def later():\n    from tests import lazy\n"
        ),
        "tests/lazy.py": "",
        "tests/unrelated_helper.py": "",
        "scenarios/phases.py": "",
        "scenarios/unrelated_phase.py": "",
        "tools/autocode_tool_containment.py": "",
        # A worker the runtime starts beside itself, a package import and an import whose file is gone.
        "tools/cli.py": 'from units import stage\nimport removed_module\nWORKER = HERE.with_name("worker.py")\n',
        "tools/units/__init__.py": "",
        "tools/units/stage.py": 'from . import common\nimport providers.client\nHELPER = HERE / "helper.py"\n',
        "tools/units/common.py": "",
        "tools/units/helper.py": "",
        "tools/providers/__init__.py": "",
        "tools/providers/client.py": "",
        "tools/worker.py": "import broken\n",
        # Unparsable: still a dependency, but its imports cannot be read.
        "tools/broken.py": "def (:\nimport never_followed\n",
        "tools/never_followed.py": "",
        "tools/fake_client.py": "",
        # Loads the flow's modules but is not loaded by it.
        "tools/unrelated.py": "import cli\nimport autocode_tool_containment\n",
    }
    UNLOADED = {
        "tests/unrelated_helper.py",
        "scenarios/unrelated_phase.py",
        "tools/never_followed.py",
        "tools/unrelated.py",
    }

    def tree(self, directory):
        root = Path(directory)
        for path, text in self.FILES.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        return root

    def test_follows_imports_and_named_scripts_and_leaves_out_what_the_flow_never_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            files = ci_scope.contained_flow_files(self.tree(directory))
        self.assertEqual(set(), set(self.FILES) - self.UNLOADED - files)
        self.assertEqual(set(), self.UNLOADED & files)
        # Deleting a module the flow still imports, or adding one that would shadow it, selects the job too.
        self.assertIn("tools/removed_module.py", files)
        self.assertTrue(all(path.endswith(".py") for path in files))

    def test_a_renamed_flow_module_fails_selection_instead_of_skipping_the_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.tree(directory)
            (root / ci_scope.CONTAINED_FLOW).unlink()
            with self.assertRaises(FileNotFoundError):
                ci_scope.contained_flow_files(root)


class WorkflowGateTests(unittest.TestCase):
    def script(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text()
        gate = workflow.split("\n  ci-required:\n", 1)[1]
        return textwrap.dedent(gate.split("python3 - <<'PYTHON'\n", 1)[1].split("\n          PYTHON", 1)[0])

    def results(self, enabled):
        flags = {flag: str(flag in enabled).lower() for flag in ci_scope.JOBS}
        jobs = {
            "suite": bool(enabled & {"suite", "scenarios", "dashboard"}),
            "extension": "extension" in enabled,
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
