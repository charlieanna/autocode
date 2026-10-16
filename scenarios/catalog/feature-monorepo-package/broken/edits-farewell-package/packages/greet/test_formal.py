import subprocess
import sys
import unittest
from pathlib import Path

from greet import greeting

GREET = Path(__file__).resolve().parent / "greet.py"


def cli(*args):
    return subprocess.run([sys.executable, str(GREET), *args], capture_output=True, text=True, timeout=30)


class TestFormal(unittest.TestCase):
    def test_formal_greeting(self):
        self.assertEqual(greeting("Ada", formal=True), "Good day, Ada.")
        self.assertEqual(greeting("Ada"), "Hello, Ada")

    def test_cli_formal_before_or_after_the_name(self):
        for args in (("--formal", "Ada"), ("Ada", "--formal")):
            with self.subTest(args=args):
                result = cli(*args)
                self.assertEqual((result.returncode, result.stdout), (0, "Good day, Ada.\n"))

    def test_cli_formal_still_needs_exactly_one_name(self):
        for args in (("--formal",), ("--formal", "Ada", "Lovelace"), ("--formal", "")):
            with self.subTest(args=args):
                result = cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
