"""The public command surface is discoverable without launching or changing a run."""

import argparse
import contextlib
import io
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode
import autocode_args
import autocode_subcommands as commands
import autocode_unattended as unattended

ROOT = Path(__file__).resolve().parents[1]


class CliSurfaceTests(unittest.TestCase):
    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["autocode", *argv]),
            patch(
                "autocode.autocode_providers.resolve", side_effect=AssertionError("help must not resolve a provider")
            ),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = autocode.main()
            except SystemExit as exit:
                code = exit.code
        return code, out.getvalue(), err.getvalue()

    def test_root_help_fits_one_screen_and_names_all_commands_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"AUTOCODE_HOME": directory, "XDG_CONFIG_HOME": directory}):
                code, out, err = self.invoke("--help")
            self.assertEqual([], list(Path(directory).iterdir()))
        self.assertEqual(0, code, err)
        self.assertLessEqual(len(out.splitlines()), 24, out)
        self.assertLessEqual(max(map(len, out.splitlines())), 80, out)
        listed = out.split("Commands, typed first:", 1)[1].replace("\n", " ")
        for name in commands.command_names():
            with self.subTest(command=name):
                self.assertIn(name, listed)
        self.assertIn("--help-all", out)
        self.assertIn("docs/cli.md", out)
        # #2330: the coding agent is chosen with --engine; --provider (external tools) led people to
        # `--provider codex`, which fails asking for a TOML file.
        self.assertIn("--engine", out)
        self.assertNotIn("--provider", out)

    def test_full_help_retains_advanced_options_and_normal_parsing(self):
        code, out, err = self.invoke("--help-all")
        self.assertEqual(0, code, err)
        parser = autocode_args.build_parser(None, autocode.opencode.DEFAULT_MODELS)
        for action in parser._actions:
            for flag in action.option_strings:
                with self.subTest(flag=flag):
                    if action.help == argparse.SUPPRESS:
                        self.assertNotIn(flag, out)
                    else:
                        self.assertIn(flag, out)
        args = parser.parse_args(["Task text", "--expected-goal-token", "literal", "--max-iterations", "3"])
        self.assertEqual("Task text", args.task)
        self.assertEqual("literal", args.expected_goal_token)
        self.assertEqual(3, args.max_iterations)

    def test_unified_entry_points_show_their_actual_help_without_actions(self):
        for name in ("issue", "arena", "dashboard", "unattended", "forget"):
            with self.subTest(command=name):
                code, out, err = self.invoke(name, "--help")
                self.assertEqual(0, code, err)
                self.assertIn("usage:", out)

    def test_every_subcommands_usage_line_names_the_subcommand(self):
        """`usage: autocode [-h] {location,list,...}` read as if `autocode list` existed (#2387)."""
        own_program = {"issue", "unattended"}  # autocode-issue and autocode-unattended are scripts of their own
        for name in sorted((set(commands.SUBCOMMANDS) | set(commands.BUILTIN_COMMANDS)) - own_program):
            if name in commands.WITHOUT_PARSER:
                continue
            with self.subTest(command=name):
                code, out, err = self.invoke(name, "--help")
                self.assertEqual(0, code, err)
                self.assertTrue(out.startswith(f"usage: autocode {name}"), out.splitlines()[:1])
        code, out, err = self.invoke("registry", "no-such-action")
        self.assertEqual(2, code)
        self.assertIn("autocode registry: error:", err)

    def test_a_user_error_names_the_problem_without_the_option_dump(self):
        code, out, err = self.invoke("Build a greeting CLI", "--new-run-dir", "/tmp/fresh-run")
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertTrue(err.startswith("usage: autocode [TASK] [OPTIONS]\n"), err)
        self.assertLessEqual(len(err.splitlines()), 2, err)
        self.assertIn("--new-run-dir requires a fresh --in-place task invocation", err)
        for internal in ("--review-token", "--close-finding", "--accept-completion", "--resolver-token"):
            self.assertNotIn(internal, err)

    def test_sysargv_entry_restores_arguments_after_help_or_failure(self):
        original = ["autocode", "dashboard", "--help"]
        for error in (None, ValueError("failure"), SystemExit(0)):
            with self.subTest(error=type(error).__name__):
                observed = []

                def entry():
                    observed.append(list(sys.argv))
                    if error is not None:
                        raise error
                    return 0

                with (
                    patch.object(sys, "argv", original),
                    patch.object(commands.importlib, "import_module", return_value=SimpleNamespace(main=entry)),
                ):
                    if error is None:
                        self.assertEqual(0, commands.dispatch(["dashboard", "--port", "9876"]))
                    else:
                        with self.assertRaises(type(error)):
                            commands.dispatch(["dashboard", "--port", "9876"])
                    self.assertIs(original, sys.argv)
                self.assertEqual([["autocode dashboard", "--port", "9876"]], observed)

    def test_option_values_and_delimited_task_text_are_not_subcommands(self):
        for argv in (["--workspace", "arena"], ["--", "issue"], ["Build the dashboard"]):
            with self.subTest(argv=argv):
                self.assertIsNone(commands.dispatch(argv))
        parser = autocode_args.build_parser(None, autocode.opencode.DEFAULT_MODELS)
        self.assertEqual("issue", parser.parse_args(["--", "issue"]).task)

    def test_new_routes_remain_operator_only_for_unattended_callers(self):
        for name in ("issue", "arena", "dashboard", "unattended", "forget"):
            with self.subTest(command=name), patch.object(unattended.subprocess, "Popen") as launch:
                code, _, err = self.invoke("unattended", name, "--help")
                self.assertEqual(2, code)
                self.assertIn("operator-only", err)
                launch.assert_not_called()
        self.assertIsNone(unattended.refused(["--run-dir", "saved", "--status"]))
        self.assertIsNone(unattended.refused(["Build it", "--no-chat"]))

    def test_public_inventory_uses_actual_dispatch_and_explicit_internal_flags(self):
        paths = commands.public_command_paths()
        self.assertEqual(len(paths), len(set(paths)))
        self.assertIn((), paths)
        self.assertEqual(
            set(commands.SUBCOMMANDS) | set(commands.BUILTIN_COMMANDS), {p[0] for p in paths if len(p) == 1}
        )
        self.assertIn(("unattended", "--analyze"), paths)
        self.assertEqual(
            {
                (): frozenset({"--new-run-dir", "--replay-recording", "--replay-checks", "--worker-receipt"}),
                ("unattended",): frozenset(
                    {"--new-run-dir", "--replay-recording", "--replay-checks", "--worker-receipt"}
                ),
                ("worker",): frozenset({"--receipt"}),
            },
            commands.INTERNAL_FLAGS,
        )

    def test_packaging_installs_only_the_autocode_command(self):
        package = tomllib.loads((ROOT / "pyproject.toml").read_text())
        policy = package["tool"]["autocode"]["cli"]
        self.assertEqual(["autocode"], policy["public-entry-points"])
        self.assertEqual([], policy["internal-entry-points"])
        self.assertEqual({"autocode": "autocode_cli.autocode:cli"}, package["project"]["scripts"])
        for unit in ("autoplanner", "autocode", "autoreview", "autoresolver"):
            with self.subTest(unit=unit):
                self.assertEqual(
                    unit,
                    autocode_args.build_parser(None, autocode.opencode.DEFAULT_MODELS)
                    .parse_args(["--unit", unit])
                    .unit,
                )

    def test_docs_cli_subcommand_exit_codes_table_lists_subcommands(self):
        doc = (ROOT / "docs" / "cli.md").read_text()
        self.assertIn("### Subcommand exit codes", doc)
        table_section = doc.split("### Subcommand exit codes", 1)[1].split("\n## ", 1)[0]
        expected_subcommands = [
            "`autocode plan`",
            "`autocode prune-run`",
            "`autocode merge`",
            "`autocode clean-worktrees`",
            "`autocode checkpoint`",
            "`autocode tasks`",
            "`autocode wait`",
            "`autocode doctor`",
            "`autocode models`",
            "`autocode verify`",
        ]
        for cmd in expected_subcommands:
            with self.subTest(command=cmd):
                self.assertIn(cmd, table_section)


if __name__ == "__main__":
    unittest.main()
