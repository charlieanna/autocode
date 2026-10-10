"""The public command surface is discoverable without launching or changing a run."""

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
import autocode_program
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

    def test_full_help_retains_advanced_options_and_normal_parsing(self):
        code, out, err = self.invoke("--help-all")
        self.assertEqual(0, code, err)
        parser = autocode_args.build_parser(None, autocode.opencode.DEFAULT_MODELS)
        for action in parser._actions:
            for flag in action.option_strings:
                with self.subTest(flag=flag):
                    self.assertIn(flag, out)
        args = parser.parse_args(["Task text", "--expected-goal-token", "literal", "--max-iterations", "3"])
        self.assertEqual("Task text", args.task)
        self.assertEqual("literal", args.expected_goal_token)
        self.assertEqual(3, args.max_iterations)

    def test_unified_entry_points_show_their_actual_help_without_actions(self):
        for name in ("issue", "arena", "dashboard", "unattended"):
            with self.subTest(command=name):
                code, out, err = self.invoke(name, "--help")
                self.assertEqual(0, code, err)
                self.assertIn("usage:", out)

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
        for name in ("issue", "arena", "dashboard", "unattended"):
            with self.subTest(command=name), patch.object(unattended.subprocess, "Popen") as launch:
                code, _, err = self.invoke("unattended", name, "--help")
                self.assertEqual(2, code)
                self.assertIn("operator-only", err)
                launch.assert_not_called()
        self.assertIsNone(unattended.refused(["--run-dir", "saved", "--status"]))
        self.assertIsNone(unattended.refused(["Build it", "--no-chat"]))

    def test_public_inventory_uses_actual_program_dispatch_and_no_hidden_flags(self):
        paths = commands.public_command_paths()
        self.assertEqual(len(paths), len(set(paths)))
        self.assertIn((), paths)
        self.assertEqual(set(autocode_program.COMMANDS), {p[1] for p in paths if len(p) == 2 and p[0] == "program"})
        self.assertEqual(
            set(commands.SUBCOMMANDS) | set(commands.BUILTIN_COMMANDS), {p[0] for p in paths if len(p) == 1}
        )
        self.assertIn(("unattended", "--analyze"), paths)
        self.assertEqual({}, commands.INTERNAL_FLAGS)

    def test_packaging_marks_only_autocode_public_and_preserves_compatibility_scripts(self):
        package = tomllib.loads((ROOT / "pyproject.toml").read_text())
        policy = package["tool"]["autocode"]["cli"]
        self.assertEqual(["autocode"], policy["public-entry-points"])
        internal = policy["internal-entry-points"]
        self.assertEqual(len(internal), len(set(internal)))
        self.assertEqual(set(package["project"]["scripts"]) - {"autocode"}, set(internal))
        self.assertTrue(
            {
                "autoplanner",
                "autocode-build",
                "autoreview",
                "autoresolver",
                "autocode-orchestrator",
                "autocode-unattended",
            }.issubset(internal)
        )
        for unit in ("autoplanner", "autocode", "autoreview", "autoresolver"):
            with self.subTest(unit=unit):
                self.assertEqual(
                    unit,
                    autocode_args.build_parser(None, autocode.opencode.DEFAULT_MODELS)
                    .parse_args(["--unit", unit])
                    .unit,
                )


if __name__ == "__main__":
    unittest.main()
