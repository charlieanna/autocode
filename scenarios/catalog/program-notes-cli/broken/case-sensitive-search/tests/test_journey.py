"""Journey J1, capture and find a note, on the merged product: add, list, search, export."""
import json
import os
import subprocess
import sys
import tempfile
import unittest


def notes(store, *args):
    return subprocess.run([sys.executable, "-m", "notes", *args], capture_output=True, text=True,
                          env={**os.environ, "NOTES_FILE": store})


class CaptureAndFind(unittest.TestCase):
    def test_capture_and_find_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "notes.json")
            notes(store, "add", "Buy milk")
            notes(store, "add", "Call Bob")
            self.assertEqual("1 Buy milk\n2 Call Bob\n", notes(store, "list").stdout)
            self.assertEqual("2 Call Bob\n", notes(store, "search", "Bob").stdout)
            self.assertEqual([{"id": 1, "text": "Buy milk"}, {"id": 2, "text": "Call Bob"}],
                             json.loads(notes(store, "export").stdout))


if __name__ == "__main__":
    unittest.main()
