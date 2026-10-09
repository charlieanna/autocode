import os
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
        return subprocess.run([sys.executable, str(TODO), *args], cwd=self.dir.name,
                              capture_output=True, text=True)

    def test_add_list_complete(self):
        self.todo("add", "buy milk")
        self.assertEqual(0, self.todo("complete", "1").returncode)
        self.assertEqual("1 buy milk [done]\n", self.todo("list").stdout)

    def test_unknown_id_leaves_store_identical(self):
        self.todo("add", "keep me")
        before = self.store.read_bytes()
        self.assertNotEqual(0, self.todo("complete", "99").returncode)
        self.assertEqual(before, self.store.read_bytes())

    def test_malformed_store_is_never_rewritten(self):
        self.store.write_text("{broken")
        for args in (("list",), ("add", "x"), ("complete", "1")):
            self.assertNotEqual(0, self.todo(*args).returncode)
            self.assertEqual("{broken", self.store.read_text())


if __name__ == "__main__":
    unittest.main()
