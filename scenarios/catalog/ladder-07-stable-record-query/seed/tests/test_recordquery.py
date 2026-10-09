import unittest
from recordquery import query

class RecordQueryTests(unittest.TestCase):
    def test_default_score_sort(self):
        rows=[{"id":"a","score":2},{"id":"b","score":1}]
        self.assertEqual([r["id"] for r in query(rows)],["b","a"])

    def test_input_preserved(self):
        rows=[{"score":2},{"score":1}]
        query(rows)
        self.assertEqual(rows,[{"score":2},{"score":1}])
