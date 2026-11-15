"""The person's decisions and the repository's conventions for `envfile merge`, judged from outside.

Forks the brief leaves open, settled by the person's answers (scenario.toml [person]):
  * a key set in several files: the LAST file's value wins, at the place the key first appeared;
  * inputs with no entries: empty output, exit 0 (unlike `show`, which refuses an empty file).
Conventions the repository already settles, never a question: the `--sorted` spelling `show`
uses, and errors through `envfile: error: MESSAGE` on stderr with exit 2 for bad input.
"""

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
        timeout=60,
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text)
        return path


class PersonDecisionTests(Base):
    def test_last_file_wins_for_a_repeated_key(self):
        a = self.write("a.env", "A=1\nB=from-a\n")
        b = self.write("b.env", "B=from-b\nC=3\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "A=1\nB=from-b\nC=3\n")

    def test_last_of_three_files_wins(self):
        paths = [self.write(f"{n}.env", f"K={n}\n") for n in ("one", "two", "three")]
        result = envfile("merge", *paths, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "K=three\n"))

    def test_a_repeated_key_keeps_its_first_place(self):
        a = self.write("a.env", "X=1\nY=2\n")
        b = self.write("b.env", "Z=3\nX=9\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "X=9\nY=2\nZ=3\n"))

    def test_inputs_without_entries_merge_to_nothing(self):
        a = self.write("a.env", "# nothing yet\n")
        b = self.write("b.env", "\n\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_one_empty_input_is_fine(self):
        a = self.write("a.env", "# nothing\n")
        b = self.write("b.env", "A=1\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "A=1\n"))


class ConventionTests(Base):
    def test_sorted_flag_spelled_like_show(self):
        a = self.write("a.env", "C=3\nA=1\n")
        b = self.write("b.env", "B=2\n")
        result = envfile("merge", a, b, "--sorted", cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "A=1\nB=2\nC=3\n"))

    def test_first_appearance_order_by_default(self):
        a = self.write("a.env", "B=2\nA=1\n")
        b = self.write("b.env", "D=4\nC=3\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual((result.returncode, result.stdout), (0, "B=2\nA=1\nD=4\nC=3\n"))

    def test_missing_file_reported_like_other_commands(self):
        a = self.write("a.env", "A=1\n")
        result = envfile("merge", a, self.dir / "missing.env", cwd=self.dir)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("envfile: error: "), result.stderr)
        self.assertIn("no such file", result.stderr)

    def test_malformed_file_reported_like_other_commands(self):
        a = self.write("a.env", "A=1\n")
        b = self.write("b.env", "B=2\nbroken line\n")
        result = envfile("merge", a, b, cwd=self.dir)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("envfile: error: "), result.stderr)
        self.assertIn(":2: expected KEY=VALUE", result.stderr)

    def test_existing_commands_unchanged(self):
        a = self.write("a.env", "# nothing\n")
        result = envfile("show", a, cwd=self.dir)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, f"envfile: error: {a}: no entries\n")


if __name__ == "__main__":
    unittest.main()
