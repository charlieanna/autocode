"""Per-test results from Mocha's built-in JSON reporter and composite npm script execution (#3467)."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_contract_identity as contracts
import autocode_mocha_tests as mocha_tests
import autocode_named_reporters as reporters
import autocode_plan_test_suite as plan_test_suite
import autocode_regression as regression
import autocode_test_cases as test_cases
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project, git

node = unittest.skipUnless(shutil.which("node"), "Node.js runs the standalone fake mocha fixture")

SRC = "'use strict';\nfunction pageCount(total, size) {\n  return Math.ceil(total / size);\n}\nmodule.exports = { pageCount };\n"
BROKEN = SRC.replace("Math.ceil(total / size)", "Math.floor(total / size)")
ONE_PAGE = SRC.replace("Math.ceil(total / size)", "Math.max(1, Math.ceil(total / size))")

TEST_HEAD = """\
'use strict';
const assert = require('node:assert');
const { pageCount } = require('../src/pager');
describe('pageCount', function () {
"""
EXACT_TEST = """\
  it('returns exact multiple', function () {
    assert.strictEqual(pageCount(10, 5), 2);
  });
"""
PARTIAL_TEST = """\
  it('counts a partial last page', function () {
    assert.strictEqual(pageCount(11, 5), 3);
  });
"""
EMPTY_TEST = """\
  it('an empty list has no pages', function () {
    assert.strictEqual(pageCount(0, 5), 0);
  });
"""
ONE_TEST = EMPTY_TEST.replace("has no pages", "still has one page").replace(", 0)", ", 1)")
TEST_TAIL = "});\n"

FAKE_MOCHA_JS = """\
#!/usr/bin/env node
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const argv = process.argv.slice(2);
let reporter = null;
let output = null;
let grep = null;
const files = [];
for (let i = 0; i < argv.length; i += 1) {
  const a = argv[i];
  if (a === '--reporter' || a === '-R') {
    reporter = argv[++i];
  } else if (a === '--reporter-option' || a === '-O') {
    const opt = argv[++i] || '';
    if (opt.startsWith('output=')) output = opt.slice('output='.length);
  } else if (a === '--grep' || a === '-g') {
    grep = new RegExp(argv[++i]);
  } else if (!a.startsWith('-')) {
    files.push(a);
  }
}

const collectedFiles = [];
for (const entry of files.length ? files : ['test']) {
  const full = path.resolve(entry);
  if (fs.existsSync(full) && fs.statSync(full).isDirectory()) {
    for (const name of fs.readdirSync(full).sort()) {
      if (name.endsWith('.test.js')) collectedFiles.push(path.join(full, name));
    }
  } else if (fs.existsSync(full)) {
    collectedFiles.push(full);
  }
}

const tests = [];
const passes = [];
const failures = [];
const pending = [];

for (const file of collectedFiles) {
  const stack = [];
  global.describe = (title, fn) => {
    stack.push(title);
    try { fn(); } finally { stack.pop(); }
  };
  global.it = (title, fn) => {
    const fullTitle = [...stack, title].join(' ');
    if (grep && !grep.test(fullTitle)) return;
    const item = { title, fullTitle, file, duration: 1, err: {} };
    tests.push(item);
    try {
      fn();
      passes.push(item);
    } catch (err) {
      failures.push({ ...item, err: { message: err && err.message ? err.message : String(err) } });
    }
  };
  global.it.skip = (title) => {
    const fullTitle = [...stack, title].join(' ');
    if (grep && !grep.test(fullTitle)) return;
    const item = { title, fullTitle, file, err: {} };
    tests.push(item);
    pending.push(item);
  };
  require(file);
}

const report = {
  stats: {
    suites: 1,
    tests: tests.length,
    passes: passes.length,
    pending: pending.length,
    failures: failures.length,
  },
  tests,
  passes,
  pending,
  failures,
};

if (reporter === 'json' && output) {
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2));
} else {
  console.log(`${passes.length} passing, ${failures.length} failing`);
}
process.exit(failures.length > 0 ? 1 : 0);
"""


def _contract(method: str, *, kind: str = "bugfix", supersedes=None) -> dict:
    row = {"id": "C1", "criterion": "pages", "verification_method": method}
    if supersedes is not None:
        row["supersedes"] = supersedes
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": kind, "acceptance_criteria": [row]}}
    return {**sealed, "hash": util.digest(sealed), "approval_status": "approved"}


class CommandAndEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / "proof.mocha.json"

    def test_command_words_and_instrumentation(self):
        self.assertEqual(
            ["npx", "--no-install", "mocha", "test/a.test.js"],
            mocha_tests.command_words("npx --no-install mocha test/a.test.js"),
        )
        self.assertIsNotNone(mocha_tests.command_words("NODE_ENV=test mocha test/a.test.js"))
        self.assertIsNotNone(mocha_tests.command_words("node node_modules/mocha/bin/mocha.js test/a.test.js"))
        for bad in ("mocha --watch", "mocha -R spec", "mocha -O output=x.json", "mocha --dry-run", "mocha; rm -rf /"):
            self.assertIsNone(mocha_tests.command_words(bad), bad)
            self.assertEqual(bad, mocha_tests.instrument(bad, self.out))
        cmd = mocha_tests.instrument("npx --no-install mocha test/a.test.js", self.out)
        self.assertIn("--reporter json --reporter-option output=", cmd)

        (self.root / "package.json").write_text(json.dumps({"scripts": {"test": "mocha test"}}))
        npm_cmd = mocha_tests.instrument("npm test --silent", self.out, self.root)
        self.assertIn("npm test --silent -- --reporter json", npm_cmd)

    def test_results_nested_pending_hook_failures_and_validation(self):
        f = str(self.root / "test/a.test.js")
        base_report = {
            "stats": {"tests": 3, "passes": 1, "failures": 2, "pending": 1},
            "tests": [
                {"title": "adds", "fullTitle": "math adds", "file": f},
                {"title": "subs", "fullTitle": "math subs", "file": f},
                {"title": "later", "fullTitle": "math later", "file": f},
            ],
            "passes": [{"title": "adds", "fullTitle": "math adds", "file": f}],
            "failures": [
                {"title": "subs", "fullTitle": "math subs", "file": f},
                {"title": '"before all" hook', "fullTitle": 'math "before all" hook', "file": f},
            ],
            "pending": [{"title": "later", "fullTitle": "math later", "file": f}],
        }
        self.out.write_text(json.dumps(base_report))
        parsed = mocha_tests.results(self.out, self.root)
        self.assertEqual(["test/a.test.js::math > adds"], parsed["passed"])
        self.assertEqual(["test/a.test.js::math > later"], parsed["skipped"])
        self.assertIn("test/a.test.js::math > subs", parsed["failed"])
        self.assertEqual(['test/a.test.js::math > "before all" hook'], parsed["collection_errors"])
        self.assertEqual([], parsed["uncollected"])

        bad = copy.deepcopy(base_report)
        bad["stats"]["tests"] = 99
        self.out.write_text(json.dumps(bad))
        self.assertIsNone(mocha_tests.results(self.out, self.root))

        outside = copy.deepcopy(base_report)
        outside["tests"][0]["file"] = str(self.root.parent / "escape.test.js")
        self.out.write_text(json.dumps(outside))
        self.assertIsNone(mocha_tests.results(self.out, self.root))


@node
class EndToEndTests(unittest.TestCase):
    def make_project(self, files: dict[str, str]) -> Project:
        project = Project({**files, "node_modules/.bin/mocha": FAKE_MOCHA_JS})
        self.addCleanup(project.close)
        (project.root / "node_modules/.bin/mocha").chmod(0o755)
        git(project.root, "add", "-A")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "runner")
        project.base = git(project.root, "rev-parse", "HEAD")
        return project

    def prove(self, seed: dict[str, str], files: dict[str, str], *methods: str, kind: str = "bugfix", supersedes=None):
        project = self.make_project(seed)
        project.write(files)
        rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
        state: dict = {"workspace": str(project.root), "base_commit": project.base, "settings": {"regression": {}}}
        state["goal_contract"] = {
            "body": {"task_kind": kind, "acceptance_criteria": rows, "open_blocking_questions": []}
        }
        if supersedes:
            rows[0]["supersedes"] = supersedes
            sealed = {"task_id": "t", "revision": 1, "body": state["goal_contract"]["body"]}
            sealed["hash"] = util.digest(sealed)
            event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(sealed)}
            state["goal_contract"] = {**sealed, "approval_status": "approved", "approval_event": event}
            state["goal_contract"]["origin"] = "planner_draft"
            state["user_events"] = [event]
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, project.root, project.evidence / "proof")

    def test_composite_script_runs_and_fails_closed_when_lint_step_fails(self):
        project = self.make_project(
            {
                "package.json": json.dumps(
                    {
                        "name": "pager",
                        "devDependencies": {"mocha": "^10.0.0"},
                        "scripts": {"test": "mocha test && node lint.js"},
                    }
                ),
                "lint.js": "process.exit(0);\n",
                "src/pager.js": SRC,
                "test/pager.test.js": TEST_HEAD + EXACT_TEST + TEST_TAIL,
            }
        )
        framework = verify.detect_framework(project.root)
        self.assertEqual("mocha", framework.name)
        self.assertTrue(reporters.reports("npm test --silent", project.root, project.root))
        receipt = verify.run_suite(
            framework, "npm test --silent", project.root, project.root / ".autocode/r1", "suite", timeout=30
        )
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertEqual(["test/pager.test.js::pageCount > returns exact multiple"], receipt["results"]["passed"])

        # Fail-closed when the companion lint step exits non-zero even though all tests passed!
        (project.root / "lint.js").write_text("process.exit(1);\n")
        failed = verify.run_suite(
            framework, "npm test --silent", project.root, project.root / ".autocode/r2", "suite", timeout=30
        )
        self.assertEqual(1, failed["exit_code"], failed)

    def test_bugfix_guard_and_supersedes_with_mocha(self):
        seed = {
            "package.json": json.dumps(
                {
                    "name": "pager",
                    "devDependencies": {"mocha": "^10.0.0"},
                    "scripts": {"test": "mocha test"},
                }
            ),
            "src/pager.js": BROKEN,
            "test/pager.test.js": TEST_HEAD + EXACT_TEST + TEST_TAIL,
        }
        check_proj = self.make_project(seed)
        self.assertIn("mocha", test_cases.TITLE_RUNNERS)
        self.assertIsNone(
            plan_test_suite.unnamed_cases(
                str(check_proj.root),
                verify.detect_framework(check_proj.root),
                [],
                _contract("test: counts a partial last page")["body"],
                lambda _d: None,
            )
        )
        proof = self.prove(
            seed,
            {"src/pager.js": SRC, "test/pager.test.js": TEST_HEAD + EXACT_TEST + PARTIAL_TEST + TEST_TAIL},
            "test: counts a partial last page",
            "guard: returns exact multiple",
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test/pager.test.js::pageCount > counts a partial last page"], proof["fail_to_pass"])
        self.assertEqual(
            {
                "AC1": ["test/pager.test.js::pageCount > counts a partial last page"],
                "AC2": ["test/pager.test.js::pageCount > returns exact multiple"],
            },
            proof["case_tests"],
        )

        # Supersedes: rename existing empty test -> works by title!
        pkg = json.dumps({"name": "pager", "devDependencies": {"mocha": "^10.0.0"}, "scripts": {"test": "mocha test"}})
        entry = {"file": "test/pager.test.js", "test": "an empty list has no pages"}
        sup_proof = self.prove(
            {
                "package.json": pkg,
                "src/pager.js": SRC,
                "test/pager.test.js": TEST_HEAD + EXACT_TEST + EMPTY_TEST + TEST_TAIL,
            },
            {"src/pager.js": ONE_PAGE, "test/pager.test.js": TEST_HEAD + EXACT_TEST + ONE_TEST + TEST_TAIL},
            "test: an empty list still has one page",
            kind="build",
            supersedes=[entry],
        )
        self.assertEqual(verify.PASS, sup_proof["verdict"], sup_proof)


if __name__ == "__main__":
    unittest.main()
