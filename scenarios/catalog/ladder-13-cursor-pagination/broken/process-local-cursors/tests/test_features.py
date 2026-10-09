import unittest

from app import paginate


class PaginationTests(unittest.TestCase):
    def test_two_pages(self):
        rows = [{"id": i, "created_at": i} for i in range(3)]
        first = paginate(rows, 2)
        self.assertEqual(first["items"], rows[:2])
        second = paginate(rows, 2, first["next_cursor"])
        self.assertEqual(second, {"items": rows[2:], "next_cursor": None})
