"""Filesystem behavior shared by Linux read-only stages."""

import errno
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_linux_copy_view as view


@unittest.skipIf(os.name == "nt", "POSIX filesystem operations")
class CopyViewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.inputs, self.outputs = root / "inputs", root / "outputs"
        (self.inputs / "src").mkdir(parents=True)
        self.outputs.mkdir()
        (self.inputs / "src/file").write_bytes(b"original")
        (self.inputs / "alias").symlink_to("src/file")
        (self.inputs / "dangling").symlink_to("absent")
        self.view = view.CopyView(self.inputs, self.outputs, None)

    def test_original_names_and_handles_are_read_only(self):
        fs = self.view
        for name in ("/", "/src", "/src/file", "/alias", "/dangling"):
            with self.subTest(name=name), self.assertRaises(OSError) as refused:
                fs.output(name)
            self.assertEqual(errno.EROFS, refused.exception.errno)
        fd = fs.open("/src/file", os.O_RDONLY)
        try:
            self.assertEqual(b"original", fs.read("/src/file", 8, 0, fd))
            with self.assertRaises(OSError):
                fs.write("/src/file", b"x", 0, fd)
            with self.assertRaises(OSError):
                fs.truncate("/src/file", 0, fd)
        finally:
            fs.release("/src/file", fd)
        self.assertEqual("absent", fs.readlink("/dangling"))
        for path in ("../other", "/src/../other", "/alias/dir"):
            with self.subTest(path=path), self.assertRaises(OSError):
                fs.destination(path)
        self.assertEqual(b"original", (self.inputs / "src/file").read_bytes())

    def test_new_outputs_support_normal_file_operations_beside_sources(self):
        fs = self.view
        path = "/src/new"
        fd = fs.create(path, 0o640)
        try:
            self.assertEqual(5, fs.write(path, b"built", 0, fd))
            self.assertEqual(5, fs.getattr(path, fd)["st_size"])
            fs.flush(path, fd)
            fs.fsync(path, False, fd)
            fs.truncate(path, 4, fd)
            self.assertEqual(b"uil", fs.read(path, 9, 1, fd))
        finally:
            fs.release(path, fd)
        fs.truncate(path, 2)
        fs.chmod(path, 0o600)
        self.assertEqual(0o600, stat.S_IMODE(fs.getattr(path)["st_mode"]))
        fs.utimens(path, None)
        fs.utimens(path, (1_000_000_000, 2_000_000_000))
        self.assertEqual(2_000_000_000, fs.getattr(path)["st_mtime"])
        fs.link("/src/link", path)
        fs.mkdir("/src/build", 0o700)
        fs.rename("/src/link", "/src/build/result")
        fs.symlink("/src/latest", "build/result")
        self.assertEqual("build/result", fs.readlink("/src/latest"))
        self.assertEqual({".", "..", "file", "new", "build", "latest"}, set(fs.readdir("/src", 0)))
        self.assertEqual(b"bu", (self.outputs / "src/latest").read_bytes())
        fs.unlink("/src/build/result")
        fs.rmdir("/src/build")
        self.assertEqual(["file"], os.listdir(self.inputs / "src"))

    @unittest.skipUnless(sys.platform == "linux", "Linux renameat2")
    def test_no_replace_preserves_an_existing_output(self):
        fs = self.view
        for name in ("one", "two"):
            (self.outputs / name).write_text(name)
        with self.assertRaises(FileExistsError):
            fs.rename("/one", "/two", flags=1)
        self.assertEqual("two", (self.outputs / "two").read_text())
        fs.rename("/one", "/three", flags=1)
        self.assertEqual("one", (self.outputs / "three").read_text())
