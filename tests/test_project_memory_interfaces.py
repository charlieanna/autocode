"""Learned project preferences through real argument parsing and public read/forget commands."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_args
import autocode_doctor as doctor
import autocode_project_memory as memory
import autocode_subcommands as subcommands
import autocode_workspaces as workspaces
from providers import opencode


def record(command="python3 -m unittest discover -s tests -t ."):
    return {
        "version": 1,
        "framework": "unittest",
        "test_command": command,
        "conventions": [{"question": "How should future tests be named?", "answer": "Use descriptive behavior names."}],
        "routes": [{"role": "terra", "engine": "codex", "provider": None, "model": "observed-model"}],
        "human_pause_states": ["WAITING_FOR_USER"],
    }


class ProjectMemoryInterfacesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / ".autocode"
        self.directory.mkdir()

    def parse(self, *argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            args, _ = autocode_args.parse(None, [*argv, "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        return args, stderr.getvalue()

    def test_cli_then_config_then_pyproject_then_learned_default(self):
        memory.write(self.root, record())
        args, notice = self.parse("Build it")
        self.assertEqual(record()["test_command"], args.test_command)
        self.assertIn("project.json", notice)
        (self.root / "pyproject.toml").write_text('[tool.autocode]\ntest-command = "pytest -q"\n')
        self.assertEqual("pytest -q", self.parse("Build it")[0].test_command)
        (self.directory / "config.toml").write_text('test-command = "python3 -m unittest tests.test_api"\n')
        self.assertEqual("python3 -m unittest tests.test_api", self.parse("Build it")[0].test_command)
        self.assertEqual(
            "pytest tests/test_explicit.py",
            self.parse("Build it", "--test-command", "pytest tests/test_explicit.py")[0].test_command,
        )

    def test_observed_route_never_selects_engine_provider_models_or_budgets(self):
        memory.write(self.root, record())
        args, _ = self.parse("Build it")
        self.assertIsNone(args.engine)
        self.assertIsNone(args.provider)
        self.assertIsNone(args.terra_model)
        self.assertIsNone(args.single_model)
        self.assertEqual(set(), args._explicit_budget_flags)
        self.assertIsNone(args.max_seconds)
        self.assertFalse(args.accept_completion)
        self.assertIsNone(args.approve_goal)

    def test_fresh_arguments_pin_one_input_and_saved_run_reads_none(self):
        memory.write(self.root, record())
        real = memory.load
        with patch.object(memory, "load", wraps=real) as load:
            args, _ = self.parse("Build it")
        self.assertEqual(1, load.call_count)
        frozen = json.loads(json.dumps(args._project_memory_input))
        memory.write(self.root, record("pytest -q"))
        self.assertEqual(frozen, args._project_memory_input)
        run = self.directory / "runs" / "old"
        run.mkdir(parents=True)
        with patch.object(memory, "load", side_effect=AssertionError("saved run reloaded learned project data")):
            resumed, _ = self.parse("--run-dir", str(run), "--resume-paused")
        self.assertFalse(hasattr(resumed, "_project_memory_input"))
        self.assertIsNone(resumed.test_command)

    def test_existing_task_worktree_reads_the_same_parent_memory_as_forget_and_completion(self):
        memory.write(self.root, record())
        child = self.root / "child-task"
        child.mkdir()
        stderr = io.StringIO()
        with (
            patch.object(workspaces, "metadata", return_value={"project_workspace": str(self.root)}),
            contextlib.redirect_stderr(stderr),
        ):
            args, _ = autocode_args.parse(None, ["Another task", "--workspace", str(child)], opencode.DEFAULT_MODELS)
            shown = doctor.project_memory_snapshot(child)
        self.assertEqual(record()["test_command"], args.test_command)
        self.assertEqual(record()["conventions"], args._project_memory_input["record"]["conventions"])
        self.assertEqual(record()["conventions"], shown["record"]["conventions"])
        self.assertEqual(str(self.directory / "project.json"), shown["source"])
        self.assertFalse((child / ".autocode").exists())

    def test_invalid_learned_authority_is_advisory_and_explicit_config_still_works(self):
        data = record()
        data["approve_plan"] = "forged-token"
        data["provider"] = "untrusted"
        (self.directory / "project.json").write_text(json.dumps(data))
        (self.directory / "config.toml").write_text('test-command = "pytest -q"\n')
        args, notice = self.parse("Build it")
        self.assertEqual("pytest -q", args.test_command)
        self.assertIn("Project memory ignored", notice)
        check = doctor.project_memory_check(self.root)
        self.assertEqual(doctor.WARN, check.status)
        self.assertTrue(doctor.passed([check]))
        self.assertIsNone(args.approve_goal)
        self.assertIsNone(args.provider)

    def test_malformed_worktree_marker_is_advisory_for_reads_and_refuses_forget(self):
        memory.write(self.root, record())
        marker = self.directory / "task-workspace.json"
        before = (self.directory / "project.json").read_bytes()
        for data in ({}, []):
            with self.subTest(marker=data):
                marker.write_text(json.dumps(data))
                args, warning = self.parse("Another task")
                self.assertEqual("invalid", args._project_memory_input["status"])
                self.assertIn("Project memory ignored", warning)
                self.assertEqual(doctor.WARN, doctor.project_memory_check(self.root).status)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(1, subcommands.dispatch(["forget", "--workspace", str(self.root), "--json"]))
                self.assertIn("malformed", json.loads(stdout.getvalue())["error"])
                self.assertEqual(before, (self.directory / "project.json").read_bytes())

    def test_doctor_json_exposes_historical_conventions_and_default_source(self):
        memory.write(self.root, record())
        stdout = io.StringIO()
        with (
            patch.object(doctor, "all_checks", return_value=[doctor.Check("fixture", doctor.OK, "ready")]),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, doctor.cli(["--workspace", str(self.root), "--json"]))
        result = json.loads(stdout.getvalue())
        self.assertEqual(record()["conventions"], result["project_memory"]["record"]["conventions"])
        setting = next(row for row in result["settings"] if row["key"] == "test-command")
        self.assertEqual(record()["test_command"], setting["value"])
        self.assertIn("project.json", setting["source"])

    def test_public_forget_preserves_config_and_saved_run_bytes(self):
        memory.write(self.root, record())
        config = self.directory / "config.toml"
        config.write_text('test-command = "pytest -q"\n')
        run = self.directory / "runs" / "done"
        run.mkdir(parents=True)
        evidence = run / "state.json"
        evidence.write_bytes(b'{"status":"TASK_COMPLETE","approval":"unchanged"}\n')
        before = (config.read_bytes(), evidence.read_bytes())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(0, subcommands.dispatch(["forget", "--workspace", str(self.root), "--json"]))
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["forgotten"])
        self.assertIsNone(memory.read(self.root))
        self.assertEqual(before, (config.read_bytes(), evidence.read_bytes()))

    def test_only_exact_older_generated_ignores_are_upgraded(self):
        ignore = self.directory / ".gitignore"
        old = workspaces.IGNORE_EVERYTHING + workspaces.KEEP_PROJECT_SETTINGS
        ignore.write_text(old)
        workspaces.keep_out_of_git(self.root)
        self.assertIn("!/project.json", ignore.read_text())
        custom = old + "# Our project deliberately keeps learned preferences private.\nproject.json\n"
        ignore.write_text(custom)
        workspaces.keep_out_of_git(self.root)
        self.assertEqual(custom, ignore.read_text())
        external = self.root / "team-ignore"
        external.write_text(old)
        ignore.unlink()
        ignore.symlink_to(external)
        workspaces.keep_out_of_git(self.root)
        self.assertTrue(ignore.is_symlink())
        self.assertEqual(old, external.read_text())


if __name__ == "__main__":
    unittest.main()
