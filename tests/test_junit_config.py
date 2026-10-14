"""Trusted JUnit selection through CLI/project settings and paused repairs."""

import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_args as args_module
import autocode_verification_config as configuration
from providers import opencode


class JUnitConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="junit-settings-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def parse(self, *argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            args, _ = args_module.parse(None, [*argv, "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        return args, stderr.getvalue()

    def refused(self, *argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stopped:
            args_module.parse(None, [*argv, "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        self.assertEqual(2, stopped.exception.code)
        return stderr.getvalue()

    def project_settings(self, text, *, pyproject=False):
        if pyproject:
            (self.root / "pyproject.toml").write_text("[tool.autocode]\n" + text)
        else:
            (self.root / ".autocode").mkdir(exist_ok=True)
            (self.root / ".autocode/config.toml").write_text(text)

    def test_cli_normalizes_report_path_and_keeps_the_explicit_command(self):
        args, _ = self.parse(
            "Fix page rounding",
            "--test-command",
            "mvn -o test",
            "--test-report",
            "junit:./target//reports/results.xml",
        )
        selected = configuration.validate({"test_command": args.test_command, "test_report": args.test_report})
        self.assertEqual(
            {"test_command": "mvn -o test", "test_report": "junit:target/reports/results.xml"},
            selected,
        )

    def test_cli_refuses_unsafe_or_unknown_report_formats_before_a_run(self):
        for report in (
            "trx:results.xml",
            "junit:",
            "junit:/tmp/results.xml",
            "junit:../results.xml",
            "junit:target/../results.xml",
            "junit:target\\results.xml",
            "junit:results\n.xml",
            "junit:results\x00.xml",
            "junit:drive:results.xml",
        ):
            with self.subTest(report=report):
                self.assertIn("--test-report", self.refused("Fix it", "--test-report", report))

    def test_report_requires_an_explicit_nonempty_trusted_suite_command(self):
        for command in (None, "", "  ", 7):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "explicit trusted --test-command"):
                configuration.validate({"test_report": "junit:results.xml", "test_command": command})

    def test_report_cannot_borrow_a_python_discovery_root(self):
        with self.assertRaisesRegex(ValueError, "--test-root.*cannot accompany --test-report"):
            configuration.validate(
                {"test_command": "mvn test", "test_report": "junit:results.xml", "test_root": "component"}
            )

    def test_commands_without_a_report_retain_existing_python_settings(self):
        options = {"test_command": "python -m unittest", "test_root": "component"}
        self.assertEqual(options, configuration.validate(dict(options)))

    def test_project_config_toml_accepts_the_command_and_normalizes_its_report(self):
        self.project_settings('test-command = "mvn -o test"\ntest-report = "junit:./target//results.xml"\n')
        args, stderr = self.parse("Fix it")
        self.assertEqual(("mvn -o test", "junit:target/results.xml"), (args.test_command, args.test_report))
        self.assertIn("test-command, test-report", stderr)
        self.assertIn(str(self.root / ".autocode/config.toml"), stderr)

    def test_pyproject_accepts_junit_settings_and_cli_report_wins(self):
        self.project_settings('test-command = "mvn -o test"\ntest-report = "junit:file.xml"\n', pyproject=True)
        args, stderr = self.parse("Fix it", "--test-report", "junit:target/typed.xml")
        self.assertEqual(("mvn -o test", "junit:target/typed.xml"), (args.test_command, args.test_report))
        self.assertIn("[tool.autocode]", stderr)
        self.assertNotIn("test-report", stderr)

    def test_invalid_project_report_names_its_source_and_setting(self):
        self.project_settings('test-command = "mvn test"\ntest-report = "junit:../elsewhere.xml"\n')
        stderr = self.refused("Fix it")
        self.assertIn(str(self.root / ".autocode/config.toml"), stderr)
        self.assertIn("is not valid for --test-report", stderr)

    def test_saved_run_does_not_adopt_a_changed_project_report(self):
        self.project_settings('test-command = "mvn test"\ntest-report = "junit:project.xml"\n')
        run = self.root / ".autocode/runs/saved"
        run.mkdir(parents=True)
        args, stderr = self.parse("--run-dir", str(run), "--resume-paused")
        self.assertIsNone(args.test_command)
        self.assertIsNone(args.test_report)
        self.assertNotIn("Project settings", stderr)


class JUnitReportRepairTests(unittest.TestCase):
    def saved(self, **overrides):
        state = {
            "status": "PAUSED_REQUESTED",
            "next_stage": "sol",
            "settings": {
                "regression": {
                    "test_command": "mvn -o test",
                    "test_report": "junit:target/old.xml",
                    "test_timeout": 42,
                },
                "roles": {"sol": {"model": "approved-model"}},
            },
            "goal_contract": {"hash": "approved"},
            "regression_proof": {"verdict": "PASS", "path": "old-proof.json"},
        }
        state.update(overrides)
        return state

    def repair(self, state, **overrides):
        args = SimpleNamespace(test_report="junit:target/new.xml", resume_paused=True, **overrides)
        return configuration.configure_resume(state, state["settings"], args)

    def test_a_report_change_is_allowed_at_either_reconciled_verification_checkpoint(self):
        for stage in ("sol", "astra_checkpoint"):
            with self.subTest(stage=stage):
                state = self.saved(next_stage=stage)
                old_contract = copy.deepcopy(state["goal_contract"])
                settings = self.repair(state)
                self.assertEqual(
                    {"test_command": "mvn -o test", "test_report": "junit:target/new.xml", "test_timeout": 42},
                    settings["regression"],
                )
                self.assertEqual(old_contract, state["goal_contract"])
                self.assertEqual({"sol": {"model": "approved-model"}}, settings["roles"])
                self.assertEqual(1, len(state["user_events"]))
                event = state["user_events"][0]
                self.assertEqual("verification_commands_changed", event["kind"])
                self.assertEqual("user_cli", event["actor"])
                self.assertEqual({"test_report": "junit:target/old.xml"}, event["previous"])
                self.assertEqual({"test_report": "junit:target/new.xml"}, event["current"])

    def test_a_suite_and_report_can_be_repaired_together(self):
        state = self.saved()
        settings = self.repair(state, test_command="./gradlew test")
        self.assertEqual("./gradlew test", settings["regression"]["test_command"])
        self.assertEqual(
            {"test_command": "./gradlew test", "test_report": "junit:target/new.xml"},
            state["user_events"][0]["current"],
        )

    def test_same_saved_report_is_a_noop_even_during_an_active_attempt(self):
        state = self.saved(status="RUNNING", next_stage="terra", active_stage={"stage": "terra"})
        before = copy.deepcopy(state)
        args = SimpleNamespace(test_report="junit:target/old.xml", resume_paused=False)
        self.assertIs(state["settings"], configuration.configure_resume(state, state["settings"], args))
        self.assertEqual(before, state)

    def test_report_repair_requires_both_a_paused_run_and_explicit_resume(self):
        for status, resumed in (("RUNNING", True), ("TASK_COMPLETE", True), ("PAUSED_REQUESTED", False)):
            with self.subTest(status=status, resumed=resumed):
                state = self.saved(status=status)
                before = copy.deepcopy(state)
                args = SimpleNamespace(test_report="junit:new.xml", resume_paused=resumed)
                with self.assertRaisesRegex(ValueError, "paused run and --resume-paused"):
                    configuration.configure_resume(state, state["settings"], args)
                self.assertEqual(before, state)

    def test_a_late_report_repair_cannot_reuse_an_accepted_proof(self):
        for stage in ("terra", "astra_review", "astra_resolve"):
            with self.subTest(stage=stage):
                state = self.saved(next_stage=stage)
                before = copy.deepcopy(state)
                with self.assertRaisesRegex(ValueError, "new proof must run before any acceptance"):
                    self.repair(state)
                self.assertEqual(before, state)

    def test_active_and_uncertain_attempts_must_be_reconciled_before_changing_the_report(self):
        for key in (
            "active_stage",
            "pending_report_repair",
            "uncertain_artifacts",
            "active_runner_check",
            "runner_check",
        ):
            with self.subTest(key=key):
                state = self.saved(**{key: {"stage": "sol"}})
                before = copy.deepcopy(state)
                with self.assertRaisesRegex(ValueError, "Reconcile the active or uncertain attempt"):
                    self.repair(state)
                self.assertEqual(before, state)

    def test_invalid_report_never_changes_saved_settings_or_appends_an_event(self):
        for report in ("trx:results.xml", "junit:../elsewhere.xml", "", " "):
            with self.subTest(report=report):
                state = self.saved(user_events=[{"kind": "earlier-user-action"}])
                before = copy.deepcopy(state)
                args = SimpleNamespace(test_report=report, resume_paused=True)
                with self.assertRaises(ValueError):
                    configuration.configure_resume(state, state["settings"], args)
                self.assertEqual(before, state)

    def test_a_report_cannot_be_added_to_saved_implicit_discovery(self):
        state = self.saved()
        state["settings"]["regression"] = {"test_timeout": 42}
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "explicit trusted --test-command"):
            self.repair(state)
        self.assertEqual(before, state)

    def test_a_saved_python_root_cannot_be_reinterpreted_as_a_junit_project(self):
        state = self.saved()
        state["settings"]["regression"]["test_root"] = "component"
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, "--test-root.*cannot accompany --test-report"):
            self.repair(state)
        self.assertEqual(before, state)


if __name__ == "__main__":
    unittest.main()
