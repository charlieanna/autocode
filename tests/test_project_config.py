"""Project settings for a new run, from .autocode/config.toml or [tool.autocode] (#707)."""

import contextlib
import io
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_args
import autocode_project_config as project_config
import autocode_workspaces as workspaces
from providers import opencode

DOCS = Path(__file__).resolve().parents[1] / "docs" / "cli.md"


class ProjectConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write(self, config=None, pyproject=None):
        if config is not None:
            (self.root / ".autocode").mkdir(exist_ok=True)
            (self.root / ".autocode" / "config.toml").write_text(config)
        if pyproject is not None:
            (self.root / "pyproject.toml").write_text(pyproject)

    def parse(self, *argv):
        """autocode_args.parse as the CLI calls it; (args, stderr)."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            args, _ = autocode_args.parse(None, [*argv, "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        return args, stderr.getvalue()

    def refused(self, *argv):
        """The parser's refusal of argv: its stderr."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            autocode_args.parse(None, [*argv, "--workspace", str(self.root)], opencode.DEFAULT_MODELS)
        self.assertEqual(2, stop.exception.code)
        return stderr.getvalue()

    def test_config_toml_wins_a_key_pyproject_also_sets_and_each_keeps_its_source(self):
        self.write('engine = "codex"\nmax-seconds = 3600\n', '[tool.autocode]\nengine = "opencode"\nsol-model = "m"\n')
        config, pyproject = str(self.root / ".autocode/config.toml"), f"{self.root / 'pyproject.toml'} [tool.autocode]"
        self.assertEqual(
            {"engine": ("codex", config), "max-seconds": (3600, config), "sol-model": ("m", pyproject)},
            project_config.read(self.root),
        )
        self.assertEqual({}, project_config.read(self.root / "missing"))

    def test_sub_tables_of_tool_autocode_belong_to_other_tooling(self):
        # AutoCode's own pyproject keeps its CLI entry-point policy in [tool.autocode.cli]; a new run
        # started in this repository must not refuse it as an unknown setting.
        self.assertEqual({}, project_config.read(Path(__file__).resolve().parents[1]))
        self.write(pyproject='[tool.autocode]\nengine = "codex"\n\n[tool.autocode.cli]\npublic-entry-points = ["x"]\n')
        args, _ = self.parse("Build it")
        self.assertEqual("codex", args.engine)

    def test_a_new_run_takes_the_files_settings_as_if_typed(self):
        self.write('engine = "codex"\ntest-command = "pytest -q"\nmax-seconds = 3600\nunlimited-iterations = true\n')
        args, stderr = self.parse("Build it")
        typed, _ = self.parse(
            "Build it", "--engine", "codex", "--test-command", "pytest -q", "--max-seconds", "3600",
            "--unlimited-iterations",
        )  # fmt: skip
        for dest in ("engine", "test_command", "max_seconds", "unlimited_iterations", "_explicit_budget_flags"):
            self.assertEqual(getattr(typed, dest), getattr(args, dest), dest)
        self.assertEqual({"max_seconds", "unlimited_iterations"}, args._explicit_budget_flags)  # never a runner default
        self.assertIn("Project settings from", stderr)
        self.assertIn("engine, test-command, max-seconds, unlimited-iterations", stderr)

    def test_a_cost_cap_in_the_file_is_an_amount_and_typed(self):
        # TOML writes an amount as a float; it is taken only for a flag that parses an amount (#721).
        for text in ("max-cost-usd = 12.5\n", 'max-cost-usd = "12.5"\n'):
            with self.subTest(text=text):
                self.write(text)
                args, _ = self.parse("Build it")
                self.assertEqual(12.5, args.max_cost_usd)
                self.assertEqual({"max_cost_usd"}, args._explicit_budget_flags)

    def test_a_typed_flag_wins_with_every_file_key_it_cannot_be_combined_with(self):
        self.write('engine = "codex"\nsol-model = "file-sol"\nunlimited-iterations = true\nmax-stage-seconds = 9\n')
        args, _ = self.parse("Build it", "--engine", "opencode", "--single-model", "one", "--max-iterations", "4")
        self.assertEqual(("opencode", "one", None), (args.engine, args.single_model, args.sol_model))
        self.assertEqual((4, False, 9), (args.max_iterations, args.unlimited_iterations, args.max_stage_seconds))
        self.write('single-model = "file-one"\n')
        args, _ = self.parse("Build it", "--astra-model", "typed")
        self.assertEqual(("typed", None), (args.astra_model, args.single_model))

    def test_engine_claude_selects_the_built_in_claude_provider(self):
        """#717: --engine claude, typed or in the project's file, is the facade engine with provider claude."""
        self.write('engine = "claude"\n')
        args, _ = self.parse("Build it")
        self.assertEqual(("opencode", "claude"), (args.engine, args.provider))
        args, _ = self.parse("Build it", "--engine", "claude")
        self.assertEqual(("opencode", "claude"), (args.engine, args.provider))
        args, _ = self.parse("Build it", "--engine", "claude", "--provider", "claude")
        self.assertEqual(("opencode", "claude"), (args.engine, args.provider))
        args, _ = self.parse("Build it", "--engine", "codex")
        self.assertEqual(("codex", None), (args.engine, args.provider))
        stderr = self.refused("Build it", "--engine", "claude", "--provider", "kilocode")
        self.assertIn("engine claude runs the built-in claude provider; it cannot run --provider kilocode", stderr)
        # #2330: codex is an engine; the old refusal asked for ~/.config/autocode/providers/codex.toml.
        self.write("")
        stderr = self.refused("Build it", "--provider", "codex")
        self.assertIn("codex is an engine, not a provider config: run with --engine codex", stderr)
        self.assertNotIn("codex.toml", stderr)

    def test_a_saved_run_never_reads_the_file(self):
        self.write('engine = "codex"\nmax-seconds = 5\n')
        run = self.root / ".autocode" / "runs" / "r1"
        run.mkdir(parents=True)
        args, stderr = self.parse("--run-dir", str(run), "--resume-paused")
        self.assertEqual((None, None, set()), (args.engine, args.max_seconds, args._explicit_budget_flags))
        self.assertNotIn("Project settings", stderr)

    def test_the_provider_task_preflight_unknown_keys_and_bad_values_are_refused_before_a_run(self):
        config = str(self.root / ".autocode/config.toml")
        cases = {
            'provider = "mine"\n': "provider cannot be set by a project",
            'sol-provider = "mine"\n': "sol-provider cannot be set by a project",
            'task-preflight = "pre.json"\n': "task-preflight cannot be set by a project",
            'colour = "blue"\n': "unknown setting 'colour'; a project file sets only engine",
            "max_cost_usd = 5\n": "unknown setting 'max_cost_usd'; did you mean 'max-cost-usd'? a project file sets only",
            'test_command = "pytest"\n': "unknown setting 'test_command'; did you mean 'test-command'?",
            "max-cost = 5\n": "unknown setting 'max-cost'; did you mean 'max-cost-usd'?",
            "max-seconds = 'soon'\n": "max-seconds = 'soon' is not valid for --max-seconds",
            'sol-reasoning-effort = "extreme"\n': "sol-reasoning-effort must be one of low, medium, high",
            'unlimited-iterations = "yes"\n': "unlimited-iterations must be true or false",
            "max-seconds = true\n": "max-seconds must be a string or a whole number",
            "max-seconds = 2.5\n": "max-seconds must be a string or a whole number",
            "max-cost-usd = -1.0\n": "max-cost-usd = -1.0 is not valid for --max-cost-usd",
            "max-cost-usd = nan\n": "max-cost-usd = nan is not valid for --max-cost-usd",
            "max-cost-usd = true\n": "max-cost-usd must be a string or a whole number",
            "engine = [\n": "cannot be read as TOML",
        }
        for text, message in cases.items():
            with self.subTest(text=text):
                self.write(text)
                stderr = self.refused("Build it")
                self.assertIn(message, stderr)
                self.assertIn(config, stderr)
        self.write(pyproject='[tool]\nautocode = "engine=codex"\n')
        (self.root / ".autocode/config.toml").unlink()
        self.assertIn("[tool.autocode] must be a table of settings", self.refused("Build it"))

    def test_every_key_is_a_flag_and_the_cli_reference_lists_exactly_the_keys(self):
        parser = autocode_args.build_parser(None, opencode.DEFAULT_MODELS)
        for key in (*project_config.KEYS, *project_config.USER_ONLY):
            with self.subTest(key=key):
                self.assertIn("--" + key, parser._option_string_actions)
        for key in project_config.FILE_ONLY:  # require-approver: no flag can set or override it (#722)
            with self.subTest(key=key):
                self.assertNotIn("--" + key, parser._option_string_actions)
        self.assertEqual(len(project_config.KEYS), len(set(project_config.KEYS)))
        text = DOCS.read_text()
        section = text[text.index("### Project settings") :]
        section = section[: section.index("\n### ", 1)]
        table = [line for line in section.splitlines() if line.startswith("| ") and "`" in line]
        documented = [key for line in table for key in re.findall(r"`([a-z-]+)`", line.split("|")[2])]
        self.assertEqual([*project_config.KEYS, *project_config.FILE_ONLY], documented)

    def test_autocodes_own_gitignore_leaves_only_reviewable_project_files_trackable(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        directory = workspaces.keep_out_of_git(self.root)
        (directory / "config.toml").write_text('engine = "codex"\n')
        (directory / "project.json").write_text("{}\n")
        (directory / "runs" / "r1").mkdir(parents=True)
        (directory / "runs" / "r1" / "config.toml").write_text("a run's own file\n")
        (directory / "runs" / "r1" / "state.json").write_text("{}")
        components = workspaces.keep_out_of_git(self.root, ".autocode-components")
        (components / "config.toml").write_text("component work\n")
        untracked = workspaces.git(self.root, "ls-files", "--others", "--exclude-standard")
        self.assertEqual([".autocode/config.toml", ".autocode/project.json"], untracked.splitlines())


if __name__ == "__main__":
    unittest.main()
