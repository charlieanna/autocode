import unittest

from csvexport import export


class CsvExportTests(unittest.TestCase):
    def test_plain_record(self):
        self.assertEqual(export([{"name":"A","email":"a@b","note":"hello"}]),"name,email,note\r\nA,a@b,hello\r\n")

    def test_header_only(self):
        self.assertEqual(export([]),"name,email,note\r\n")
