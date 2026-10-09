import os
import subprocess
import sys
import tempfile
import unittest


def notes(store, *args):
    return subprocess.run([sys.executable, "-m", "notes", *args], capture_output=True, text=True,
                          env={**os.environ, "NOTES_FILE": store})


class Search(unittest.TestCase):
    def test_search_prints_the_matching_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "notes.json")
            notes(store, "add", "Buy milk")
            notes(store, "add", "Call Bob")
            self.assertEqual("1 Buy milk\n", notes(store, "search", "milk").stdout)
            self.assertEqual("", notes(store, "search", "tea").stdout)

    def test_search_without_words_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(2, notes(os.path.join(tmp, "notes.json"), "search").returncode)


if __name__ == "__main__":
    unittest.main()
