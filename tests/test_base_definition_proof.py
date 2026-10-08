"""The base suite definition's run over the candidate decides an exit-code-only suite (#587, #652).

#625 runs the base revision's suite definition once more over the candidate's product code when
the suite reports no per-test results. Each test named after a gap (G1-G12) got a wrong verdict
on master deb3a7a; the controls name what must not change. Real git, node and npm (Yarn 1 and
pnpm when installed) through verify.baseline + verify.verify, and the completion gate's
regression.prove, whose run folder is inside the workspace. In every narrowing case the
candidate breaks add(), whose old test fails when the base definition runs.
"""
import json
import os
import shutil
import subprocess
import unittest

import autocode_regression as regression
import autocode_verify as verify
from tests.test_verify import Project

LABEL = "suite_base_definition_on_candidate"
CALC = "exports.add = (a, b) => a + b;\n"
BROKEN = "exports.add = (a, b) => a - b;\nexports.mul = (a, b) => a * b;\n"
FIXED = "exports.add = (a, b) => a + b;\nexports.mul = (a, b) => a * b;\n"
NODE_TEST = "const {test} = require('node:test');\nconst assert = require('node:assert/strict');\n"
CALC_TEST = NODE_TEST + "const {add} = require('../calc.js');\ntest('add', () => assert.equal(add(2, 3), 5));\n"
FEATURE_TEST = NODE_TEST + "const {mul} = require('../calc.js');\ntest('mul', () => assert.equal(mul(2, 3), 6));\n"
OTHER_TEST = NODE_TEST + "test('other', () => assert.ok(true));\n"
NARROWED = "The base suite definition fails against the candidate code"
# Stand-ins for runners whose configuration selects or rewrites tests (#652 review); each runs node --test.
PLAYWRIGHT = """#!/usr/bin/env node
const fs = require('fs'), path = require('path'), {spawnSync} = require('child_process');
const config = require(path.resolve('playwright.config.js'));
const ignore = [].concat(config.testIgnore || []).map(p => p.replace('**/', ''));
const files = fs.readdirSync(config.testDir).filter(n => n.endsWith('.test.js')).filter(n => !ignore.some(i => n.endsWith(i)))
  .map(n => path.join(config.testDir, n)).sort();
process.exit(spawnSync(process.execPath, ['--test', ...files], {stdio: 'inherit'}).status);
"""
JEST = """#!/usr/bin/env node
const fs = require('fs'), path = require('path'), {spawnSync} = require('child_process');
let skip = fs.existsSync('babel.config.js') ? (require(path.resolve('babel.config.js')).skip || []) : [];
const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8'));
if (pkg.babel && pkg.babel.skip) skip = skip.concat(pkg.babel.skip);
const files = fs.readdirSync('test').filter(n => n.endsWith('.test.js')).map(n => 'test/' + n).filter(f => !skip.includes(f)).sort();
process.exit(spawnSync(process.execPath, ['--test', ...files], {stdio: 'inherit'}).status);
"""
# The G5 fake vitest, honouring a positional filter: only test files whose path contains it run.
FILTERING_VITEST = """#!/usr/bin/env node
const fs = require('fs'), {spawnSync} = require('child_process');
const out = (process.argv.find(arg => arg.startsWith('--outputFile=')) || '').slice('--outputFile='.length);
const filters = process.argv.slice(2).filter(arg => !arg.startsWith('-') && arg !== 'run');
let failed = false, total = 0;
const files = fs.readdirSync('test').filter(name => name.endsWith('.test.js')).map(name => 'test/' + name)
  .filter(file => !filters.length || filters.some(f => file.includes(f))).sort().map(file => {
    const run = spawnSync(process.execPath, ['--test', '--test-reporter=tap', file], {encoding: 'utf8'});
    const tests = [...run.stdout.matchAll(/^(not )?ok \\d+ - (.+?)( #.*)?$/gm)].map(
      match => ({name: match[2], state: match[1] ? 'failed' : 'passed', collection_error: false}));
    failed = failed || tests.some(test => test.state === 'failed');
    total += tests.length;
    return {file, collection_error: false, tests};
  });
if (out) fs.writeFileSync(out, JSON.stringify({protocol: 'autocode-vitest-tests', version: 1, vitest_version: '4.1.6',
  complete: true, reason: failed ? 'failed' : 'passed', files, total, unhandled_errors: 0}));
process.exit(failed ? 1 : 0);
"""
BOTH = "module.exports = ['test/calc.test.js', 'test/other.test.js'];\n"
NARROW = "module.exports = ['test/other.test.js'];\n"


def manifest(scripts, **fields):
    return json.dumps({"name": "calc", "version": "1.0.0", **fields, "scripts": scripts}, indent=2) + "\n"


def seed(scripts=None, **fields):
    return {"package.json": manifest(scripts or {"test": "node --test"}, **fields), "calc.js": CALC,
            "test/calc.test.js": CALC_TEST}


def change(source=BROKEN, **files):
    """The candidate: add() broken (or kept, with FIXED) and mul() added with its own test."""
    return {"calc.js": source, "test/feature.test.js": FEATURE_TEST, **files}


def runner(selector, *, inline=False):
    """A runner that takes its spec list from a selector module and runs it in a child process: inside
    the exec call's argument (``inline``, the base definition pins the selector) or through a variable
    (the selector could as well be product code, so the boundary is unestablished)."""
    if inline:
        return ('require("child_process").execSync("node --test " + require(%s).join(" "), {stdio: "inherit"});\n'
                % json.dumps(selector))
    return (f"const list = require({selector!r});\n"
            "require('child_process').execSync('node --test ' + list.join(' '), {stdio: 'inherit'});\n")


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
class BaseDefinitionProofTests(unittest.TestCase):
    def project(self, files, candidate):
        project = Project(files)
        self.addCleanup(project.close)
        project.write(candidate)
        return project

    def commit(self, project):
        for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "more")):
            subprocess.run(["git", *args], cwd=project.root, check=True, capture_output=True)
        project.base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project.root, check=True,
                                      capture_output=True, text=True).stdout.strip()

    def install(self, project, name, text):
        """A runner stand-in in the project's node_modules/.bin (ignored by git, linked into the proof trees)."""
        project.write({f"node_modules/.bin/{name}": text})
        os.chmod(project.root / f"node_modules/.bin/{name}", 0o755)

    def link(self, project, links):
        """Links on the base revision: {path: target}, committed."""
        for path, target in links.items():
            (project.root / path).parent.mkdir(parents=True, exist_ok=True)
            (project.root / path).symlink_to(target)
        self.commit(project)

    def verified(self, project, suite=None, base_patch=None, new_behavior=True, dependencies=None):
        framework = verify.detect_framework(project.root)
        suite = suite or framework.suite
        base = verify.baseline(project.root, project.base, project.evidence, framework=framework,
                               suite_command=suite, timeout=120, base_patch=base_patch,
                               dependencies_from=dependencies)
        return verify.verify(project.root, project.base, project.evidence, framework=framework,
                             suite_command=suite, base_suite=base, new_behavior=new_behavior, timeout=120,
                             base_patch=base_patch, dependencies_from=dependencies)

    def proved(self, project, suite):
        """The completion gate's proof, whose run folder (and so its scratch trees) is inside the workspace."""
        state = {"base_commit": project.base, "iteration": 1, "stages": [], "history": [],
                 "settings": {"regression": {"test_timeout": 120, "test_command": suite}},
                 "goal_contract": {"body": {"task_kind": "build"}}}
        run = project.root / ".autocode" / "runs" / "proof"
        run.mkdir(parents=True, exist_ok=True)
        return regression.prove(state, project.root, run)

    def assertNarrowed(self, result):
        """FAIL because the base definition fails over the candidate code while its own suite passed."""
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(any(reason.startswith(NARROWED) for reason in result["failures"]), result["failures"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)

    def assertPreserved(self, result):
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(0, result["checks"][LABEL]["exit_code"], result)

    # --- G1: the extra run starts whenever both suites completed ----------------------------

    def test_g1_a_failing_base_suite_cannot_hide_a_break_behind_a_narrowed_script(self):
        red = NODE_TEST + "test('known red', () => assert.equal(1, 2));\n"
        project = self.project({**seed(), "test/red.test.js": red},
                               change(**{"package.json": manifest({"test": "node --test test/feature.test.js"})}))
        result = self.verified(project)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual("failing", result["baseline"]["health"], result)
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)
        self.assertTrue(any(reason.startswith(NARROWED) and "did not pass either" in reason
                            for reason in result["unverified"]), result["unverified"])

    def test_g1_a_base_without_a_manifest_cannot_pass_a_narrowed_new_suite(self):
        project = self.project({"calc.js": CALC, "test/calc.test.js": CALC_TEST, "test/other.test.js": OTHER_TEST},
                               change(**{"package.json": manifest({"test": "node --test test/other.test.js"})}))
        result = self.verified(project, "npm test --silent")
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)
        self.assertTrue(any(reason.startswith(NARROWED) for reason in result["unverified"]), result["unverified"])

    def test_g1_control_a_fix_for_an_already_failing_test_still_passes(self):
        project = self.project({**seed(), "calc.js": "exports.add = (a, b) => a - b;\n"}, change(FIXED))
        result = self.verified(project)
        self.assertEqual("failing", result["baseline"]["health"], result)
        self.assertPreserved(result)

    # --- G2: the extra run's tree is outside the workspace -------------------------------

    def test_g2_configuration_above_the_tree_cannot_reach_the_base_definition(self):
        cases = [(manager, files) for manager, files in (
            ("yarn", {".yarnrc": 'script-shell "/usr/bin/true"\n'}),
            ("yarn", {".npmrc": "script-shell=/usr/bin/true\n"}),
            ("pnpm", {"pnpm-workspace.yaml": "packages: []\nscriptShell: /usr/bin/true\n"}))
            if shutil.which(manager)]
        if not cases:
            self.skipTest("Yarn 1 or pnpm is required")
        for manager, files in cases:
            with self.subTest(manager=manager, files=sorted(files)):
                proof = self.proved(self.project(seed(), change(**files)), f"{manager} test")
                self.assertNarrowed(proof)

    # --- G3, G4: what counts as the definition --------------------------------------------

    @unittest.skipUnless(shutil.which("yarn"), "Yarn is required")
    def test_g3_a_new_yarnrc_cannot_skip_the_old_tests(self):
        project = self.project(seed(), change(**{".yarnrc": 'script-shell "/usr/bin/true"\n'}))
        self.assertNarrowed(self.verified(project, "yarn test"))

    def test_g3_a_narrowed_test_runner_configuration_fails(self):
        # A stand-in for mocha, which reads its spec list from .mocharc.json without the script naming it.
        mocha = ("#!/usr/bin/env node\nconst fs = require('fs'), {spawnSync} = require('child_process');\n"
                 "const spec = JSON.parse(fs.readFileSync('.mocharc.json', 'utf8')).spec;\n"
                 "process.exit(spawnSync(process.execPath, ['--test', ...spec], {stdio: 'inherit'}).status);\n")
        project = self.project({**seed({"test": "mocha"}), "test/other.test.js": OTHER_TEST, ".gitignore": "node_modules/\n",
                                ".mocharc.json": '{"spec": ["test/calc.test.js", "test/other.test.js"]}\n'},
                               change(**{".mocharc.json": '{"spec": ["test/other.test.js"]}\n'}))
        project.write({"node_modules/.bin/mocha": mocha})
        os.chmod(project.root / "node_modules/.bin/mocha", 0o755)
        self.assertNarrowed(self.verified(project, dependencies=project.root))

    def test_g4_the_file_a_linked_npmrc_points_at_is_the_base_definition(self):
        project = self.project({**seed(), "config/npmrc": "fund=false\n"}, {})
        (project.root / ".npmrc").symlink_to("config/npmrc")
        self.commit(project)
        project.write(change(**{"config/npmrc": "fund=false\nscript-shell=/usr/bin/true\n"}))
        self.assertNarrowed(self.verified(project))

    # --- G5: per-test results of the extra run ------------------------------------------------

    def test_g5_per_test_results_of_the_base_definition_are_compared_with_base(self):
        # The new script hides the per-test results; the runner skips what exclude.json lists.
        vitest = """#!/usr/bin/env node
const fs = require('fs'), {spawnSync} = require('child_process');
const out = (process.argv.find(arg => arg.startsWith('--outputFile=')) || '').slice('--outputFile='.length);
let exclude = [];
try { exclude = JSON.parse(fs.readFileSync('exclude.json', 'utf8')); } catch {}
let failed = false, total = 0;
const files = fs.readdirSync('test').filter(name => name.endsWith('.test.js')).map(name => 'test/' + name)
  .filter(file => !exclude.includes(file)).sort().map(file => {
    const run = spawnSync(process.execPath, ['--test', '--test-reporter=tap', file], {encoding: 'utf8'});
    const tests = [...run.stdout.matchAll(/^(not )?ok \\d+ - (.+?)( #.*)?$/gm)].map(
      match => ({name: match[2], state: match[1] ? 'failed' : 'passed', collection_error: false}));
    failed = failed || tests.some(test => test.state === 'failed');
    total += tests.length;
    return {file, collection_error: false, tests};
  });
if (out) fs.writeFileSync(out, JSON.stringify({protocol: 'autocode-vitest-tests', version: 1, vitest_version: '4.1.6',
  complete: true, reason: failed ? 'failed' : 'passed', files, total, unhandled_errors: 0}));
process.exit(failed ? 1 : 0);
"""
        sub = NODE_TEST + "test('sub', () => assert.equal(2 - 1, 1));\n"
        project = self.project({**seed({"test": "vitest run"}), "test/sub.test.js": sub,
                                ".gitignore": "node_modules/\n"},
                               change(**{"package.json": manifest({"test": "vitest run --reporter=dot"}),
                                         "exclude.json": '["test/calc.test.js"]\n'}))
        project.write({"node_modules/.bin/vitest": vitest})
        os.chmod(project.root / "node_modules/.bin/vitest", 0o755)
        result = self.verified(project, dependencies=project.root)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIsNone(result["checks"]["suite_on_candidate"]["results"], result)
        self.assertEqual(0, result["checks"][LABEL]["exit_code"], result)
        self.assertEqual(["test/sub.test.js::sub"], result["checks"][LABEL]["results"]["passed"], result)
        self.assertTrue(any(reason.startswith("The base suite definition over the candidate code")
                            and "test/calc.test.js::add" in reason for reason in result["failures"]),
                        result["failures"])

    # --- G6: comments in a runner -----------------------------------------------------------

    def test_g6_an_apostrophe_in_a_runner_comment_cannot_hide_its_selector(self):
        for inline in (True, False):
            with self.subTest(inline=inline):
                project = self.project(
                    {**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                     "run-tests.js": "// Don't add tests here; edit scripts/select-tests.js\n"
                                     + runner("./scripts/select-tests.js", inline=inline),
                     "scripts/select-tests.js": "module.exports = ['test/calc.test.js', 'test/other.test.js'];\n"},
                    change(**{"scripts/select-tests.js": "module.exports = ['test/other.test.js'];\n"}))
                result = self.verified(project)
                if inline:
                    self.assertNarrowed(result)
                else:
                    self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
                    self.assertTrue(any("run-tests.js reaches scripts/select-tests.js" in reason
                                        for reason in result["unverified"]), result["unverified"])

    # --- G7: a follow-up measured against the reviewed change ---------------------------------

    def test_g7_a_follow_up_cannot_undo_the_reviewed_change_and_its_test(self):
        reviewed_test = CALC_TEST + "test('mul', () => assert.equal(require('../calc.js').mul(2, 3), 6));\n"
        sub_test = NODE_TEST + "const {sub} = require('../sub.js');\ntest('sub', () => assert.equal(sub(3, 2), 1));\n"
        project = self.project(seed(), {"calc.js": FIXED, "test/calc.test.js": reviewed_test})
        patch = project.evidence.parent / "reviewed.patch"
        patch.write_text(subprocess.run(["git", "diff"], cwd=project.root, check=True, capture_output=True,
                                        text=True).stdout)
        # The follow-up puts calc.js and its test back as base had them (dropping mul()) and adds sub().
        project.write({"calc.js": CALC, "test/calc.test.js": CALC_TEST, "sub.js": "exports.sub = (a, b) => a - b;\n",
                       "test/sub.test.js": sub_test})
        self.assertNotIn("calc.js", verify.changed_files(project.root, project.base))
        self.assertNarrowed(self.verified(project, base_patch=patch))

    # --- G9: correct changes need no closure of unrelated scripts ----------------------------

    def test_g9_correct_changes_are_not_refused_over_scripts_the_suite_never_runs(self):
        start = {"test": "node --test", "start": "node server.js"}
        cases = {
            "start reaches an untested module": (
                {**seed(start), "server.js": "const routes = require('./routes.js');\nconsole.log(routes);\n",
                 "routes.js": "module.exports = {home: '/'};\n"}),
            "computed require": ({**seed(start), "server.js": "require(require('path').join(__dirname, 'r.js'));\n",
                                  "r.js": "module.exports = {};\n"}),
            "cd into build output": seed({"test": "node --test", "start": "cd dist && node index.js"}),
            "computed cd": seed({"test": "node --test", "deploy": "cd $OUT_DIR && ls"}),
            "apostrophe in a script": seed({"test": "node --test", "hello": "echo don't forget"}),
            "template literal import": ({**seed(start), "server.js": "const name = 'home';\n"
                                         "import(`./routes/${name}.js`);\n", "routes/home.js": "module.exports = {};\n"}),
            "runner requires its selector outside the exec call": (
                {**seed({"test": "node run-tests.js"}), "run-tests.js": runner("./scripts/select-tests.js"),
                 "scripts/select-tests.js": "module.exports = ['test/calc.test.js'];\n"}),
        }
        for name, files in cases.items():
            with self.subTest(name):
                self.assertPreserved(self.verified(self.project(files, change(FIXED))))

    # --- G10: package.json fields the candidate's code loads through ---------------------------

    def test_g10_package_fields_that_load_the_candidates_code_are_the_candidates(self):
        cases = {
            "imports": ("module.exports = require('#impl');\n", {"imports": {"#impl": "./impl.js"}}),
            "exports self-reference": ("module.exports = require('calc/impl');\n",
                                       {"exports": {".": "./calc.js", "./impl": "./impl.js"}}),
        }
        for name, (source, fields) in cases.items():
            with self.subTest(name):
                project = self.project(seed(), change(source, **{
                    "impl.js": FIXED, "package.json": manifest({"test": "node --test"}, **fields)}))
                self.assertPreserved(self.verified(project))

    # --- G11: when the extra run is needed -----------------------------------------------------

    def test_g11_no_extra_run_once_the_verdict_has_a_failure(self):
        # A bug fix whose new test already passes on base is FAIL before any extra run.
        zero = CALC_TEST.replace("add(2, 3), 5", "add(0, 0), 0")
        project = self.project(seed(), {"calc.js": FIXED, "test/zero.test.js": zero,
                                        "package.json": manifest({"test": "node --test", "start": "node calc.js"})})
        result = self.verified(project, new_behavior=False)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertNotIn(LABEL, result["checks"])

    def test_g11_control_an_added_test_that_ends_the_suite_early_is_caught_without_a_definition_change(self):
        # Why the extra run is not skipped when only product code and added tests changed: an
        # in-process runner exits 0 when an added test ends the process before the old ones run.
        in_process = ("const fs = require('fs'), path = require('path');\n"
                      "for (const name of fs.readdirSync('test').filter(n => n.endsWith('.test.js')).sort())\n"
                      "  require(path.resolve('test', name));\n")
        old = "const assert = require('assert');\nconst {add} = require('../calc.js');\nassert.equal(add(2, 3), 5);\n"
        early = ("const assert = require('assert');\nconst {mul} = require('../calc.js');\n"
                 "assert.equal(mul(2, 3), 6);\nprocess.exit(0);\n")
        project = self.project({"package.json": manifest({"test": "node run-tests.js"}), "calc.js": CALC,
                                "run-tests.js": in_process, "test/calc.test.js": old},
                               {"calc.js": BROKEN, "test/aaa.test.js": early})
        result = self.verified(project, "npm test --silent")
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)

    # --- G12: runner literals that name no tracked file as written -----------------------------

    def test_g12_a_selector_required_without_its_extension_is_the_base_definition(self):
        project = self.project(
            {**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
             "run-tests.js": runner("./scripts/select-tests", inline=True),
             "scripts/select-tests.js": "module.exports = ['test/calc.test.js', 'test/other.test.js'];\n"},
            change(**{"scripts/select-tests.js": "module.exports = ['test/other.test.js'];\n"}))
        self.assertNarrowed(self.verified(project))

    def test_g12_an_optional_selector_the_base_lacks_stays_absent(self):
        optional = ("let list = ['test/calc.test.js', 'test/other.test.js'];\n"
                    "try { list = require('./selection.js'); } catch (error) {}\n"
                    "require('child_process').execSync('node --test ' + list.join(' '), {stdio: 'inherit'});\n")
        project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                "run-tests.js": optional},
                               change(**{"selection.js": "module.exports = ['test/other.test.js'];\n"}))
        self.assertNarrowed(self.verified(project))

    # --- the #652 review of the port: eighteen reproduced wrong verdicts ---------------------------

    def test_r1_a_narrowed_playwright_configuration_fails(self):
        project = self.project({**seed({"test": "playwright test"}), "test/other.test.js": OTHER_TEST,
                                ".gitignore": "node_modules/\n",
                                "playwright.config.js": "module.exports = {testDir: './test'};\n"},
                               change(**{"playwright.config.js":
                                         "module.exports = {testDir: './test', testIgnore: '**/calc.test.js'};\n"}))
        self.install(project, "playwright", PLAYWRIGHT)
        self.assertNarrowed(self.verified(project, dependencies=project.root))

    def test_r2_a_selector_the_base_tests_also_import_is_not_the_candidates(self):
        runner = ('require("child_process").execSync("node --test " + require("./config.js").tests.join(" "), '
                  '{stdio: "inherit"});\n')
        test = NODE_TEST + ("const {add} = require('../calc.js');\nconst {precision} = require('../config.js');\n"
                            "test('add', () => assert.equal(add(2, 3).toFixed(precision), '5.00'));\n")
        project = self.project({**seed({"test": "node run-tests.js"}), "test/calc.test.js": test,
                                "test/other.test.js": OTHER_TEST, "run-tests.js": runner,
                                "config.js": "module.exports = {tests: ['test/calc.test.js', 'test/other.test.js'], precision: 2};\n"},
                               change(**{"config.js": "module.exports = {tests: ['test/other.test.js'], precision: 2};\n"}))
        result = self.verified(project)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertTrue(any("config.js, which the base tests also import" in reason for reason in result["unverified"]),
                        result["unverified"])

    def test_r3_transpiler_configuration_that_skips_a_test_stays_the_bases(self):
        cases = {"babel.config.js": {"babel.config.js": "module.exports = {skip: ['test/calc.test.js']};\n"},
                 "package.json babel field": {"package.json": manifest({"test": "jest"},
                                                                       babel={"skip": ["test/calc.test.js"]})}}
        for name, files in cases.items():
            with self.subTest(name):
                project = self.project({**seed({"test": "jest"}), "test/other.test.js": OTHER_TEST,
                                        ".gitignore": "node_modules/\n"}, change(**files))
                self.install(project, "jest", JEST)
                result = self.verified(project, dependencies=project.root)
                self.assertNarrowed(result)
                self.assertTrue(any("the run kept " + next(iter(files)) + " as the base has them" in reason
                                    for reason in result["failures"]), result["failures"])

    def test_r4_product_code_a_base_test_imports_through_main_is_the_candidates(self):
        # The runner's computed require leaves the closure unestablished, so a changed file is placed only
        # when it is product code: lib/calc.js is, through the manifest's main.
        computed = ("const list = require(process.env.TEST_LIST || './list.js');\n"
                    "require('child_process').execSync('node --test ' + list.join(' '), {stdio: 'inherit'});\n")
        project = self.project({"package.json": manifest({"test": "node run-tests.js"}, main="lib/calc.js"),
                                "run-tests.js": computed, "list.js": "module.exports = ['test/calc.test.js'];\n",
                                "lib/calc.js": CALC, "test/calc.test.js": CALC_TEST.replace("'../calc.js'", "'..'")},
                               {"lib/calc.js": FIXED, "test/feature.test.js": FEATURE_TEST.replace("'../calc.js'", "'..'")})
        self.assertPreserved(self.verified(project))

    def test_r5_a_link_where_the_folder_holding_a_pinned_selector_was_is_refused(self):
        project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                "run-tests.js": runner("./scripts/select-tests.js", inline=True),
                                "scripts/select-tests.js": BOTH}, {})
        project.write(change(**{"selectors/select-tests.js": NARROW}))
        shutil.rmtree(project.root / "scripts")
        (project.root / "scripts").symlink_to("selectors")
        result = self.verified(project)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertTrue(any("put scripts where the folder holding scripts/select-tests.js was" in reason
                            for reason in result["unverified"]), result["unverified"])

    def test_r6_what_a_linked_test_or_runner_points_at_is_the_base_definition(self):
        cases = {
            "a selector that is a link": ({"config/tests.js": BOTH}, {"scripts/select-tests.js": "../config/tests.js"},
                                          {"config/tests.js": NARROW}),
            "a literal through a linked folder": ({"tooling/select-tests.js": BOTH}, {"scripts": "tooling"},
                                                  {"tooling/select-tests.js": NARROW}),
        }
        for name, (files, links, narrowed) in cases.items():
            with self.subTest(name):
                project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                        "run-tests.js": runner("./scripts/select-tests.js", inline=True), **files}, {})
                self.link(project, links)
                project.write(change(**narrowed))
                self.assertNarrowed(self.verified(project))
        with self.subTest("a test that is a link"):
            project = self.project({"package.json": manifest({"test": "node --test"}), "calc.js": CALC,
                                    "cases/calc.js": CALC_TEST}, {})
            self.link(project, {"test/calc.test.js": "../cases/calc.js"})
            project.write(change(**{"cases/calc.js": NODE_TEST + "test('add', () => assert.ok(true));\n"}))
            self.assertNarrowed(self.verified(project))

    def test_r7_a_runner_resolving_through_imports_or_exports_keeps_the_base_map(self):
        cases = {
            "imports": ({"imports": {"#select": "./scripts/select-tests.js"}}, '"#select"',
                        {"imports": {"#select": "./scripts/narrow.js"}}),
            "exports self-reference": ({"exports": {".": "./calc.js", "./select": "./scripts/select-tests.js"}}, '"calc/select"',
                                       {"exports": {".": "./calc.js", "./select": "./scripts/narrow.js"}}),
        }
        for name, (fields, specifier, narrowed) in cases.items():
            with self.subTest(name):
                runner_text = ('require("child_process").execSync("node --test " + require(%s).join(" "), '
                               '{stdio: "inherit"});\n' % specifier)
                project = self.project({**seed({"test": "node run-tests.js"}, **fields), "test/other.test.js": OTHER_TEST,
                                        "run-tests.js": runner_text, "scripts/select-tests.js": BOTH},
                                       change(**{"package.json": manifest({"test": "node run-tests.js"}, **narrowed),
                                                 "scripts/narrow.js": NARROW}))
                self.assertNarrowed(self.verified(project))

    def test_r8_per_test_results_only_the_candidate_reports_do_not_skip_the_extra_run(self):
        for name, extra in (("passing base", {}), ("failing base", {"test/red.test.js": NODE_TEST + "test('red', () => assert.equal(1, 2));\n"})):
            with self.subTest(name):
                project = self.project({**seed(), ".gitignore": "node_modules/\n", **extra},
                                       change(**{"package.json": manifest({"test": "vitest run test/feature"})}))
                self.install(project, "vitest", FILTERING_VITEST)
                result = self.verified(project, dependencies=project.root)
                self.assertIn(LABEL, result["checks"], result)
                self.assertNotEqual(verify.PASS, result["verdict"], result)
                if not extra:
                    self.assertNarrowed(result)

    def test_r9_scripts_the_suite_never_runs_do_not_refuse_a_new_module_or_a_doc(self):
        base = {**seed({"test": "node --test", "start": "node server.js", "deploy": "cd $OUT_DIR && ls"}),
                "server.js": "const routes = require('./routes.js');\nconsole.log(routes);\n",
                "routes.js": "module.exports = {home: '/'};\n", "README.md": "# calc\n"}
        cases = {"a new module": {"mul.js": "exports.mul = (a, b) => a * b;\n",
                                  "test/mul.test.js": NODE_TEST + "const {mul} = require('../mul.js');\ntest('mul', () => assert.equal(mul(2, 3), 6));\n"},
                 "a doc": {"README.md": "# calc\n\nMore.\n", **change(FIXED)}}
        for name, files in cases.items():
            with self.subTest(name):
                self.assertPreserved(self.verified(self.project(base, files)))

    def test_r11_a_product_module_added_beside_a_subdirectory_runners_optional_require_enters(self):
        project = self.project({**seed({"test": "node scripts/run.js"}),
                                "scripts/run.js": "try { require('./config'); } catch (e) {}\n"
                                                  "require('child_process').execSync('node --test test/calc.test.js', {stdio: 'inherit'});\n"},
                               change("const {times} = require('./config.js');\n" + FIXED,
                                      **{"config.js": "exports.times = 1;\n"}))
        self.assertPreserved(self.verified(project))

    def test_r12_a_regex_literal_in_a_runner_cannot_hide_its_selector(self):
        for line in ("const cwd = process.cwd().replace(/\\/*$/, '');\n", "const skip = /can't/;\n",
                     "const q = String(1).replace(/'/g, \"'\\\\''\");\n"):
            with self.subTest(line=line):
                project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                        "run-tests.js": line + runner("./scripts/select-tests.js", inline=True),
                                        "scripts/select-tests.js": BOTH}, change(**{"scripts/select-tests.js": NARROW}))
                self.assertNarrowed(self.verified(project))

    def test_r13_a_command_word_is_the_file_node_runs(self):
        cases = {
            "without its extension": ({"test": "node scripts/run"},
                                      {"scripts/run.js": runner("./select-tests.js", inline=True), "scripts/select-tests.js": BOTH},
                                      {"scripts/select-tests.js": NARROW}),
            "an option joined with =": ({"test": "node --require=./setup.js --test"}, {"setup.js": "// nothing yet\n"},
                                        {"setup.js": "process.exit(0);\n"}),
            "an environment assignment": ({"test": "NODE_OPTIONS='--require ./setup.js' node --test"},
                                          {"setup.js": "// nothing yet\n"}, {"setup.js": "process.exit(0);\n"}),
        }
        for name, (scripts, files, narrowed) in cases.items():
            with self.subTest(name):
                project = self.project({**seed(scripts), "test/other.test.js": OTHER_TEST, **files}, change(**narrowed))
                self.assertNarrowed(self.verified(project))

    def test_r14_inline_javascript_names_the_runner_it_requires(self):
        runner_text = "require('child_process').execSync('node --test test/calc.test.js test/other.test.js', {stdio: 'inherit'});\n"
        project = self.project({**seed({"test": "node -e \"require('./scripts/run-tests.js')\""}),
                                "test/other.test.js": OTHER_TEST, "scripts/run-tests.js": runner_text},
                               change(**{"scripts/run-tests.js": runner_text.replace("test/calc.test.js ", "")}))
        self.assertNarrowed(self.verified(project))

    def test_r15_a_subshell_runner_is_pinned(self):
        runner_text = ("require('child_process').execSync('node --test test/calc.test.js test/other.test.js', "
                       "{stdio: 'inherit', cwd: '..'});\n")
        project = self.project({**seed({"test": "(cd lib && node run.js)"}), "test/other.test.js": OTHER_TEST,
                                "lib/run.js": runner_text},
                               change(**{"lib/run.js": runner_text.replace("test/calc.test.js ", "")}))
        self.assertNarrowed(self.verified(project))

    def test_r17_manifest_fields_cannot_redirect_a_runner_past_the_closure(self):
        optional = ("let list = ['test/calc.test.js', 'test/other.test.js'];\n"
                    "try { list = require('./selection'); } catch (error) {}\n"
                    "require('child_process').execSync('node --test ' + list.join(' '), {stdio: 'inherit'});\n")
        with self.subTest("an added folder package"):
            project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "run-tests.js": optional},
                                   change(**{"selection/package.json": '{"main": "narrow.js"}\n', "selection/narrow.js": NARROW}))
            self.assertNarrowed(self.verified(project))
        with self.subTest("a changed main"):
            runner_text = 'require("child_process").execSync("node --test " + require("..").join(" "), {stdio: "inherit"});\n'
            project = self.project({**seed({"test": "node scripts/run-tests.js"}, main="select-tests.js"),
                                    "test/other.test.js": OTHER_TEST, "scripts/run-tests.js": runner_text,
                                    "select-tests.js": BOTH},
                                   change(**{"package.json": manifest({"test": "node scripts/run-tests.js"}, main="narrow.js"),
                                             "narrow.js": NARROW}))
            self.assertNarrowed(self.verified(project))

    # --- the second review round: sixteen more reproduced wrong verdicts ---------------------------

    def test_r18_every_argument_of_a_child_process_call_and_every_binding_of_the_module_count(self):
        # A product selector read outside the exec parentheses narrowed the suite when the tests were
        # passed as an args array, or the function was aliased, .call'ed or promisified (r2's rule bypassed).
        test = NODE_TEST + ("const {add} = require('../calc.js');\nconst {precision} = require('../config.js');\n"
                            "test('add', () => assert.equal(add(2, 3).toFixed(precision), '5.00'));\n")
        runners = {
            "spawnSync with an args array": "const {spawnSync} = require('child_process');\nconst {tests} = require('./config.js');\n"
                                            "process.exit(spawnSync('node', ['--test', ...tests], {stdio: 'inherit'}).status);\n",
            "execFileSync": "const {execFileSync} = require('child_process');\nconst {tests} = require('./config.js');\n"
                            "execFileSync('node', ['--test', ...tests], {stdio: 'inherit'});\n",
            "an alias": "const {execSync: run} = require('child_process');\nconst {tests} = require('./config.js');\n"
                        "run('node --test ' + tests.join(' '), {stdio: 'inherit'});\n",
            ".call": "const cp = require('child_process');\nconst {tests} = require('./config.js');\n"
                     "cp.execSync.call(cp, 'node --test ' + tests.join(' '), {stdio: 'inherit'});\n",
            "promisify under another name": "const run = require('util').promisify(require('child_process').exec);\n"
                                            "run('node --test ' + require('./config.js').tests.join(' ')).then("
                                            "r => process.stdout.write(r.stdout), "
                                            "e => { process.stdout.write(e.stdout || ''); process.exitCode = 1; });\n",
        }
        for name, runner_text in runners.items():
            with self.subTest(name):
                project = self.project(
                    {**seed({"test": "node run-tests.js"}), "test/calc.test.js": test, "test/other.test.js": OTHER_TEST,
                     "run-tests.js": runner_text,
                     "config.js": "module.exports = {tests: ['test/calc.test.js', 'test/other.test.js'], precision: 2};\n"},
                    change(**{"config.js": "module.exports = {tests: ['test/other.test.js'], precision: 2};\n"}))
                result = self.verified(project)
                self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
                self.assertTrue(any("config.js, which the base tests also import" in reason for reason in result["unverified"]),
                                result["unverified"])

    def test_r19_a_child_script_started_from_literal_arguments_is_the_bases(self):
        child = "require('child_process').execSync('node --test test/calc.test.js test/other.test.js', {stdio: 'inherit'});\n"
        runners = {"execSync": "require('child_process').execSync('node scripts/child.js', {stdio: 'inherit'});\n",
                   "fork": "const {fork} = require('child_process');\n"
                           "fork('./scripts/child.js').on('exit', code => process.exit(code));\n",
                   "spawnSync": "const {spawnSync} = require('child_process');\n"
                                "process.exit(spawnSync('node', ['scripts/child.js'], {stdio: 'inherit'}).status);\n"}
        for name, runner_text in runners.items():
            with self.subTest(name):
                project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                        "scripts/child.js": child, "run-tests.js": runner_text},
                                       change(**{"scripts/child.js": child.replace("test/calc.test.js ", "")}))
                self.assertNarrowed(self.verified(project))

    def test_r20_a_runner_with_a_hashbang_line_is_read(self):
        runner_text = ("#!/usr/bin/env node\nrequire('child_process').execSync('node --test test/calc.test.js test/other.test.js', "
                       "{stdio: 'inherit'});\n")
        new_module = {"mul.js": "exports.mul = (a, b) => a * b;\n",
                      "test/mul.test.js": NODE_TEST + "const {mul} = require('../mul.js');\ntest('mul', () => assert.equal(mul(2, 3), 6));\n"}
        with self.subTest("run by node"):
            project = self.project({**seed({"test": "node scripts/run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "scripts/run-tests.js": runner_text}, new_module)
            self.assertPreserved(self.verified(project))
        with self.subTest("run directly"):
            project = self.project({**seed({"test": "./scripts/run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "scripts/run-tests.js": runner_text}, {})
            os.chmod(project.root / "scripts/run-tests.js", 0o755)
            self.commit(project)
            project.write(new_module)
            self.assertPreserved(self.verified(project))
        with self.subTest("narrowed"):
            project = self.project({**seed({"test": "node scripts/run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "scripts/run-tests.js": runner_text},
                                   change(**{"scripts/run-tests.js": runner_text.replace("test/calc.test.js ", "")}))
            self.assertNarrowed(self.verified(project))

    def test_r21_a_regexp_exec_in_a_runner_that_loads_the_product_is_no_child_process(self):
        runner_text = ("const {add} = require('./calc.js');\nconst m = /^(\\d+)$/.exec(String(add(1, 1)));\n"
                       "if (!m) throw new Error('smoke');\n"
                       "require('child_process').execSync('node --test test/calc.test.js test/other.test.js', {stdio: 'inherit'});\n")
        base = {**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST, "run-tests.js": runner_text}
        with self.subTest("a correct change"):
            self.assertPreserved(self.verified(self.project(base, change(FIXED))))
        with self.subTest("a narrowed runner"):
            project = self.project(base, change(**{"run-tests.js": runner_text.replace("test/calc.test.js ", "")}))
            self.assertNarrowed(self.verified(project))

    def test_r22_options_before_a_shells_c_or_nodes_e_do_not_hide_the_runner(self):
        runner_text = "require('child_process').execSync('node --test test/calc.test.js test/other.test.js', {stdio: 'inherit'});\n"
        narrowed = runner_text.replace("test/calc.test.js ", "")
        cases = {
            "bash -lc": ("bash -lc 'node run-tests.js'", {"run-tests.js": runner_text}, {"run-tests.js": narrowed}),
            "sh -e -c": ("sh -e -c 'node run-tests.js'", {"run-tests.js": runner_text}, {"run-tests.js": narrowed}),
            "bash -euo pipefail -c": ("bash -euo pipefail -c 'node run-tests.js'", {"run-tests.js": runner_text},
                                      {"run-tests.js": narrowed}),
            "node --no-warnings -e": ("node --no-warnings -e \"require('./scripts/run-tests.js')\"",
                                      {"scripts/run-tests.js": runner_text}, {"scripts/run-tests.js": narrowed}),
            "node -r x -e": ("node -r ./setup.js -e \"require('./scripts/run-tests.js')\"",
                             {"scripts/run-tests.js": runner_text, "setup.js": "// nothing\n"}, {"scripts/run-tests.js": narrowed}),
        }
        for name, (script, files, narrowed_files) in cases.items():
            with self.subTest(name):
                project = self.project({**seed({"test": script}), "test/other.test.js": OTHER_TEST, **files},
                                       change(**narrowed_files))
                self.assertNarrowed(self.verified(project))

    def test_r23_a_workspace_packages_script_is_seeded_however_it_is_selected(self):
        runner_text = "require('child_process').execSync('node --test test/calc.test.js test/other.test.js', {stdio: 'inherit'});\n"
        package = {"packages/pkg/package.json": json.dumps({"name": "pkg", "version": "1.0.0",
                                                            "scripts": {"check": "node run.js"}}) + "\n",
                   "packages/pkg/run.js": runner_text, "packages/pkg/calc.js": CALC,
                   "packages/pkg/test/calc.test.js": CALC_TEST, "packages/pkg/test/other.test.js": OTHER_TEST}
        narrowed = {"packages/pkg/calc.js": BROKEN, "packages/pkg/run.js": runner_text.replace("test/calc.test.js ", ""),
                    "packages/pkg/test/feature.test.js": FEATURE_TEST}

        def root(script, **fields):
            return json.dumps({"name": "root", "version": "1.0.0", "private": True, **fields, "scripts": {"test": script}}) + "\n"

        workspace = "packages:\n  - packages/*\n"
        cases = {
            "npm --workspace": ("npm test", {"package.json": root("npm --workspace packages/pkg run check", workspaces=["packages/*"])}),
            "npm --prefix": ("npm test", {"package.json": root("npm --prefix packages/pkg run check")}),
        }
        if shutil.which("pnpm"):
            cases["pnpm --filter"] = ("pnpm test", {"package.json": root("pnpm --filter pkg run check"), "pnpm-workspace.yaml": workspace})
            cases["a chained pnpm -r"] = ("pnpm test", {"package.json": root("pnpm -r run check"), "pnpm-workspace.yaml": workspace})
        if shutil.which("yarn"):
            cases["yarn workspace"] = ("yarn test", {"package.json": root("yarn workspace pkg run check", workspaces=["packages/*"])})
        for name, (suite, files) in cases.items():
            with self.subTest(name):
                self.assertNarrowed(self.verified(self.project({**files, **package}, narrowed), suite))

    def test_r24_a_name_node_tries_before_the_pinned_file_cannot_be_added(self):
        cases = {
            "an extensionless file before select.js": ({"run-tests.js": runner("./select", inline=True), "select.js": BOTH},
                                                       {"select": NARROW}),
            "selectors.js before selectors/index.js": ({"run-tests.js": runner("./selectors", inline=True), "selectors/index.js": BOTH},
                                                       {"selectors.js": NARROW}),
        }
        for name, (files, shadow) in cases.items():
            with self.subTest(name):
                project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST, **files},
                                       change(**shadow))
                self.assertNarrowed(self.verified(project))
        with self.subTest("an extensionless command file before scripts/run.js"):
            project = self.project({**seed({"test": "node scripts/run"}), "test/other.test.js": OTHER_TEST,
                                    "scripts/run.js": runner("./select-tests.js", inline=True), "scripts/select-tests.js": BOTH},
                                   change(**{"scripts/run": "require('child_process').execSync('node --test test/other.test.js', "
                                                            "{stdio: 'inherit'});\n"}))
            self.assertNarrowed(self.verified(project))

    def test_r25_a_folder_where_a_pinned_file_or_link_was_is_refused(self):
        with self.subTest("a folder where the pinned selector was"):
            project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "run-tests.js": runner("./scripts/select-tests.js", inline=True),
                                    "scripts/select-tests.js": BOTH}, {})
            os.unlink(project.root / "scripts/select-tests.js")
            project.write(change(**{"scripts/select-tests.js/index.js": NARROW}))
            result = self.verified(project)
            self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
            self.assertTrue(any("put scripts/select-tests.js/index.js below the file or link scripts/select-tests.js" in reason
                                for reason in result["unverified"]), result["unverified"])
        with self.subTest("a folder where the pinned runner was"):
            runner_text = "require('child_process').execSync('node --test test/calc.test.js test/other.test.js', {stdio: 'inherit'});\n"
            project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "run-tests.js": runner_text}, {})
            os.unlink(project.root / "run-tests.js")
            project.write(change(**{"run-tests.js/index.js": runner_text.replace("test/calc.test.js ", "")}))
            result = self.verified(project)
            self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        with self.subTest("a folder where the pinned link was"):
            project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "run-tests.js": runner("./scripts/select-tests.js", inline=True),
                                    "tooling/select-tests.js": BOTH}, {})
            self.link(project, {"scripts": "tooling"})
            os.unlink(project.root / "scripts")
            project.write(change(**{"scripts/select-tests.js": NARROW}))
            result = self.verified(project)
            self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
            self.assertTrue(any("put scripts/select-tests.js below the file or link scripts" in reason
                                for reason in result["unverified"]), result["unverified"])

    def test_r26_pattern_map_entries_resolve_as_node_does(self):
        cases = {
            "imports pattern": ({"imports": {"#x/*": "./scripts/*.js"}}, '"#x/select-tests"', {"imports": {"#x/*": "./narrow.js"}}),
            "exports pattern": ({"exports": {".": "./calc.js", "./select/*": "./scripts/*.js"}}, '"calc/select/select-tests"',
                                {"exports": {".": "./calc.js", "./select/*": "./narrow.js"}}),
        }
        for name, (fields, specifier, narrowed) in cases.items():
            with self.subTest(name):
                runner_text = ('require("child_process").execSync("node --test " + require(%s).join(" "), '
                               '{stdio: "inherit"});\n' % specifier)
                project = self.project({**seed({"test": "node run-tests.js"}, **fields), "test/other.test.js": OTHER_TEST,
                                        "run-tests.js": runner_text, "scripts/select-tests.js": BOTH},
                                       change(**{"package.json": manifest({"test": "node run-tests.js"}, **narrowed),
                                                 "narrow.js": NARROW}))
                self.assertNarrowed(self.verified(project))

    def test_r27_a_conditional_map_entry_pins_the_file_node_loads(self):
        with self.subTest("the import condition of an ESM runner"):
            conditions = {"require": "./scripts/select-tests.js", "import": "./scripts/select-tests.mjs"}
            project = self.project(
                {**seed({"test": "node run-tests.mjs"}, imports={"#select": conditions}), "test/other.test.js": OTHER_TEST,
                 "run-tests.mjs": "import {execSync} from 'node:child_process';\n"
                                  "execSync('node --test ' + (await import('#select')).default.join(' '), {stdio: 'inherit'});\n",
                 "scripts/select-tests.js": BOTH,
                 "scripts/select-tests.mjs": "export default ['test/calc.test.js', 'test/other.test.js'];\n"},
                change(**{"scripts/select-tests.mjs": "export default ['test/other.test.js'];\n"}))
            self.assertNarrowed(self.verified(project))
        with self.subTest("the first matching condition in map order"):
            conditions = {"default": "./scripts/first.js", "require": "./scripts/select-tests.js"}
            project = self.project(
                {**seed({"test": "node run-tests.js"}, imports={"#select": conditions}), "test/other.test.js": OTHER_TEST,
                 "run-tests.js": runner("#select", inline=True), "scripts/first.js": BOTH, "scripts/select-tests.js": BOTH},
                change(**{"scripts/first.js": NARROW}))
            self.assertNarrowed(self.verified(project))

    def test_r28_an_added_nested_manifest_cannot_rescope_a_pinned_runner(self):
        with self.subTest("a closer imports map"):
            project = self.project(
                {**seed({"test": "node scripts/run.js"}, imports={"#select": "./scripts/select-tests.js"}),
                 "test/other.test.js": OTHER_TEST, "scripts/run.js": runner("#select", inline=True), "scripts/select-tests.js": BOTH},
                change(**{"scripts/package.json": '{"imports": {"#select": "./narrow.js"}}\n', "scripts/narrow.js": NARROW}))
            self.assertNarrowed(self.verified(project, "npm test --silent"))
        with self.subTest("a folder main before its index"):
            project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "run-tests.js": runner("./scripts", inline=True), "scripts/index.js": BOTH},
                                   change(**{"scripts/package.json": '{"main": "narrow.js"}\n', "scripts/narrow.js": NARROW}))
            self.assertNarrowed(self.verified(project, "npm test --silent"))
        with self.subTest("control: a nested manifest that changes nothing a pinned runner loads"):
            project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                    "run-tests.js": runner("./scripts", inline=True), "scripts/index.js": BOTH},
                                   change(FIXED, **{"scripts/package.json": '{"type": "commonjs"}\n'}))
            self.assertPreserved(self.verified(project, "npm test --silent"))

    def test_r29_what_a_configuration_file_loads_is_the_bases(self):
        project = self.project({**seed({"test": "jest"}), "test/other.test.js": OTHER_TEST, ".gitignore": "node_modules/\n",
                                "babel.config.js": "module.exports = require('./babel.base.js');\n",
                                "babel.base.js": "module.exports = {};\n"},
                               change(**{"babel.base.js": "module.exports = {skip: ['test/calc.test.js']};\n"}))
        self.install(project, "jest", JEST)
        self.assertNarrowed(self.verified(project, dependencies=project.root))

    def test_r30_npm_aliases_of_test_seed_the_test_script(self):
        runner_text = "require('child_process').execSync('node --test test/calc.test.js test/other.test.js', {stdio: 'inherit'});\n"
        for suite in ("npm t", "npm tst"):
            with self.subTest(suite=suite):
                project = self.project({**seed({"test": "node run-tests.js"}), "test/other.test.js": OTHER_TEST,
                                        "run-tests.js": runner_text},
                                       change(**{"run-tests.js": runner_text.replace("test/calc.test.js ", "")}))
                self.assertNarrowed(self.verified(project, suite))


if __name__ == "__main__":
    unittest.main()
