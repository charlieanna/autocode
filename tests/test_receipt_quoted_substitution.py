"""A captured check's own quoted program may hold `$(` or a backtick.

The receipt matcher refused any event whose command line contained `$(` or a backtick anywhere,
so a Tester's `capture -- sh -c '... $(grep -c x log) ...'` check, whose substitution runs inside
the captured program and is recorded in the receipt's argv, could never be authenticated. Its
report repair failed the same way twice and the run paused PAUSED_REPEATED_FAILURE. A
substitution the shell running the event line performs is still refused.
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_receipt_auth as receipt_auth
import autocode_support as support

CAPTURE = [sys.executable, str(Path(support.__file__).parent / "autocode.py"), "capture"]
REF = ".autocode/evidence/quoted.json"
ARGV = ["sh", "-c", "n=$(printf 'a\\nb\\n' | grep -c .); echo \"lines=$n `echo ok`\""]


class QuotedSubstitutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
        environment["AUTOCODE_OUTPUT_STORE"] = str(self.workspace / ".output-store")
        command = [*CAPTURE, "--output", REF, "--", *ARGV]
        ran = subprocess.run(
            command,
            cwd=self.workspace,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, ran.returncode, ran.stdout)
        self.assertEqual(
            "lines=2 ok\n", Path(json.loads((self.workspace / REF).read_text())["full_output"]).read_text()
        )
        self.output = ran.stdout
        self.command = shlex.join(command)
        self.events = self.workspace / "events.jsonl"

    def verify(self, command):
        item = {"type": "command_execution", "id": "c1", "command": command, "exit_code": 0}
        item["aggregated_output"] = self.output
        self.events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
        check = {"command": None, "exit_code": None, "evidence_ref": REF}
        support.verify_checks([check], self.workspace, self.events)
        return check

    def test_a_substitution_quoted_inside_the_captured_program_authenticates(self):
        for command in (self.command, "/bin/sh -c " + shlex.quote(self.command)):
            with self.subTest(command=command):
                self.assertEqual(
                    {"command": shlex.join(ARGV), "exit_code": 0, "evidence_ref": REF}, self.verify(command)
                )

    def test_a_substitution_the_event_shell_runs_is_still_refused(self):
        prefix = shlex.join([*CAPTURE, "--output", REF, "--"])
        for command in (
            self.command + " $(cat .autocode/evidence/quoted.json)",
            self.command + " `true`",
            '/bin/sh -c "' + self.command.replace("'", "") + ' $(true)"',
            "/bin/sh -c " + shlex.quote(self.command + " $(true)"),
            prefix + ' sh -c "$(cat ' + REF + ')"',
        ):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "independently executed"):
                self.verify(command)


class SubstitutionScanTests(unittest.TestCase):
    def test_literal_text_is_not_a_substitution(self):
        for line in (
            "sh -c 'echo $(date) `id`'",
            "echo \\$(date) \\`id\\`",
            'echo "\\$(date)"',
            "echo $'a\\'b $(date)'",
            "echo x # $(date) `id`",
            "echo a#$b",
            "echo a#'\n$(date) #'",
        ):
            with self.subTest(line=line):
                self.assertFalse(receipt_auth._substitutes(line))

    def test_a_live_or_unreadable_substitution_counts(self):
        for line in (
            "echo $(date)",
            "echo `id`",
            'echo "$(date)"',
            'echo "`id`"',
            "echo $((1 + 1))",
            "echo ${x:-$(date)}",
            "echo $'\\'' $(date) ''",
            "echo x # '\n$(date) #'",
            "echo 'unterminated",
            "echo trailing\\",
        ):
            with self.subTest(line=line):
                self.assertTrue(receipt_auth._substitutes(line))


if __name__ == "__main__":
    unittest.main()
