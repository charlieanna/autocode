import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from safezip import extract


class ArchiveContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve(); self.archive=self.root/'input.zip'; self.target=self.root/'output'
        (self.root/'sentinel').write_text('keep')
    def make(self,entries):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            with zipfile.ZipFile(self.archive,'w') as z:
                for name,data in entries: z.writestr(name,data)
    def reject(self,entries,limit=100):
        self.make(entries)
        with self.assertRaises(ValueError): extract(self.archive,self.target,limit)
        self.assertFalse(self.target.exists()); self.assertEqual((self.root/'sentinel').read_text(),'keep')
        self.assertEqual({p.name for p in self.root.iterdir()},{'sentinel','input.zip'})
    def test_all_entries_validated_without_partial_output(self):
        for path in ('../sentinel','/absolute','C:/drive','a/../../sentinel','a\\b','./a','a//b'):
            self.reject([('valid.txt','ok'),(path,'bad')])
        for entries in ([('a','x'),('a/b','y')],[('a/b','y'),('a','x')],[('a','x'),('a','y')],[('a/',''),('a','x')]):
            self.reject(entries)
    def test_symlink_and_total_limit(self):
        link=zipfile.ZipInfo('link'); link.create_system=3; link.external_attr=(stat.S_IFLNK|0o777)<<16
        self.reject([('good','x'),(link,'../sentinel')])
        self.reject([('a','123'),('b','456')],5)
        self.make([('café/',''),('café/data','123456')])
        self.assertEqual(extract(self.archive,self.target,6),['café/data'])
    def test_existing_destinations_and_corrupt_archive(self):
        self.make([('a','x')]); self.target.mkdir(); (self.target/'keep').write_text('old')
        with self.assertRaises(ValueError): extract(self.archive,self.target)
        self.assertEqual((self.target/'keep').read_text(),'old')
        (self.target/'keep').unlink(); self.archive.write_bytes(b'not zip')
        with self.assertRaises(ValueError): extract(self.archive,self.target)
        self.assertEqual(list(self.target.iterdir()),[])
        self.target.rmdir(); self.target.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError): extract(self.archive,self.target)

    def test_corrupt_member_payload_raises_value_error_and_cleans_stage(self):
        import struct
        for compression in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression):
                with zipfile.ZipFile(self.archive,'w',compression=compression) as archive:
                    archive.writestr('payload',b'x'*4096)
                raw=bytearray(self.archive.read_bytes())
                name_length,extra_length=struct.unpack_from('<HH',raw,26)
                payload_offset=30+name_length+extra_length
                # Deflate block type 3 is reserved; stored corruption fails CRC.
                raw[payload_offset]=7
                self.archive.write_bytes(raw)
                with self.assertRaises(ValueError): extract(self.archive,self.target)
                self.assertFalse(self.target.exists())
                self.assertEqual((self.root/'sentinel').read_text(),'keep')
                self.assertEqual({p.name for p in self.root.iterdir()},{'sentinel','input.zip'})

    def test_corrupt_directory_payload_is_rejected_before_publication(self):
        import struct
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for existing_destination in (False, True):
                with self.subTest(compression=compression, existing_destination=existing_destination):
                    if self.target.exists():
                        self.target.rmdir()
                    if existing_destination:
                        self.target.mkdir()
                    with zipfile.ZipFile(self.archive, "w", compression=compression) as archive:
                        archive.writestr("folder/", b"x" * 4096)
                    raw = bytearray(self.archive.read_bytes())
                    name_length, extra_length = struct.unpack_from("<HH", raw, 26)
                    raw[30 + name_length + extra_length] = 7
                    self.archive.write_bytes(raw)
                    with self.assertRaises(ValueError):
                        extract(self.archive, self.target)
                    self.assertEqual(existing_destination, self.target.exists())
                    if existing_destination:
                        self.assertEqual([], list(self.target.iterdir()))
                    self.assertEqual("keep", (self.root / "sentinel").read_text())
                    expected = {"sentinel", "input.zip"} | ({"output"} if existing_destination else set())
                    self.assertEqual(expected, {p.name for p in self.root.iterdir()})
