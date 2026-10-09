import copy
import itertools
import unittest

from recordquery import query


class HiddenRecordQueryTests(unittest.TestCase):
    def test_stability_and_missing_values_in_both_directions(self):
        rows=[{"id":"missing"},{"id":"a","score":3},{"id":"b","score":3},{"id":"null","score":None},{"id":"c","score":1}]
        self.assertEqual([r["id"] for r in query(rows)],["c","a","b","missing","null"])
        self.assertEqual([r["id"] for r in query(rows,descending=True)],["a","b","c","missing","null"])

    def test_filter_sort_and_page_cross_product(self):
        rows=[{"id":0,"status":"open","name":"Zed","score":2}, {"id":1,"status":"closed","name":"Amy","score":9},
              {"id":2,"status":"open","name":"Amy","score":2}, {"id":3,"status":"open","name":"bob","score":1},
              {"id":4,"status":"OPEN","name":"A","score":0}]
        before=copy.deepcopy(rows)
        for status,key,descending,offset,limit in itertools.product([None,"open","closed","absent"],["score","name"],[False,True],[0,1,8],[None,0,1,9]):
            filtered=[r for r in rows if status is None or r["status"]==status]
            ordered=sorted(filtered,key=lambda r:r[key],reverse=descending)
            expected=ordered[offset:] if limit is None else ordered[offset:offset+limit]
            got=query(rows,status=status,sort_by=key,descending=descending,offset=offset,limit=limit)
            self.assertEqual(got,expected)
            self.assertIsNot(got,rows)
        self.assertEqual(rows,before)

    def test_invalid_options_even_with_empty_input(self):
        for options in [{"sort_by":"id"},{"offset":-1},{"offset":True},{"offset":1.0},{"limit":-1},{"limit":False},{"limit":"1"}]:
            with self.subTest(options=options),self.assertRaises(ValueError):
                query([],**options)
