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
                     "karma.conf.js",
                     # #652 review: runners whose configuration selects tests, and transpilers that rewrite them
                     "playwright.config.js", "e2e/cypress.config.ts", "wdio.conf.js", "web-test-runner.config.mjs",
                     "spec/support/jasmine.json", "babel.config.js", ".babelrc", ".babelrc.json", "tsconfig.json",
                     "tsconfig.test.json", "jsconfig.json", ".swcrc", ".yarnrc.yaml", "pnpmfile.js"):
            with self.subTest(path):
                self.assertTrue(suite_definition.is_definition_file(path))
        for path in ("calc.js", "jest.setup.js", ".yarn/cache/x.zip", "releases/notes.md", "config/npmrc", "README.md"):
            with self.subTest(path):
                self.assertFalse(suite_definition.is_definition_file(path))

    def test_babel_is_not_a_field_the_candidate_keeps(self):
        # #652 review: a Babel plugin configured through package.json can turn a base test into a skipped one.
        self.assertNotIn("babel", suite_definition.KEPT_FIELDS)

    def test_inert_files_need_no_closure(self):
        for path in ("README.md", "docs/guide.rst", "LICENSE", "LICENSE.txt", "CHANGELOG.md", "logo.png", ".gitignore"):
            with self.subTest(path):
                self.assertTrue(suite_definition.is_inert(path))
        for path in ("run.sh", "bin/run", "data.json", "select.js", "tests.yml", ".eslintrc.json"):
            with self.subTest(path):
                self.assertFalse(suite_definition.is_inert(path))


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

    def test_regex_literals_are_masked(self):
        # #652 review: a quote, // or /* inside a regex opened a phantom string or comment that hid the require.
        sites = suite_definition.import_sites
        for prefix in ("const cwd = process.cwd().replace(/\\/*$/, '');\n",
                       "if (/don't/.test(process.env.X)) {}\n",
                       "const q = s.replace(/'/g, \"'\\\\''\");\n",
                       "const m = 'x'.match(/https?:\\/\\//); ",
                       "function f(v) { return /x'y/.test(v); }\n",
                       "const r = [/a\\]'b/, /c/g];\n"):
            with self.subTest(prefix=prefix):
                self.assertEqual([("./x.js", False)], sites(prefix + "require('./x.js');\n"))
        # A division is not a regex: the quote after it is a real string.
        self.assertEqual([("./x.js", False)], sites("const r = a / b / c;\nrequire('./x.js');\n"))
        self.assertEqual([("./x.js", False)], sites("const r = f(1) / 2; const s = 'q';\nrequire('./x.js');\n"))

    def test_text_the_runtime_would_refuse_is_unreadable(self):
        for text in ("const s = 'abc\nrequire('./x.js');\n", "/* open\nrequire('./x.js');\n",
                     "const r = /abc\nrequire('./x.js');\n", "const t = `open ${1}\n"):
            with self.subTest(text=text):
                with self.assertRaises(suite_definition.Unreadable):
                    suite_definition.import_sites(text)

    def test_two_static_imports_on_one_line(self):
        self.assertEqual([("./a.js", False), ("./b.js", False)],
                         suite_definition.import_sites("import './a.js'; import './b.js';\n"))


class ShellTests(unittest.TestCase):
    """Shell words of a script, as the base runner closure reads them (#652 review)."""

    TRACKED = {"package.json", "lib/run.js", "root.js", "setup.js", "scripts/run-tests.js", "test/calc.test.js"}

    def words(self, command, directory="."):
        bindings, reason = suite_definition._shell_bindings(command, directory, self.TRACKED)
        return bindings, reason

    def test_operators_and_parentheses_split_whatever_the_spacing(self):
        bindings, reason = self.words("(cd lib && node run.js) && node root.js")
        self.assertEqual("", reason)
        self.assertIn(("run.js", "lib"), bindings)
        self.assertIn(("root.js", "."), bindings)  # the subshell's cd ended with it
        for command in ("node a.js;node b.js", "node a.js&&node b.js", "cd lib; node run.js"):
            with self.subTest(command):
                bindings, reason = self.words(command)
                self.assertEqual("", reason)
                self.assertTrue(any(word in ("a.js", "run.js") for word, _ in bindings), bindings)

    def test_assignment_and_option_values_are_command_lines(self):
        for command in ("NODE_OPTIONS='--require ./setup.js' node --test", "node --require=./setup.js --test",
                        "node -r ./setup.js --test"):
            with self.subTest(command):
                bindings, reason = self.words(command)
                self.assertEqual("", reason)
                self.assertIn(("./setup.js", "."), bindings)

    def test_inline_javascript_names_its_modules(self):
        bindings, reason = self.words("node -e \"require('./scripts/run-tests.js')\"")
        self.assertEqual("", reason)
        self.assertIn(("./scripts/run-tests.js", "."), bindings)
        _, reason = self.words("node -e \"require(process.env.RUNNER)\"")
        self.assertIn("computed path", reason)

    def test_what_the_shell_computes_is_a_boundary(self):
        for command in ("node --test $(node list.js)", "node --test test/*.js", "cd $OUT && ls", "node $RUNNER"):
            with self.subTest(command):
                _, reason = self.words(command)
                self.assertTrue(reason, command)

    def test_a_redirection_names_no_runner(self):
        bindings, reason = self.words("node --test > /dev/null 2>&1")
        self.assertEqual("", reason)
        self.assertNotIn("/dev/null", [word for word, _ in bindings])


class ReferenceTests(unittest.TestCase):
    def test_scripts_a_command_runs_by_name(self):
        scripts = {"test": "x", "unit": "y", "lint": "z", "start": "s"}
        refs = suite_definition._script_references
        self.assertEqual([("test", True)], refs([("npm", "."), ("test", ".")], scripts))
        self.assertEqual([("unit", True)], refs([("pnpm", "."), ("-r", "."), ("run", "."), ("unit", ".")], scripts))
        self.assertEqual([("lint", True)], refs([("yarn", "."), ("lint", ".")], scripts))
        self.assertEqual({("lint", False), ("unit", False)},
                         set(refs([("run-s", "."), ("lint", "."), ("unit", ".")], scripts)))
        self.assertEqual([("unit", False)], refs([("concurrently", "."), ("npm:unit", ".")], scripts))
        self.assertEqual([], refs([("node", "."), ("run.js", ".")], scripts))


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

    def test_a_field_a_pinned_runner_resolves_through_stays_the_bases(self):
        # #652 review: the candidate's imports map redirected a pinned runner's `#select` require.
        base = {"imports": {"#select": "./scripts/select-tests.js"}, "main": "a.js", "scripts": {"test": "x"}}
        candidate = {"imports": {"#select": "./scripts/narrow.js"}, "main": "b.js", "scripts": {"test": "x"}}
        kept = suite_definition.KEPT_FIELDS - {"imports"}
        merged = json.loads(suite_definition.merged_manifest(json.dumps(base).encode(), json.dumps(candidate).encode(),
                                                             kept))
        self.assertEqual({"imports": base["imports"], "main": "b.js", "scripts": {"test": "x"}}, merged)


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

    def test_an_unsplittable_script_the_suite_runs_names_itself(self):
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "npm run hello && node --test",
                                                                               "hello": "echo don't"}})})
        found = repository.definition()
        self.assertIn('the "hello" script in package.json cannot be split into shell words', found.boundary)

    def test_a_script_the_suite_never_runs_does_not_bind_the_closure(self):
        # #652 review: start, build and deploy scripts cannot select the suite's tests.
        repository = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "node --test", "hello": "echo don't",
                                                    "start": "node server.js", "deploy": "cd $OUT && ls"}}),
            "server.js": "require('./routes.js');\n", "routes.js": "module.exports = {};\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)
        self.assertNotIn("server.js", found.pinned)

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

    def links(self, repository, links):
        for path, target in links.items():
            (repository.root / path).parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, repository.root / path)
        git(repository.root, "add", "-A")
        git(repository.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "links")
        repository.base = git(repository.root, "rev-parse", "HEAD")

    def test_a_command_word_resolves_as_node_does(self):
        # #652 review: `node scripts/run` ran scripts/run.js, which was never pinned nor scanned.
        repository = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "node scripts/run"}}),
            "scripts/run.js": self.RUNNER.replace("./scripts/select", "./select"),
            "scripts/select.js": "module.exports = ['test/a.test.js'];\n", "test/a.test.js": "1;\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)
        self.assertLessEqual({"scripts/run.js", "scripts/select.js"}, found.pinned)

    def test_words_of_assignments_options_and_inline_code_are_runners(self):
        for script in ("NODE_OPTIONS='--require ./setup.js' node --test", "node --require=./setup.js --test",
                       "node -e \"require('./setup.js'); require('node:test')\"", "(cd scripts && node ../setup.js)"):
            with self.subTest(script):
                repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": script}}),
                                               "setup.js": "1;\n", "scripts/.keep": "", "test/a.test.js": "1;\n"})
                found = repository.definition()
                self.assertEqual("", found.boundary)
                self.assertIn("setup.js", found.pinned)

    def test_a_subshell_runner_is_pinned(self):
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "(cd lib && node run.js)"}}),
                                       "lib/run.js": "1;\n", "run.js": "2;\n", "test/a.test.js": "1;\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)
        self.assertIn("lib/run.js", found.pinned)
        self.assertNotIn("run.js", found.pinned)

    def test_a_runner_literal_resolves_relative_to_its_file_only(self):
        # #652 review: a subdirectory runner's optional require pinned the same name absent at the root too.
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node scripts/run.js"}}),
                                       "scripts/run.js": "try { require('./config'); } catch (e) {}\n",
                                       "test/a.test.js": "1;\n"})
        found = repository.definition()
        self.assertIn("scripts/config.js", found.pinned)
        self.assertNotIn("config.js", found.pinned)
        self.assertEqual([], suite_definition._module_paths("./config", "scripts")[:0])

    def test_links_among_tests_and_runners_are_followed(self):
        # #652 review: only definition-named links were followed, so a linked selector or test was placeable.
        both = "module.exports = ['test/a.test.js'];\n"
        cases = {
            "a selector that is a link": ({"scripts/.keep": "", "config/tests.js": both},
                                          {"scripts/select.js": "../config/tests.js"}, "config/tests.js"),
            "a literal through a linked folder": ({"tooling/select.js": both}, {"scripts": "tooling"},
                                                  "tooling/select.js"),
            "a test that is a link": ({"cases/calc.js": "require('../calc.js');\n", "calc.js": "1;\n"},
                                      {"test/a.test.js": "../cases/calc.js"}, "cases/calc.js"),
        }
        for name, (files, links, expected) in cases.items():
            with self.subTest(name):
                repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                                               "run-tests.js": self.RUNNER, **files})
                self.links(repository, links)
                found = repository.definition()
                self.assertEqual("", found.boundary)
                self.assertIn(expected, found.pinned)
                if name == "a test that is a link":
                    self.assertIn("calc.js", found.product)  # read through the link, not the link text

    def test_manifest_fields_resolve_a_runners_import_and_stay_the_bases(self):
        # #652 review: `#select`, a self-reference and a folder's main resolved past the closure.
        runner = "require('child_process').execSync('node --test ' + require(%s).join(' '));\n"
        cases = {
            "imports": ({"name": "calc", "imports": {"#select": "./scripts/select.js"}}, runner % "'#select'",
                        {"package.json": {"imports"}}),
            "exports self-reference": ({"name": "calc", "exports": {"./select": "./scripts/select.js"}},
                                       runner % "'calc/select'", {"package.json": {"exports", "name"}}),
            "folder main": ({"name": "calc", "main": "scripts/select.js"}, runner % "'..'", {"package.json": {"main"}}),
        }
        for name, (fields, text, used) in cases.items():
            with self.subTest(name):
                repository = Repository(self, {
                    "package.json": json.dumps({**fields, "scripts": {"test": "node lib/run.js"}}),
                    "lib/run.js": text, "scripts/select.js": "module.exports = ['test/a.test.js'];\n",
                    "test/a.test.js": "1;\n"})
                found = repository.definition()
                self.assertEqual("", found.boundary)
                self.assertIn("scripts/select.js", found.pinned)
                self.assertEqual(used, {key: set(value) for key, value in found.used_fields.items()})

    def test_a_base_test_that_imports_through_main_names_product_code(self):
        repository = Repository(self, {"package.json": json.dumps({"main": "lib/calc.js", "scripts": {"test": "node --test"}}),
                                       "lib/calc.js": "exports.add = (a, b) => a + b;\n",
                                       "test/a.test.js": "const {add} = require('..');\n"})
        found = repository.definition()
        self.assertIn("lib/calc.js", found.product)

    def test_an_absent_folder_package_is_pinned_absent(self):
        # #652 review: an added selection/package.json with a main filled an optional require.
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node run.js"}}),
                                       "run.js": "let l = []; try { l = require('./selection') } catch {}\n",
                                       "test/a.test.js": "1;\n"})
        self.assertIn("selection/package.json", repository.definition().pinned)

    def test_a_runner_the_scanner_cannot_read_is_a_boundary(self):
        # Text Node would refuse too (a string reaching a newline): the closure says so instead of reading nothing.
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                                       "run-tests.js": "const s = 'oops\n" + self.RUNNER,
                                       "scripts/select.js": "module.exports = [];\n", "test/a.test.js": "1;\n"})
        self.assertIn("run-tests.js cannot be read", repository.definition().boundary)

    def test_a_runner_may_load_the_product_when_its_exec_arguments_are_literals(self):
        # #587 T16 kept: the product file cannot select tests when every exec argument is a literal.
        exec_literal = "require('child_process').execSync('node --test test/a.test.js', {stdio: 'inherit'});\n"
        files = {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                 "config.js": "module.exports = {tests: ['test/a.test.js']};\n", "test/a.test.js": "require('../config.js');\n"}
        found = Repository(self, {**files, "run-tests.js": "require('./config.js');\n" + exec_literal}).definition()
        self.assertEqual("", found.boundary)
        self.assertNotIn("config.js", found.pinned)
        computed = "const list = require('./config.js').tests;\nrequire('child_process').execSync('node --test ' + list.join(' '));\n"
        found = Repository(self, {**files, "run-tests.js": computed}).definition()
        self.assertIn("config.js, which the base tests also import", found.boundary)
        self.assertTrue(suite_definition.exec_computed(computed))
        self.assertFalse(suite_definition.exec_computed(exec_literal))
        self.assertTrue(suite_definition.exec_computed("spawnSync(process.execPath, ['--test', ...spec]);\n"))
        self.assertTrue(suite_definition.exec_computed("execSync(`node --test ${file}`);\n"))

    def test_a_selector_the_base_tests_import_is_a_conflict(self):
        # #652 review: a file both imported by a base test and read by the runner was the candidate's.
        repository = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "run-tests.js": "require('child_process').execSync('node --test ' + require('./config.js').tests.join(' '));\n",
            "config.js": "module.exports = {tests: ['test/a.test.js'], precision: 2};\n",
            "test/a.test.js": "require('../config.js');\n"})
        found = repository.definition()
        self.assertIn("which the base tests also import", found.boundary)
        self.assertEqual({"config.js"}, set(found.conflicts))
        plan = suite_definition.plan(repository.root, {"config.js": "modified"}, found, is_test=verify.is_test_path)
        self.assertEqual(["config.js"], plan["unclassified"])

    def test_chained_and_workspace_scripts_are_seeded(self):
        repository = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "npm run unit && run-s lint", "unit": "node unit.js",
                                                    "lint": "node lint.js", "start": "node start.js"}}),
            "packages/a/package.json": json.dumps({"scripts": {"test": "node run.js"}}),
            "unit.js": "1;\n", "lint.js": "1;\n", "start.js": "1;\n", "packages/a/run.js": "1;\n",
            "test/a.test.js": "1;\n"})
        found = repository.definition("pnpm -r test")
        self.assertEqual("", found.boundary)
        self.assertLessEqual({"unit.js", "lint.js", "packages/a/run.js"}, found.pinned)
        self.assertNotIn("start.js", found.pinned)

    def test_the_plan_refuses_a_file_where_a_pinned_folder_was_and_places_inert_files(self):
        # #652 review: a link at scripts/ replaced the folder holding the pinned selector.
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                                       "run-tests.js": self.RUNNER,
                                       "scripts/select.js": "module.exports = ['test/a.test.js'];\n",
                                       "test/a.test.js": "1;\n", "README.md": "# calc\n"})
        found = repository.definition()
        plan = suite_definition.plan(repository.root, {"scripts": "added", "scripts/select.js": "deleted",
                                                       "selectors/select.js": "added", "README.md": "modified"},
                                     found, is_test=verify.is_test_path)
        self.assertEqual([("scripts", "scripts/select.js")], plan["blocked"])
        self.assertNotIn("scripts", plan["overlay"])
        self.assertIn("README.md", plan["overlay"])
        self.assertEqual(["selectors/select.js"], plan["unclassified"])

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
