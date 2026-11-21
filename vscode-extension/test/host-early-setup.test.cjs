// Execute the real host callback with a failed fixture and stubbed CLI calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { test } = require('node:test');
const vm = require('node:vm');

test('failed quota fixture never stops an inherited operator host', async () => {
  const hostFile = path.join(__dirname, 'host/job-route-effort.test.cjs');
  const realRequire = createRequire(hostFile);
  const calls = [];
  let callback, fixtureRoot, fixtureEnvironment;
  const fakeFs = { ...fs, mkdtempSync(prefix) { fixtureRoot = fs.mkdtempSync(prefix); return fixtureRoot; } };
  const fakeChild = { spawnSync(_command, args, options) {
    if (args.some((value) => String(value).endsWith('job_route_vscode_fixture.py'))) {
      fixtureEnvironment = options.env;
      return { status: 7, stdout: '', stderr: 'simulated fixture setup failure' };
    }
    if (args.includes('serve')) {
      calls.push({ operation: args.includes('--stop') ? 'stop' : 'check', registry: options.env.AUTOCODE_HOME });
      return { status: 0, stdout: '{"running":false}', stderr: '' };
    }
    throw new Error(`unexpected subprocess ${args[0]}`);
  } };
  const fakeRequire = (id) => {
    if (id === 'node:test') return { test: (_name, _options, body) => { callback = body; } };
    if (id === 'node:fs') return fakeFs;
    if (id === 'node:child_process') return fakeChild;
    return realRequire(id);
  };
  const inherited = '/synthetic/inherited/operator';
  const isolatedProcess = { ...process, env: { ...process.env, AUTOCODE_HOME: inherited } };
  const sandbox = { require: fakeRequire, __dirname: path.dirname(hostFile), process: isolatedProcess,
    console: { error() {} }, setTimeout, clearTimeout, Buffer };
  vm.runInNewContext(fs.readFileSync(hostFile, 'utf8'), sandbox, { filename: hostFile });
  assert.equal(typeof callback, 'function');
  try {
    await assert.rejects(callback(), /fixture setup failure|offline quota fixture|Editor test or owned cleanup failed/);
    assert.deepEqual(calls, [], 'setup failure must not invoke inherited-host cleanup');
    assert.equal(fixtureEnvironment.AUTOCODE_HOME, path.join(fixtureRoot, 'registry'));
    assert.equal(fixtureEnvironment.HOME, process.env.HOME);
    assert.equal(fixtureEnvironment.CODEX_HOME, process.env.CODEX_HOME);
    assert.ok(fs.existsSync(fixtureRoot), 'failure evidence remains available');
  } finally {
    if (fixtureRoot) fs.rmSync(fixtureRoot, { recursive: true, force: true });
  }
});
