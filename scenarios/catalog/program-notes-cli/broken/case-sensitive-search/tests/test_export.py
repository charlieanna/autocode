import json
import os
import subprocess
import sys
import tempfile
import unittest


def notes(store, *args):
    return subprocess.run(
        [sys.executable, "-m", "notes", *args], capture_output=True, text=True, env={**os.environ, "NOTES_FILE": store}
    )


class Export(unittest.TestCase):
    def test_export_prints_every_note_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "notes.json")
            self.assertEqual([], json.loads(notes(store, "export").stdout))
            notes(store, "add", "Buy milk")
            self.assertEqual([{"id": 1, "text": "Buy milk"}], json.loads(notes(store, "export").stdout))


if __name__ == "__main__":
    unittest.main()
