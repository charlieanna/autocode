"""The walking skeleton's journey: add notes, then list them."""
import os
import subprocess
import sys
import tempfile
import unittest


def notes(store, *args):
    return subprocess.run([sys.executable, "-m", "notes", *args], capture_output=True, text=True,
                          env={**os.environ, "NOTES_FILE": store})


class SkeletonJourney(unittest.TestCase):
    def test_added_notes_are_listed_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "notes.json")
            self.assertEqual("added 1\n", notes(store, "add", "Buy milk").stdout)
            self.assertEqual("added 2\n", notes(store, "add", "Call", "Bob").stdout)
            listed = notes(store, "list")
            self.assertEqual((0, "1 Buy milk\n2 Call Bob\n"), (listed.returncode, listed.stdout))

    def test_add_without_text_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(2, notes(os.path.join(tmp, "notes.json"), "add").returncode)


if __name__ == "__main__":
    unittest.main()
