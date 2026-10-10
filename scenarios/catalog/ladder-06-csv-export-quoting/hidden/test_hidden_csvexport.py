import copy
import csv
import io
import random
import unittest

from csvexport import export


class HiddenCsvExportTests(unittest.TestCase):
    def parse(self, text):
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))

    def test_special_characters_in_every_column(self):
        values = [
            "comma,value",
            'say "hello"',
            "line\nfeed",
            "carriage\rreturn",
            "both\r\nlines",
            " leading ",
            "",
            "東京 café",
            None,
        ]
        for value in values:
            for field in ["name", "email", "note"]:
                record = {"name": "Alice", "email": "a@b", "note": "ok", field: value}
                before = copy.deepcopy(record)
                text = export(iter([record]))
                self.assertEqual(
                    self.parse(text),
                    [
                        ["name", "email", "note"],
                        ["" if record[k] is None else record[k] for k in ["name", "email", "note"]],
                    ],
                )
                self.assertTrue(text.endswith("\r\n"))
                self.assertEqual(record, before)

    def test_missing_fields_and_ignored_extra_keys(self):
        records = [{}, {"name": "N", "extra": "ignored"}, {"email": None, "note": "x"}]
        self.assertEqual(
            self.parse(export(records)), [["name", "email", "note"], ["", "", ""], ["N", "", ""], ["", "", "x"]]
        )

    def test_generated_round_trips(self):
        rng = random.Random(601)
        alphabet = 'ab, "\n\rΩ'
        rows = [
            {
                field: "".join(rng.choice(alphabet) for _ in range(rng.randrange(20)))
                for field in ["name", "email", "note"]
            }
            for _ in range(80)
        ]
        expected = [["name", "email", "note"]] + [[row[k] for k in ["name", "email", "note"]] for row in rows]
        self.assertEqual(self.parse(export(rows)), expected)
