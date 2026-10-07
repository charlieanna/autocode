"""A package suite is judged as its original definitions define it (#528).

npm, Yarn and pnpm run each tree's own package.json and package-manager
configuration, so the same suite command can run fewer tests on the candidate.
When the suite reports no per-test results, the proof runs it once more on the
candidate's code with the original definitions restored. Real git, node and npm
(and pnpm when present) through verify.baseline + verify.verify and the
completion gate's regression.prove. In every narrowing case the candidate breaks
add(), whose old test still fails when the original suite runs; before the
original-definitions run each of these was PASS (or UNVERIFIED for a widened
script). Yarn 1 and pnpm cases run when those are installed.
"""
import json
import os
import shutil
import subprocess
import unittest

import autocode_original_scripts as original_scripts
import autocode_regression as regression
import autocode_verify as verify
from tests.test_verify import Project

CALC = "exports.add = (a, b) => a + b;\n"
BROKEN = "exports.add = (a, b) => a - b;\nexports.mul = (a, b) => a * b;\n"
FIXED = "exports.add = (a, b) => a + b;\nexports.mul = (a, b) => a * b;\n"
NODE_TEST = "const {test} = require('node:test');\nconst assert = require('node:assert/strict');\n"
CALC_TEST = NODE_TEST + "const {add} = require('../calc.js');\ntest('add', () => assert.equal(add(2, 3), 5));\n"
FEATURE_TEST = NODE_TEST + "const {mul} = require('../calc.js');\ntest('mul', () => assert.equal(mul(2, 3), 6));\n"
LABEL = original_scripts.LABEL


def manifest(scripts, **fields):
    return json.dumps({"name": "calc", "version": "1.0.0", **fields, "scripts": scripts}, indent=2) + "\n"


def seed(scripts=None, **fields):
    return {"package.json": manifest(scripts or {"test": "node --test"}, **fields), "calc.js": CALC,
            "test/calc.test.js": CALC_TEST}


def change(source=BROKEN, prefix="", **files):
    """The candidate: add() broken (or kept, with FIXED) and mul() added with its own test."""
    return {f"{prefix}calc.js": source, f"{prefix}test/feature.test.js": FEATURE_TEST, **files}


def workspace(root_scripts, **files):
    return {"package.json": json.dumps({"name": "root", "private": True, "workspaces": ["packages/*"],
                                        "scripts": root_scripts}) + "\n",
            "packages/calc/package.json": manifest({"test": "node --test"}),
            "packages/calc/calc.js": CALC, "packages/calc/test/calc.test.js": CALC_TEST, **files}


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
class OriginalDefinitionsProofTests(unittest.TestCase):
    def project(self, files, candidate):
        project = Project(files)
        self.addCleanup(project.close)
        project.write(candidate)
        return project

    def verified(self, project, suite=None, base_patch=None, new_behavior=True, dependencies=None):
        framework = verify.detect_framework(project.root)
        suite = suite or framework.suite
        base = verify.baseline(project.root, project.base, project.evidence, framework=framework,
                               suite_command=suite, timeout=120, base_patch=base_patch, dependencies_from=dependencies)
        return verify.verify(project.root, project.base, project.evidence, framework=framework, suite_command=suite,
                             base_suite=base, new_behavior=new_behavior, timeout=120, base_patch=base_patch,
                             dependencies_from=dependencies)

    def run_dir(self, project):
        return project.root / ".autocode" / "runs" / "proof"

    def proved(self, project, suite=None, base=None):
        state = {"base_commit": base or project.base, "iteration": 1, "stages": [], "history": [],
                 "settings": {"regression": {"test_timeout": 120, **({"test_command": suite} if suite else {})}},
                 "goal_contract": {"body": {"task_kind": "build"}}}
        run = self.run_dir(project)
        run.mkdir(parents=True, exist_ok=True)
        return regression.prove(state, project.root, run)

    def assertOriginalFails(self, result, *restored):
        """FAIL because the original definitions fail on the candidate, while its own suite passed."""
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)
        reasons = [reason for reason in result["failures"] if "as originally defined passes on base" in reason]
        self.assertEqual(1, len(reasons), result["failures"])
        for path in restored:
            self.assertIn(path, reasons[0])
        self.assertEqual([], result["unverified"], result)

    def assertOriginalPasses(self, result):
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(0, result["checks"][LABEL]["exit_code"], result)
        self.assertTrue(any("as originally defined" in note and "also passes" in note for note in result["notes"]),
                        result["notes"])

    def test_an_in_place_run_is_judged_by_the_definitions_and_tests_it_launched_with(self):
        # #575: an in-place run starts from its checkout, uncommitted and untracked files included. Here
        # the launch widened HEAD's script to every test and added sub() with its test; the candidate
        # breaks sub() and narrows the script again. Judged against HEAD, the original script would skip
        # sub's test (and that test would count as added), so the proof would pass.
        sub_test = NODE_TEST + "const {sub} = require('../sub.js');\ntest('sub', () => assert.equal(sub(3, 2), 1));\n"
        launch = {"package.json": manifest({"test": "node --test"}), "sub.js": "exports.sub = (a, b) => a - b;\n",
                  "test/sub.test.js": sub_test, ".npmrc": "fund=false\n"}
        narrowed = manifest({"test": "node --test test/calc.test.js test/feature.test.js"})
        project = self.project(seed({"test": "node --test test/calc.test.js"}), launch)
        base = regression.launch_base(project.root, self.run_dir(project))
        self.assertNotEqual(project.base, base)
        project.write(change(FIXED, **{"sub.js": "exports.sub = (a, b) => a + b;\n", "package.json": narrowed}))
        proof = self.proved(project, base=base)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual(0, proof["checks"]["suite_on_candidate"]["exit_code"], proof)
        self.assertNotEqual(0, proof["checks"][LABEL]["exit_code"], proof)
        self.assertTrue(any("as originally defined passes on base" in reason and "restored: package.json;" in reason
                            for reason in proof["failures"]), proof["failures"])
        # Launch files the candidate leaves alone, the untracked .npmrc included, are not its change.
        project = self.project(seed({"test": "node --test test/calc.test.js"}), launch)
        base = regression.launch_base(project.root, self.run_dir(project))
        project.write(change(FIXED))
        proof = self.proved(project, base=base)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertNotIn(LABEL, proof["checks"])

    def test_a_narrowed_script_that_the_test_script_runs_fails(self):
        scripts = {"test": "npm run test:unit --silent", "test:unit": "node --test"}
        project = self.project(seed(scripts), change(**{"package.json": manifest(
            {**scripts, "test:unit": "node --test test/feature.test.js"})}))
        self.assertOriginalFails(self.verified(project), "package.json")
        proof = self.proved(project)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(any("as originally defined passes on base" in reason for reason in proof["failures"]), proof)
        self.assertIn(LABEL, proof["checks"])

    def test_a_new_pretest_script_cannot_remove_an_old_test(self):
        scripts = {"pretest": "node -e \"require('fs').rmSync('test/calc.test.js')\"", "test": "node --test"}
        project = self.project(seed(), change(**{"package.json": manifest(scripts)}))
        self.assertOriginalFails(self.verified(project), "package.json")

    def test_a_narrowed_or_deleted_workspace_package_fails(self):
        narrowed = {"packages/calc/package.json": manifest({"test": "node --test test/feature.test.js"})}
        project = self.project(workspace({"test": "npm test --workspaces --silent"}),
                               change(prefix="packages/calc/", **narrowed))
        self.assertOriginalFails(self.verified(project), "packages/calc/package.json")
        # Without its package.json the package drops out of the workspaces, and so do its tests.
        other = {"packages/other/package.json": manifest({"test": "node --test"}, name="other"),
                 "packages/other/test/other.test.js": NODE_TEST + "test('other', () => assert.ok(true));\n"}
        project = self.project(workspace({"test": "npm test --workspaces --silent"}, **other),
                               change(prefix="packages/calc/"))
        (project.root / "packages/calc/package.json").unlink()
        self.assertOriginalFails(self.verified(project), "packages/calc/package.json")

    @unittest.skipUnless(shutil.which("pnpm"), "pnpm is required")
    def test_a_narrowed_package_in_a_pnpm_recursive_run_fails(self):
        project = self.project(
            workspace({"test": "pnpm -r test"}, **{"pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n"}),
            change(prefix="packages/calc/", **{"packages/calc/package.json": manifest(
                {"test": "node --test test/feature.test.js"})}))
        self.assertOriginalFails(self.verified(project), "packages/calc/package.json")

    def test_narrowed_package_fields_that_the_suite_reads_fail(self):
        runner = ("const {execFileSync} = require('child_process');\n"
                  "execFileSync(process.execPath, ['--test', ...require('./package.json').testFiles],"
                  " {stdio: 'inherit'});\n")
        cases = {
            "config": (seed({"test": "node --test $npm_package_config_tests"}, config={"tests": "test/*.test.js"}),
                       {"package.json": manifest({"test": "node --test $npm_package_config_tests"},
                                                 config={"tests": "test/feature.test.js"})}),
            "custom field": ({**seed({"test": "node run-tests.js"}, testFiles=["test/calc.test.js"]),
                              "run-tests.js": runner},
                             {"package.json": manifest({"test": "node run-tests.js"},
                                                       testFiles=["test/feature.test.js"])}),
        }
        for name, (files, package) in cases.items():
            with self.subTest(name):
                self.assertOriginalFails(self.verified(self.project(files, change(**package))), "package.json")

    def test_a_new_npmrc_cannot_skip_the_old_tests(self):
        for npmrc in ("script-shell=/usr/bin/true\n", "node-options=--test-name-pattern=mul\n"):
            with self.subTest(npmrc=npmrc):
                project = self.project(seed(), change(**{".npmrc": npmrc}))
                self.assertOriginalFails(self.verified(project), ".npmrc")

    def test_a_suite_command_is_never_parsed(self):
        narrowed = {"package.json": manifest({"test": "node --test test/feature.test.js"})}
        for suite in ("timeout 120 npm test --silent", "sh -c 'npm test --silent'", "node --run test"):
            with self.subTest(suite=suite):
                self.assertOriginalFails(self.verified(self.project(seed(), change(**narrowed)), suite),
                                         "package.json")
        web = {f"web/{path}": text for path, text in seed().items()}
        project = self.project(web, change(prefix="web/", **{"web/package.json": manifest(
            {"test": "node --test test/feature.test.js"})}))
        self.assertOriginalFails(self.verified(project, "cd web && npm test --silent"), "web/package.json")

    def test_a_widened_script_passes_when_the_original_suite_passes(self):
        for before, after in (("node --test test/calc.test.js", "node --test test/calc.test.js test/feature.test.js"),
                              ("node --test", "node --test --test-reporter=spec"),
                              ("node --test", "node --test && node calc.js")):
            with self.subTest(after=after):
                project = self.project(seed({"test": before}),
                                       change(FIXED, **{"package.json": manifest({"test": after})}))
                result = self.verified(project)
                self.assertOriginalPasses(result)
                self.assertIn("test/feature.test.js::mul", result["fail_to_pass"])
        proof = self.proved(project)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertIn(LABEL, proof["checks"])

    def test_unrelated_scripts_added_to_a_correct_change_pass(self):
        project = self.project(seed(), change(FIXED, **{"package.json": manifest(
            {"test": "node --test", "start": "node calc.js", "lint": "node -e 0"})}))
        self.assertOriginalPasses(self.verified(project))

    def test_an_edited_old_test_cannot_stand_in_for_the_original_one(self):
        # The protected-test gate reruns the original tests under the candidate's own package
        # definitions, so an edited assertion plus a narrowed suite would pass both checks.
        edited = CALC_TEST.replace("5));", "-1));")
        for definitions in ({"package.json": manifest({"test": "node --test test/feature.test.js"})},
                            {".npmrc": "script-shell=/usr/bin/true\n"}):
            with self.subTest(definitions=definitions):
                project = self.project(seed(), change(**{"test/calc.test.js": edited, **definitions}))
                result = self.verified(project)
                self.assertOriginalFails(result, *definitions)
                self.assertTrue(any("1 existing test file(s) were undone" in reason for reason in result["failures"]))

    def test_a_new_case_in_an_old_test_file_passes_with_the_original_file(self):
        extended = CALC_TEST + "test('add 0', () => assert.equal(add(0, 0), 0));\n"
        project = self.project(seed(), change(FIXED, **{"test/calc.test.js": extended, "package.json": manifest(
            {"test": "node --test", "start": "node calc.js"})}))
        result = self.verified(project)
        self.assertOriginalPasses(result)
        self.assertEqual(["test/calc.test.js"], result["checks"][LABEL]["restored_tests"])

    def test_a_follow_up_is_judged_by_the_scripts_of_the_reviewed_change(self):
        # The reviewed change widened the script. The follow-up puts back the narrower one,
        # which matches base, breaks add() and adds div().
        narrow = manifest({"test": "node --test test/feature.test.js"})
        project = self.project({**seed(), "package.json": narrow, "calc.js": FIXED,
                                "test/feature.test.js": FEATURE_TEST}, {"package.json": manifest({"test": "node --test"})})
        patch = project.evidence.parent / "reviewed.patch"
        patch.write_text(subprocess.run(["git", "diff"], cwd=project.root, check=True, capture_output=True,
                                        text=True).stdout)
        project.write({"package.json": narrow, "calc.js": BROKEN + "exports.div = (a, b) => a / b;\n",
                       "test/div.test.js": NODE_TEST + "const {div} = require('../calc.js');\n"
                       "test('div', () => assert.equal(div(6, 3), 2));\n"})
        self.assertNotIn("package.json", verify.changed_files(project.root, project.base))
        self.assertOriginalFails(self.verified(project, base_patch=patch), "package.json")

    def test_configuration_above_the_tree_cannot_reach_the_original_suite(self):
        # regression.prove keeps its run folder inside the workspace. Yarn 1 reads .npmrc and .yarnrc
        # from every parent folder and pnpm takes the nearest parent pnpm-workspace.yaml, so a tree
        # built there would still get the candidate's configuration.
        cases = [(manager, files) for manager, files in (
            ("yarn", {".yarnrc": 'script-shell "/usr/bin/true"\n'}),
            ("yarn", {".npmrc": "script-shell=/usr/bin/true\n"}),
            ("pnpm", {"pnpm-workspace.yaml": "packages: []\nscriptShell: /usr/bin/true\n"}))
            if shutil.which(manager)]
        if not cases:
            self.skipTest("Yarn 1 or pnpm is required")
        for manager, files in cases:
            with self.subTest(manager=manager, files=files):
                proof = self.proved(self.project(seed(), change(**files)), f"{manager} test")
                self.assertEqual(verify.FAIL, proof["verdict"], proof)
                self.assertTrue(any("as originally defined passes on base" in reason
                                    for reason in proof["failures"]), proof)
                self.assertNotEqual(0, proof["checks"][LABEL]["exit_code"], proof)

    def test_a_test_script_changed_with_a_file_it_runs_is_unproven(self):
        runner = ("const {execFileSync} = require('child_process');\n"
                  "execFileSync(process.execPath, ['--test', %s], {stdio: 'inherit'});\n")
        files = {**seed({"test": "node run-tests.js"}), "run-tests.js": runner % ""}
        narrowed = self.project(files, change(**{"package.json": manifest({"test": "node run-tests.js && echo done"}),
                                                 "run-tests.js": runner % "'test/feature.test.js'"}))
        # A moved build step: the original pretest runs a file that is gone.
        build = "require('fs').copyFileSync('src/calc.js', 'generated.js');\n"
        moved = self.project({"package.json": manifest({"pretest": "node build.js", "test": "node --test"}),
                              "build.js": build, "src/calc.js": CALC, ".gitignore": "generated.js\n",
                              "test/calc.test.js": CALC_TEST.replace("../calc.js", "../generated.js")},
                             {"src/calc.js": FIXED, "scripts/build.js": build,
                              "test/feature.test.js": FEATURE_TEST.replace("../calc.js", "../src/calc.js"),
                              "package.json": manifest({"pretest": "node scripts/build.js", "test": "node --test"})})
        (moved.root / "build.js").unlink()
        for project, path, script in ((narrowed, "run-tests.js", "test"), (moved, "build.js", "pretest")):
            with self.subTest(path=path):
                result = self.verified(project)
                self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
                self.assertNotIn(LABEL, result["checks"])
                self.assertTrue(any(f'{path} with the "{script}" script in package.json' in reason
                                    for reason in result["unverified"]), result["unverified"])
        # Changing the script alone leaves the run under the original script and runner to decide.
        widened = self.project(files, change(FIXED, **{"package.json": manifest(
            {"test": "node run-tests.js && echo done"})}))
        self.assertOriginalPasses(self.verified(widened))

    def test_per_test_results_of_the_original_suite_are_compared_with_base(self):
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
        project = self.project({**seed({"test": "vitest run"}), "test/sub.test.js": sub, ".gitignore": "node_modules/\n"},
                               change(**{"package.json": manifest({"test": "vitest run --reporter=dot"}),
                                         "exclude.json": '["test/calc.test.js"]\n'}))
        project.write({"node_modules/.bin/vitest": vitest})
        os.chmod(project.root / "node_modules/.bin/vitest", 0o755)
        result = self.verified(project, dependencies=project.root)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIsNone(result["checks"]["suite_on_candidate"]["results"])
        self.assertEqual(["test/sub.test.js::sub"], result["checks"][LABEL]["results"]["passed"])
        self.assertTrue(any("as originally defined" in reason and "test/calc.test.js::add" in reason
                            for reason in result["failures"]), result["failures"])

    def test_a_workspace_member_under_a_test_folder_is_judged_as_originally_defined(self):
        root = json.dumps({"name": "root", "private": True, "workspaces": ["packages/*", "tests"],
                           "scripts": {"test": "npm test --workspaces --silent"}}) + "\n"
        tests = json.dumps({"name": "tests", "private": True, "scripts": {"test": "node --test"}}) + "\n"
        project = self.project(
            {"package.json": root, "packages/calc/package.json": manifest({"test": "node -e 0"}),
             "packages/calc/calc.js": CALC, "tests/package.json": tests,
             "tests/calc.test.js": CALC_TEST.replace("../calc.js", "../packages/calc/calc.js")},
            {"packages/calc/calc.js": BROKEN, "tests/package.json": tests.replace("node --test", "node --test x.test.js"),
             "tests/x.test.js": FEATURE_TEST.replace("../calc.js", "../packages/calc/calc.js")})
        self.assertOriginalFails(self.verified(project), "tests/package.json")

    def test_a_new_package_does_not_run_in_the_original_suite(self):
        # Its scripts would run first in `npm test --workspaces`; without its package.json the folder drops out.
        remover = {"test": "node -e \"require('fs').rmSync('../calc/test/calc.test.js')\""}
        project = self.project(workspace({"test": "npm test --workspaces --silent"}), change(
            prefix="packages/calc/", **{"packages/aaa/package.json": manifest(remover, name="aaa")}))
        self.assertOriginalFails(self.verified(project), "packages/aaa/package.json")
        # Without a package.json on base the suite never ran there, and still does not.
        project = self.project({"calc.js": CALC, "test/calc.test.js": CALC_TEST},
                               change(**{"package.json": manifest({"test": "node --test test/feature.test.js"})}))
        result = self.verified(project, "npm test --silent")
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertTrue(any("did not pass on base either" in reason for reason in result["unverified"]), result)

    def test_a_removed_package_stays_removed(self):
        demo = {"packages/demo/package.json": manifest({"test": "node index.js"}, name="demo"),
                "packages/demo/index.js": "require('../calc/calc.js');\n"}
        project = self.project(workspace({"test": "npm test --workspaces --silent"}, **demo),
                               change(FIXED, prefix="packages/calc/"))
        for path in demo:
            (project.root / path).unlink()
        result = self.verified(project)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertNotIn(LABEL, result["checks"])

    def commit(self, project):
        for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "more")):
            subprocess.run(["git", *args], cwd=project.root, check=True, capture_output=True)
        project.base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project.root, check=True, capture_output=True,
                                      text=True).stdout.strip()

    def test_a_file_a_linked_npmrc_points_at_is_restored(self):
        project = self.project({**seed(), "config/npmrc": "fund=false\n"}, {})
        (project.root / ".npmrc").symlink_to("config/npmrc")
        self.commit(project)
        project.write(change(**{"config/npmrc": "fund=false\nscript-shell=/usr/bin/true\n"}))
        self.assertOriginalFails(self.verified(project), "config/npmrc")

    def test_added_tests_and_changed_test_helpers_do_not_run_under_the_original_suite(self):
        # A new test may need what the candidate changed (a template's scripts, a test helper, a fixture
        # link); the original suite is the original tests on the new code.
        generator = ("exports.scaffold = () => JSON.parse(require('fs').readFileSync("
                     "require('path').join(__dirname, 'templates/app/package.json')));\n")
        start = NODE_TEST + ("const {scaffold} = require('../gen.js');\n"
                             "test('start', () => assert.equal(scaffold().scripts.start, 'node index.js'));\n")
        lint = start.replace("'start'", "'lint'").replace("scripts.start, 'node index.js'", "scripts.lint, 'eslint .'")
        helper = "exports.pair = () => [2, 3];\n"
        cases = {
            "template": ({**seed(), "gen.js": generator, "test/gen.test.js": start,
                          "templates/app/package.json": manifest({"start": "node index.js"}, name="app")},
                         {"templates/app/package.json": manifest({"start": "node index.js", "lint": "eslint ."},
                                                                 name="app"), "test/lint.test.js": lint}),
            "helper": ({**seed(), "test/helpers.js": helper,
                        "test/calc.test.js": CALC_TEST.replace("add(2, 3)", "add(...require('./helpers.js').pair())")},
                       change(FIXED, **{"test/helpers.js": helper + "exports.triple = () => [2, 3, 4];\n",
                                        "test/feature.test.js": FEATURE_TEST.replace(
                                            "mul(2, 3)", "mul(...require('./helpers.js').triple().slice(0, 2))"),
                                        "package.json": manifest({"test": "node --test", "start": "node calc.js"})})),
            "fixture link": ({**seed(), "test/fixtures/v1.json": '{"a": 2, "b": 3}\n',
                              "test/calc.test.js": NODE_TEST + "const {add} = require('../calc.js');\n"
                              "const c = require('./fixtures/current.json');\n"
                              "test('add', () => assert.equal(add(c.a, c.b), c.a + c.b));\n"},
                             change(FIXED, **{"test/fixtures/v2.json": '{"a": 4, "b": 5}\n', "package.json": manifest(
                                 {"test": "node --test", "start": "node calc.js"})})),
        }
        for name, (files, candidate) in cases.items():
            with self.subTest(name):
                project = self.project(files, {})
                if name == "fixture link":
                    (project.root / "test/fixtures/current.json").symlink_to("v1.json")
                    self.commit(project)
                    (project.root / "test/fixtures/current.json").unlink()
                    (project.root / "test/fixtures/current.json").symlink_to("v2.json")
                project.write(candidate)
                self.assertOriginalPasses(self.verified(project))

    @unittest.skipUnless(shutil.which("yarn"), "Yarn is required")
    def test_a_yarn_release_comes_back_with_the_configuration_that_names_it(self):
        # `yarn set version` swaps the release .yarnrc.yml names; Yarn 1 runs that release.
        release = ("const {execSync} = require('child_process');\n"
                   "execSync(require(process.cwd() + '/package.json').scripts[process.argv[2]], {stdio: 'inherit'});\n")
        project = self.project({**seed(), ".yarnrc.yml": "yarnPath: .yarn/releases/yarn-1.cjs\n",
                                ".yarn/releases/yarn-1.cjs": release},
                               change(FIXED, **{".yarnrc.yml": "yarnPath: .yarn/releases/yarn-2.cjs\n",
                                                ".yarn/releases/yarn-2.cjs": "// 2\n" + release}))
        (project.root / ".yarn/releases/yarn-1.cjs").unlink()
        result = self.verified(project, "yarn test")
        self.assertOriginalPasses(result)
        self.assertIn(".yarn/releases/yarn-1.cjs", result["checks"][LABEL]["restored"])

    def test_no_second_run_when_per_test_results_or_a_failure_decided(self):
        scripts = {"package.json": manifest({"test": "node --test", "start": "node calc.js"})}
        result = self.verified(self.project(seed(), change(FIXED, **scripts)), "node --test")
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertIsNotNone(result["checks"]["suite_on_candidate"]["results"])
        self.assertNotIn(LABEL, result["checks"])
        # A bug fix whose new test already passes on base is FAIL before any second run.
        zero = CALC_TEST.replace("add(2, 3), 5", "add(0, 0), 0")
        result = self.verified(self.project(seed(), {**scripts, "calc.js": FIXED, "test/zero.test.js": zero}),
                               new_behavior=False)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertNotIn(LABEL, result["checks"])


if __name__ == "__main__":
    unittest.main()
