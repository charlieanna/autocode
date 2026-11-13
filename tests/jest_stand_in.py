"""A stand-in for Jest that a test project carries in its own ``node_modules``, run by Node.

It runs the given test files, or every ``tests/*.test.js``, in one process with ``describe``, ``test`` and
``expect(...).toBe/toThrow``, prints a summary, and with ``--json --outputFile=FILE`` writes the fields of
Jest's JSON report that AutoCode reads (autocode_jest_tests), shaped as Jest 30.5.2 writes them. The proof
runs it as the project's ``npm test`` and as ``npx --no-install jest FILE``, exactly as it runs Jest.
"""

from __future__ import annotations

from .test_verify import Project, git

RUNNER = r"""#!/usr/bin/env node
'use strict';
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const out = (args.find((arg) => arg.startsWith('--outputFile=')) || '').slice('--outputFile='.length);
let files = args.filter((arg) => !arg.startsWith('-'));
if (!files.length) {
  files = fs.readdirSync('tests').filter((name) => name.endsWith('.test.js')).sort().map((name) => path.join('tests', name));
}
const groups = [];
let current;
global.describe = (name, body) => { groups.push(name); body(); groups.pop(); };
global.test = global.it = (title, body) => {
  let status = 'passed';
  try { body(); } catch (error) { status = 'failed'; console.error(`  x ${title}: ${error.message}`); }
  current.assertionResults.push({ ancestorTitles: [...groups], title, fullName: [...groups, title].join(' '), status });
};
global.test.skip = (title) => {
  current.assertionResults.push({ ancestorTitles: [...groups], title, fullName: [...groups, title].join(' '), status: 'pending' });
};
global.expect = (actual) => ({
  toBe(expected) { if (actual !== expected) throw new Error(`expected ${expected}, received ${actual}`); },
  toThrow(kind) {
    try { actual(); } catch (error) { if (!kind || error instanceof kind) return; throw error; }
    throw new Error('expected a throw');
  },
});
const results = [];
for (const file of files) {
  current = { name: path.resolve(file), status: 'passed', message: '', assertionResults: [] };
  try { require(path.resolve(file)); } catch (error) { current.status = 'failed'; current.message = String(error); }
  if (current.assertionResults.some((test) => test.status === 'failed')) current.status = 'failed';
  results.push(current);
}
const all = results.flatMap((file) => file.assertionResults);
const failed = all.filter((test) => test.status === 'failed').length;
const success = results.every((file) => file.status === 'passed');
if (args.includes('--json') && out) {
  fs.writeFileSync(out, JSON.stringify({
    numFailedTests: failed,
    numPassedTests: all.filter((test) => test.status === 'passed').length,
    numPendingTests: all.filter((test) => test.status === 'pending').length,
    numTotalTestSuites: results.length,
    numTotalTests: all.length,
    success,
    wasInterrupted: false,
    testResults: results,
  }));
}
console.log(`Tests: ${failed} failed, ${all.length} total`);
process.exit(success ? 0 : 1);
"""
PACKAGE = (
    '{\n  "name": "pager",\n  "private": true,\n  "scripts": { "test": "jest" },\n'
    '  "devDependencies": { "jest": "30.5.2" }\n}\n'
)
FILES = {
    "package.json": PACKAGE,
    "node_modules/jest/bin/jest.js": RUNNER,
    "node_modules/.bin/jest": "#!/usr/bin/env node\nrequire('../jest/bin/jest.js');\n",
}


def project(files) -> Project:
    """A committed project of ``files`` whose ``npm test`` and ``npx --no-install jest`` run the stand-in."""
    made = Project({**FILES, **files})
    (made.root / "node_modules/.bin/jest").chmod(0o755)
    git(made.root, "add", "-A")
    git(made.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "runner")
    made.base = git(made.root, "rev-parse", "HEAD")
    return made
