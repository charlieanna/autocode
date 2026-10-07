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
        project = self.project({"calc.js": CALC, "test/calc.test.js": CALC_TEST},
                               change(**{"package.json": manifest({"test": "node --test test/feature.test.js"})}))
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


if __name__ == "__main__":
    unittest.main()
