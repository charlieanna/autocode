import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def envfile(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "envfile", *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(ROOT), "PATH": ""},
    )


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path

    def test_merge_keeps_first_appearance_order(self):
        a = self.write("a.env", "B=2\nA=1\n")
        b = self.write("b.env", "C=3\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "B=2\nA=1\nC=3\n"))

    def test_merge_sorted(self):
        a = self.write("a.env", "B=2\n")
        b = self.write("b.env", "C=3\nA=1\n")
        result = envfile("merge", a, b, "--sorted", cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "A=1\nB=2\nC=3\n"))

    def test_merge_first_file_wins(self):
        a = self.write("a.env", "A=1\nB=2\n")
        b = self.write("b.env", "B=3\n")
        c = self.write("c.env", "B=4\nC=5\n")
        result = envfile("merge", a, b, c, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "A=1\nB=2\nC=5\n"))

    def test_merge_of_empty_files_prints_nothing(self):
        a = self.write("a.env", "# nothing\n")
        b = self.write("b.env", "\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_merge_missing_file(self):
        a = self.write("a.env", "A=1\n")
        result = envfile("merge", a, self.dir / "missing.env", cwd=self.dir)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("envfile: error: "), result.stderr)


if __name__ == "__main__":
    unittest.main()
