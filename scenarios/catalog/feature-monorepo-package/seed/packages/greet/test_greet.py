import subprocess
import sys
import unittest
from pathlib import Path

from greet import greeting

GREET = Path(__file__).resolve().parent / "greet.py"


def cli(*args):
    return subprocess.run([sys.executable, str(GREET), *args], capture_output=True, text=True, timeout=30)


class TestGreet(unittest.TestCase):
    def test_greeting(self):
        self.assertEqual(greeting("Ada"), "Hello, Ada")

    def test_cli_prints_the_greeting(self):
        result = cli("Ada")
        self.assertEqual((result.returncode, result.stdout), (0, "Hello, Ada\n"))

    def test_cli_needs_exactly_one_name(self):
        for args in ((), ("Ada", "Lovelace"), ("",)):
            with self.subTest(args=args):
                result = cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
