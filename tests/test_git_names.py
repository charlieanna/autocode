"""A file name that is not UTF-8 must not stop the source snapshot (#1116)."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_util as util

LATIN1 = b"caf\xe9.txt"


class GitNamesTests(unittest.TestCase):
    def test_a_name_that_is_not_utf8_stays_one_name_and_round_trips_to_the_same_bytes(self):
        names = util.git_names(b"a.txt\0" + LATIN1 + b"\0")
        self.assertEqual(["a.txt", os.fsdecode(LATIN1)], names)
        self.assertEqual(LATIN1, os.fsencode(names[1]))
        self.assertEqual([], util.git_names(b""))

    def test_the_snapshot_survives_a_listing_with_a_non_utf8_name_and_can_be_saved(self):
        listing = b"a.txt\0" + LATIN1 + b"\0"

        def run(args, **kwargs):
            return listing if "ls-files" in args else "0123abcd\n"

        with tempfile.TemporaryDirectory() as temporary, patch.object(util.subprocess, "check_output", run):
            (Path(temporary) / "a.txt").write_text("a")
            result = util.snapshot(temporary)
            util.atomic_json(Path(temporary) / "before.json", result)
            saved = json.loads((Path(temporary) / "before.json").read_text())
        self.assertEqual({"a.txt", os.fsdecode(LATIN1)}, set(result["files"]))
        self.assertEqual(result, saved)

    def test_a_real_file_with_a_non_utf8_name_is_part_of_the_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                cwd=root,
                check=True,
            )
            try:
                os.close(os.open(os.fsencode(root) + b"/" + LATIN1, os.O_CREAT | os.O_WRONLY))
            except OSError as error:
                self.skipTest(f"this file system refuses a non-UTF-8 name: {error}")  # APFS does
            result = util.snapshot(root)
        self.assertIn(os.fsdecode(LATIN1), result["files"])


if __name__ == "__main__":
    unittest.main()
