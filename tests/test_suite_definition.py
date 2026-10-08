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
    def sites(self, text):
        return [(site.specifier, site.inside_exec) for site in suite_definition.import_sites(text)]

    def test_comments_are_masked(self):
        # #652 G6: an apostrophe in a comment opened a phantom string that swallowed the require.
        text = "// Don't add tests here\nconst list = require('./select.js');\n/* require('./old.js') */\n"
        self.assertEqual([("./select.js", False)], self.sites(text))

    def test_template_literals(self):
        sites = self.sites
        self.assertEqual([("./plain.js", False)], sites("require(`./plain.js`);\n"))
        self.assertEqual([(None, False)], sites("import(`./routes/${name}.js`);\n"))
        # Code inside a substitution is still read, and `//` inside template text is not a comment.
        self.assertEqual([(None, False), ("./inner.js", False)], sites("require(`${require('./inner.js')}`);\n"))
        self.assertEqual([("./select.js", False)],
                         sites("const url = `http://example.test/a'b`; require('./select.js');\n"))

    def test_a_literal_inside_an_exec_argument(self):
        text = "require('child_process').execSync('node --test ' + require('./list.js').join(' '));\n"
        self.assertIn(("./list.js", True), self.sites(text))

    def test_regex_literals_are_masked(self):
        # #652 review: a quote, // or /* inside a regex opened a phantom string or comment that hid the require.
        sites = self.sites
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
                         self.sites("import './a.js'; import './b.js';\n"))

    def test_a_hashbang_line_is_a_comment(self):
        # #652 review: `#!/usr/bin/env node` was read as a regex literal, so every runner with one was unreadable.
        self.assertEqual([("./x.js", False)], self.sites("#!/usr/bin/env node\nrequire('./x.js');\n"))
        self.assertEqual([], self.sites("#!/usr/bin/env node\n"))

    def test_a_site_says_how_it_loads(self):
        # A conditional exports/imports entry resolves by the load kind (#652 review).
        text = "require('./a'); import('./b'); import './c';\nimport x from './d';\nexport {y} from './e';\n"
        self.assertEqual({("./a", "require"), ("./b", "import"), ("./c", "import"), ("./d", "import"), ("./e", "import")},
                         {(site.specifier, site.kind) for site in suite_definition.import_sites(text)})

    def test_every_argument_of_a_child_process_call_is_judged(self):
        # #652 review: only the first argument was judged, so spawnSync('node', [...tests]) counted as literal.
        computed = suite_definition.exec_computed
        for text in ("const {spawnSync} = require('child_process');\nspawnSync('node', ['--test', ...tests]);\n",
                     "const {execFileSync} = require('child_process');\nexecFileSync('node', ['--test', ...tests]);\n",
                     "const cp = require('child_process');\ncp.execSync(cmd);\n",
                     "require('child_process').execSync('node --test ' + list.join(' '));\n",
                     "execSync(`node --test ${file}`);\n",
                     "spawnSync(process.execPath, ['--test', ...spec]);\n",
                     "import {execa} from 'execa';\nawait execa('node', args);\n",
                     "require('child_process').execSync('node run.js', {cwd: process.env.DIR});\n"):
            with self.subTest(text=text):
                self.assertTrue(computed(text))
        for text in ("require('child_process').execSync('node --test test/a.test.js', {stdio: 'inherit'});\n",
                     "const {spawnSync} = require('node:child_process');\n"
                     "spawnSync(process.execPath, ['--test', 'test/a.test.js', ...process.argv.slice(2)], "
                     "{stdio: 'inherit', env: {...process.env, FORCE_COLOR: '1'}, timeout: 5000, cwd: __dirname});\n",
                     "import {fork} from 'child_process';\nfork('./scripts/child.js', [], {cwd: '..'});\n",
                     "const {$} = require('zx');\nawait $`node --test test/a.test.js`;\n",
                     "(await import('node:child_process')).execSync('ls');\n"):
            with self.subTest(text=text):
                self.assertFalse(computed(text))

    def test_a_child_process_binding_used_other_than_as_a_call_is_opaque(self):
        # #652 review: an aliased, promisified or .call'ed child_process function was no exec call at all.
        for text in ("const {execSync: run} = require('child_process');\nrun('node --test ' + tests.join(' '));\n",
                     "const cp = require('child_process');\ncp.execSync.call(cp, 'node --test ' + tests.join(' '));\n",
                     "const run = require('util').promisify(require('child_process').exec);\nrun('node --test x');\n",
                     "import * as cp from 'node:child_process';\nconst run = cp.execSync;\nrun('x');\n",
                     "const {...cp} = require('child_process');\n",
                     "import {execSync as sh} from 'child_process';\nmodule.exports = {sh};\n"):
            with self.subTest(text=text):
                self.assertTrue(suite_definition.exec_computed(text))
        aliased = "const {execSync: run} = require('child_process');\nrun('node --test test/a.test.js');\n"
        _, children, opaque = suite_definition.scan_source(aliased)
        self.assertEqual("", opaque)
        self.assertEqual([["node --test test/a.test.js"]], [child.arguments for child in children])

    def test_a_method_named_exec_or_fork_on_something_else_is_no_child_process(self):
        # #652 review: /re/.exec(line), cluster.fork() and db.exec(sql) counted as computed child processes.
        for text in ("const m = /^(\\d+)$/.exec(line);\n", "cluster.fork();\n", "db.exec(sql);\n", "statement.exec();\n",
                     "pool.spawn(worker);\n", "const {fork} = require('cluster');\nfork();\n"):
            with self.subTest(text=text):
                self.assertFalse(suite_definition.exec_computed(text))

    def test_the_command_a_literal_child_process_runs(self):
        def first(text, directory=".", folder="scripts"):
            return suite_definition._child_command(suite_definition.scan_source(text)[1][0], directory, folder)
        self.assertEqual(("node scripts/child.js", "."),
                         first("require('child_process').execSync('node scripts/child.js', {stdio: 'inherit'});\n"))
        self.assertEqual(("node ./scripts/child.js", "."),
                         first("const {fork} = require('child_process');\nfork('./scripts/child.js');\n"))
        self.assertEqual(("node scripts/child.js --flag", "."),
                         first("const {spawnSync} = require('child_process');\nspawnSync('node', ['scripts/child.js', '--flag']);\n"))
        self.assertEqual(("node --test 'a b.js'", "."),
                         first("require('child_process').execFileSync(process.execPath, ['--test', 'a b.js']);\n"))
        self.assertEqual(("node run.js", "lib"), first("require('child_process').execSync('node run.js', {cwd: 'lib'});\n"))
        self.assertEqual(("node run.js", "scripts"),
                         first("require('child_process').execSync('node run.js', {cwd: __dirname});\n"))
        self.assertIsNone(first("require('child_process').execSync('node run.js', {cwd: '/tmp'});\n"))
        self.assertIsNone(first("require('child_process').execSync('node run.js', {cwd: '../elsewhere'});\n"))


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

    def test_options_before_a_shells_c_or_nodes_e_do_not_hide_the_operand(self):
        # #652 review: `bash -lc '...'`, `sh -e -c '...'` and `node --no-warnings -e "..."` left the operand a word.
        for command in ("bash -lc 'node scripts/run-tests.js'", "sh -e -c 'node scripts/run-tests.js'",
                        "bash -euo pipefail -c 'node scripts/run-tests.js'", "bash --posix -c 'node scripts/run-tests.js'"):
            with self.subTest(command):
                bindings, reason = self.words(command)
                self.assertEqual("", reason)
                self.assertIn(("scripts/run-tests.js", "."), bindings)
                self.assertNotIn(("node scripts/run-tests.js", "."), bindings)
        for command in ("node --no-warnings -e \"require('./scripts/run-tests.js')\"",
                        "node -r ./setup.js -e \"require('./scripts/run-tests.js')\"",
                        "node --require=./setup.js --no-warnings -pe \"require('./scripts/run-tests.js')\""):
            with self.subTest(command):
                bindings, reason = self.words(command)
                self.assertEqual("", reason)
                self.assertIn(("./scripts/run-tests.js", "."), bindings)
        bindings, reason = self.words("node -r ./setup.js --test test/calc.test.js")
        self.assertEqual("", reason)
        self.assertIn(("./setup.js", "."), bindings)
        self.assertIn(("test/calc.test.js", "."), bindings)

    def test_inline_javascript_child_processes_are_command_lines(self):
        bindings, reason = self.words("node -e \"require('child_process').execSync('node scripts/run-tests.js')\"")
        self.assertEqual("", reason)
        self.assertIn(("scripts/run-tests.js", "."), bindings)


class ReferenceTests(unittest.TestCase):
    SCRIPTS = {"test": "x", "unit": "y", "lint": "z", "start": "s"}
    EVERY = {"test", "unit", "lint", "start", "check"}

    def refs(self, *words):
        found, _ = suite_definition._script_references([(word, ".") for word in words], self.SCRIPTS, self.EVERY)
        return found

    def test_scripts_a_command_runs_by_name(self):
        self.assertEqual([("test", True)], self.refs("npm", "test"))
        self.assertEqual([("unit", True)], self.refs("pnpm", "-r", "run", "unit"))
        self.assertEqual([("lint", True)], self.refs("yarn", "lint"))
        self.assertEqual({("lint", False), ("unit", False)}, set(self.refs("run-s", "lint", "unit")))
        self.assertEqual([("unit", False)], self.refs("concurrently", "npm:unit"))
        self.assertEqual([], self.refs("node", "run.js"))

    def test_workspace_selectors_aliases_and_task_runners(self):
        # #652 review: `npm --workspace pkg run check`, `pnpm --filter pkg run check`, `yarn workspace pkg run check`,
        # a chained `pnpm -r run check` and `npm t` seeded nothing.
        for words in (("npm", "--workspace", "packages/pkg", "run", "check"), ("npm", "-w", "pkg", "run", "check"),
                      ("npm", "--prefix", "packages/pkg", "run", "check"), ("pnpm", "--filter", "pkg", "run", "check"),
                      ("pnpm", "--filter", "pkg", "check"), ("yarn", "workspace", "pkg", "run", "check"),
                      ("yarn", "workspace", "pkg", "check"), ("pnpm", "-r", "run", "check"),
                      ("turbo", "run", "check"), ("lerna", "run", "check", "--scope", "pkg"), ("nx", "run-many", "-t", "check"),
                      ("yarn", "workspaces", "foreach", "-A", "run", "check")):
            with self.subTest(words):
                self.assertEqual([("check", True)], self.refs(*words))
        for words in (("npm", "t"), ("npm", "tst"), ("pnpm", "t"), ("npm", "run-script", "test")):
            with self.subTest(words):
                self.assertEqual([("test", True)], self.refs(*words))
        self.assertEqual([], self.refs("npm", "ci"))

    def test_the_words_of_an_invocation_name_no_file(self):
        words = ("npm", "--workspace", "packages/pkg", "run", "check", "node", "extra.js")
        found, consumed = suite_definition._script_references([(word, ".") for word in words], self.SCRIPTS, self.EVERY)
        self.assertEqual([("check", True)], found)
        self.assertEqual({0, 1, 2, 3, 4}, consumed)


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

    def test_a_child_process_started_from_literal_arguments_is_followed(self):
        # #652 review: a child script named by an exec literal was neither pinned nor a boundary.
        child = "require('child_process').execSync('node --test test/a.test.js', {stdio: 'inherit'});\n"
        for text in ("require('child_process').execSync('node scripts/child.js', {stdio: 'inherit'});\n",
                     "const {fork} = require('child_process');\nfork('./scripts/child.js');\n",
                     "const {spawnSync} = require('child_process');\n"
                     "process.exit(spawnSync('node', ['scripts/child.js'], {stdio: 'inherit'}).status);\n",
                     "require('child_process').execSync('node child.js', {cwd: 'scripts'});\n",
                     "require('child_process').execSync('npm run child');\n"):
            with self.subTest(text=text):
                repository = Repository(self, {
                    "package.json": json.dumps({"scripts": {"test": "node run-tests.js", "child": "node scripts/child.js"}}),
                    "run-tests.js": text, "scripts/child.js": child, "test/a.test.js": "1;\n"})
                found = repository.definition()
                self.assertEqual("", found.boundary)
                self.assertIn("scripts/child.js", found.pinned)
        computed = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                                     "run-tests.js": "require('child_process').execSync('node --test $(cat list.txt)');\n",
                                     "test/a.test.js": "1;\n"}).definition()
        self.assertIn("in the child process run-tests.js starts, the word", computed.boundary)

    def test_an_aliased_child_process_call_reaching_the_product_is_a_conflict(self):
        # #652 review: r2's rule was bypassed by an args array, an alias, .call and promisify.
        files = {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                 "config.js": "module.exports = {tests: ['test/a.test.js']};\n", "test/a.test.js": "require('../config.js');\n"}
        for text in ("const {spawnSync} = require('child_process');\nconst {tests} = require('./config.js');\n"
                     "spawnSync('node', ['--test', ...tests]);\n",
                     "const {execSync: run} = require('child_process');\n"
                     "run('node --test ' + require('./config.js').tests.join(' '));\n",
                     "const cp = require('child_process');\ncp.execSync.call(cp, 'node --test ' + require('./config.js').tests.join(' '));\n",
                     "const run = require('util').promisify(require('child_process').exec);\n"
                     "run('node --test ' + require('./config.js').tests.join(' '));\n"):
            with self.subTest(text=text):
                found = Repository(self, {**files, "run-tests.js": text}).definition()
                self.assertIn("config.js, which the base tests also import", found.boundary)
                self.assertEqual({"config.js"}, set(found.conflicts))
        # A RegExp's exec is no child process: the runner loads the product and runs a literal command.
        smoke = ("const {add} = require('./calc.js');\nif (!/^\\d+$/.exec(String(add(1, 1)))) throw new Error('smoke');\n"
                 "require('child_process').execSync('node --test test/a.test.js');\n")
        found = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                                  "calc.js": "exports.add = (a, b) => a + b;\n", "test/a.test.js": "require('../calc.js');\n",
                                  "run-tests.js": smoke}).definition()
        self.assertEqual("", found.boundary)

    def test_a_runner_with_a_hashbang_line_is_read(self):
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node scripts/run"}}),
                                       "scripts/run.js": "#!/usr/bin/env node\n" + self.RUNNER.replace("./scripts/select", "./select"),
                                       "scripts/select.js": "module.exports = [];\n", "test/a.test.js": "1;\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)
        self.assertIn("scripts/select.js", found.pinned)

    def test_names_node_tries_before_the_file_it_loads_stay_absent(self):
        # #652 review: a candidate-added `select` (no extension), `selectors.js` or `scripts/run` shadowed the pinned file.
        repository = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "node scripts/run"}}),
            "scripts/run.js": "require('child_process').execSync('node --test ' + require('./select').concat(require('../selectors')).join(' '));\n",
            "scripts/select.js": "module.exports = [];\n", "selectors/index.js": "module.exports = [];\n", "test/a.test.js": "1;\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)
        self.assertLessEqual({"scripts/run", "scripts/select", "selectors", "selectors.js", "selectors/package.json"},
                             found.pinned - found.present)
        self.assertLessEqual({"scripts/run.js", "scripts/select.js", "selectors/index.js"}, found.present)

    def test_pattern_and_conditional_map_entries_resolve_as_node_does(self):
        # #652 review: a `#x/*` entry was unreadable without holding the field, and a conditional entry was
        # read in a fixed order instead of the load kind's conditions in the object's order.
        runner = "require('child_process').execSync('node --test ' + require(%s).join(' '));\n"
        esm = "import {execSync} from 'node:child_process';\nexecSync('node --test ' + (await import('#select')).default.join(' '));\n"
        cases = {
            "imports pattern": ({"imports": {"#x/*": "./scripts/*.js"}}, "lib/run.js", runner % "'#x/select'", "scripts/select.js"),
            "exports pattern": ({"exports": {".": "./calc.js", "./s/*": "./scripts/*.js"}}, "lib/run.js",
                                runner % "'calc/s/select'", "scripts/select.js"),
            "the require condition": ({"imports": {"#select": {"import": "./scripts/select.mjs", "require": "./scripts/select.js"}}},
                                      "lib/run.js", runner % "'#select'", "scripts/select.js"),
            "the first matching condition": ({"imports": {"#select": {"default": "./scripts/first.js", "require": "./scripts/select.js"}}},
                                             "lib/run.js", runner % "'#select'", "scripts/first.js"),
            "the import condition": ({"imports": {"#select": {"require": "./scripts/select.js", "import": "./scripts/select.mjs"}}},
                                     "lib/run.mjs", esm, "scripts/select.mjs"),
        }
        for name, (fields, path, text, expected) in cases.items():
            with self.subTest(name):
                repository = Repository(self, {
                    "package.json": json.dumps({"name": "calc", **fields, "scripts": {"test": "node " + path}}), path: text,
                    "scripts/select.js": "module.exports = [];\n", "scripts/select.mjs": "export default [];\n",
                    "scripts/first.js": "module.exports = [];\n", "test/a.test.js": "1;\n"})
                found = repository.definition()
                self.assertEqual("", found.boundary)
                self.assertIn(expected, found.pinned)
                self.assertTrue(found.used_fields.get("package.json"))
        custom = Repository(self, {
            "package.json": json.dumps({"imports": {"#select": {"custom": "./a.js", "default": "./b.js"}},
                                        "scripts": {"test": "node run.js"}}),
            "run.js": runner % "'#select'", "a.js": "1;\n", "b.js": "1;\n", "test/a.test.js": "1;\n"}).definition()
        self.assertIn("--conditions", custom.boundary)
        self.assertEqual({"imports"}, set(custom.used_fields["package.json"]))

    def test_manifests_an_added_one_could_rescope_a_pinned_file_through_stay_absent(self):
        # #652 review: an added scripts/package.json with an imports map or a main redirected the pinned runner.
        repository = Repository(self, {
            "package.json": json.dumps({"imports": {"#select": "./scripts/select.js"}, "scripts": {"test": "node scripts/run.js"}}),
            "scripts/run.js": "require('child_process').execSync('node --test ' + require('#select').concat(require('./tools')).join(' '));\n",
            "scripts/select.js": "module.exports = [];\n", "scripts/tools/index.js": "module.exports = [];\n", "test/a.test.js": "1;\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)
        self.assertLessEqual({"scripts/package.json", "scripts/tools/package.json", "test/package.json"},
                             found.pinned - found.present)
        repository.write({"scripts/package.json": '{"imports": {"#select": "./narrow.js"}}\n'})
        plan = suite_definition.plan(repository.root, {"scripts/package.json": "added"}, found, is_test=verify.is_test_path)
        self.assertEqual({}, plan["manifests"])

    def test_javascript_configuration_is_followed(self):
        # #652 review: babel.config.js = require('./babel.base.js') pinned the config but not what it loads.
        repository = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "jest"}}),
            "babel.config.js": "module.exports = require('./babel.base.js');\n", "babel.base.js": "module.exports = {};\n",
            "jest.config.js": "module.exports = {...require('./jest.base'), rootDir: __dirname};\n", "jest.base.js": "module.exports = {};\n",
            "tsconfig.json": '{\n  // comments are allowed here\n  "extends": "./config/base",\n}\n', "config/base.json": "{}\n",
            "packages/a/jest.config.js": "module.exports = require(process.env.X);\n", "test/a.test.js": "1;\n"})
        found = repository.definition()
        self.assertEqual("", found.boundary)  # packages/a's computed require: no command runs there
        self.assertLessEqual({"babel.base.js", "jest.base.js", "config/base.json"}, found.pinned)
        product = Repository(self, {
            "package.json": json.dumps({"scripts": {"test": "jest"}}), "calc.js": "exports.skip = [];\n",
            "jest.config.js": "module.exports = {skip: require('./calc.js').skip};\n",
            "test/a.test.js": "require('../calc.js');\n"}).definition()
        self.assertIn("jest.config.js reaches calc.js, which the base tests also import", product.boundary)

    def test_the_plan_refuses_a_folder_below_a_pinned_file_or_link(self):
        # #652 review: a folder put where a pinned file was made the tree builder unlink the pinned file.
        repository = Repository(self, {"package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
                                       "run-tests.js": self.RUNNER,
                                       "scripts/select.js": "module.exports = ['test/a.test.js'];\n", "test/a.test.js": "1;\n"})
        found = repository.definition()
        plan = suite_definition.plan(repository.root, {"scripts/select.js": "deleted", "scripts/select.js/index.js": "added",
                                                       "run-tests.js": "deleted", "run-tests.js/index.js": "added"},
                                     found, is_test=verify.is_test_path)
        self.assertEqual([("run-tests.js/index.js", "run-tests.js"), ("scripts/select.js/index.js", "scripts/select.js")],
                         plan["blocked"])
        self.assertEqual({}, plan["overlay"])

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
