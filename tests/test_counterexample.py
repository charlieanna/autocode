"""The counterexample a probe must fail on is made in a scratch copy, from the file it names (#1769)."""

import os
import tempfile
import unittest
from pathlib import Path

import autocode_counterexample as counterexample


class EditedTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="counterexample-"))
        (self.root / "app.py").write_text("LIMIT = 60\r\n")
        self.edit = {"file": "app.py", "find": "LIMIT = 60", "replace": "LIMIT = 6"}

    def test_the_edit_is_made_on_the_file_bytes_as_they_are(self):
        self.assertEqual("LIMIT = 6\r\n", counterexample.edited("app.py", self.edit, self.root))

    def test_a_file_already_in_the_probes_overlay_is_edited_there(self):
        copy = self.root / "overlay.json"
        copy.write_text('{"LIMIT = 60": 1}')
        self.assertEqual(
            '{"LIMIT = 6": 1}', counterexample.edited("run/x.json", self.edit, self.root, {"run/x.json": copy})
        )

    def test_a_file_outside_the_repository_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="counterexample-outside-"))
        (outside / "app.py").write_text("LIMIT = 60\n")
        os.symlink(outside, self.root / "linked")
        for name, reason in (("linked/app.py", "outside the repository"), ("/etc/hosts", "repository-relative")):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, reason):
                counterexample.edited(name, self.edit, self.root)

    def test_the_runner_overlays_the_edited_file_and_keeps_its_mode(self):
        (self.root / "app.py").chmod(0o755)
        seen = {}

        def scratch_run(workspace, directory, *, command, files, timeout):
            seen.update(files=files, command=command)
            return {"exit_code": 1}

        run = counterexample.runner(scratch_run, self.root, self.root / "out", timeout=5)
        self.assertEqual({"exit_code": 1}, run("probe", self.edit))
        edited = Path(seen["files"]["app.py"])
        self.assertEqual(b"LIMIT = 6\r\n", edited.read_bytes())
        self.assertEqual(0o755, edited.stat().st_mode & 0o777)
        self.assertEqual(b"LIMIT = 60\r\n", (self.root / "app.py").read_bytes())
        self.assertEqual(
            {"error": "the counterexample's file 'gone.py' is not a file in the repository"},
            run("probe", {**self.edit, "file": "gone.py"}),
        )


if __name__ == "__main__":
    unittest.main()
