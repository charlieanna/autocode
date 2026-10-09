import unittest
from intervalset import coalesce

class IntervalTests(unittest.TestCase):
    def test_overlaps(self):
        self.assertEqual(coalesce([(1,4),(3,6)]),[(1,6)])

    def test_empty(self):
        self.assertEqual(coalesce([]),[])

    def test_touching_is_not_overlap(self):
        self.assertEqual(coalesce([(1,3),(3,5)]),[(1,3),(3,5)])
