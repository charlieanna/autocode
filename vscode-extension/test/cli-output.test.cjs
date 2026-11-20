const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { editor } = require('./helpers/editor.cjs');

const host = editor();
const cli = host.load('core/cli.js');
const { activate } = host.load('extension.js');
const { RunController } = host.load('controller.js');
const options = { command: 'autocode', prefix: [] };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function children(t) {
  const rows = [];
  t.mock.method(require('node:child_process'), 'spawn', (_file, argv, settings) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.pid = 13000 + rows.length;
    child.argv = argv;
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
function finish(child) {
  child.emit('exit', 0);
  child.emit('close', 0);
}
/** A read-log sink, installed only where the export exists so a base copy fails on the missing line. */
function sink(t) {
  const lines = [];
  cli.setReadLog?.({ appendLine: (line) => lines.push(line) });
  t.after(() => cli.setReadLog?.(undefined));
  return lines;
}

const characters = [['é', [0xc3, 0xa9]], ['€', [0xe2, 0x82, 0xac]], ['😀', [0xf0, 0x9f, 0x98, 0x80]]];

test('test_ac1_split_utf8_intact_on_both_streams', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  let cases = 0;
  for (const [character, bytes] of characters) {
    const text = `a${character}b`;
    const encoded = Buffer.from(text, 'utf8');
    assert.deepEqual([...encoded.subarray(1, -1)], bytes);
    for (let cut = 1; cut < bytes.length; cut++) {
      cases += 1;
      const pieces = [encoded.subarray(0, 1 + cut), encoded.subarray(1 + cut)];
      for (const stream of ['stdout', 'stderr']) {
        const output = [];
        const calls = [
          ['stream', () => cli.stream(options, ['--resume-paused', stream, String(cut)], {}, (chunk) => output.push(chunk))],
          ['invoke', () => cli.invoke(options, ['--resume-paused', stream, String(cut)])],
          ['invokeRead', () => cli.invokeRead(options, ['--status', stream, String(cut)])],
        ];
        for (const [name, call] of calls) {
          output.length = 0;
          const result = call();
          const child = rows.at(-1);
          for (const piece of pieces) child[stream].emit('data', Buffer.from(piece));
          finish(child);
          const value = await result;
          const label = `${name} ${stream} ${JSON.stringify(character)} cut after byte ${cut}`;
          assert.equal(value.code, 0, label);
          assert.equal(value[stream], text, label);
          assert.ok(!value[stream].includes('�'), label);
          if (name === 'stream') assert.equal(output.join(''), text, `onOutput ${label}`);
        }
      }
    }
  }
  assert.equal(cases, 6, 'one case per split point inside each character');
});

test('test_ac2_invalid_utf8_decodes_as_before', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = children(t);
  const inputs = [[[0x61, 0xff, 0x62], 'a�b'], [[0x61, 0xe2, 0x82], 'a�']];
  for (const [bytes, expected] of inputs) {
    for (const stream of ['stdout', 'stderr']) {
      const output = [];
      const result = cli.stream(options, ['--resume-paused'], {}, (chunk) => output.push(chunk));
      const child = rows.at(-1);
      child[stream].emit('data', Buffer.from(bytes));
      finish(child);
      const value = await result;
      assert.equal(value[stream], expected, `${stream} ${bytes}`);
      assert.equal(output.join(''), expected, `onOutput ${stream} ${bytes}`);
    }
  }
});

test('test_ac8_slow_read_logs_phases', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
  const rows = children(t);
  const lines = sink(t);
  const slow = cli.invokeRead(options, ['--status']);
  t.mock.timers.tick(1000);
  rows[0].stdout.emit('data', Buffer.from('{}'));
  t.mock.timers.tick(5000);
  rows[0].emit('exit', 0);
  t.mock.timers.tick(100);
  rows[0].emit('close', 0);
  assert.equal((await slow).code, 0);
  assert.equal(lines.length, 1, lines.join('\n'));
  for (const part of ['--status', 'spawned 0ms', 'first output 1000ms', 'exited 6000ms', 'closed 6100ms', 'delivered 6100ms']) {
    assert.ok(lines[0].includes(part), `${part} in ${lines[0]}`);
  }

  const fast = cli.invokeRead(options, ['--status']);
  t.mock.timers.tick(10);
  rows[1].stdout.emit('data', Buffer.from('{}'));
  t.mock.timers.tick(40);
  rows[1].emit('exit', 0);
  t.mock.timers.tick(10);
  rows[1].emit('close', 0);
  assert.equal((await fast).code, 0);
  assert.equal(lines.length, 1, 'a fast read writes nothing');
});

test('test_ac9_missed_deadline_logs_phases', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
  const rows = children(t);
  const lines = sink(t);
  const late = cli.invokeRead(options, ['--status', 'late'], {}, 100).catch((error) => error);
  t.mock.timers.tick(100); await flush();
  assert.equal((await late).name, 'ReadDeadlineExceeded');
  assert.equal(lines.length, 1, lines.join('\n'));
  for (const part of ['missed its 100ms deadline', 'spawned 0ms', 'first output -', 'exited -']) {
    assert.ok(lines[0].includes(part), `${part} in ${lines[0]}`);
  }
  t.mock.timers.tick(150);
  rows[0].emit('exit', 0);
  rows[0].emit('close', 0);
  assert.equal(lines.length, 2, lines.join('\n'));
  assert.ok(lines[1].includes('exited 250ms'), lines[1]);
  assert.ok(lines[1].includes('closed 250ms'), lines[1]);
});

test('a reader that missed its deadline and exited with its pipes held open is still logged once', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
  const rows = children(t);
  const lines = sink(t);
  const late = cli.invokeRead(options, ['--status', 'held'], {}, 100).catch((error) => error);
  t.mock.timers.tick(100); await flush();
  await late;
  t.mock.timers.tick(150);
  rows[0].stdout.emit('data', Buffer.from('late'));
  rows[0].emit('exit', 0);
  assert.equal(lines.length, 1, 'the collection line waits for the pipes or the grace');
  t.mock.timers.tick(cli.EXIT_GRACE_MS);
  assert.equal(lines.length, 2, lines.join('\n'));
  for (const part of ['first output 250ms', 'exited 250ms', 'closed -', 'delivered -']) {
    assert.ok(lines[1].includes(part), `${part} in ${lines[1]}`);
  }
  rows[0].emit('close', 0);
  assert.equal(lines.length, 2, 'one collection line per reader');
});

test('a read log that throws does not fail the read it describes', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
  const rows = children(t);
  cli.setReadLog?.({ appendLine: () => { throw new Error('channel closed'); } });
  t.after(() => cli.setReadLog?.(undefined));
  const slow = cli.invokeRead(options, ['--status', 'throwing']);
  t.mock.timers.tick(6000);
  rows[0].stdout.emit('data', Buffer.from('{}'));
  finish(rows[0]);
  const value = await slow;
  assert.deepEqual({ code: value.code, stdout: value.stdout }, { code: 0, stdout: '{}' });
});

test('test_ac22_activate_wires_read_log_to_output_channel', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 0 });
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  t.mock.method(RunController.prototype, 'status', async () => ({ status: 'RUNNING', done: false, needs: { kind: 'continue' } }));
  const rows = children(t);
  const appended = t.mock.method(host.log, 'appendLine', () => {});
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const result = cli.invokeRead(options, ['--status', 'wired']);
  const child = rows.find((row) => row.argv.includes('wired'));
  t.mock.timers.tick(1000);
  child.stdout.emit('data', Buffer.from('{}'));
  t.mock.timers.tick(5000);
  child.emit('exit', 0);
  t.mock.timers.tick(100);
  child.emit('close', 0);
  assert.equal((await result).code, 0);
  const lines = appended.mock.calls.map((call) => String(call.arguments[0])).filter((line) => line.includes('--status wired'));
  assert.equal(lines.length, 1, lines.join('\n'));
  for (const part of ['spawned 0ms', 'first output 1000ms', 'exited 6000ms', 'closed 6100ms', 'delivered 6100ms']) {
    assert.ok(lines[0].includes(part), `${part} in ${lines[0]}`);
  }
});

test('a disposed extension writes no further read phases to its channel', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'], now: 0 });
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  t.mock.method(RunController.prototype, 'status', async () => ({ status: 'RUNNING', done: false, needs: { kind: 'continue' } }));
  const rows = children(t);
  const appended = t.mock.method(host.log, 'appendLine', () => {});
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  context.subscriptions.forEach((item) => item.dispose?.());
  const result = cli.invokeRead(options, ['--status', 'disposed']);
  const child = rows.find((row) => row.argv.includes('disposed'));
  t.mock.timers.tick(6000);
  finish(child);
  assert.equal((await result).code, 0);
  const lines = appended.mock.calls.map((call) => String(call.arguments[0])).filter((line) => line.includes('--status disposed'));
  assert.deepEqual(lines, []);
});
