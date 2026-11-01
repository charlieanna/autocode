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
            return subprocess.run(
                [sys.executable, "-m", "csvcheck", str(path)], capture_output=True, text=True, timeout=10
            )

    def test_valid(self):
        p = self.invoke("name,age,email\nAlice,20,a@example.test\n")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout), {"rows": 1, "errors": []})

    def test_reports_errors(self):
        p = self.invoke("name,age,email\nBob,nope,invalid\n")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout), {"rows": 1, "errors": [{"row": 2, "fields": ["age", "email"]}]})

    def test_bad_header(self):
        p = self.invoke("age,name,email\n20,A,a@b\n")
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertTrue(p.stderr)

    def invoke_file(self, rows):
        return self.invoke("name,age,email\n" + rows)

    def test_doubled_quote_inside_quoted_field_is_valid(self):
        p = self.invoke("name,age,email\n" + '"A""b",20,a@b\n')
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(json.loads(p.stdout), {"rows": 1, "errors": []})

    def test_quote_in_unquoted_field_is_rejected(self):
        p = self.invoke('A"b,20,a@b\n')
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertEqual("malformed CSV quoting", p.stderr.strip())

    def test_unterminated_quoted_field_is_rejected(self):
        p = self.invoke('"Ab,20,a@b\n')
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertEqual("unterminated quoted field", p.stderr.strip())

    def test_quote_after_a_closed_quoted_field_is_rejected(self):
        p = self.invoke('"A"b,20,a@b\n')
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertEqual("malformed CSV quoting", p.stderr.strip())

    def test_reports_wrong_column_count(self):
        p = self.invoke("name,age,email\n" + "Bob,20\n")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout), {"rows": 1, "errors": [{"row": 2, "fields": ["columns"]}]})

    def test_reports_empty_name(self):
        p = self.invoke("name,age,email\n" + " ,20,a@b\n")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout), {"rows": 1, "errors": [{"row": 2, "fields": ["name"]}]})

    def test_age_zero_and_130_are_valid(self):
        p = self.invoke("name,age,email\n" + "A,0,a@b\nB,130,b@c\n")
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(json.loads(p.stdout), {"rows": 2, "errors": []})

    def test_age_131_and_1000_are_invalid(self):
        p = self.invoke("name,age,email\n" + "A,131,a@b\nB,1000,b@c\n")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(
            json.loads(p.stdout)["errors"],
            [{"row": 2, "fields": ["age"]}, {"row": 3, "fields": ["age"]}],
        )

    def test_blank_data_row_is_not_counted_or_reported(self):
        p = self.invoke("name,age,email\n" + "\nA,20,a@b\n")
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(json.loads(p.stdout), {"rows": 1, "errors": []})

    def test_long_field_is_accepted(self):
        p = self.invoke("name,age,email\n" + "a" * 200000 + ",20,a@b\n")
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertEqual(json.loads(p.stdout), {"rows": 1, "errors": []})
