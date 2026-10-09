import subprocess
import sys
import unittest

class TemperatureTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run([sys.executable, "-m", "temperature", *args], capture_output=True, text=True, timeout=10)

    def test_celsius(self):
        p = self.invoke("0", "C")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "32.0 F\n", ""))

    def test_fahrenheit(self):
        p = self.invoke("212", "F")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "100.0 C\n", ""))

    def test_bad_number(self):
        p = self.invoke("bad", "C")
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertTrue(p.stderr)
