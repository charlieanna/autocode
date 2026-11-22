"""A capture run may follow setup steps and share its Bash call with other capture runs.

Live Testers ran ``cd <workspace>; C="<capture command>"; $C --output r.json -- <check> 2>&1 | tail -c 1500``.
The receipt matcher read only ``cd <dir> &&`` before one capture run and only ``/usr/bin/tail``, so it
refused every such event although its output showed capture's attestation of the receipt. It then said
the filter had dropped the receipt line, so each fresh Tester attempt kept the filter and was refused
again (#4110). On this host's saved Tester events 71 of 302 capture commands were read as capture runs.
A step that can print a line of its own is still refused, and the rejection now says what to change.
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
REF = ".autocode/evidence/setup.json"
ARGV = ["python3", "-c", "print('ok')"]


class CaptureAfterSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
        environment["AUTOCODE_OUTPUT_STORE"] = str(self.workspace / ".output-store")
        environment["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), environment.get("PATH", "")])
        ran = subprocess.run(
            [*CAPTURE, "--output", REF, "--", *ARGV],
            cwd=self.workspace,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, ran.returncode, ran.stdout)
        self.output = ran.stdout
        self.capture = shlex.join(CAPTURE)
        self.check = shlex.join(ARGV)
        self.events = self.workspace / "events.jsonl"

    def verify(self, command, output=None):
        item = {"type": "command_execution", "id": "c1", "command": command, "exit_code": 0}
        item["aggregated_output"] = self.output if output is None else output
        self.events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
        check = {"command": None, "exit_code": None, "evidence_ref": REF}
        support.verify_checks([check], self.workspace, self.events)
        return check

    def test_setup_steps_and_bare_filters_before_and_after_one_capture_run_authenticate(self):
        workspace, capture, check = self.workspace, self.capture, self.check
        run = f"{capture} --output {REF} -- {check}"
        variable = f'C="{capture}"; $C --output {REF} -- {check}'
        for command in (
            f"cd {workspace}; {variable} 2>&1 | tail -c 1500",
            f"cd {workspace}\n{variable.replace('; ', chr(10))} 2>&1 | tail -c 600",
            f'cd {workspace} && C="{capture}" && ${{C}} --output {REF} -- {check} | head -5',
            f"mkdir -p .autocode/evidence .autocode/scratch; {run} 2>&1 | grep -v progress",
            f"cd .autocode && cd .. && {run} | cut -c 1-4000",
            f"{run} 2>&1 | tail -3; {capture} --output .autocode/evidence/other.json -- ruff check a.py | tail -3",
            "/bin/bash -lc " + shlex.quote(f"cd {workspace}; {variable} 2>&1 | tail -c 1500"),
        ):
            with self.subTest(command=command):
                self.assertEqual({"command": check, "exit_code": 0, "evidence_ref": REF}, self.verify(command))

    def test_a_step_that_could_print_a_line_of_its_own_is_still_refused(self):
        capture, check = self.capture, self.check
        run = f"{capture} --output {REF} -- {check}"
        for command in (
            f"{run} 2>&1 | tail -c 1500; cat .autocode/evidence/setup.log",
            f"{run}; python3 -c 'print(1)'",
            f"echo start; {run}",
            f"{run} | sed -n 1,5p",
            f"{run} | tail -c 1500 | ./tail",
            f'C="$(printf %s {shlex.quote(capture)})"; $C --output {REF} -- {check}',
            f'C="{capture} $EXTRA"; $C --output {REF} -- {check}',
            f"$UNSET --output {REF} -- {check}",
            f"cd /; {run}",
            f"mkdir -pv x; {run}",
            f"C={shlex.quote(capture)} {run}",
        ):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "independently executed"):
                self.verify(command)

    def test_the_rejection_says_whether_the_receipt_reached_the_output(self):
        run = f"{self.capture} --output {REF} -- {self.check}"
        with self.assertRaisesRegex(ValueError, "output shows this receipt, but its command line ran capture"):
            self.verify(f"{run} 2>&1 | tail -c 1500; cat .autocode/evidence/setup.log")
        with self.assertRaisesRegex(ValueError, "dropped both the receipt line"):
            self.verify(f"{run} 2>&1 | tail -c 1500", output="ok\n")


class TokenTests(unittest.TestCase):
    def test_an_unquoted_newline_separates_commands_and_a_quoted_one_does_not(self):
        self.assertEqual(["a", ";", "b", "&&", "c"], receipt_auth._tokens("a\n\nb &&\nc\n"))
        self.assertEqual(["sh", "-c", "a\nb"], receipt_auth._tokens("sh -c 'a\nb'"))


if __name__ == "__main__":
    unittest.main()
