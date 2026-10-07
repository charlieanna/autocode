"""A package suite is judged as its original definitions define it (#528).

npm, Yarn and pnpm run each tree's own package.json and package-manager
configuration, so the same suite command can run fewer tests on the candidate.
When the suite reports no per-test results, the proof runs it once more on the
candidate's code with the original definitions restored. Real git, node and npm
(and pnpm when present) through verify.baseline + verify.verify and the
completion gate's regression.prove. In every narrowing case the candidate breaks
add(), whose old test still fails when the original suite runs; before the
original-definitions run each of these was PASS (or UNVERIFIED for a widened
script).
"""
import json
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

    def verified(self, project, suite=None, base_patch=None):
        framework = verify.detect_framework(project.root)
        suite = suite or framework.suite
        base = verify.baseline(project.root, project.base, project.evidence, framework=framework,
                               suite_command=suite, timeout=120, base_patch=base_patch)
        return verify.verify(project.root, project.base, project.evidence, framework=framework, suite_command=suite,
                             base_suite=base, new_behavior=True, timeout=120, base_patch=base_patch)

    def proved(self, project, suite=None):
        state = {"base_commit": project.base, "iteration": 1, "stages": [], "history": [],
                 "settings": {"regression": {"test_timeout": 120, **({"test_command": suite} if suite else {})}},
                 "goal_contract": {"body": {"task_kind": "build"}}}
        run = project.root / ".autocode" / "runs" / "proof"
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

if __name__ == "__main__":
    unittest.main()
