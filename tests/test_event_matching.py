"""Executed shell wrappers must preserve the complete claimed program text."""

import shlex
import unittest

from autocode_event_matching import capture_invocation, same_command


class ZshCommandEvidenceTests(unittest.TestCase):
    def test_whole_plain_shell_wrapper_matches_on_either_side(self):
        for command in ("python3 probe.py success", "python3 probe.py failure", "printf '%s\\n' 'a b'"):
            with self.subTest(command=command):
                event = "/bin/zsh -c " + shlex.quote(command)
                self.assertTrue(same_command(event, command))
                self.assertTrue(same_command(command, event))
                self.assertTrue(same_command(event, "/bin/zsh -lc " + shlex.quote(command)))

    def test_plain_wrapper_does_not_drop_positional_arguments_or_operator_chains(self):
        command = "python3 probe.py success"
        wrapped = "/bin/zsh -c " + shlex.quote(command)
        for event in (wrapped + " extra", wrapped + " && false", wrapped + "; false", wrapped + " 2>/dev/null"):
            with self.subTest(event=event):
                self.assertFalse(same_command(event, command))
                self.assertFalse(same_command(command, event))

    def test_plain_wrapper_requires_well_formed_original_quotes(self):
        command = "python3 probe.py success"
        for event in ("/bin/zsh -c '" + command, "/bin/zsh -c '" + command + "''"):
            with self.subTest(event=event):
                self.assertFalse(same_command(event, command))

    def test_unwrapped_program_keeps_operator_quoting_and_argument_values(self):
        for literal, operator in (("'&&'", "&&"), ("';'", ";"), ("'|'", "|"), ("'>'", ">")):
            printed = "printf '%s\\n' " + literal + " false"
            executed = "printf '%s\\n' " + operator + " false"
            wrapped = "/bin/zsh -c " + shlex.quote(printed)
            with self.subTest(literal=literal):
                self.assertTrue(same_command(wrapped, printed))
                self.assertFalse(same_command(wrapped, executed))
        self.assertFalse(same_command("/bin/zsh -c 'python3 probe.py failure'", "python3 probe.py success"))


class CaptureInvocationTests(unittest.TestCase):
    PREFIX = "/venv/bin/python /runner/tools/autocode.py capture"

    def test_one_capture_run_names_its_receipt_and_the_command_it_captured(self):
        for options in (
            "--output e/r.json",
            "--mode conservative --output e/r.json",
            "--output=e/r.json --no-compress",
        ):
            with self.subTest(options=options):
                command = f"{self.PREFIX} {options} -- ruff check 'a b.py'"
                self.assertEqual(("e/r.json", ["ruff", "check", "a b.py"]), capture_invocation(command))
        self.assertEqual(("r.json", ["pytest", "-q"]), capture_invocation("autocode capture --output r.json pytest -q"))
        quoted = f"{self.PREFIX} --output r.json -- sh -c 'a && b | c'"
        self.assertEqual(("r.json", ["sh", "-c", "a && b | c"]), capture_invocation(quoted))

    def test_anything_more_than_one_capture_run_is_not_read_as_one(self):
        for command in (
            f"{self.PREFIX} --output r.json -- ruff check a.py | tail -3",
            f"{self.PREFIX} --output r.json -- ruff check a.py && echo ok",
            f"{self.PREFIX} --output r.json -- ruff check $FILES",
            f"cd /w && {self.PREFIX} --output r.json -- ruff check a.py",
            f"{self.PREFIX} --compare-base HEAD --output r.json -- ruff check a.py",
            f"{self.PREFIX} --output r.json --",
            f"{self.PREFIX} -- ruff check a.py",
            "ruff check a.py",
            "/venv/bin/python /runner/tools/other.py capture --output r.json -- ruff",
            None,
        ):
            with self.subTest(command=command):
                self.assertIsNone(capture_invocation(command))
