"""Which folders outside Git a task works on where they are (autocode_folder_adoption)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_folder_adoption as adoption


class FolderAdoptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def folder(self, name, files=()):
        folder = self.root / name
        for path in files:
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_text("x = 1\n")
        folder.mkdir(exist_ok=True)
        return folder

    def test_a_folder_of_code_is_a_project_and_a_folder_without_code_is_not(self):
        self.assertEqual(adoption.PROJECT, adoption.reason(self.folder("app", ["pager.py", "tests/test_pager.py"])))
        self.assertEqual(adoption.PROJECT, adoption.reason(self.folder("deep", ["README.md", "src/lib/main.rs"])))
        self.assertTrue(adoption.in_place(self.root / "app"))
        self.assertEqual(adoption.NO_CODE, adoption.reason(self.folder("papers", ["notes.txt", "2026/tax.pdf"])))
        self.assertFalse(adoption.in_place(self.root / "papers"))
        self.assertEqual(adoption.EMPTY, adoption.reason(self.folder("empty")))
        self.assertEqual(adoption.EMPTY, adoption.reason(self.folder("finder", [".DS_Store"])))
        self.assertFalse(adoption.in_place(self.root / "empty"))

    def test_a_folder_that_holds_git_checkouts_is_not_one_project(self):
        for name, marker in (
            ("child", "api/.git/HEAD"),
            ("deep", "work/client/api/.git/HEAD"),
            ("linked", "tree/.git"),
        ):
            with self.subTest(name=name):
                self.assertEqual(adoption.CHECKOUTS, adoption.reason(self.folder(name, ["tool.py", marker])))
        # AutoCode's own private projects and a dependency's checkout are not the person's checkouts.
        own = self.folder("own", ["tool.py", ".autocode/projects/abc/.git/HEAD", "node_modules/left/.git/HEAD"])
        self.assertEqual(adoption.PROJECT, adoption.reason(own))

    def test_the_home_folder_and_the_folders_above_it_are_never_a_project(self):
        home = self.folder("users/dana", ["script.py"])
        project = self.folder("users/dana/code/app", ["main.go"])
        with mock.patch.object(Path, "home", return_value=home):
            self.assertEqual(adoption.HOME, adoption.reason(home))
            self.assertEqual(adoption.HOME, adoption.reason(home.parent))
            self.assertEqual(adoption.HOME, adoption.reason(Path(home.anchor)))
            self.assertEqual(adoption.PROJECT, adoption.reason(project))
        with mock.patch.object(Path, "home", side_effect=RuntimeError("no home")):
            self.assertEqual(adoption.PROJECT, adoption.reason(project))

    def test_a_folder_too_large_to_record_as_a_runs_start_is_not_a_project(self):
        many = self.folder("many", ["a.py", "b.py", "c.py", "d.py"])
        with mock.patch.object(adoption, "FILES", 3):
            self.assertEqual(adoption.LARGE, adoption.reason(many))
        with mock.patch.object(adoption, "FILES", 4):
            self.assertEqual(adoption.PROJECT, adoption.reason(many))
        with mock.patch.object(adoption, "BYTES", 23):  # four files of six bytes
            self.assertEqual(adoption.LARGE, adoption.reason(many))
        with mock.patch.object(adoption, "BYTES", 24):
            self.assertEqual(adoption.PROJECT, adoption.reason(many))

    def test_installed_dependencies_caches_and_links_are_not_counted(self):
        folder = self.folder(
            "web",
            [
                "index.js",
                "node_modules/a/index.js",
                "node_modules/b/index.js",
                ".venv/lib/site.py",
                "__pycache__/x.pyc",
            ],
        )
        outside = self.folder("outside", ["one.py", "two.py", "three.py"])
        os.symlink(outside, folder / "linked")
        with mock.patch.object(adoption, "FILES", 1):
            self.assertEqual(adoption.PROJECT, adoption.reason(folder))
        # Code only among installed dependencies is not the person's code.
        self.assertEqual(adoption.NO_CODE, adoption.reason(self.folder("installed", ["node_modules/a/index.js"])))

    def test_every_reason_to_keep_a_private_project_has_its_words(self):
        self.assertEqual({adoption.HOME, adoption.CHECKOUTS, adoption.NO_CODE, adoption.LARGE}, set(adoption.BECAUSE))
        self.assertEqual("holds more than 2,000 files or 100 MB", adoption.BECAUSE[adoption.LARGE])
        self.assertEqual(("node_modules/", ".venv/", "venv/"), adoption.EXCLUDED)


if __name__ == "__main__":
    unittest.main()
