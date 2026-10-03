from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

import safezip


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for entry in entries:
            if len(entry) == 2:
                archive.writestr(*entry)
            else:
                info, data = entry
                archive.writestr(info, data)


def corrupt_central_directory_crc(path):
    data = bytearray(path.read_bytes())
    offset = data.index(b"PK\x01\x02")
    data[offset + 16] ^= 1
    path.write_bytes(data)


def unix_entry(name, mode):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    return info


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_ac1_extracts_nested_unicode_and_empty_files(self):
        archive = self.root / "source.zip"
        destination = self.root / "output"
        write_zip(archive, [("nested/", b""), ("nested/a.txt", b"A"),
                            ("unicod\u00e9/\u7a7a.txt", b"B"), ("empty.txt", b"")])

        result = safezip.extract(archive, destination)

        self.assertEqual(result, ["empty.txt", "nested/a.txt", "unicod\u00e9/\u7a7a.txt"])
        self.assertEqual((destination / "empty.txt").read_bytes(), b"")
        self.assertEqual((destination / "nested" / "a.txt").read_bytes(), b"A")
        self.assertEqual((destination / "unicod\u00e9" / "\u7a7a.txt").read_bytes(), b"B")

    def test_ac2_extracts_into_existing_empty_destination(self):
        archive = self.root / "source.zip"
        destination = self.root / "output"
        destination.mkdir()
        write_zip(archive, [("file.txt", b"x")])

        self.assertEqual(safezip.extract(archive, destination), ["file.txt"])
        self.assertEqual((destination / "file.txt").read_bytes(), b"x")

    def test_ac3_rejects_missing_destination_parent(self):
        archive = self.root / "source.zip"
        destination = self.root / "missing" / "output"
        write_zip(archive, [("file.txt", b"x")])

        with self.assertRaises(ValueError):
            safezip.extract(archive, destination)
        self.assertFalse(destination.exists())

    def test_ac4_rejects_symlink_in_destination_path(self):
        archive = self.root / "source.zip"
        target = self.root / "target"
        link = self.root / "link"
        target.mkdir()
        link.symlink_to(target, target_is_directory=True)
        write_zip(archive, [("file.txt", b"x")])

        with self.assertRaises(ValueError):
            safezip.extract(archive, link / "output")
        self.assertFalse((target / "output").exists())

    def test_ac5_rejects_nonempty_destination_without_overwrite(self):
        archive = self.root / "source.zip"
        destination = self.root / "output"
        destination.mkdir()
        (destination / "keep.txt").write_bytes(b"keep")
        write_zip(archive, [("new.txt", b"x")])

        with self.assertRaises(ValueError):
            safezip.extract(archive, destination)
        self.assertEqual((destination / "keep.txt").read_bytes(), b"keep")
        self.assertFalse((destination / "new.txt").exists())

    def test_ac6_rejects_unsafe_names_types_duplicates_and_collisions(self):
        cases = [
            [("/absolute", b"")], [("C:drive", b"")], [("dir\\file", b"")],
            [(".", b"")], [("..", b"")], [("dir//file", b"")],
            [("same", b""), ("same/", b"")],
            [(unix_entry("link", stat.S_IFLNK | 0o777), b"target")],
            [(unix_entry("socket", stat.S_IFIFO | 0o600), b"")],
            [("file", b""), ("file/child", b"")],
            [("dir/child", b""), ("dir", b"")],
        ]
        for number, entries in enumerate(cases):
            with self.subTest(number=number):
                archive = self.root / ("unsafe-%d.zip" % number)
                destination = self.root / ("output-%d" % number)
                write_zip(archive, entries)
                with self.assertRaises(ValueError):
                    safezip.extract(archive, destination)
                self.assertFalse(destination.exists())
        archive = self.root / "directory.zip"
        write_zip(archive, [("dir/", b"")])
        self.assertEqual(safezip.extract(archive, self.root / "directory-output"), [])

    def test_ac7_validates_all_entries_before_publication(self):
        archive = self.root / "source.zip"
        write_zip(archive, [("good.txt", b"good"), ("../bad.txt", b"bad")])
        existing = self.root / "existing"
        existing.mkdir()
        absent = self.root / "absent"

        with self.assertRaises(ValueError):
            safezip.extract(archive, existing)
        with self.assertRaises(ValueError):
            safezip.extract(archive, absent)
        self.assertEqual(list(existing.iterdir()), [])
        self.assertFalse(absent.exists())

    def test_ac8_enforces_limits_and_accepts_unbounded_python_integers(self):
        archive = self.root / "source.zip"
        write_zip(archive, [("two.bin", b"ab")])
        self.assertEqual(safezip.extract(archive, self.root / "exact", 2), ["two.bin"])
        for number, limit in enumerate((1, -1, True, 1.0)):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    safezip.extract(archive, self.root / ("bad-%d" % number), limit)
        for number, limit in enumerate((2**63 - 1, 2**63, 10**5000)):
            with self.subTest(limit=limit):
                self.assertEqual(
                    safezip.extract(archive, self.root / ("large-%d" % number), limit),
                    ["two.bin"],
                )

    def test_ac9_rejects_crc_failure_without_publication(self):
        archive = self.root / "source.zip"
        destination = self.root / "output"
        write_zip(archive, [("file.txt", b"payload")])
        corrupt_central_directory_crc(archive)

        with self.assertRaises(ValueError):
            safezip.extract(archive, destination)
        self.assertFalse(destination.exists())

    def test_ac11_rejects_corrupt_zip_and_preserves_empty_destination_on_crc_failure(self):
        corrupt = self.root / "corrupt.zip"
        corrupt.write_bytes(b"not a zip")
        destination = self.root / "absent"
        with self.assertRaises(ValueError):
            safezip.extract(corrupt, destination)
        self.assertFalse(destination.exists())

        archive = self.root / "source.zip"
        existing = self.root / "existing"
        existing.mkdir()
        write_zip(archive, [("file.txt", b"payload")])
        corrupt_central_directory_crc(archive)
        with self.assertRaises(ValueError):
            safezip.extract(archive, existing)
        self.assertEqual(list(existing.iterdir()), [])

    def test_ac12_rejects_nested_dot_components_exact_duplicates_and_preserves_outside_file(self):
        cases = [
            [("dir/./file", b"")], [("dir/../file", b"")],
            [("same.txt", b""), ("same.txt", b"")],
            [("same/", b""), ("same/", b"")],
        ]
        for number, entries in enumerate(cases):
            with self.subTest(number=number):
                archive = self.root / ("bad-%d.zip" % number)
                destination = self.root / ("bad-output-%d" % number)
                write_zip(archive, entries)
                with self.assertRaises(ValueError):
                    safezip.extract(archive, destination)
                self.assertFalse(destination.exists())
        outside = self.root / "bad.txt"
        outside.write_bytes(b"keep")
        archive = self.root / "traversal.zip"
        destination = self.root / "traversal-output"
        write_zip(archive, [("../bad.txt", b"overwrite")])
        with self.assertRaises(ValueError):
            safezip.extract(archive, destination)
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertFalse(destination.exists())

    def test_ac13_rejects_regular_file_and_symlink_destinations_without_changes(self):
        archive = self.root / "source.zip"
        write_zip(archive, [("file.txt", b"x")])
        regular = self.root / "regular"
        regular.write_bytes(b"keep")
        with self.assertRaises(ValueError):
            safezip.extract(archive, regular)
        self.assertEqual(regular.read_bytes(), b"keep")

        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            safezip.extract(archive, link)
        self.assertTrue(link.is_symlink())
        self.assertEqual(list(target.iterdir()), [])
