import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class HiddenCsvTests(unittest.TestCase):
    def invoke(self, raw):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.csv"
            if raw is not None:
                path.write_bytes(raw.encode("utf-8") if isinstance(raw,str) else raw)
            return subprocess.run([sys.executable,"-m","csvcheck",str(path)],capture_output=True,text=True, timeout=10)

    def assert_result(self, text, code, result):
        p = self.invoke(text)
        self.assertEqual((p.returncode,p.stderr),(code,""))
        self.assertEqual(json.loads(p.stdout),result)

    def test_quoting_multiline_and_whitespace(self):
        self.assert_result('name,age,email\r\n"Doe, Jane",0,j@e\r\n"Two\nLines",130,t@e\r\n\r\n A , 001 , a@b \r\n',0,{"rows":3,"errors":[]})

    def test_all_errors_and_record_numbers(self):
        text='name,age,email\n,131,nope\nA,-1,a@@b\nB,1.0,a@ b\nC,١,a@b\nD,4\nE,8,e@f,extra\nGood,130,g@h\n'
        errors=[{"row":2,"fields":["name","age","email"]}, {"row":3,"fields":["age","email"]},
                {"row":4,"fields":["age","email"]}, {"row":5,"fields":["age"]},
                {"row":6,"fields":["columns"]}, {"row":7,"fields":["columns"]}]
        self.assert_result(text,1,{"rows":7,"errors":errors})

    def test_age_range_and_email_edges(self):
        for age in [0,1,42,129,130,131,999]:
            text=f'name,age,email\nUser,{age},u@d\n'
            errors=[] if age<=130 else [{"row":2,"fields":["age"]}]
            self.assert_result(text,bool(errors),{"rows":1,"errors":errors})
        for email in ["@", "a@", "@b", "a@b@c", "a b@c", "a@b c"]:
            self.assert_result(f'name,age,email\nUser,20,{email}\n',1,{"rows":1,"errors":[{"row":2,"fields":["email"]}]})

    def test_long_ascii_age_is_a_row_error_and_long_zero_padding_is_valid(self):
        # Validating a bounded age must not depend on Python's decimal conversion
        # limit: both long invalid numbers and valid zero padding are ordinary data.
        for age, invalid in (("9" * 5000, True), ("0" * 5000 + "131", True),
                             ("0" * 5000, False), ("0" * 5000 + "130", False),
                             ("0" * 140000 + "130", False), ("9" * 140000, True)):
            with self.subTest(invalid=invalid, suffix=age[-3:]):
                errors = [{"row": 2, "fields": ["age"]}] if invalid else []
                self.assert_result(f'name,age,email\nUser,{age},u@d\n', int(invalid),
                                   {"rows": 1, "errors": errors})

    def test_fatal_errors_never_print_partial_result(self):
        for raw in [None,b'\xff', '', 'name,age\n', 'name,age,email,extra\n', 'name,age,email\nA,2,a@b\n"unterminated']:
            p=self.invoke(raw)
            self.assertEqual((p.returncode,p.stdout),(2,""))
            self.assertTrue(p.stderr)

    def test_quote_syntax_is_fatal_in_every_field_and_after_prior_valid_records(self):
        valid = 'name,age,email\nA,0,a@b\n'
        for record in ('Jo"e,30,j@e\n', 'Joe,3"0,j@e\n', 'Joe,30,j@"e\n',
                       '"Joe"x,30,j@e\n', ' "Joe",30,j@e\n', '"Joe,30,j@e\n'):
            with self.subTest(record=record):
                result = self.invoke(valid + record)
                self.assertEqual((2, ""), (result.returncode, result.stdout))
                self.assertTrue(result.stderr)

    def test_escaped_quotes_and_multiline_fields_are_valid(self):
        self.assert_result('name,age,email\r\n"Jo""e",30,j@e\r\n"Two\r\nLines",1,t@e\r\n',
                           0, {"rows": 2, "errors": []})

    def test_header_only(self):
        self.assert_result('name,age,email\n',0,{"rows":0,"errors":[]})
        self.assert_result('\n\r\nname,age,email\n\n',0,{"rows":0,"errors":[]})
