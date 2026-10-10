"""The oracle's journey J1, capture and find a note, through the documented command line only."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT = os.getcwd()


def notes(*args, store=None, cwd=None):
    env = {**os.environ, "PYTHONPATH": PROJECT}
    env.pop("NOTES_FILE", None)
    if store:
        env["NOTES_FILE"] = store
    return subprocess.run(
        [sys.executable, "-m", "notes", *args], capture_output=True, text=True, env=env, cwd=cwd or PROJECT, timeout=60
    )


class CaptureAndFind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = os.path.join(self.tmp.name, "notes.json")

    def run_ok(self, *args):
        result = notes(*args, store=self.store)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout

    def test_add_numbers_each_note(self):
        self.assertEqual("added 1\n", self.run_ok("add", "Buy milk"))
        self.assertEqual("added 2\n", self.run_ok("add", "Call", "Bob"))

    def test_list_prints_every_note_in_order(self):
        self.assertEqual("", self.run_ok("list"))
        for text in ("Buy milk", "Call Bob", "Pay rent"):
            self.run_ok("add", text)
        self.assertEqual("1 Buy milk\n2 Call Bob\n3 Pay rent\n", self.run_ok("list"))

    def test_search_ignores_case(self):
        for text in ("Buy milk", "Call Bob", "Milkshake recipe"):
            self.run_ok("add", text)
        self.assertEqual("1 Buy milk\n3 Milkshake recipe\n", self.run_ok("search", "MILK"))
        self.assertEqual("2 Call Bob\n", self.run_ok("search", "call bob"))
        self.assertEqual("", self.run_ok("search", "tea"))

    def test_export_is_a_json_array_of_id_and_text(self):
        self.assertEqual([], json.loads(self.run_ok("export")))
        self.run_ok("add", "Buy milk")
        self.run_ok("add", "Call Bob")
        self.assertEqual(
            [{"id": 1, "text": "Buy milk"}, {"id": 2, "text": "Call Bob"}], json.loads(self.run_ok("export"))
        )

    def test_notes_default_to_notes_json_in_the_current_directory(self):
        self.assertEqual(0, notes("add", "Buy milk", cwd=self.tmp.name).returncode)
        with open(os.path.join(self.tmp.name, "notes.json")) as handle:
            saved = json.load(handle)
        self.assertEqual(["Buy milk"], [row["text"] for row in saved])
        self.assertEqual("1 Buy milk\n", notes("list", cwd=self.tmp.name).stdout)

    def test_missing_arguments_and_unknown_commands_are_usage_errors(self):
        for args in (("add",), ("search",), ("delete", "1"), ()):
            with self.subTest(args=args):
                result = notes(*args, store=self.store)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertTrue(result.stderr.strip())
