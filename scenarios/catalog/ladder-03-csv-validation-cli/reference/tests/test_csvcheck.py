import contextlib
import csv
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class CsvCheckTests(unittest.TestCase):
    def test_field_limit_retries_a_smaller_platform_supported_value(self):
        from csvcheck import main

        real_limit = csv.field_size_limit
        self.addCleanup(real_limit, real_limit())
        attempts = []

        def platform_limit(value):
            attempts.append(value)
            if len(attempts) > 2:
                raise AssertionError("The field limit did not converge after reducing the platform maximum")
            if value >= sys.maxsize:
                raise OverflowError("The platform C long cannot hold this field limit")
            return real_limit(value)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rows.csv"
            path.write_text("name,age,email\nAlice,20,a@example.test\n")
            output = io.StringIO()
            with patch("csvcheck.csv.field_size_limit", side_effect=platform_limit), contextlib.redirect_stdout(output):
                self.assertEqual(0, main([str(path)]))
            self.assertEqual({"rows": 1, "errors": []}, json.loads(output.getvalue()))
        self.assertEqual(2, len(attempts))
        self.assertEqual(sys.maxsize, attempts[0])
        self.assertGreater(attempts[1], 0)
        self.assertLess(attempts[1], attempts[0])

    def invoke(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rows.csv"
            path.write_text(text)
            # Each command gets fresh package globals and its original CSV limit.
            # The independent scenario oracle still launches the real module CLI.
            stdout, stderr = io.StringIO(), io.StringIO()
            code, limit = 0, csv.field_size_limit()
            try:
                with (
                    patch.dict(sys.modules),
                    patch.object(sys, "argv", ["csvcheck", str(path)]),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    for name in ("csvcheck", "csvcheck.__main__"):
                        sys.modules.pop(name, None)
                    try:
                        runpy.run_module("csvcheck", run_name="__main__", alter_sys=True)
                    except SystemExit as error:
                        code = error.code if isinstance(error.code, int) else 0 if error.code is None else 1
                        if error.code is not None and not isinstance(error.code, int):
                            print(error.code, file=sys.stderr)
            finally:
                csv.field_size_limit(limit)
            return subprocess.CompletedProcess(
                [sys.executable, "-m", "csvcheck", str(path)], code, stdout.getvalue(), stderr.getvalue()
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
