"""A check is matched to its capture receipt by the command that ran, not by how it was quoted.

Validator reports on live Claude-model runs (2026-09-29) were sent back for repair because a
check restated its captured command: requoted (`-p "test_*.py"`), with a placeholder
(`go build -o <tmpdir>/policy .`), or summarized (`python3 -c <combined assertions>`).
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_receipts as receipts
import autocode_support as support

AUTOCODE = Path(receipts.__file__).with_name("autocode.py")


class AdoptCommandTests(unittest.TestCase):
    ARGV = ["python", "-m", "unittest", "discover", "-v", "-s", "tests", "-p", "test_*.py"]

    def test_the_same_arguments_quoted_differently_match_and_take_the_receipts_spelling(self):
        check = {"command": 'python -m unittest discover -v -s tests -p "test_*.py"'}
        self.assertTrue(receipts.adopt_command(check, self.ARGV))
        self.assertEqual("python -m unittest discover -v -s tests -p 'test_*.py'", check["command"])

    def test_placeholders_summaries_and_other_arguments_do_not(self):
        for text in ("python -m unittest discover -v -s <tests> -p 'test_*.py'",
                     "python -m unittest (all tests)", "python -m unittest discover -v -s tests",
                     "python -m unittest discover -v -s tests -p 'test_*.py", ""):
            check = {"command": text}
            with self.subTest(text=text):
                self.assertFalse(receipts.adopt_command(check, self.ARGV))
                self.assertEqual(text, check["command"])

    def test_the_message_shows_both_commands(self):
        check = {"command": "go build -o <tmpdir>/policy .", "exit_code": 0, "evidence_ref": ".autocode/e/b.json"}
        message = receipts.mismatch(check, {"command": ["go", "build", "-o", "/tmp/x/policy", "."], "exit_code": 0})
        self.assertIn("differs from receipt .autocode/e/b.json", message)
        self.assertIn("'go build -o <tmpdir>/policy .'", message)
        self.assertIn("'go build -o /tmp/x/policy .'", message)


class VerifyChecksTests(unittest.TestCase):
    """Through the runner's own check verification, with a receipt written by `autocode capture`."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.context = {"attempt": "sol-01", "nonce": "n", "source_revision": "r"}
        env = {"AUTOCODE_CAPTURE_CONTEXT": json.dumps(self.context), "PATH": "/usr/bin:/bin"}
        subprocess.run([sys.executable, str(AUTOCODE), "capture", "--output", ".autocode/evidence/check.json", "--",
                        sys.executable, "-c", "print('ok: test_*.py')"], cwd=self.root, env=env, check=True,
                       capture_output=True)
        self.receipt = json.loads((self.root / ".autocode/evidence/check.json").read_text())

    def verify(self, command):
        check = {"command": command, "exit_code": 0, "evidence_ref": ".autocode/evidence/check.json"}
        support.verify_checks([check], self.root, self.root / "none.jsonl", receipt_only=True,
                              capture_context=self.context)
        return check

    def test_a_requoted_command_is_accepted_and_recorded_as_it_ran(self):
        requoted = f'{sys.executable} -c "print(\'ok: test_*.py\')"'
        self.assertEqual(support.shlex.join(self.receipt["command"]), self.verify(requoted)["command"])

    def test_a_placeholder_or_a_wrong_exit_code_is_still_refused_and_says_what_to_copy(self):
        with self.assertRaisesRegex(ValueError, r"differs from receipt .*the receipt ran"):
            self.verify(f"{sys.executable} -c <the ok check>")
        check = {"command": support.shlex.join(self.receipt["command"]), "exit_code": 1,
                 "evidence_ref": ".autocode/evidence/check.json"}
        with self.assertRaisesRegex(ValueError, "differs from receipt"):
            support.verify_checks([check], self.root, self.root / "none.jsonl", receipt_only=True,
                                  capture_context=self.context)


if __name__ == "__main__":
    unittest.main()


class ValidatorEvidenceInstructionTests(unittest.TestCase):
    def test_every_event_reference_instruction_names_the_receipt_alternative(self):
        # A live run (2026-10-04) told a report-file Validator both to cite 'event:' references and,
        # in its provider's text, never to cite event IDs; the runner rejected 11 such reports in 9 runs.
        sentences = re.split(r"(?<=[.;])\s+", " ".join(support.STABLE["sol"].split()))
        for sentence in sentences:
            if "'event:'" in sentence:
                with self.subTest(sentence=sentence):
                    self.assertIn("receipt", sentence)

    def test_the_scratch_directory_is_named_as_uncitable(self):
        # Live greenfield-todo-cli (2026-10-04): the prompt told writers to keep scratch under the
        # evidence directory (.autocode/), and the Validator then cited `sh .autocode/probe.sh` as a
        # check. The replay runs on a clean source copy with no .autocode/, so it could never pass.
        self.assertIn(".autocode/", support.COMMON)
        self.assertIn("Never cite", support.COMMON)
        self.assertIn("clean-copy replay cannot pass", support.COMMON)
