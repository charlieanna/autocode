"""Where `autocode capture` may write its receipt (#1630)."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_capture_command as capture


class CaptureOutputLocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capture-location-")
        self.addCleanup(temporary.cleanup)
        # A task worktree inside an outer project, as AutoCode creates them.
        self.outer = Path(temporary.name).resolve()
        self.project = self.outer / ".autocode/worktrees/task"
        # The Investigator's runner-prepared scratch copy of the project.
        self.scratch = self.project / ".autocode/investigation/bug-1"
        self.scratch.mkdir(parents=True)
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)

    def run_capture(self, cwd, output):
        os.chdir(cwd)
        command = [sys.executable, "-c", "import os; print(os.getcwd())"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            try:
                return capture.cli(["--output", str(output), "--", *command]), errors.getvalue()
            except SystemExit as stop:
                return stop.code, errors.getvalue()

    def test_a_scratch_copy_may_write_to_the_project_evidence_directory(self):
        receipt = self.project / ".autocode/evidence/repro.json"
        code, _ = self.run_capture(self.scratch, receipt)
        self.assertEqual(0, code)
        saved = json.loads(receipt.read_text())
        self.assertEqual(0, saved["exit_code"])
        self.assertEqual(str(self.scratch), Path(saved["full_output"]).read_text().strip())

    def test_a_scratch_copy_may_still_write_beside_itself(self):
        code, _ = self.run_capture(self.scratch, Path(".autocode/evidence/repro.json"))
        self.assertEqual(0, code)
        self.assertTrue((self.scratch / ".autocode/evidence/repro.json").is_file())

    def test_outside_the_nearest_project_autocode_is_refused(self):
        for cwd, output in (
            (self.scratch, self.project / "repro.json"),
            (self.scratch, self.outer / ".autocode/repro.json"),
            (self.project, self.outer / ".autocode/repro.json"),
        ):
            with self.subTest(cwd=cwd, output=output):
                code, errors = self.run_capture(cwd, output)
                self.assertEqual(2, code)
                self.assertIn("evidence output must be under this project's .autocode directory", errors)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
