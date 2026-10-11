"""Regression controls for the remaining #652 false-PASS cases.

Real test commands exercise the public baseline/verify and regression.prove
interfaces. G5 uses an explicit local reporter fixture to provide test identities."""

import json
import os
import shutil
import subprocess
import unittest

import autocode_regression as regression
import autocode_verify as verify

from tests.test_verify import Project

VITEST_REPORTER = r"""#!/usr/bin/env node
const fs = require('fs'), {spawnSync} = require('child_process');
const out = (process.argv.find(arg => arg.startsWith('--outputFile=')) || '').slice('--outputFile='.length);
let exclude = [];
try { exclude = JSON.parse(fs.readFileSync('exclude.json', 'utf8')); } catch {}
let failed = false, total = 0;
const files = fs.readdirSync('test').filter(name => name.endsWith('.test.js')).map(name => 'test/' + name)
  .filter(file => !exclude.includes(file)).sort().map(file => {
    const run = spawnSync(process.execPath, ['--test', '--test-reporter=tap', file], {encoding: 'utf8'});
    const tests = [...run.stdout.matchAll(/^(not )?ok \d+ - (.+?)( #.*)?$/gm)].map(
      match => ({name: match[2], state: match[1] ? 'failed' : 'passed', collection_error: false}));
    failed = failed || tests.some(test => test.state === 'failed');
    total += tests.length;
    return {file, collection_error: false, tests};
  });
if (out) fs.writeFileSync(out, JSON.stringify({protocol: 'autocode-vitest-tests', version: 1, vitest_version: '4.1.6',
  complete: true, reason: failed ? 'failed' : 'passed', files, total, unhandled_errors: 0}));
process.exit(failed ? 1 : 0);
"""

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
    return {
        "package.json": manifest(scripts or {"test": "node --test"}, **fields),
        "calc.js": CALC,
        "test/calc.test.js": CALC_TEST,
    }


def change(source=BROKEN, **files):
    """The candidate: add() broken (or kept, with FIXED) and mul() added with its own test."""
    return {"calc.js": source, "test/feature.test.js": FEATURE_TEST, **files}


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
        project.base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project.root, check=True, capture_output=True, text=True
        ).stdout.strip()

    def verified(self, project, suite=None, base_patch=None, new_behavior=True, dependencies=None):
        framework = verify.detect_framework(project.root)
        suite = suite or framework.suite
        base = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            timeout=120,
            base_patch=base_patch,
            dependencies_from=dependencies,
        )
        return verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            base_suite=base,
            new_behavior=new_behavior,
            timeout=120,
            base_patch=base_patch,
            dependencies_from=dependencies,
        )

    def proved(self, project, suite):
        """The completion gate's proof, whose run folder (and so its scratch trees) is inside the workspace."""
        state = {
            "base_commit": project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {"test_timeout": 120, "test_command": suite}},
            "goal_contract": {"body": {"task_kind": "build"}},
        }
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

    def test_g2_configuration_above_the_tree_cannot_reach_the_base_definition(self):
        cases = [
            (manager, files)
            for manager, files in (
                ("yarn", {".yarnrc": 'script-shell "/usr/bin/true"\n'}),
                ("yarn", {".npmrc": "script-shell=/usr/bin/true\n"}),
                ("pnpm", {"pnpm-workspace.yaml": "packages: []\nscriptShell: /usr/bin/true\n"}),
            )
            if shutil.which(manager)
        ]
        if not cases:
            self.skipTest("Yarn 1 or pnpm is required")
        for manager, files in cases:
            with self.subTest(manager=manager, files=sorted(files)):
                proof = self.proved(self.project(seed(), change(**files)), f"{manager} test")
                self.assertNarrowed(proof)

    def test_g4_the_file_a_linked_npmrc_points_at_is_the_base_definition(self):
        project = self.project({**seed(), "config/npmrc": "fund=false\n"}, {})
        (project.root / ".npmrc").symlink_to("config/npmrc")
        self.commit(project)
        project.write(change(**{"config/npmrc": "fund=false\nscript-shell=/usr/bin/true\n"}))
        self.assertNarrowed(self.verified(project))

    def test_g4_a_valid_change_with_a_linked_configuration_still_passes(self):
        project = self.project({**seed(), "config/npmrc": "fund=false\n"}, {})
        (project.root / ".npmrc").symlink_to("config/npmrc")
        self.commit(project)
        project.write(change(FIXED))
        self.assertPreserved(self.verified(project))

    def test_g5_per_test_results_of_the_base_definition_are_compared_with_base(self):
        vitest = VITEST_REPORTER
        sub = NODE_TEST + "test('sub', () => assert.equal(2 - 1, 1));\n"
        project = self.project(
            {**seed({"test": "vitest run"}), "test/sub.test.js": sub, ".gitignore": "node_modules/\n"},
            change(
                **{
                    "package.json": manifest({"test": "vitest run --reporter=dot"}),
                    "exclude.json": '["test/calc.test.js"]\n',
                }
            ),
        )
        project.write({"node_modules/.bin/vitest": vitest})
        os.chmod(project.root / "node_modules/.bin/vitest", 493)
        result = self.verified(project, dependencies=project.root)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIsNone(result["checks"]["suite_on_candidate"]["results"], result)
        self.assertEqual(0, result["checks"][LABEL]["exit_code"], result)
        self.assertEqual(["test/sub.test.js::sub"], result["checks"][LABEL]["results"]["passed"], result)
        self.assertTrue(
            any(
                reason.startswith("The base suite definition over the candidate code")
                and "test/calc.test.js::add" in reason
                for reason in result["failures"]
            ),
            result["failures"],
        )

    def test_g7_a_follow_up_cannot_undo_the_reviewed_change_and_its_test(self):
        reviewed_test = CALC_TEST + "test('mul', () => assert.equal(require('../calc.js').mul(2, 3), 6));\n"
        sub_test = NODE_TEST + "const {sub} = require('../sub.js');\ntest('sub', () => assert.equal(sub(3, 2), 1));\n"
        project = self.project(seed(), {"calc.js": FIXED, "test/calc.test.js": reviewed_test})
        patch = project.evidence.parent / "reviewed.patch"
        patch.write_text(
            subprocess.run(["git", "diff"], cwd=project.root, check=True, capture_output=True, text=True).stdout
        )
        project.write(
            {
                "calc.js": CALC,
                "test/calc.test.js": CALC_TEST,
                "sub.js": "exports.sub = (a, b) => a - b;\n",
                "test/sub.test.js": sub_test,
            }
        )
        self.assertNotIn("calc.js", verify.changed_files(project.root, project.base))
        self.assertNarrowed(self.verified(project, base_patch=patch))

    def test_g7_a_follow_up_preserving_the_reviewed_change_still_passes(self):
        reviewed_test = CALC_TEST + "test('mul', () => assert.equal(require('../calc.js').mul(2, 3), 6));\n"
        project = self.project(seed(), {"calc.js": FIXED, "test/calc.test.js": reviewed_test})
        patch = project.evidence.parent / "reviewed.patch"
        patch.write_text(
            subprocess.run(["git", "diff"], cwd=project.root, check=True, capture_output=True, text=True).stdout
        )
        project.write(
            {
                "sub.js": "exports.sub = (a, b) => a - b;\n",
                "test/sub.test.js": NODE_TEST + "test('sub', () => assert.equal(require('../sub.js').sub(3, 2), 1));\n",
            }
        )
        self.assertPreserved(self.verified(project, base_patch=patch))


class DefinitionReceiptTests(unittest.TestCase):
    def results(self, passed=(), failed=(), skipped=(), *, complete=True, collection_errors=()):
        return {
            "passed": list(passed),
            "failed": list(failed),
            "skipped": list(skipped),
            "total": len(passed) + len(failed) + len(skipped),
            "complete": complete,
            "collection_errors": list(collection_errors),
        }

    def judge(self, definition_results, *, detailed_base=True, **receipt_fields):
        base = {
            "health": "passing",
            "receipt": {
                "exit_code": 0,
                "timed_out": False,
                "results": self.results(["a", "b"]) if detailed_base else None,
            },
        }
        opaque_candidate = {"exit_code": 0, "timed_out": False, "results": None}
        definition_run = {**opaque_candidate, "results": definition_results, **receipt_fields}
        failures, unverified, notes = [], [], []
        verify._judge_suite(
            opaque_candidate, base, failures, unverified, notes, base_definition={"run": definition_run, "boundary": ""}
        )
        return failures, unverified

    def test_dropped_skipped_or_failed_base_tests_block_pass_despite_zero_exit(self):
        for results in (self.results(["a"]), self.results(["a"], skipped=["b"]), self.results(["a"], failed=["b"])):
            with self.subTest(results=results):
                failures, _ = self.judge(results)
                self.assertTrue(failures)
                self.assertIn("b", " ".join(failures))

    def test_missing_incomplete_and_collection_error_reports_are_unverified(self):
        for results in (
            None,
            self.results(),
            self.results(["a"], complete=False),
            self.results(["a", "b"], collection_errors=["uncollected module"]),
        ):
            with self.subTest(results=results):
                _, unverified = self.judge(results)
                self.assertTrue(unverified)
        _, unverified = self.judge(None, detailed_base=False, results_expected=True)
        self.assertTrue(unverified)

    def test_an_opaque_base_does_not_allow_all_skipped_or_failed_definition_results(self):
        failures, _ = self.judge(self.results(failed=["a"]), detailed_base=False)
        self.assertTrue(failures)
        _, unverified = self.judge(self.results(skipped=["a"]), detailed_base=False)
        self.assertTrue(unverified)

    def test_preserved_per_test_evidence_and_legacy_exit_only_proofs_remain_valid(self):
        self.assertEqual(([], []), self.judge(self.results(["a", "b"])))
        self.assertEqual(([], []), self.judge(None, detailed_base=False))


if __name__ == "__main__":
    unittest.main()
