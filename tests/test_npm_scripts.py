"""The package ``test`` script an ``npm test`` command runs, read without running npm."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_npm_scripts as npm_scripts


class TestScriptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "project"
        (self.root / "web").mkdir(parents=True)
        (self.root / "package.json").write_text(json.dumps({"scripts": {"test": "node --test", "pretest": "tsc"}}))
        (self.root / "web" / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
        (self.root.parent / "package.json").write_text(json.dumps({"scripts": {"test": "outside"}}))

    def test_every_spelling_of_the_test_script_reads_the_package_scripts(self):
        scripts = {"test": "node --test", "pretest": "tsc"}
        for command in ("npm test", "npm t", "npm run test", "npm run-script test", "npm test --silent", "npm -s t"):
            with self.subTest(command=command):
                self.assertEqual((scripts, [], "."), npm_scripts.test_script(command, self.root))
        self.assertEqual(
            (scripts, ["-t", "name"], "."), npm_scripts.test_script("npm test --silent -- -t name", self.root)
        )

    def test_a_prefix_names_a_package_inside_the_tree_only(self):
        for command in ("npm --prefix web test", "npm --prefix=web run test"):
            with self.subTest(command=command):
                self.assertEqual(({"test": "vitest run"}, [], "web"), npm_scripts.test_script(command, self.root))
        for command in ("npm --prefix .. test", "npm --prefix missing test", "npm test --prefix"):
            with self.subTest(command=command):
                self.assertIsNone(npm_scripts.test_script(command, self.root))

    def test_any_other_command_is_not_the_test_script(self):
        for command in (
            None,
            "npm install",
            "npm run build",
            "npm test && true",
            "npm test | cat",
            "npm --script-shell bash test",
            "yarn test",
            'npm test "unterminated',
        ):
            with self.subTest(command=command):
                self.assertIsNone(npm_scripts.test_script(command, self.root))
        self.assertIsNone(npm_scripts.test_script("npm test", None), "no tree, no package to read")

    def test_an_unreadable_package_has_no_script(self):
        for text in ("{not json", "[]", json.dumps({"scripts": ["test"]})):
            with self.subTest(text=text):
                (self.root / "package.json").write_text(text)
                self.assertIsNone(npm_scripts.test_script("npm test", self.root))
        (self.root / "package.json").write_text("{}")
        self.assertEqual(({}, [], "."), npm_scripts.test_script("npm test", self.root))


if __name__ == "__main__":
    unittest.main()
