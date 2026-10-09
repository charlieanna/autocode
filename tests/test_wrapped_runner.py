"""A node:test case that runs another test runner is not named proof (#380).

Vitest exits 0 when a -t filter matches no test, so a node:test wrapper that asserts only the
exit code and the name in the output passed for a case that does not exist.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_wrapped_runner as wrapped_runner

from tests.test_verify import Project

HEADER = "const {test} = require('node:test');\nconst assert = require('node:assert/strict');\n"
CP = "const {spawnSync, execSync, execFileSync} = require('node:child_process');\n"

# The wrapper from the issue, as a run delivered it.
ISSUE_WRAPPER = HEADER + CP + """
function focused(name) {
  const r = spawnSync('npm', ['--prefix', 'frontend', 'test', '--', '--no-cache',
    'src/app/__tests__/ThinkFirstPanel.test.tsx', '-t', name], {encoding: 'utf8'});
  const out = (r.stdout || '') + (r.stderr || '');
  assert.equal(r.status, 0, out);
  assert.ok(out.includes(name), out);
}
test('test_t1_panel_opens', () => focused('test_t1_panel_opens'));
"""

WRAPPERS = {
    "issue shape": ISSUE_WRAPPER,
    "shell string": HEADER + CP + "test('test_c1', () => { execSync(`npm test -- -t \"${'test_c1'}\"`); });",
    "cd then npm": HEADER + CP + "test('test_c1', () => { execSync('cd frontend && npm test -- -t x'); });",
    "npm run test script": HEADER + CP + "test('test_c1', () => { spawnSync('npm', ['run', 'test:unit']); });",
    "npm t": HEADER + CP + "test('test_c1', () => { execSync('npm t'); });",
    "pnpm": HEADER + CP + "test('test_c1', () => { execFileSync('pnpm', ['--dir', 'web', 'test']); });",
    "yarn workspace": HEADER + CP + "test('test_c1', () => { execSync('yarn workspace web test -t x'); });",
    "npx vitest": HEADER + CP + "test('test_c1', () => { spawnSync('npx', ['--no-install', 'vitest', 'run', '-t', 'x']); });",
    "npm exec jest": HEADER + CP + "test('test_c1', () => { execSync('npm exec -- jest -t x'); });",
    "npx playwright test": HEADER + CP + "test('test_c1', () => { execSync('npx playwright test -g x'); });",
    "jest directly": HEADER + CP + "test('test_c1', () => { spawnSync('jest', ['-t', 'x']); });",
    "mocha with env": HEADER + CP + "test('test_c1', () => { execSync('CI=1 mocha --grep x'); });",
    "bin path": HEADER + CP + "const path = require('node:path');\n"
                              "test('test_c1', () => { spawnSync(path.join(__dirname, '..', 'node_modules', '.bin', 'vitest'), ['run']); });",
    "node runs vitest entry": HEADER + CP + "test('test_c1', () => { spawnSync(process.execPath, "
                                            "[require.resolve('vitest/vitest.mjs'), 'run', '-t', 'x']); });",
    "fork vitest entry": HEADER + "const {fork} = require('child_process');\n"
                         "test('test_c1', () => { fork('node_modules/vitest/vitest.mjs', ['run']); });",
    "node --test": HEADER + CP + "const node = process.execPath;\n"
                               "test('test_c1', () => { spawnSync(node, ['--test', 'tests/inner.cjs']); });",
    "node --test in shell": HEADER + CP + "test('test_c1', () => { execSync('node --test tests/inner.cjs'); });",
    "node --run test": HEADER + CP + "test('test_c1', () => { execSync('node --run test -- -t x'); });",
    "sh -c": HEADER + CP + "test('test_c1', () => { spawnSync('sh', ['-c', 'npm test -- -t x']); });",
    "command in a variable": HEADER + CP + "const NPM = process.platform === 'win32'\n  ? 'npm.cmd'\n  : 'npm';\n"
                                           "const ARGS = ['--prefix', 'frontend', 'test', '--', '-t'];\n"
                                           "test('test_c1', () => { spawnSync(NPM, [...ARGS, 'x'], {shell: true}); });",
    "shell command in a variable": HEADER + CP + "const cmd = `npx vitest run -t \"${name}\"`;\n"
                                                 "test('test_c1', () => { execSync(cmd); });",
    "promisified exec": HEADER + "const util = require('node:util');\nconst cp = require('node:child_process');\n"
                                 "const run = util.promisify(cp.exec);\n"
                                 "test('test_c1', async () => { await run('npm test -- -t x'); });",
    "renamed import": "import {test} from 'node:test';\nimport {spawnSync as sh} from 'node:child_process';\n"
                      "test('test_c1', () => { sh('yarn', ['test']); });",
    "renamed destructuring": HEADER + "const {execSync: run} = require('child_process');\n"
                             "test('test_c1', () => { run('pnpm vitest run -t x'); });",
    "member call": HEADER + "const cp = require('child_process');\n"
                   "test('test_c1', () => { cp.spawnSync('npm', ['test']); });",
    "cross-spawn": HEADER + "const spawn = require('cross-spawn');\n"
                   "test('test_c1', () => { spawn.sync('npx', ['jest', '-t', 'x']); });",
    "dynamic import": "const {test} = require('node:test');\n"
                      "test('test_c1', async () => { const cp = await import('node:child_process');"
                      " cp.execSync('npm test'); });",
}

# Tests that run the product's own CLI, or other tools, through child_process stay named proof.
ALLOWED = {
    "node cli": HEADER + CP + "test('test_c1_add', () => {\n"
                "  const r = spawnSync('node', ['cli.js', 'add', 'x'], {encoding: 'utf8'});\n"
                "  assert.equal(r.status, 0);\n});",
    "execPath cli": HEADER + CP + "const CLI = require('node:path').join(__dirname, '..', 'cli.js');\n"
                    "test('test_c1_add', () => { execFileSync(process.execPath, [CLI, 'add', 'x']); });",
    "shell cli": HEADER + CP + "test('test_c1_list', () => { assert.match(execSync('node cli.js list').toString(), /x/); });",
    "cli through npx and npm scripts": HEADER + CP + "test('test_c1', () => { spawnSync('npx', ['tsx', 'src/cli.ts', 'test']);"
                                      " execSync('npm run cli -- add x'); execSync('npx playwright install chromium'); });",
    "cli flag named --test": HEADER + CP + "test('test_c1', () => { spawnSync('node', ['cli.js', '--test']); });",
    "git, npm build and a fixture server": HEADER + CP + "test('test_c1', () => { spawnSync('git', ['init']);"
                                           " spawnSync('npm', ['run', 'build']); execSync('npm install --no-audit');"
                                           " spawn('npm', ['run', 'test-server']); });",
    # A scaffolding CLI's test: runner commands appear only in the output it checks.
    "runner names in expectations": HEADER + CP + """
test('test_c1_init_writes_scripts', () => {
  const r = spawnSync('node', ['bin/create-app.js', 'demo'], {encoding: 'utf8'});
  assert.match(r.stdout, /Run `npm test` to start/);
  const pkg = JSON.parse(require('node:fs').readFileSync('demo/package.json', 'utf8'));
  assert.equal(pkg.scripts.test, 'vitest run');
  assert.equal('npx vitest', pkg.scripts.watch);
});""",
    "regex exec": HEADER + CP + "test('test_c1', () => { const out = execSync('node cli.js').toString();"
                  " const m = /(\\d+) passed/.exec(out); assert.ok(m); });",
    "commented out": HEADER + CP + "// spawnSync('npm', ['test']);\n/* execSync('npx vitest run') */\n"
                     "test('test_c1', () => { spawnSync('node', ['cli.js']); });",
    # Without child_process the file cannot spawn a runner, whatever its strings say.
    "no child_process": HEADER + "const spawnSync = () => ({status: 0});\n"
                        "test('test_c1', () => { assert.equal(spawnSync('npm', ['test']).status, 0); });",
    "plain node:test": HEADER + "test('test_c1', () => assert.equal(1 + 1, 2));",
}


class DetectorTests(unittest.TestCase):
    def test_a_test_file_that_spawns_a_test_runner_is_recognized(self):
        for label, source in WRAPPERS.items():
            with self.subTest(label):
                self.assertTrue(wrapped_runner.spawned_runner(source), source)

    def test_a_test_file_that_runs_the_products_own_cli_is_not(self):
        for label, source in ALLOWED.items():
            with self.subTest(label):
                self.assertIsNone(wrapped_runner.spawned_runner(source))

    def test_the_finding_names_the_runner_command(self):
        self.assertIn("npm --prefix frontend test", wrapped_runner.spawned_runner(ISSUE_WRAPPER))

    def test_only_node_test_files_inside_the_tree_are_read(self):
        root = Path(tempfile.mkdtemp(prefix="wrapped-runner-"))
        self.addCleanup(shutil.rmtree, root)
        (root / "tests").mkdir()
        (root / "tests" / "wrap.cjs").write_text(ISSUE_WRAPPER)
        (root / "tests" / "cli.cjs").write_text(ALLOWED["node cli"])
        (root / "tests" / "script.cjs").write_text(CP + "spawnSync('npm', ['test']);\n")  # not node:test
        refused = wrapped_runner.refusals(root, [
            "tests/wrap.cjs::test_t1_panel_opens", "tests/wrap.cjs::group::test_t2", "tests/cli.cjs::test_c1_add",
            "tests/script.cjs::test_x", "tests/missing.cjs::test_x", "../outside.cjs::test_x",
            "tests.test_mod.Cases.test_c1", "example.com/pkg::TestC1"])
        self.assertEqual(["tests/wrap.cjs::group::test_t2", "tests/wrap.cjs::test_t1_panel_opens"], sorted(refused))
        reason = refused["tests/wrap.cjs::test_t1_panel_opens"]
        self.assertIn("tests/wrap.cjs", reason)
        self.assertIn("assert the behavior directly in node:test", reason)


class CaseMatchingTests(unittest.TestCase):
    def test_a_refused_test_never_proves_a_case_and_the_failure_says_why(self):
        # The wrapper failed on base and passes now: a flip, but of the inner runner's exit code.
        proof = {"verdict": "PASS", "failures": [], "notes": [], "pass_to_pass": [], "not_run_on_base": [],
                 "fail_to_pass": ["tests/wrap.cjs::test_c1_renamed", "tests/direct.cjs::test_c2_direct"]}
        cases = [{"id": "C1", "text": "one", "test_name": "test_c1_renamed"},
                 {"id": "C2", "text": "two", "test_name": "test_c2_direct"}]
        regression.check_cases(proof, cases, {"tests/wrap.cjs::test_c1_renamed": "tests/wrap.cjs runs vitest"})
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual({"C1": [], "C2": ["tests/direct.cjs::test_c2_direct"]}, proof["case_tests"])
        self.assertEqual(["Test case C1: one has a test named after it that cannot prove it: tests/wrap.cjs runs vitest"],
                         proof["failures"])


# A stand-in for Vitest: like the real one, a -t filter that matches no test skips every test and exits 0.
FAKE_VITEST = """const args = process.argv.slice(2);
const i = args.indexOf('-t'); const name = i >= 0 ? args[i + 1] : '';
const known = {test_c1_adds: () => require('../app.cjs')(2, 3) === 5};
if (!(name in known)) { console.log(' Tests  4 skipped (4)'); process.exit(0); }
const ok = known[name](); console.log((ok ? ' ok ' : ' FAIL ') + name); process.exit(ok ? 0 : 1);
"""
NAMED_WRAPPER = HEADER + CP + """
function focused(name) {
  const r = spawnSync('npm', ['--prefix', 'frontend', 'test', '--', '-t', name], {encoding: 'utf8'});
  const out = (r.stdout || '') + (r.stderr || '');
  assert.equal(r.status, 0, out);
  assert.ok(out.includes(name), out);
}
test('test_c4_typo_that_does_not_exist', () => focused('test_c4_typo_that_does_not_exist'));
"""
SEED = {"app.cjs": "module.exports = (a, b) => a - b;\n",
        "cli.cjs": "console.log(String(require('./app.cjs')(Number(process.argv[2]), Number(process.argv[3]))));\n",
        "frontend/package.json": json.dumps({"name": "frontend", "private": True,
                                             "scripts": {"test": "node fake-vitest.cjs"}}),
        "frontend/fake-vitest.cjs": FAKE_VITEST,
        "tests/existing.test.cjs": HEADER + "test('test_existing', () => {});\n"}
# The fix, its CLI test (child_process running the product's own CLI) and the wrapper case.
FIX = {"app.cjs": "module.exports = (a, b) => a + b;\n",
       "tests/cli.test.cjs": HEADER + CP + "test('test_c1_adds', () => {\n"
                             "  const r = spawnSync(process.execPath, ['cli.cjs', '2', '3'], {encoding: 'utf8'});\n"
                             "  assert.equal(r.stdout.trim(), '5');\n});\n",
       "frontend/__tests__/named-proof.cjs": NAMED_WRAPPER}
C1 = {"id": "C1", "criterion": "Given 2 and 3; when the CLI adds them; then it prints 5",
      "verification_method": "test: test_c1_adds", "human_review": False}
C4 = {"id": "C4", "criterion": "Given the panel; when it renders; then it still shows the title",
      "verification_method": "guard: test_c4_typo_that_does_not_exist", "human_review": False}


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm run the wrapper")
class NamedProofTests(unittest.TestCase):
    """The runner's own proof (autocode_regression.prove) on a real repository, no model."""

    def test_a_wrapper_whose_filter_matches_nothing_does_not_prove_a_guard(self):
        project = Project(SEED)
        self.addCleanup(project.close)
        project.write(FIX)
        state = {"base_commit": project.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
                 "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": [C1, C4],
                                            "milestones": [{"id": "M1"}]}}}
        run_dir = Path(tempfile.mkdtemp(prefix="wrapped-proof-"))
        self.addCleanup(shutil.rmtree, run_dir, ignore_errors=True)
        proof = regression.prove(state, project.root, run_dir)
        # The wrapper did pass, before and after the change: the stand-in Vitest ran no test and exited 0.
        self.assertIn("frontend/__tests__/named-proof.cjs::test_c4_typo_that_does_not_exist", proof["pass_to_pass"])
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C4"])
        self.assertEqual(1, len(proof["failures"]), proof["failures"])
        self.assertIn("C4", proof["failures"][0])
        self.assertIn("frontend/__tests__/named-proof.cjs", proof["failures"][0])
        self.assertIn("npm --prefix frontend test", proof["failures"][0])
        self.assertIn("assert the behavior directly in node:test", proof["failures"][0])
        # A node:test case that runs the product's own CLI through child_process still proves its case.
        self.assertEqual(["tests/cli.test.cjs::test_c1_adds"], proof["case_tests"]["C1"])

    def test_a_wrapper_the_change_leaves_alone_does_not_prove_a_guard_either(self):
        # The guard is matched against the whole suite's pass-to-pass, so the refusal must read that file too.
        project = Project({**SEED, "frontend/__tests__/named-proof.cjs": NAMED_WRAPPER})
        self.addCleanup(project.close)
        project.write({path: text for path, text in FIX.items() if path != "frontend/__tests__/named-proof.cjs"})
        state = {"base_commit": project.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
                 "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": [C1, C4],
                                            "milestones": [{"id": "M1"}]}}}
        run_dir = Path(tempfile.mkdtemp(prefix="wrapped-proof-"))
        self.addCleanup(shutil.rmtree, run_dir, ignore_errors=True)
        proof = regression.prove(state, project.root, run_dir)
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C4"])
        self.assertIn("npm --prefix frontend test", " ".join(proof["failures"]))
        self.assertEqual(["tests/cli.test.cjs::test_c1_adds"], proof["case_tests"]["C1"])


if __name__ == "__main__":
    unittest.main()
