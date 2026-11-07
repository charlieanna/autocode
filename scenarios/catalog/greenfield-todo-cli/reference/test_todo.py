import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TODO = Path(__file__).resolve().parent / "todo.py"


class TodoTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Path(self.dir.name) / "todos.json"

    def todo(self, *args):
        return subprocess.run([sys.executable, str(TODO), *args], cwd=self.dir.name, capture_output=True, text=True)

    def test_add_list_complete(self):
        self.todo("add", "buy milk")
        self.assertEqual(0, self.todo("complete", "1").returncode)
        self.assertEqual("1 buy milk [done]\n", self.todo("list").stdout)

    def test_unknown_id_leaves_store_identical(self):
        self.todo("add", "keep me")
        before = self.store.read_bytes()
        refused = self.todo("complete", "99")
        self.assertEqual((1, "todo: no to-do with id 99\n"), (refused.returncode, refused.stderr))
        self.assertEqual(before, self.store.read_bytes())

    def test_malformed_store_is_never_rewritten(self):
        for text in ("{broken", '{"next_id": "1", "todos": []}'):
            self.store.write_text(text)
            for args in (("list",), ("add", "x"), ("complete", "1")):
                refused = self.todo(*args)
                self.assertEqual(1, refused.returncode)
                self.assertIn("todos.json is malformed", refused.stderr)
                self.assertEqual(text, self.store.read_text())

    def test_usage_errors_exit_2_and_create_no_store(self):
        for args in ((), ("remove",), ("list", "extra"), ("add",)):
            refused = self.todo(*args)
            self.assertEqual(2, refused.returncode, args)
            self.assertTrue(refused.stderr.startswith("usage: todo.py"), refused.stderr)
        self.assertFalse(self.store.exists())


if __name__ == "__main__":
    unittest.main()
