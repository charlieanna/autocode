import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CsvCheckTests(unittest.TestCase):
    def invoke(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rows.csv"
            path.write_text(text)
            return subprocess.run([sys.executable, "-m", "csvcheck", str(path)], capture_output=True, text=True, timeout=10)

    def test_valid(self):
        p = self.invoke("name,age,email\nAlice,20,a@example.test\n")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout), {"rows":1,"errors":[]})

    def test_reports_errors(self):
        p = self.invoke("name,age,email\nBob,nope,invalid\n")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout), {"rows":1,"errors":[{"row":2,"fields":["age","email"]}]})

    def test_bad_header(self):
        p = self.invoke("age,name,email\n20,A,a@b\n")
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertTrue(p.stderr)
