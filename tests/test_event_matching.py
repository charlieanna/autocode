"""Executed shell wrappers must preserve the complete claimed program text."""

import shlex
import unittest

from autocode_event_matching import same_command


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
