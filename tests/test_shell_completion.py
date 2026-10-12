"""`autocode completion` scripts are read from the CLI's parser and complete in real shells (#714)."""

import argparse
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_args
import autocode_configure
import autocode_program
import autocode_shell_completion as completion
import autocode_subcommands as subcommands


def main_parser():
    return autocode_args.build_parser(None, autocode_configure.DEFAULT_ROLE_MODELS)


class InventoryTests(unittest.TestCase):
    def test_every_public_option_is_offered_and_internal_ones_never(self):
        offered = {name for option in completion.inventory().options for name in option.names}
        for action in main_parser()._actions:
            for flag in action.option_strings:
                with self.subTest(flag=flag):
                    if action.help == argparse.SUPPRESS:
                        self.assertNotIn(flag, offered)
                    else:
                        self.assertIn(flag, offered)
        for shell in completion.SHELLS:
            text = completion.script(shell)
            for flag in subcommands.INTERNAL_FLAGS[()]:
                with self.subTest(shell=shell, flag=flag):
                    self.assertNotIn(flag, text)

    def test_values_follow_the_parser(self):
        actions = {action.option_strings[-1]: action for action in main_parser()._actions if action.option_strings}
        options = {option.names[-1]: option for option in completion.inventory().options}
        self.assertEqual(tuple(actions["--unit"].choices), options["--unit"].choices)
        self.assertEqual(("--workspace", True), (options["--workspace"].names[-1], options["--workspace"].path))
        self.assertEqual((True, False), (options["--brief"].takes_value, options["--brief"].path))
        self.assertFalse(options["--no-chat"].takes_value)

    def test_commands_come_from_the_dispatch_table(self):
        found = completion.inventory()
        self.assertEqual(subcommands.command_names(), found.commands)
        self.assertIn("completion", found.own_parser)
        self.assertIn("doctor", found.own_parser)
        # These take the main command's options.
        for name in (*autocode_args.COMMAND_WORDS, "unattended"):
            self.assertNotIn(name, found.own_parser)
        self.assertEqual(set(autocode_program.COMMANDS), set(found.children["program"]))
        self.assertEqual(("--analyze",), found.extras["unattended"])

    def test_a_word_that_is_not_plain_is_refused_rather_than_written_into_shell_source(self):
        with mock.patch.object(subcommands, "command_names", return_value=("--version", "doctor", "$(touch x)")):
            with self.assertRaisesRegex(ValueError, "plain"):
                completion.inventory()

    def test_option_summaries_are_one_short_clause(self):
        self.assertEqual("Existing run directory to resume", completion.summary("Existing run directory to resume"))
        self.assertEqual("Use the model", completion.summary("Use the model. Default %(default)s; see docs."))
        self.assertLessEqual(len(completion.summary("word " * 40)), 72)


class CliTests(unittest.TestCase):
    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = subcommands.dispatch(["completion", *argv])
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def test_each_shell_prints_its_script_and_others_are_refused(self):
        for shell in completion.SHELLS:
            with self.subTest(shell=shell):
                code, out, err = self.invoke(shell)
                self.assertEqual(0, code, err)
                self.assertIn(f"# {shell} completion for autocode {subcommands.package_version()}", out)
        self.assertTrue(self.invoke("zsh")[1].startswith("#compdef autocode\n"))
        for argv in ((), ("tcsh",)):
            with self.subTest(argv=argv):
                code, out, err = self.invoke(*argv)
                self.assertEqual(2, code)
                self.assertEqual("", out)
                self.assertIn("usage: autocode completion", err)


@unittest.skipIf(shutil.which("bash") is None, "bash is not installed")
class BashTests(unittest.TestCase):
    # Runs the generated function the way bash's programmable completion calls it.
    CHECK = (
        'source "$1"; shift; COMP_WORDS=("$@"); COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 )); '
        '_autocode; if [ ${#COMPREPLY[@]} -gt 0 ]; then printf "%s\\n" "${COMPREPLY[@]}"; fi'
    )

    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.cwd = Path(folder.name)
        cls.script = cls.cwd / "autocode.bash"
        cls.script.write_text(completion.script("bash"))
        (cls.cwd / "brief.md").write_text("A brief\n")

    def complete(self, *words):
        command = ["bash", "--norc", "--noprofile", "-c", self.CHECK, "bash", str(self.script), "autocode", *words]
        result = subprocess.run(command, cwd=self.cwd, capture_output=True, text=True, timeout=30, check=True)
        return result.stdout.split()

    def test_completions(self):
        unit = tuple(next(a.choices for a in main_parser()._actions if "--unit" in a.option_strings))
        for words, expected in (
            (("res",), ["resume"]),
            (("--wor",), ["--workspace", "--workflow"]),
            (("--unit", ""), list(unit)),
            (("--unit", "=", "auto"), [choice for choice in unit if choice.startswith("auto")]),
            (("program", ""), list(autocode_program.COMMANDS)),
            (("program", "plan", "--"), ["--help"]),
            (("doctor", "--"), ["--help"]),
            (("resume", "--run"), ["--run-dir"]),
            (("unattended", "--ana"), ["--analyze"]),
            (("--brief-file", "bri"), ["brief.md"]),
            (("--new-run",), []),
            (("--vers",), ["--version"]),
            (("resume", "--vers"), []),
        ):
            with self.subTest(words=words):
                self.assertEqual(sorted(expected), sorted(self.complete(*words)))


@unittest.skipIf(shutil.which("zsh") is None, "zsh is not installed")
class ZshTests(unittest.TestCase):
    # compsys itself needs a terminal; its three calls are stubbed to print what they were given.
    CHECK = r"""
compadd() { [[ $1 == -- ]] && shift; print -rl -- "$@"; }
_describe() { local name=$4; print -rl -- ${${(M)${(P)name}:#${PREFIX}*}%%:*}; }
_files() { print -r -- FILES; }
compdef() { print -r -- "compdef $*"; }
source $1; shift
words=(autocode "$@"); CURRENT=$#words; PREFIX=${words[-1]}
_autocode
"""

    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.script = Path(folder.name) / "_autocode"
        cls.script.write_text(completion.script("zsh"))

    def complete(self, *words):
        command = ["zsh", "-f", "-c", self.CHECK, "zsh", str(self.script), *words]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
        lines = result.stdout.split()
        self.assertEqual(["compdef", "_autocode", "autocode"], lines[:3])  # sourcing registers the function
        return lines[3:]

    def test_the_script_parses(self):
        subprocess.run(["zsh", "-n", str(self.script)], check=True, timeout=30)

    def test_completions(self):
        unit = tuple(next(a.choices for a in main_parser()._actions if "--unit" in a.option_strings))
        self.assertIn("resume", self.complete("res"))  # compadd filters by prefix itself
        self.assertEqual(["--workflow", "--workspace"], sorted(self.complete("--wor")))
        self.assertEqual(list(unit), self.complete("--unit", ""))
        self.assertEqual(list(autocode_program.COMMANDS), self.complete("program", ""))
        self.assertEqual(["--help"], self.complete("doctor", "--"))
        self.assertEqual(["FILES"], self.complete("doctor", ""))
        self.assertEqual(["--run-dir"], self.complete("resume", "--run"))
        self.assertEqual(["--analyze"], self.complete("unattended", "--ana"))
        self.assertEqual([], self.complete("--brief", ""))
        self.assertEqual([], self.complete("--new-run"))
        self.assertEqual(["--version"], self.complete("--vers"))


@unittest.skipIf(shutil.which("fish") is None, "fish is not installed")
class FishTests(unittest.TestCase):
    def test_completions(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "autocode.fish"
            script.write_text(completion.script("fish"))
            # One line per command line, empty when nothing completes.
            check = (
                f"source {script}; for line in $argv; "
                "echo (complete -C $line | string replace -r '\\t.*' '' | string join ' '); end"
            )
            lines = [
                "autocode res",
                "autocode --unit ",
                "autocode program ",
                "autocode doctor --",
                "autocode --new-run",
            ]
            result = subprocess.run(
                ["fish", "--no-config", "-c", check, *lines], capture_output=True, text=True, timeout=30, check=True
            )
        got = dict(zip(lines, result.stdout.splitlines(), strict=True))
        unit = next(a.choices for a in main_parser()._actions if "--unit" in a.option_strings)
        self.assertEqual("resume", got["autocode res"])
        self.assertEqual(sorted(unit), sorted(got["autocode --unit "].split()))
        self.assertEqual(sorted(autocode_program.COMMANDS), sorted(got["autocode program "].split()))
        self.assertEqual("--help", got["autocode doctor --"])
        self.assertEqual("", got["autocode --new-run"])


if __name__ == "__main__":
    unittest.main()
