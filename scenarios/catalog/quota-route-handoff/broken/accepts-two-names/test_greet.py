import subprocess
import sys
import unittest

from greet import greet


class TestGreet(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("Ada"), "Hello, Ada")

    def test_no_arg(self):
        proc = subprocess.run([sys.executable, "greet.py"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr.lower())

    def test_two_arg(self):
        proc = subprocess.run([sys.executable, "greet.py", "Ada", "Lovelace"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_unicode(self):
        self.assertEqual(greet("Zoë"), "Hello, Zoë")


if __name__ == "__main__":
    unittest.main()
