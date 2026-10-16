"""Hidden acceptance tests for --formal, run from the greeting package's directory."""

import subprocess
import sys
import unittest
from pathlib import Path

from greet import greeting

GREET = Path(__file__).resolve().parents[1] / "greet.py"


def cli(*args):
    return subprocess.run([sys.executable, str(GREET), *args], capture_output=True, text=True, timeout=30)


class HiddenFormalTests(unittest.TestCase):
    def test_function(self):
        self.assertEqual(greeting("Grace Hopper", formal=True), "Good day, Grace Hopper.")
        self.assertEqual(greeting("Zoë"), "Hello, Zoë")

    def test_formal_before_or_after_the_name(self):
        for args in (("--formal", "Grace Hopper"), ("Grace Hopper", "--formal")):
            with self.subTest(args=args):
                result = cli(*args)
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr), (0, "Good day, Grace Hopper.\n", "")
                )

    def test_without_the_option_nothing_changes(self):
        result = cli("Grace Hopper")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "Hello, Grace Hopper\n", ""))

    def test_usage_errors(self):
        for args in ((), ("--formal",), ("--formal", ""), ("--formal", "Ada", "Lovelace"), ("Ada", "Lovelace"), ("",)):
            with self.subTest(args=args):
                result = cli(*args)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("usage", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
