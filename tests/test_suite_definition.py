"""What the base suite definition's extra run receives (autocode_suite_definition, #587, #652)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_suite_definition as suite_definition
import autocode_verify as verify


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Repository:
    """A committed tree; the working tree is the candidate."""

    def __init__(self, test_case, files):
        temporary = tempfile.TemporaryDirectory(prefix="suite-definition-")
        test_case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "project"
        self.write(files)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")

    def write(self, files):
        for path, text in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

    def definition(self, suite="npm test", base_patch=None):
        return suite_definition.definition(self.root, self.base, suite, is_test=verify.is_test_path,
                                           base_patch=base_patch)


class DefinitionFileTests(unittest.TestCase):
    def test_package_manager_task_runner_and_test_runner_configuration_count(self):
        for path in ("package.json", "web/package-lock.json", ".npmrc", ".yarnrc", "web/.yarnrc.yml", ".pnpmfile.cjs",
                     ".pnpmfile.mjs", "bunfig.toml", "turbo.json", "nx.json", "lerna.json", "pnpm-workspace.yaml",
                     ".yarn/releases/yarn-4.1.0.cjs", "web/.yarn/plugins/@yarnpkg/plugin-x.cjs", "jest.config.ts",
                     "web/vitest.config.mjs", ".mocharc.json", ".mocharc.yml", "ava.config.js", ".c8rc.json", ".nycrc",
                     "karma.conf.js"):
            with self.subTest(path):
                self.assertTrue(suite_definition.is_definition_file(path))
        for path in ("calc.js", "jest.setup.js", ".yarn/cache/x.zip", "releases/notes.md", "config/npmrc", "README.md"):
            with self.subTest(path):
                self.assertFalse(suite_definition.is_definition_file(path))


class ScannerTests(unittest.TestCase):
    def test_comments_are_masked(self):
        # #652 G6: an apostrophe in a comment opened a phantom string that swallowed the require.
        text = "// Don't add tests here\nconst list = require('./select.js');\n/* require('./old.js') */\n"
        self.assertEqual([("./select.js", False)], suite_definition.import_sites(text))

    def test_template_literals(self):
        sites = suite_definition.import_sites
        self.assertEqual([("./plain.js", False)], sites("require(`./plain.js`);\n"))
        self.assertEqual([(None, False)], sites("import(`./routes/${name}.js`);\n"))
        # Code inside a substitution is still read, and `//` inside template text is not a comment.
        self.assertEqual([(None, False), ("./inner.js", False)], sites("require(`${require('./inner.js')}`);\n"))
        self.assertEqual([("./select.js", False)],
                         sites("const url = `http://example.test/a'b`; require('./select.js');\n"))

    def test_a_literal_inside_an_exec_argument(self):
        text = "require('child_process').execSync('node --test ' + require('./list.js').join(' '));\n"
        self.assertIn(("./list.js", True), suite_definition.import_sites(text))


class ManifestMergeTests(unittest.TestCase):
    def merged(self, original, candidate):
        encode = lambda value: None if value is None else json.dumps(value).encode()  # noqa: E731
        merged = suite_definition.merged_manifest(encode(original), encode(candidate))
        return None if merged is None else json.loads(merged)

    def test_loading_fields_are_the_candidates_and_everything_else_the_bases(self):
        base = {"name": "calc", "scripts": {"test": "node --test"}, "files": ["a"], "main": "a.js"}
        candidate = {"name": "renamed", "scripts": {"test": "node --test x"}, "files": ["b"], "main": "b.js",
                     "imports": {"#impl": "./impl.js"}, "custom": 1}
        self.assertEqual({"name": "calc", "scripts": {"test": "node --test"}, "files": ["a"], "main": "b.js",
                          "imports": {"#impl": "./impl.js"}}, self.merged(base, candidate))

    def test_unchanged_or_unreadable_manifests_stay_the_bases(self):
        base = json.dumps({"scripts": {"test": "node --test"}}).encode()
        self.assertIs(base, suite_definition.merged_manifest(base, b'{"scripts": {"test": "x"}}'))
        self.assertIs(base, suite_definition.merged_manifest(base, b"{not json"))
        self.assertIs(base, suite_definition.merged_manifest(base, None))

    def test_an_added_manifest_enters_only_with_loading_fields(self):
        self.assertEqual({"type": "module"}, self.merged(None, {"type": "module"}))
        self.assertIsNone(self.merged(None, {"type": "module", "scripts": {"test": "node --test"}}))


class DefinitionTests(unittest.TestCase):
    RUNNER = "require('child_process').execSync('node --test ' + require('./scripts/select').join(' '));\n"

    def test_runner_literals_resolve_as_node_does_and_missing_ones_stay_absent(self):
        repository = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "run-tests.js": self.RUNNER + "try { require('./local.js'); } catch (error) {}\n",
            "scripts/select.js": "module.exports = ['test/a.test.js'];\n",
            "calc.js": "exports.add = (a, b) => a + b;\n",
            "test/a.test.js": "require('../calc');\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)
        self.assertIn("scripts/select.js", found.pinned)  # #652 G12: no extension in the literal
        self.assertIn("local.js", found.pinned)  # absent on base, so absent in the extra run
        self.assertIn("calc.js", found.product)  # a test's require without the extension

    def test_a_definition_named_link_pins_its_target(self):
        repository = Repository(self, {"package.json": "{}", "config/npmrc": "fund=false\n"})
        os.symlink("config/npmrc", repository.root / ".npmrc")
        git(repository.root, "add", "-A")
        git(repository.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "link")
        repository.base = git(repository.root, "rev-parse", "HEAD")
        self.assertIn("config/npmrc", repository.definition().pinned)

    def test_an_unsplittable_script_names_itself(self):
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node --test",
                                                                               "hello": "echo don't"}})})
        found = repository.definition()
        self.assertIn('the "hello" script in package.json cannot be split into shell words', found.boundary)

    def test_the_plan_measures_against_the_reviewed_patch(self):
        # #652 G7: a file the follow-up put back as base had it replaces the patch's version.
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node --test"}}),
                                       "calc.js": "exports.add = (a, b) => a + b;\n",
                                       "test/calc.test.js": "require('../calc.js');\n"})
        repository.write({"calc.js": "exports.add = (a, b) => a + b;\nexports.mul = (a, b) => a * b;\n",
                          "added.js": "module.exports = 1;\n"})
        patch = repository.root.parent / "reviewed.patch"
        git(repository.root, "add", "-N", "added.js")
        patch.write_text(git(repository.root, "diff") + "\n")
        git(repository.root, "checkout", "--", "calc.js")
        git(repository.root, "rm", "-q", "--cached", "added.js")
        (repository.root / "added.js").unlink()
        found = repository.definition(base_patch=patch)
        self.assertEqual({"calc.js", "added.js"}, set(found.patched))
        plan = suite_definition.plan(repository.root, {}, found, is_test=verify.is_test_path)
        self.assertEqual({"calc.js": "modified", "added.js": "deleted"}, plan["overlay"])
        self.assertEqual(["added.js"], plan["unclassified"])

    def test_many_test_files_are_read_through_one_git_process(self):
        # #652 G11: reading each test file spawned one git process.
        tests = {f"test/t{index}.test.js": f"require('../calc.js'); // {index}\n" for index in range(60)}
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node --test"}}),
                                       "calc.js": "exports.add = (a, b) => a + b;\n", **tests})
        real_popen, started = subprocess.Popen, []

        def popen(*args, **kwargs):  # subprocess.run starts its process through Popen too
            started.append(args[0])
            return real_popen(*args, **kwargs)

        with mock.patch.object(suite_definition.subprocess, "Popen", popen):
            found = repository.definition()
        self.assertEqual({"calc.js"}, set(found.product))
        self.assertLessEqual(len(started), 6, started)


if __name__ == "__main__":
    unittest.main()
