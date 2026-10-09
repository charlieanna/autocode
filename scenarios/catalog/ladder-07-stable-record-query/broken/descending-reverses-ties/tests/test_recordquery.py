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

    def test_filter_and_page(self):
        rows=[{"id":"a","status":"open","score":2},{"id":"b","status":"closed","score":0},{"id":"c","status":"open","score":1}]
        self.assertEqual([r["id"] for r in query(rows,status="open",offset=1,limit=1)],["a"])

    def test_descending(self):
        self.assertEqual([r["score"] for r in query([{"score":1},{"score":3}],descending=True)],[3,1])
