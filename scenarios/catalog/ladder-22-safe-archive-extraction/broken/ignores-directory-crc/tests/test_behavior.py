import tempfile
import unittest
import zipfile
from pathlib import Path

from safezip import extract


class ArchiveTests(unittest.TestCase):
    def test_nested_files_extract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            archive = root / "input.zip"
            target = root / "output"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("src/main.txt", "hello")
                z.writestr("empty.txt", "")
            self.assertEqual(extract(archive, target), ["empty.txt", "src/main.txt"])
            self.assertEqual((target / "src/main.txt").read_text(), "hello")
