"""The serial assignment boundary measures the whole assignment, from saved snapshots."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_assignment as assignment


class RetainedChangesTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def snapshot(self, name, files):
        path = self.dir / name
        path.write_text(json.dumps({"head": "h", "files": files, "revision": name}))
        return str(path)

    def attempt(self, started, before, after, **extra):
        return {
            "stage": "terra",
            "iteration": 1,
            "task_id": "task-1",
            "started_at": started,
            "before_ref": before,
            "after_ref": after,
            **extra,
        }

    def test_an_earlier_attempts_edit_counts_against_a_retry_that_changed_nothing(self):
        start = self.snapshot("a.before", {"src/a.py": "1", "notes.txt": "n"})
        left = self.snapshot("a.after", {"src/a.py": "2", "notes.txt": "stray"})
        first = self.attempt("t1", start, left, changed_files=["notes.txt", "src/a.py"])
        retry = self.attempt(
            "t2", left, self.snapshot("b.after", {"src/a.py": "2", "notes.txt": "stray"}), changed_files=[]
        )
        self.assertEqual(["notes.txt"], assignment.outside(["src"], [first], retry))

    def test_a_deleted_path_is_a_change(self):
        start = self.snapshot("a.before", {"src/a.py": "1", "keep.txt": "k"})
        first = self.attempt("t1", start, self.snapshot("a.after", {"src/a.py": "2"}))
        self.assertEqual(["keep.txt"], assignment.outside(["src/"], [], first))

    def test_the_archived_copy_of_an_attempt_is_found_when_another_copy_is_stale(self):
        archived = self.snapshot("archived.before", {"src/a.py": "1"})
        stale = self.attempt("t1", str(self.dir / "moved.before"), None)
        rejected = dict(stale, before_ref=archived, rejected=True)
        retry = self.attempt("t2", archived, self.snapshot("b.after", {"src/a.py": "2"}))
        self.assertEqual([], assignment.outside(["src"], [stale, rejected], retry))

    def test_an_unreadable_start_is_missing_evidence_not_an_empty_delta(self):
        first = self.attempt("t1", str(self.dir / "gone"), None)
        retry = self.attempt("t2", str(self.dir / "gone"), self.snapshot("b.after", {}), changed_files=[])
        self.assertIsNone(assignment.outside(["src"], [first], retry))

    def test_a_new_compiled_program_a_build_left_behind_is_not_a_scope_violation(self):
        # A live Go run's Builder ran `go build .`, which wrote ./policy; the stage was refused for it.
        workspace = Path(tempfile.mkdtemp())
        (workspace / "policy").write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 64)
        (workspace / "notes.txt").write_text("stray")
        (workspace / "script.sh").write_text("#!/bin/sh\necho hi\n")
        start = self.snapshot("a.before", {"src/a.go": "1"})
        after = self.snapshot(
            "a.after", {"src/a.go": "2", "policy": "executable:x", "notes.txt": "n", "script.sh": "executable:y"}
        )
        first = self.attempt("t1", start, after)
        self.assertEqual(["notes.txt", "script.sh"], assignment.outside(["src"], [], first, workspace=workspace))
        # Without the workspace nothing can be read, so nothing is excused.
        self.assertEqual(["notes.txt", "policy", "script.sh"], assignment.outside(["src"], [], first))

    def test_changing_an_existing_executable_still_counts(self):
        workspace = Path(tempfile.mkdtemp())
        (workspace / "tool").write_bytes(b"\x7fELF" + b"\0" * 64)
        start = self.snapshot("a.before", {"src/a.go": "1", "tool": "executable:old"})
        first = self.attempt("t1", start, self.snapshot("a.after", {"src/a.go": "2", "tool": "executable:new"}))
        self.assertEqual(["tool"], assignment.outside(["src"], [], first, workspace=workspace))

    def test_a_first_attempt_without_snapshots_falls_back_to_its_measured_delta(self):
        only = {"stage": "terra", "changed_files": ["src/a.py", "other.txt"]}
        self.assertEqual(["other.txt"], assignment.outside(["src"], [], only))


class UndoCreatedTests(RetainedChangesTests):
    """Out-of-scope files the assignment created are removed; anything else is kept for a person (2026-09-30).

    Since #218: a pre-existing file the snapshots prove clean at the assignment's start is restored from Git,
    so one stray edit cannot refuse every retry; unprovably-clean files keep today's behavior."""

    def workspace(self, files):
        root = Path(tempfile.mkdtemp())
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return root

    def snapshot_at(self, name, files, head):
        path = self.dir / name
        path.write_text(json.dumps({"head": head, "files": files, "revision": name}))
        return str(path)

    def git_workspace(self, files):
        import subprocess

        root = self.workspace(files)

        def git(*args):
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

        git("init", "-q")
        git("add", "-A")
        git("-c", "user.email=fixture@example.com", "-c", "user.name=fixture", "commit", "-qm", "start")
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return root, head

    def test_a_created_file_is_removed_and_an_edited_one_is_kept(self):
        import autocode_stray_writes as stray

        root = self.workspace({"src/a.py": "2", "README.md": "new", "notes.txt": "edited"})
        start = self.snapshot("a.before", {"src/a.py": "1", "notes.txt": "n"})
        after = self.snapshot(
            "a.after", {name: stray.current(root / name) for name in ("src/a.py", "README.md", "notes.txt")}
        )
        first = self.attempt("t1", start, after)
        paths = assignment.outside(["src"], [], first)
        self.assertEqual(["README.md", "notes.txt"], paths)
        message = assignment.undo_created(paths, [], first, root)
        self.assertEqual(
            "Builder attempts for this task changed files outside the assigned paths; the runner removed "
            "the files they created, so a retry starts without them: README.md; edits retained for "
            "inspection: notes.txt",
            message,
        )
        self.assertFalse((root / "README.md").exists())
        self.assertEqual("edited", (root / "notes.txt").read_text())

    def test_a_created_file_changed_since_the_attempt_is_kept(self):
        import autocode_stray_writes as stray

        root = self.workspace({"README.md": "the attempt's"})
        after = self.snapshot("a.after", {"README.md": stray.current(root / "README.md")})
        first = self.attempt("t1", self.snapshot("a.before", {}), after)
        (root / "README.md").write_text("changed by someone since")
        self.assertIn(
            "edits retained for inspection: README.md", assignment.undo_created(["README.md"], [], first, root)
        )
        self.assertTrue((root / "README.md").exists())

    def test_an_edited_committed_file_is_restored_and_the_retry_is_not_refused(self):
        import autocode_stray_writes as stray

        root, head = self.git_workspace({"src/a.py": "1", "notes.txt": "committed note\n"})
        start = self.snapshot_at(
            "a.before",
            {"src/a.py": stray.current(root / "src/a.py"), "notes.txt": stray.current(root / "notes.txt")},
            head,
        )
        (root / "notes.txt").write_text("edited out of scope\n")
        after = self.snapshot_at("a.after", {"notes.txt": stray.current(root / "notes.txt")}, head)
        first = self.attempt("t1", start, after)
        self.assertEqual(["notes.txt"], assignment.outside(["src"], [], first))
        message = assignment.undo_created(["notes.txt"], [], first, root)
        self.assertIn(
            "the runner restored pre-existing files to their committed content, so a retry is not "
            "refused for them: notes.txt",
            message,
        )
        self.assertEqual("committed note\n", (root / "notes.txt").read_text())
        # The retry is gated against the same starting snapshot, now over a restored tree.
        retry = self.attempt(
            "t2",
            start,
            self.snapshot_at(
                "b.after",
                {"src/a.py": stray.current(root / "src/a.py"), "notes.txt": stray.current(root / "notes.txt")},
                head,
            ),
        )
        self.assertEqual([], assignment.outside(["src"], [first], retry))

    def test_a_file_that_held_uncommitted_edits_at_start_is_kept(self):
        import autocode_stray_writes as stray

        root, head = self.git_workspace({"src/a.py": "1", "notes.txt": "committed\n"})
        (root / "notes.txt").write_text("uncommitted before the assignment\n")
        start = self.snapshot_at("a.before", {"notes.txt": stray.current(root / "notes.txt")}, head)
        (root / "notes.txt").write_text("edited further\n")
        after = self.snapshot_at("a.after", {"notes.txt": stray.current(root / "notes.txt")}, head)
        first = self.attempt("t1", start, after)
        message = assignment.undo_created(["notes.txt"], [], first, root)
        self.assertIn("edits retained for inspection: notes.txt", message)
        self.assertEqual("edited further\n", (root / "notes.txt").read_text())

    def test_a_deleted_committed_file_is_restored(self):
        import autocode_stray_writes as stray

        root, head = self.git_workspace({"src/a.py": "1", "notes.txt": "committed note\n"})
        start = self.snapshot_at(
            "a.before",
            {"src/a.py": stray.current(root / "src/a.py"), "notes.txt": stray.current(root / "notes.txt")},
            head,
        )
        (root / "notes.txt").unlink()
        after = self.snapshot_at("a.after", {"src/a.py": stray.current(root / "src/a.py")}, head)
        first = self.attempt("t1", start, after)
        message = assignment.undo_created(["notes.txt"], [], first, root)
        self.assertIn("the runner restored pre-existing files", message)
        self.assertEqual("committed note\n", (root / "notes.txt").read_text())


if __name__ == "__main__":
    unittest.main()
