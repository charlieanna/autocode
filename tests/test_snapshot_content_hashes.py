"""Source snapshots reuse a file's hash only while its file-system identity holds (#711).

A snapshot of a large repository read every file at every stage. The hashes are now
remembered in the process, keyed by device, inode, size and both timestamps, and a
remembered hash is reused only when that identity is unchanged and the file was
already settled (older than the settle margin) when it was read. These tests drive
the clock instead of waiting for files to age.
"""

import builtins
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_util as util


class Clock:
    """The real clocks, with the wall clock moved by ``offset`` nanoseconds."""

    def __init__(self, offset):
        self.offset = offset

    def wall_ns(self):
        return time.time_ns() + self.offset

    def monotonic_ns(self):
        return time.monotonic_ns()


def legacy_snapshot(root):
    """The snapshot as it was before the cache: every file read, every time."""
    names = util.git_names(
        subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root)
    )
    files = {}
    for name in sorted(set(names)):
        if name.startswith((".autocode/", ".autocode-ui/")) or "/__pycache__/" in f"/{name}" or name.endswith(".pyc"):
            continue
        path = root / name
        if path.is_symlink():
            files[name] = "symlink:" + os.readlink(path)
        elif path.is_file():
            files[name] = ("executable:" if path.stat().st_mode & 0o111 else "") + util.file_hash(path)
        elif path.is_dir():
            files[name] = (
                "submodule:" + legacy_snapshot(path)["revision"]
                if (path / ".git").exists()
                else "uninitialized-submodule"
            )
        elif not path.exists():
            files[name] = "deleted"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return {"head": head, "files": files, "revision": util.digest({"head": head, "files": files})}


class SnapshotContentHashTests(unittest.TestCase):
    SETTLED = 2 * util.ContentHashes.SETTLE_NS  # every file in the fixture counts as old

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git(self.root, "init", "-q")
        self.write("README.md", "seed\n")
        self.write("pkg/a.py", "A = 1\n")
        self.write("pkg/b.py", "B = 2\n")
        self.write("tool.sh", "#!/bin/sh\n").chmod(0o755)
        self.write("gone.txt", "deleted later\n")
        (self.root / "link").symlink_to("pkg/a.py")
        self.write(".gitignore", "ignored/\n")
        self.write("ignored/x.txt", "not source\n")
        nested = self.root / "nested"
        nested.mkdir()
        self.git(nested, "init", "-q")
        (nested / "inner.txt").write_text("inner\n")
        self.git(nested, "add", ".")
        self.commit(nested)
        self.git(self.root, "add", "README.md", "pkg", "tool.sh", "gone.txt", "link", ".gitignore")
        self.commit(self.root)
        (self.root / "gone.txt").unlink()
        self.write("untracked.py", "U = 3\n")
        self.clock = Clock(self.SETTLED)
        self.hashes = util.ContentHashes(self.clock.wall_ns, self.clock.monotonic_ns)
        patcher = mock.patch.object(util, "_CONTENT_HASHES", self.hashes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, root, *args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    def commit(self, root):
        self.git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def reads(self):
        """Count the files a snapshot opens."""
        opened = []

        def counting_open(path, *args, **kwargs):
            opened.append(os.fspath(path))
            return builtins.open(path, *args, **kwargs)

        patcher = mock.patch.object(util, "open", counting_open, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        return opened

    def test_every_snapshot_equals_reading_every_file(self):
        expected = legacy_snapshot(self.root)
        self.assertEqual("deleted", expected["files"]["gone.txt"])
        self.assertTrue(expected["files"]["tool.sh"].startswith("executable:"))
        self.assertEqual("symlink:pkg/a.py", expected["files"]["link"])
        self.assertTrue(expected["files"]["nested/"].startswith("submodule:"))  # Git lists it with the slash
        self.assertNotIn("ignored/x.txt", expected["files"])
        self.assertEqual(expected, util.snapshot(self.root))
        self.assertEqual(expected, util.snapshot(self.root))  # from remembered hashes

    def test_a_settled_unchanged_file_is_not_read_again(self):
        util.snapshot(self.root)
        opened = self.reads()
        util.snapshot(self.root)
        self.assertEqual([], opened)

    def test_a_file_too_recent_to_trust_its_times_is_read_at_every_snapshot(self):
        self.clock.offset = -util.ContentHashes.SETTLE_NS  # however slow the host, the fixture is recent
        opened = self.reads()
        util.snapshot(self.root)
        first = len(opened)
        util.snapshot(self.root)
        self.assertGreater(first, 0)
        self.assertEqual(2 * first, len(opened))

    def test_an_edit_and_a_mode_change_after_a_snapshot_are_seen(self):
        util.snapshot(self.root)
        self.write("pkg/a.py", "A = 1  # edited\n")
        (self.root / "pkg/b.py").chmod(0o755)
        after = util.snapshot(self.root)
        self.assertEqual(legacy_snapshot(self.root), after)
        self.assertTrue(after["files"]["pkg/b.py"].startswith("executable:"))

    def test_a_same_size_rewrite_is_seen(self):
        util.snapshot(self.root)
        path = self.write("pkg/a.py", "A = 9\n")
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10**9))  # a distinct time, whatever the tick
        self.assertEqual(legacy_snapshot(self.root), util.snapshot(self.root))

    def test_a_same_size_rewrite_with_its_modification_time_put_back_is_seen(self):
        util.snapshot(self.root)
        path = self.root / "pkg/a.py"
        before = os.lstat(path)
        path.write_text("A = 9\n")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))  # any writer can; only the change time tells
        after = os.lstat(path)
        if after.st_ctime_ns == before.st_ctime_ns:
            self.skipTest("this file system's change times are too coarse to tell the rewrite from the fixture")
        self.assertEqual(
            (before.st_ino, before.st_size, before.st_mtime_ns), (after.st_ino, after.st_size, after.st_mtime_ns)
        )
        self.assertEqual(legacy_snapshot(self.root), util.snapshot(self.root))

    def test_a_wall_clock_jump_forgets_every_hash(self):
        util.snapshot(self.root)
        self.clock.offset -= 5 * 10**9
        opened = self.reads()
        util.snapshot(self.root)
        self.assertIn(os.path.join(self.root, "pkg/a.py"), opened)

    def test_a_clock_jump_found_by_a_nested_snapshot_forgets_the_outer_one_too(self):
        _, _, outer = self.hashes.begin("/w")
        self.clock.offset -= 5 * 10**9
        _, _, nested = self.hashes.begin("/w/nested")  # the jump is found mid-way through /w
        self.hashes.finish("/w/nested", {("identity", 1): "hash"}, nested)
        self.hashes.finish("/w", {("identity", 0): "hash"}, outer)
        self.assertEqual({}, self.hashes.begin("/w")[0])
        self.assertEqual({("identity", 1): "hash"}, self.hashes.begin("/w/nested")[0])

    def test_without_reuse_every_snapshot_reads_every_file(self):
        self.hashes.REUSE = False  # Windows: st_ctime is the creation time
        opened = self.reads()
        first = util.snapshot(self.root)
        count = len(opened)
        self.assertEqual(first, util.snapshot(self.root))
        self.assertGreater(count, 0)
        self.assertEqual(2 * count, len(opened))
        self.assertEqual(legacy_snapshot(self.root), first)

    def test_a_file_that_changed_while_it_was_read_is_not_remembered(self):
        path = self.root / "pkg/a.py"
        info = os.lstat(path)
        stale = SimpleNamespace(  # the times before a write that landed during the read
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns - 1,
            st_ctime_ns=info.st_ctime_ns - 1,
        )
        kept = {}
        value = util.ContentHashes.content(os.fspath(path), stale, {}, kept, time.time_ns())
        self.assertEqual(util.file_hash(path), value)
        self.assertEqual({}, kept)

    def test_only_the_most_recent_workspaces_are_remembered(self):
        hashes = util.ContentHashes(self.clock.wall_ns, self.clock.monotonic_ns)
        for index in range(hashes.ROOTS + 1):
            epoch = hashes.begin(f"/w{index}")[2]
            hashes.finish(f"/w{index}", {("identity", index): "hash"}, epoch)
        self.assertEqual({}, hashes.begin("/w0")[0])
        self.assertEqual({("identity", hashes.ROOTS): "hash"}, hashes.begin(f"/w{hashes.ROOTS}")[0])


if __name__ == "__main__":
    unittest.main()
