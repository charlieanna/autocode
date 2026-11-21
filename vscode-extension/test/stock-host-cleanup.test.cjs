// Exercise the real stock host lifecycle with failed setup, without launching VS Code.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { test } = require('node:test');
const vm = require('node:vm');

for (const phase of ['fixture', 'editor']) test(`stock host retains evidence and cleans only owned processes after ${phase} failure`, async () => {
  const hostFile = path.join(__dirname, 'host/editor-host.test.cjs');
  const realRequire = createRequire(hostFile);
  const calls = [];
  let setup, cleanup, root, fixtureEnvironment;
  const fakeFs = { ...fs, mkdtempSync(prefix) { root = fs.mkdtempSync(prefix); return root; } };
  const fakeChild = { spawnSync(_command, args, options) {
    if (args.some((arg) => String(arg).endsWith('provider_auth_fixture.py'))) {
      fixtureEnvironment = options.env;
      if (phase === 'fixture') return { status: 7, stdout: '', stderr: 'forced fixture failure' };
      const registry = path.join(root, 'run/registry');
      fs.mkdirSync(registry, { recursive: true });
      return { status: 0, stderr: '', stdout: JSON.stringify({
        cli: ['python', 'owned-autocode.py'], workspace: root, runDir: root,
        env: { AUTOCODE_HOME: registry }, view: { status: 'PAUSED_PROVIDER_UNCERTAIN' },
      }) };
    }
    if (args.some((arg) => String(arg).endsWith('status_retry_host_cleanup.py'))) {
      if (args.includes('identity')) return { status: 0, stderr: '', stdout: '{"started":123.5}' };
      calls.push('stop owned editor');
      return { status: 1, stdout: '', stderr: 'forced owned editor cleanup failure' };
    }
    if (args.some((arg) => String(arg).endsWith('host_reader_cleanup.py'))) {
      calls.push(args.length === 3 ? 'check unrecorded readers' : 'check owned readers');
      return { status: 0, stderr: '', stdout: '{"running":[]}' };
    }
    if (args.includes('serve')) {
      assert.equal(options.env.AUTOCODE_HOME, path.join(root, 'run/registry'));
      assert.equal(options.env.TMPDIR, path.join(root, 'tmp'));
      calls.push(args.includes('--stop') ? 'stop owned host' : 'check owned host');
      return { status: 0, stderr: '', stdout: '{"running":false}' };
    }
    throw new Error(`unexpected subprocess ${args[0]}`);
  } };
  const fakeRequire = (id) => {
    if (id === 'node:test') return { before: (body) => { setup = body; }, after: (body) => { cleanup = body; }, test() {} };
    if (id === 'node:fs') return fakeFs;
    if (id === 'node:child_process') return fakeChild;
    if (id === 'playwright-core') return { _electron: { async launch({ env }) {
      return { process: () => ({ pid: 123 }), firstWindow: async () => { throw new Error('forced editor setup failure'); },
        close: async () => { calls.push('close owned editor'); throw new Error('forced close failure'); } };
    } } };
    return realRequire(id);
  };
  const inherited = { ...process.env, AUTOCODE_HOME: '/synthetic/inherited/operator', AUTOCODE_VSCODE_EXECUTABLE: '/synthetic/code' };
  const sandbox = { require: fakeRequire, __dirname: path.dirname(hostFile), process: { ...process, env: inherited },
    console: { error() {}, log() {} }, setTimeout, clearTimeout, Buffer };
  try {
    const source = process.env.AUTOCODE_STOCK_HOST_SOURCE || hostFile;
    vm.runInNewContext(fs.readFileSync(source, 'utf8'), sandbox, { filename: hostFile });
    await assert.rejects(setup(), /forced .* failure/);
    let cleanupError;
    try { await cleanup(); } catch (error) { cleanupError = error; }
    if (phase === 'editor') assert.ok(calls.includes('stop owned host'), 'the claimed background host is explicitly stopped');
    assert.match(cleanupError?.message ?? '', /Editor test or owned cleanup failed/);
    assert.equal(fixtureEnvironment.HOME, process.env.HOME);
    assert.equal(fixtureEnvironment.CODEX_HOME, process.env.CODEX_HOME);
    assert.equal(fixtureEnvironment.AUTOCODE_HOME, path.join(root, 'run/registry'));
    assert.ok(fs.existsSync(root), 'failed fixture remains reviewable');
    if (phase === 'editor') {
      assert.deepEqual(calls, ['close owned editor', 'stop owned editor', 'check owned readers', 'stop owned host', 'check owned host', 'check unrecorded readers']);
    } else {
      assert.deepEqual(calls, ['check owned readers'], 'no host or editor is claimed before successful setup');
    }
  } finally {
    if (root) fs.rmSync(root, { recursive: true, force: true });
  }
});
