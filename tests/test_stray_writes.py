"""A read-only stage that wrote to the repository is rejected, and its files are put back.

A live review (Claude models, 2026-09-29) applied the patch under review to the project
itself (``... && cd $S && git init -q . ; git apply pr-184.patch``). Four report repairs and
an Investigator retry were rejected the same way because nothing restored the files, and
the run stopped for a person.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_review_job as review_job
import autocode_stray_writes as stray
import autocode_util as util

from . import test_autocode as base

runner = base.runner


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
                   check=True, capture_output=True)


def snapshot_to(path: Path, root: Path) -> str:
    path.write_text(json.dumps(util.snapshot(root)))
    return str(path)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "project"
        self.root.mkdir()
        (self.root / "client.py").write_text("RETRY = False\n")
        (self.root / "tool.sh").write_text("echo hi\n")
        (self.root / "tool.sh").chmod(0o755)
        (self.root / "notes.py").write_text("committed\n")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "seed")
        (self.root / "notes.py").write_text("the user's uncommitted edit\n")
        self.record = {"before_ref": snapshot_to(Path(temp.name) / "before.json", self.root)}

    def attempt(self):
        """What the stray review did: apply a change to committed files and add a new one."""
        (self.root / "client.py").write_text("RETRY = True\n")
        (self.root / "tool.sh").write_text("rm -rf /\n")
        (self.root / "tool.sh").chmod(0o644)
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_new.py").write_text("x = 1\n")
        (self.root / "notes.py").write_text("overwritten by the stage\n")
        self.record["after_ref"] = snapshot_to(Path(self.record["before_ref"]).with_name("after.json"), self.root)
        return ["client.py", "notes.py", "tests/test_new.py", "tool.sh"]

    def test_committed_files_come_back_and_new_files_go(self):
        restored, kept = stray.restore(self.root, self.record, self.attempt())
        self.assertEqual((["client.py", "tests/test_new.py", "tool.sh"], ["notes.py"]), (restored, kept))
        self.assertEqual("RETRY = False\n", (self.root / "client.py").read_text())
        self.assertEqual("echo hi\n", (self.root / "tool.sh").read_text())
        self.assertTrue((self.root / "tool.sh").stat().st_mode & 0o111)
        self.assertFalse((self.root / "tests" / "test_new.py").exists())
        # A file with uncommitted edits before the attempt cannot be restored from Git: it is left and named.
        self.assertEqual("overwritten by the stage\n", (self.root / "notes.py").read_text())

    def test_a_file_changed_again_after_the_attempt_is_left_alone(self):
        paths = self.attempt()
        (self.root / "client.py").write_text("RETRY = 'edited by the user since'\n")
        restored, kept = stray.restore(self.root, self.record, paths)
        self.assertIn("client.py", kept)
        self.assertEqual("RETRY = 'edited by the user since'\n", (self.root / "client.py").read_text())

    def test_other_errors_pass_through_and_the_message_says_what_happened(self):
        other = ValueError("Missing summary")
        self.assertIs(other, stray.undo({"workspace": str(self.root)}, self.record, other))
        paths = self.attempt()
        error = stray.undo({"workspace": str(self.root)}, self.record,
                           stray.StrayWrites("A review must not change the repository", paths))
        self.assertIsInstance(error, stray.StrayWrites)
        self.assertIn("the runner restored client.py, tests/test_new.py, tool.sh", str(error))
        self.assertIn("left as they are (changed since, or not clean before the attempt): notes.py", str(error))


class RejectionTests(unittest.TestCase):
    """Through the runner's own rejection of a completed stage."""

    setUp = base.RetrofitTest.setUp

    def test_a_review_that_wrote_files_is_restored_and_gets_no_report_repair(self):
        (self.root / "client.py").write_text("RETRY = False\n")
        git(self.root, "add", "client.py")
        git(self.root, "commit", "-qm", "client")
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        stage = self.run / "iterations" / "001" / "review_change-01"
        stage.parent.mkdir(parents=True)
        record = {"role": "sol", "stage": "review_change", "iteration": 1, "exit_code": 0, "duration_seconds": 1,
                  "source_revision": util.snapshot(self.root)["revision"],
                  "before_ref": snapshot_to(Path(f"{stage}.before.json"), self.root)}
        (self.root / "client.py").write_text("RETRY = True\n")
        record["after_ref"] = snapshot_to(Path(f"{stage}.after.json"), self.root)
        for key, suffix in (("output", ".json"), ("events", ".jsonl"), ("schema", ".schema.json")):
            Path(f"{stage}{suffix}").write_text("{}")
            record[key] = f"{stage}{suffix}"
        record["changed_files"] = ["client.py"]
        with self.assertRaises(stray.StrayWrites) as raised:
            review_job.apply(self.state, {"verdict": "approve", "findings": []}, record, self.root)
        with self.assertRaises(runner.support.Paused) as stopped:
            runner.reject_completed_stage(self.state, self.run, record, raised.exception)
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual("RETRY = False\n", (self.root / "client.py").read_text())
        self.assertIn("the runner restored client.py", str(stopped.exception))


if __name__ == "__main__":
    unittest.main()
