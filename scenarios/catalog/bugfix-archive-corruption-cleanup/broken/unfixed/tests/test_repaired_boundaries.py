import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from safezip import extract


class CorruptPayloads(unittest.TestCase):
    def check_corruption(self, name, compression):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as root:
                root = Path(root).resolve()
                archive, destination = root / "input.zip", root / "output"
                (root / "sentinel").write_text("keep")
                if existing:
                    destination.mkdir()
                with zipfile.ZipFile(archive, "w", compression=compression) as handle:
                    handle.writestr(name, b"x" * 4096)
                raw = bytearray(archive.read_bytes())
                name_length, extra_length = struct.unpack_from("<HH", raw, 26)
                raw[30 + name_length + extra_length] = 7
                archive.write_bytes(raw)
                with self.assertRaises(ValueError):
                    extract(archive, destination)
                self.assertEqual(existing, destination.exists())
                if existing:
                    self.assertEqual([], list(destination.iterdir()))
                expected = {"input.zip", "sentinel"} | ({"output"} if existing else set())
                self.assertEqual(expected, {item.name for item in root.iterdir()})
                self.assertEqual("keep", (root / "sentinel").read_text())

    def test_t1_corrupt_deflated_file_uses_public_error_and_cleans_stage(self):
        self.check_corruption("payload", zipfile.ZIP_DEFLATED)

    def test_t2_corrupt_directory_payload_is_not_published(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            self.check_corruption("folder/", compression)
