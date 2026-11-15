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


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path

    def test_show_prints_entries(self):
        path = self.write("a.env", "B=2\nA=1\n")
        result = envfile("show", path, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "B=2\nA=1\n"))

    def test_show_sorted(self):
        path = self.write("a.env", "B=2\nA=1\n")
        result = envfile("show", path, "--sorted", cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "A=1\nB=2\n"))

    def test_show_empty_file_is_an_error(self):
        path = self.write("a.env", "# nothing yet\n")
        result = envfile("show", path, cwd=self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, f"envfile: error: {path}: no entries\n")

    def test_show_missing_file(self):
        result = envfile("show", self.dir / "missing.env", cwd=self.dir)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("envfile: error: "), result.stderr)
        self.assertIn("no such file", result.stderr)

    def test_get_value_and_default(self):
        path = self.write("a.env", "A=1\n")
        self.assertEqual(envfile("get", path, "A", cwd=self.dir).stdout, "1\n")
        self.assertEqual(envfile("get", path, "B", "--default", "x", cwd=self.dir).stdout, "x\n")

    def test_get_unset_key(self):
        path = self.write("a.env", "A=1\n")
        result = envfile("get", path, "B", cwd=self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, f"envfile: error: B: not set in {path}\n")


if __name__ == "__main__":
    unittest.main()
