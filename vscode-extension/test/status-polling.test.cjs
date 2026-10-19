const assert = require('node:assert/strict');
const { test, afterEach, mock } = require('node:test');
const { editor, flush } = require('./helpers/editor.cjs');

const host = editor();
const cli = host.load('core/cli.js');
const { RunController } = host.load('controller.js');
const run = { workspace: '/ws', runDir: '/ws/.autocode/runs/test' };
const ok = { code: 0, stdout: '', stderr: '', rejected: false };
const view = (status) => ({ status, done: false, needs: { kind: 'continue' } });

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// The preceding client used fresh status() for polling. Keeping that fallback
// makes the concurrency regression fail by assertion on the preceding runtime.
const poll = (controller, target = run) => typeof controller.pollStatus === 'function'
  ? controller.pollStatus(target) : controller.status(target);

function reader(t) {
  const reads = [];
  const called = t.mock.method(cli, 'readStatus', (settings, workspace, runDir) => {
    const result = deferred();
    reads.push({ ...result, settings, workspace, runDir });
    return result.promise;
  });
  return { reads, called };
}

afterEach(() => { mock.restoreAll(); host.config.clear(); });

test('polls share only an in-flight status and read again after success', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const first = poll(controller), second = poll(controller), third = poll(controller);
  assert.equal(reads.length, 1, 'simultaneous surfaces/ticks must share one CLI read');
  reads[0].resolve(view('FIRST'));
  assert.deepEqual((await Promise.all([first, second, third])).map((v) => v.status), ['FIRST', 'FIRST', 'FIRST']);
  const next = poll(controller);
  assert.equal(reads.length, 2, 'a completed status must never be cached');
  reads[1].resolve(view('NEXT'));
  assert.equal((await next).status, 'NEXT');
});

test('polls keep different runs and CLI settings independent', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const other = { workspace: run.workspace, runDir: '/ws/.autocode/runs/other' };
  const first = poll(controller), otherRead = poll(controller, other);
  reads[1].resolve(view('OTHER'));
  assert.equal((await otherRead).status, 'OTHER');
  host.config.set('cliPath', 'python3');
  host.config.set('cliArgs', ['/other/core.py']);
  const changed = poll(controller);
  const sharedChanged = poll(controller);
  assert.equal(reads.length, 3, 'different run or executable/argv cannot share a read');
  assert.equal(reads[2].settings.command, 'python3');
  assert.deepEqual(reads[2].settings.prefix, ['/other/core.py']);
  reads[2].resolve(view('NEW SETTINGS'));
  assert.equal((await changed).status, 'NEW SETTINGS');
  assert.equal((await sharedChanged).status, 'NEW SETTINGS');
  reads[0].resolve(view('OLD SETTINGS'));
  await flush();
  assert.equal(reads.length, 4, 'the old settings result must not land after a configuration change');
  reads[3].resolve(view('CURRENT SETTINGS'));
  assert.equal((await first).status, 'CURRENT SETTINGS');
});

test('a rejected shared poll rejects all waiters and permits the next read', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const first = poll(controller), second = poll(controller);
  const failed = Promise.all([assert.rejects(first, /transport failed/), assert.rejects(second, /transport failed/)]);
  assert.equal(reads.length, 1);
  reads[0].reject(new Error('transport failed'));
  await failed;
  const retry = poll(controller);
  assert.equal(reads.length, 2, 'rejection must remove the pending entry');
  reads[1].resolve(view('RECOVERED'));
  assert.equal((await retry).status, 'RECOVERED');
});

for (const [setting, value] of [['cliPath', 'other-autocode'], ['cliArgs', ['/other/core.py']]]) {
  test(`poll request identity independently includes ${setting}`, async (t) => {
    const controller = new RunController(host.log);
    const { reads } = reader(t);
    const old = poll(controller);
    host.config.set(setting, value);
    const changed = poll(controller), duplicate = poll(controller);
    assert.equal(reads.length, 2, 'changed CLI options need their own read; identical options share');
    reads[1].resolve(view('CHANGED'));
    assert.equal((await changed).status, 'CHANGED');
    assert.equal((await duplicate).status, 'CHANGED');
    reads[0].resolve(view('OLD'));
    await flush();
    assert.equal(reads.length, 3);
    reads[2].resolve(view('CURRENT'));
    assert.equal((await old).status, 'CURRENT');
  });
}

test('the same run-dir string in another workspace has an independent poll', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const first = poll(controller);
  const second = poll(controller, { ...run, workspace: '/other-workspace' });
  assert.equal(reads.length, 2);
  reads[0].resolve(view('FIRST'));
  reads[1].resolve(view('SECOND'));
  assert.deepEqual((await Promise.all([first, second])).map((v) => v.status), ['FIRST', 'SECOND']);
});

test('action preflight status is fresh rather than joined to a pending poll', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const pending = poll(controller);
  const preflight = controller.status(run);
  assert.equal(reads.length, 2);
  reads[1].resolve({ ...view('AWAITING_GOAL_APPROVAL'), needs: { kind: 'approve_plan', token: 'new-token' } });
  assert.equal((await preflight).needs.token, 'new-token');
  reads[0].resolve(view('OLDER POLL'));
  await pending;
});

for (const kind of ['take', 'message', 'pause', 'restore']) {
  test(`poll started before ${kind} cannot return its old status after the mutation`, async (t) => {
    const controller = new RunController(host.log);
    const old = deferred(), current = deferred(), action = deferred();
    const checkpoint = { ...view('PAUSED'), code_checkpoints: { rows: [{ id: 'checkpoint-1', actions: [{
      id: 'restore', next_command: ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir,
        '--restore', 'checkpoint-1', '--expected-token', '-', '--request-id', 'REQUEST_ID'],
    }] }] } };
    const pause = { ...view('RUNNING'), pause: { requested: false, next_command: [
      'intervention', 'submit', '--workspace', run.workspace, '--run-dir', run.runDir,
      '--request-id', 'REQUEST_ID', '--kind', 'pause', '--json',
    ] } };
    let calls = 0;
    t.mock.method(cli, 'readStatus', () => {
      calls++;
      if (calls === 1) return old.promise;
      if ((kind === 'pause' || kind === 'restore') && calls === 2) return Promise.resolve(kind === 'pause' ? pause : checkpoint);
      return current.promise;
    });
    const launch = t.mock.method(cli, kind === 'pause' || kind === 'restore' ? 'invoke' : 'stream', () => action.promise);
    const pending = poll(controller);
    const mutate = kind === 'take' ? controller.take(view('RUNNING'), run)
      : kind === 'message' ? controller.sendMessage({ ...view('PAUSED'), chat: {
        context: 'shown', input: { kind: 'feedback', next_command: 'autocode --feedback MESSAGE' },
      } }, run, 'exact feedback', 'shown')
      : kind === 'pause' ? controller.pause(run, 'pause-1')
      : controller.restoreCheckpoint(run, 'checkpoint-1', 'restore-token', 'restore-1');
    await flush();
    assert.equal(launch.mock.callCount(), 1);
    action.resolve(kind === 'restore' ? { ...ok, stdout: '{}' } : ok);
    await mutate;
    old.resolve(view('OBSOLETE'));
    await flush();
    const expectedCalls = kind === 'pause' || kind === 'restore' ? 3 : 2;
    assert.equal(calls, expectedCalls, 'late poll must make/join a post-mutation read');
    const together = poll(controller);
    assert.equal(calls, expectedCalls, 'the replacement read is itself shared');
    current.resolve(view('AFTER ACTION'));
    assert.equal((await pending).status, 'AFTER ACTION');
    assert.equal((await together).status, 'AFTER ACTION');
  });
}

test('a poll begun during an action is refreshed after completion; other runs are not invalidated', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const action = deferred();
  t.mock.method(cli, 'stream', () => action.promise);
  const other = { workspace: run.workspace, runDir: '/ws/.autocode/runs/other' };
  const otherRead = poll(controller, other);
  const taking = controller.take(view('RUNNING'), run);
  const during = poll(controller);
  assert.equal(reads.length, 2);
  action.resolve(ok); await taking;
  reads[0].resolve(view('OTHER'));
  assert.equal((await otherRead).status, 'OTHER');
  assert.equal(reads.length, 2, 'mutation is scoped to its run');
  reads[1].resolve(view('DURING'));
  await flush();
  assert.equal(reads.length, 3);
  reads[2].resolve(view('AFTER'));
  assert.equal((await during).status, 'AFTER');
});

test('a rejected action also invalidates older polls without swallowing its error', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const pending = poll(controller);
  const failure = new Error('action rejected');
  t.mock.method(cli, 'stream', async () => { throw failure; });
  await assert.rejects(controller.take(view('RUNNING'), run), (error) => error === failure);
  reads[0].resolve(view('BEFORE REJECTION'));
  await flush();
  assert.equal(reads.length, 2);
  reads[1].resolve(view('AFTER REJECTION'));
  assert.equal((await pending).status, 'AFTER REJECTION');
});

test('settings A to B to A retires superseded requests without a promise cycle', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const first = poll(controller);
  host.config.set('cliPath', 'other-autocode');
  const second = poll(controller);
  reads[0].resolve(view('OLD A'));
  await flush();
  assert.equal(reads.length, 2, 'old A joins the in-flight B request');
  host.config.delete('cliPath');
  reads[1].resolve(view('OLD B'));
  await flush();
  assert.equal(reads.length, 3, 'returning to A requires a new read, never joining old A');
  reads[2].resolve(view('CURRENT A'));
  assert.deepEqual((await Promise.all([first, second])).map((v) => v.status), ['CURRENT A', 'CURRENT A']);
});

test('a superseded poll error cannot replace the post-action view with an old read failure', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const pending = poll(controller);
  t.mock.method(cli, 'stream', async () => ok);
  await controller.take(view('RUNNING'), run);
  reads[0].reject(new Error('old transport failure'));
  await flush();
  assert.equal(reads.length, 2);
  reads[1].resolve(view('FRESH BOUNDARY'));
  assert.equal((await pending).status, 'FRESH BOUNDARY');
});

test('a current read error after invalidation is still propagated', async (t) => {
  const controller = new RunController(host.log);
  const { reads } = reader(t);
  const pending = poll(controller);
  t.mock.method(cli, 'stream', async () => ok);
  await controller.take(view('RUNNING'), run);
  const rejected = assert.rejects(pending, /current transport failure/);
  reads[0].reject(new Error('old transport failure'));
  await flush();
  reads[1].reject(new Error('current transport failure'));
  await rejected;
});
