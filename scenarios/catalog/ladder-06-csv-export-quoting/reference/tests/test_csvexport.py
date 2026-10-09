import csv
import io
import unittest
from csvexport import export

class CsvExportTests(unittest.TestCase):
    def test_plain_record(self):
        self.assertEqual(export([{"name":"A","email":"a@b","note":"hello"}]),"name,email,note\r\nA,a@b,hello\r\n")

    def test_header_only(self):
        self.assertEqual(export([]),"name,email,note\r\n")

    def test_comma_round_trip(self):
        text=export([{"name":"Doe, Jane","email":"j@e","note":"hello"}])
        self.assertEqual(list(csv.reader(io.StringIO(text,newline="")))[1],["Doe, Jane","j@e","hello"])
