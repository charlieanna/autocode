import subprocess
import sys
import unittest


class GreetingTests(unittest.TestCase):
    def test_c1_exact_streams_and_exits(self):
        for args, code, out, err in (
                (["Ada"], 0, "Hello, Ada\n", ""),
                ([""], 2, "", "usage: greet.py NAME\n"),
                ([" \t "], 2, "", "usage: greet.py NAME\n"),
                ([], 2, "", "usage: greet.py NAME\n"),
                (["Ada", "Lovelace"], 2, "", "usage: greet.py NAME\n")):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "greet.py", *args],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual((code, out, err), (result.returncode, result.stdout, result.stderr))


if __name__ == "__main__":
    unittest.main()
