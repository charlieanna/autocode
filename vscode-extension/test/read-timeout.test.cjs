const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { editor } = require('./helpers/editor.cjs');

const host = editor();
const cli = host.load('core/cli.js');
const { RunController } = host.load('controller.js');
const options = { command: 'autocode', prefix: [] };
const run = { workspace: '/owned', runDir: '/owned/.autocode/runs/read-timeout' };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
// A base copy must fail on behavior rather than the absence of the new helper.
const read = (...args) => (cli.invokeRead ?? cli.invoke)(...args);

function children(t) {
  const rows = [];
  t.mock.method(require('node:child_process'), 'spawn', (_file, _argv, settings) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.pid = 12000 + rows.length;
    child.kills = [];
    child.settings = settings;
    child.kill = (signal) => { child.kills.push(signal); return true; };
    rows.push(child);
    return child;
  });
  t.after(() => {
    for (const child of rows) { child.emit('exit', null); child.emit('close', null); }
    host.config.clear();
  });
  return rows;
}
function complete(child, value = {}) {
  child.stdout.emit('data', Buffer.from(JSON.stringify(value)));
  child.emit('exit', 0);
  child.emit('close', 0);
}

test('a timed-out poll retains reader admission for polls and fresh action preflights', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  const controller = new RunController(host.log);
  const result = controller.pollStatus(run).catch((error) => error);
  t.mock.timers.tick(cli.STATUS_TIMEOUT_MS - 1);
  let landed = false;
  void result.then(() => { landed = true; });
  await flush();
  assert.equal(landed, false, 'the UI deadline is still exactly 30s');
  t.mock.timers.tick(1); await flush();
  const timeout = await result;
  assert.match(timeout.message, /did not finish|timed out/);
  const poll = controller.pollStatus(run).catch((error) => error);
  const preflight = controller.status(run).catch((error) => error);
  await flush();
  assert.equal(rows.length, 1, 'an uncollected reader must not be replaced by another poll or action preflight');
  assert.equal(await poll, timeout);
  assert.equal(await preflight, timeout);
  assert.equal(timeout.name, 'ReadDeadlineExceeded');
  assert.deepEqual(rows[0].kills, [], 'a UI deadline cannot destroy the remote completion chain');
  rows[0].stdout.emit('data', Buffer.from(JSON.stringify({ view: { status: 'STALE' } })));
  rows[0].emit('exit', 0); rows[0].emit('close', 0);
  await timeout.collected;
  const next = controller.status(run);
  assert.equal(rows.length, 2, 'after collection the preflight really executes a fresh read');
  complete(rows[1], { view: { status: 'AWAITING_GOAL_APPROVAL', needs: { kind: 'approve_plan', token: 'new-token' } } });
  assert.equal((await next).needs.token, 'new-token', 'late timed-out output never grants old approval authority');
});

test('healthy action preflights stay concurrent and older collection cannot release another expired lease', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  const first = read(options, ['--status'], {}, 100).catch((error) => error);
  const second = read(options, ['--status'], {}, 100).catch((error) => error);
  assert.equal(rows.length, 2, 'healthy reads are fresh, not a completed or pending view cache');
  t.mock.timers.tick(100); await flush();
  const [old, current] = await Promise.all([first, second]);
  rows[0].emit('exit', 0); rows[0].emit('close', 0);
  const held = read(options, ['--status'], {}, 100).catch((error) => error);
  await flush();
  assert.equal(rows.length, 2, 'the older exit must not retire the newer expired reader');
  assert.equal(await held, current);
  await old.collected;
  rows[1].emit('exit', 0); rows[1].emit('close', 0);
  await current.collected;
  const fresh = read(options, ['--status'], {}, 100);
  assert.equal(rows.length, 3);
  complete(rows[2], { current: true });
  assert.deepEqual(JSON.parse((await fresh).stdout), { current: true });
});

test('read admission binds executable, prefix, cwd, effective environment and arguments', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  const base = { ...options, cwd: '/owned', env: { FIXTURE_A: 'a', FIXTURE_B: 'b' } };
  const expired = read(base, ['--status', 'one'], {}, 100).catch((error) => error);
  t.mock.timers.tick(100); await flush();
  const timeout = await expired;
  const same = read({ ...base, env: { FIXTURE_B: 'b', FIXTURE_A: 'a' } }, ['--status', 'one'], {}, 100).catch((error) => error);
  await flush(); assert.equal(rows.length, 1, 'environment order cannot bypass the same launch identity');
  assert.equal(await same, timeout);
  const variants = [
    [{ ...base, command: 'other-autocode' }, ['--status', 'one']],
    [{ ...base, prefix: ['other.py'] }, ['--status', 'one']],
    [{ ...base, cwd: '/other' }, ['--status', 'one']],
    [{ ...base, env: { ...base.env, FIXTURE_A: 'different' } }, ['--status', 'one']],
    [base, ['--status', 'other-run']],
  ];
  for (const [settings, argv] of variants) {
    const result = read(settings, argv, {}, 100);
    complete(rows.at(-1));
    assert.equal((await result).code, 0);
  }
  assert.equal(rows.length, 6, 'unrelated exact launch identities remain available');
});

test('close-only collection releases a read, without pretending that timeout killed it', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  const expired = read(options, ['--status', 'close-only'], {}, 100).catch((error) => error);
  t.mock.timers.tick(100); await flush();
  const timeout = await expired;
  assert.deepEqual(rows[0].kills, []);
  rows[0].emit('close', null);
  await timeout.collected;
  const fresh = read(options, ['--status', 'close-only'], {}, 100);
  assert.equal(rows.length, 2);
  complete(rows[1]); await fresh;
});

// Replaces "the read deadline stays strict when an exited child leaves inherited pipes open" (#3850):
// a child that exited before its deadline has a known result, delivered at close or grace end.
test('test_ac23_exit_then_close_after_deadline_resolves_at_close', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  const watch = (promise) => {
    const state = { outcome: 'pending' };
    state.done = promise.then((value) => { state.outcome = 'resolved'; return value; },
      (error) => { state.outcome = 'rejected'; throw error; });
    return state;
  };

  const result = watch(cli.invokeRead(options, ['--status', 'exited'], {}, 100));
  t.mock.timers.tick(99);
  rows[0].stdout.emit('data', Buffer.from('{}'));
  rows[0].emit('exit', 0);
  t.mock.timers.tick(1); await flush();
  assert.equal(result.outcome, 'pending', 'at the deadline an exited read has neither resolved nor rejected');
  t.mock.timers.tick(50);
  rows[0].emit('close', 0); await flush();
  const value = await result.done;
  assert.equal(value.code, 0);
  assert.equal(value.stdout, '{}', 'the close delivers the output, long before the grace alone would end at 2099 ms');
  assert.deepEqual(rows[0].kills, []);
  const fresh = cli.invokeRead(options, ['--status', 'exited'], {}, 100);
  assert.equal(rows.length, 2, 'the exited reader no longer holds admission');
  complete(rows[1]); await fresh;

  const action = watch(cli.invoke(options, ['--resume-paused'], {}, 100));
  t.mock.timers.tick(99);
  rows[2].stdout.emit('data', Buffer.from('{}'));
  rows[2].emit('exit', 0);
  t.mock.timers.tick(1); await flush();
  assert.equal(action.outcome, 'pending', 'at the deadline an exited action has neither resolved nor rejected');
  t.mock.timers.tick(50);
  rows[2].emit('close', 0); await flush();
  const done = await action.done;
  assert.deepEqual({ code: done.code, stdout: done.stdout, rejected: done.rejected }, { code: 0, stdout: '{}', rejected: false });
  assert.deepEqual(rows[2].kills, [], 'an action that exited is never killed');
});

// A real Node child that writes its JSON and exits 0 while a grandchild inherits and holds its pipes.
function pipeHolder(t) {
  const childProcess = require('node:child_process');
  const spawn = childProcess.spawn;
  const rows = [];
  t.mock.method(childProcess, 'spawn', (...args) => {
    const child = spawn(...args);
    child.holder = '';
    child.stderr.on('data', (chunk) => { child.holder += chunk; });
    child.exited = new Promise((resolve) => child.once('exit', resolve));
    child.wrote = new Promise((resolve) => child.stdout.once('data', resolve));
    child.signals = t.mock.method(child, 'kill', () => true);
    rows.push(child);
    return child;
  });
  // The grandchild ends itself after 10 s at the latest; the test ends it as soon as it is done.
  t.after(() => {
    for (const child of rows) {
      const pid = Number(child.holder);
      if (pid > 0) { try { process.kill(pid, 'SIGKILL'); } catch { /* already gone */ } }
      child.stdout.destroy(); child.stderr.destroy();
    }
  });
  const script = [
    "const { spawn } = require('node:child_process');",
    "const holder = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 10000)'], { stdio: ['ignore', 'inherit', 'inherit'] });",
    'holder.unref();',
    'process.stderr.write(String(holder.pid));',
    "process.stdout.write('{\"ok\":true}');",
  ].join('\n');
  return { rows, launch: { command: process.execPath, prefix: ['-e', script] } };
}

test('test_ac3_read_exit_before_deadline_resolves', { timeout: 10000 }, async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { rows, launch } = pipeHolder(t);
  let outcome = 'pending';
  const result = cli.invokeRead(launch, [], {}, 1000)
    .then((value) => { outcome = 'resolved'; return value; }, (error) => { outcome = 'rejected'; return error; });
  await Promise.all([rows[0].exited, rows[0].wrote]);
  t.mock.timers.tick(1000); await flush();
  assert.equal(outcome, 'pending', 'no ReadDeadlineExceeded for a reader that already exited');
  t.mock.timers.tick(cli.EXIT_GRACE_MS); await flush();
  const value = await result;
  assert.equal(outcome, 'resolved', String(value && value.message));
  assert.equal(value.code, 0);
  assert.equal(value.stdout, '{"ok":true}');
  assert.equal(rows[0].signals.mock.callCount(), 0, 'kill is never called on an exited reader');
});

test('test_ac4_action_exit_before_deadline_resolves', { timeout: 10000 }, async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { rows, launch } = pipeHolder(t);
  let outcome = 'pending';
  const result = cli.invoke(launch, [], {}, 1000)
    .then((value) => { outcome = 'resolved'; return value; }, (error) => { outcome = 'rejected'; return error; });
  await Promise.all([rows[0].exited, rows[0].wrote]);
  t.mock.timers.tick(1000); await flush();
  assert.equal(outcome, 'pending', 'no ActionDeadlineExceeded for an action that already exited');
  t.mock.timers.tick(cli.EXIT_GRACE_MS); await flush();
  const value = await result;
  assert.equal(outcome, 'resolved', String(value && value.message));
  assert.doesNotMatch(String(value.message ?? ''), /stopped, so its result is unknown/);
  assert.deepEqual({ code: value.code, stdout: value.stdout, rejected: value.rejected }, { code: 0, stdout: '{"ok":true}', rejected: false });
  assert.equal(rows[0].signals.mock.callCount(), 0, 'a known successful action is never killed');
});

test('test_ac5_running_action_at_deadline_is_killed', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  const result = cli.invoke(options, ['--resume-paused'], {}, 100).catch((error) => error);
  t.mock.timers.tick(100); await flush();
  const error = await result;
  assert.equal(error.name, 'ActionDeadlineExceeded');
  assert.equal(error.message, 'AutoCode did not finish within 1s and was stopped, so its result is unknown.');
  assert.deepEqual(rows[0].kills, ['SIGKILL']);
});

test('no stdout or stderr callback leaks after timeout or successful settlement', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t); const output = [];
  const failed = cli.stream(options, ['--status'], {}, (chunk) => output.push(chunk), 2000, 100).catch((error) => error);
  t.mock.timers.tick(100); await flush();
  assert.match((await failed).message, /did not finish within/);
  rows[0].stdout.emit('data', Buffer.from('late stdout'));
  rows[0].stderr.emit('data', Buffer.from('late stderr'));
  rows[0].emit('error', new Error('late error'));
  assert.deepEqual(output, [], 'timeout settlement ends the output contract');
  const success = cli.stream(options, ['--resume-paused'], {}, (chunk) => output.push(chunk));
  complete(rows[1]); await success;
  const before = [...output];
  rows[1].stdout.emit('data', Buffer.from('after close'));
  rows[1].emit('error', new Error('after close'));
  assert.deepEqual(output, before, 'late events cannot revive a completed action either');
});

test('a read which could not start releases admission without a phantom child hold', async (t) => {
  const rows = children(t);
  const failed = read(options, ['--status', 'not-started'], {}, 100).catch((error) => error);
  rows[0].pid = undefined;
  rows[0].emit('error', Object.assign(new Error('missing'), { code: 'ENOENT' }));
  assert.match((await failed).message, /could not start/);
  const fresh = read(options, ['--status', 'not-started'], {}, 100);
  assert.equal(rows.length, 2);
  complete(rows[1]); await fresh;
});

test('ordinary advancing actions keep their launch and completed-action pipe grace', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t); let settled = false;
  const result = cli.stream(options, ['--resume-paused'], {}, undefined, 2000).then((value) => { settled = true; return value; });
  rows[0].emit('exit', 2);
  t.mock.timers.tick(1999); await flush(); assert.equal(settled, false);
  t.mock.timers.tick(1); await flush(); assert.equal((await result).code, 2);
  assert.deepEqual(rows[0].kills, []);
  assert.equal(rows[0].settings.detached, undefined);
});

test('an actual local read stays alive at the UI deadline and releases admission only after natural exit', { timeout: 10000 }, async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const childProcess = require('node:child_process');
  const spawn = childProcess.spawn;
  const rows = []; const ready = [];
  t.mock.method(childProcess, 'spawn', (...args) => {
    const child = spawn(...args);
    rows.push(child);
    ready.push(new Promise((resolve) => child.stdout.once('data', resolve)));
    return child;
  });
  t.after(() => rows.forEach((child) => child.stdin.end()));
  const script = "process.stdout.write('ready\\n'); process.stdin.resume(); process.stdin.on('end', () => process.stdout.end('finished'));";
  const launch = { command: process.execPath, prefix: ['-e', script] };
  const expired = read(launch, [], {}, 100).catch((error) => error);
  await ready[0];
  const signals = t.mock.method(rows[0], 'kill', (...args) => Reflect.apply(require('node:child_process').ChildProcess.prototype.kill, rows[0], args));
  t.mock.timers.tick(100); await flush();
  const timeout = await expired;
  assert.equal(signals.mock.callCount(), 0, 'the UI deadline sends no local or remote cancellation signal');
  assert.equal(rows[0].exitCode, null, 'the actual reader is still running at its read deadline');
  const denied = read(launch, [], {}, 100).catch((error) => error);
  assert.equal(await denied, timeout);
  assert.equal(rows.length, 1, 'no replacement process is admitted');
  rows[0].stdin.end();
  await timeout.collected;
  const fresh = read(launch, [], {}, 100);
  assert.equal(rows.length, 2);
  await ready[1];
  rows[1].stdin.end();
  assert.equal((await fresh).code, 0, 'the next fresh reader finishes naturally');
});
