import copy
import json
import subprocess
import sys
import unittest

from app import paginate


class PaginationContract(unittest.TestCase):
    def test_ties_are_ordered_without_skips_or_repeats(self):
        rows = [{"id": i, "created_at": i // 3, "label": f"row-{i}"} for i in [8, 2, 1, 7, 4, 6, 0, 5, 3]]
        original = copy.deepcopy(rows)
        for limit in (1, 2, 3, 4, 100):
            cursor, emitted = None, []
            for _ in range(len(rows) + 1):
                page = paginate(rows, limit, cursor)
                self.assertEqual(set(page), {"items", "next_cursor"})
                emitted.extend(page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                self.assertIsInstance(cursor, str)
                self.assertTrue(cursor)
            else:
                self.fail("pagination did not terminate")
            self.assertEqual(emitted, sorted(original, key=lambda row: (row["created_at"], row["id"])))
        self.assertEqual(rows, original)

    def test_cursor_survives_insertion_and_deleted_boundary(self):
        rows = [{"id": i, "created_at": 10} for i in (10, 20, 30, 40)]
        first = paginate(rows, 2)
        changed = [rows[2], {"id": 5, "created_at": 5}, {"id": 25, "created_at": 10}, rows[3]]
        second = paginate(changed, 100, first["next_cursor"])
        self.assertEqual([row["id"] for row in second["items"]], [25, 30, 40])
        self.assertIsNone(second["next_cursor"])

    def test_empty_and_invalid_arguments(self):
        self.assertEqual(paginate([], 3), {"items": [], "next_cursor": None})
        for limit in (0, -1, 101, True, 1.5, "2"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                paginate([], limit)
        for cursor in ("", "not-a-cursor", "W10=", "bnVsbA==", "WyJ4IiwxXQ==", 22, []):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                paginate([], 2, cursor)

    def test_cursor_continues_in_a_fresh_process(self):
        command = [sys.executable, "-c", "import json, sys; from app import paginate; request = json.load(sys.stdin); print(json.dumps(paginate(request['rows'], request['limit'], request.get('cursor'))))"]
        rows = [{"id": i, "created_at": 10} for i in (10, 20, 30, 40)]
        first_run = subprocess.run(command, input=json.dumps({"rows": rows, "limit": 2}), capture_output=True, text=True, timeout=10)
        self.assertEqual(first_run.returncode, 0, first_run.stderr)
        first = json.loads(first_run.stdout)
        self.assertEqual(first["items"], rows[:2])
        self.assertIsInstance(first["next_cursor"], str)
        later_rows = [rows[2], {"id": 5, "created_at": 5}, {"id": 25, "created_at": 10}, rows[3]]
        second_run = subprocess.run(command, input=json.dumps({"rows": later_rows, "limit": 100, "cursor": first["next_cursor"]}), capture_output=True, text=True, timeout=10)
        self.assertEqual(second_run.returncode, 0, second_run.stderr)
        second = json.loads(second_run.stdout)
        self.assertEqual([row["id"] for row in second["items"]], [25, 30, 40])
        self.assertIsNone(second["next_cursor"])
