import subprocess
import sys
import unittest


class PublicInterfaceTests(unittest.TestCase):
    def test_package_imports(self):
        result = subprocess.run([sys.executable, "-c", "import app"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", "importing the package must not run the program")
