const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');
const { editor, flush } = require('./helpers/editor.cjs');

const host = editor();
const { ConnectionsPanel } = host.load('connections.js');
const checkArgs = ['connections', 'check', '--provider', 'kilocode', '--json'];
const connection = {
  id: 'kilocode', label: 'Kilo', connection_status: 'not_checked', detail: 'Installed; not checked',
  actions: [{ id: 'check', label: 'Check connection', next_command: checkArgs }],
};
const list = { version: 1, connections: [connection] };

function complete(child, value, code = 0, stderr = '') {
  if (value !== undefined) child.stdout.emit('data', Buffer.from(JSON.stringify(value)));
  if (stderr) child.stderr.emit('data', Buffer.from(stderr));
  child.emit('exit', code); child.emit('close', code);
}

function processes(t, holdList = () => false) {
  const rows = [];
  t.mock.method(require('node:child_process'), 'spawn', (_file, argv) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
    child.pid = 17000 + rows.length; child.argv = argv; child.signals = [];
    child.kill = (signal) => { child.signals.push(signal); return true; };
    rows.push(child);
    if (argv[0] === 'connections' && argv[1] === 'list' && !holdList(child)) queueMicrotask(() => complete(child, list));
    return child;
  });
  t.after(() => {
    rows.forEach((child) => { child.emit('exit', null); child.emit('close', null); });
    host.config.clear();
  });
  return rows;
}

async function open(t, owner = new ConnectionsPanel(host.workspaceState)) {
  await owner.show();
  const panel = host.panels.at(-1);
  const dom = new JSDOM(panel.webview.html, { runScripts: 'dangerously', beforeParse(window) {
    window.acquireVsCodeApi = () => ({ postMessage: (message) => panel.webview.receive(message) });
  } });
  panel.webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  t.after(() => { panel.dispose(); dom.window.close(); });
  await flush();
  return { owner, panel, document: dom.window.document };
}

function button(ui) {
  return [...ui.document.querySelectorAll('button')].find((item) => item.textContent === 'Check connection');
}
function checks(rows) { return rows.filter((child) => child.argv[1] === 'check'); }
function checked(detail) { return { ...connection, connection_status: 'credentials_present', detail }; }

test('a named connection check can finish after 30s without killing its owned Core', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = processes(t);
  const ui = await open(t);
  button(ui).click(); await flush();
  const child = checks(rows)[0];
  assert.deepEqual(child.argv, checkArgs, 'the fresh public action is passed unchanged');
  t.mock.timers.tick(45_000); await flush();
  assert.deepEqual(child.signals, [], 'a still-owned 45s check cannot be killed at the old 30s UI deadline');
  assert.equal(button(ui).disabled, true, 'the actual panel remains busy until the read completes');
  complete(child, checked('Fresh saved credentials confirmed'));
  await flush();
  assert.ok(ui.document.body.textContent.includes('Fresh saved credentials confirmed'));
  assert.equal(button(ui).disabled, false);
  assert.equal(ui.document.querySelector('#error').textContent, '');
});

test('the 60s check deadline retains its reader, refuses duplicate checks and discards late credentials', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const rows = processes(t);
  const ui = await open(t);
  button(ui).click(); await flush();
  const child = checks(rows)[0];
  t.mock.timers.tick(59_999); await flush();
  assert.equal(ui.document.querySelector('#error').textContent, '', 'the check gets its declared metadata allowance');
  t.mock.timers.tick(1); await flush();
  assert.match(ui.document.querySelector('#error').textContent, /within 60s.*Waiting for its reader/);
  assert.deepEqual(child.signals, [], 'UI expiry sends no cancellation signal to Core');
  const before = rows.length;
  button(ui).click(); await flush();
  assert.ok(rows.length > before, 'another check still refreshes the public offer');
  assert.equal(checks(rows).length, 1, 'an expired uncollected check cannot be duplicated');
  complete(child, checked('Stale late credentials'));
  await flush();
  assert.ok(!ui.document.body.textContent.includes('Stale late credentials'));
  button(ui).click(); await flush();
  assert.equal(checks(rows).length, 2, 'natural collection permits one fresh check');
  complete(checks(rows)[1], checked('Current fresh credentials'));
  await flush();
  assert.ok(ui.document.body.textContent.includes('Current fresh credentials'));
  assert.equal(ui.document.querySelector('#error').textContent, '');
});

for (const late of ['success', 'failure']) {
  test(`a disposed Connections check cannot publish late ${late} or clear a reopened panel's busy state`, async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const rows = processes(t);
    const first = await open(t);
    button(first).click(); await flush();
    const old = checks(rows)[0];
    first.panel.dispose();
    const current = await open(t, first.owner);
    button(current)?.click(); await flush();
    const fresh = checks(rows)[1];
    if (late === 'success') complete(old, checked('Old disposed credentials'));
    else complete(old, undefined, 2, 'Old disposed failure');
    await flush();
    assert.ok(!current.document.body.textContent.includes('Old disposed'), 'late output belongs only to the closed panel');
    assert.ok(fresh, 'reopening must not inherit the disposed panel busy flag');
    assert.equal(button(current).disabled, true, 'old completion cannot clear the new check busy state');
    assert.deepEqual(old.signals, [], 'closing a panel does not cancel Core ownership');
    complete(fresh, checked('New panel credentials'));
    await flush();
    assert.ok(current.document.body.textContent.includes('New panel credentials'));
    assert.equal(button(current).disabled, false);
    assert.equal(current.document.querySelector('#error').textContent, '');
  });
}


test('a disposed action preflight cannot publish its old offer or dispatch a connection check', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let hold = false;
  const rows = processes(t, () => hold);
  const first = await open(t);
  hold = true;
  button(first).click(); await flush();
  const oldOffer = rows.at(-1);
  hold = false;
  first.panel.dispose();
  const current = await open(t, first.owner);
  button(current)?.click(); await flush();
  const fresh = checks(rows)[0];
  complete(oldOffer, { version: 1, connections: [{ ...connection, detail: 'Old disposed offer' }] });
  await flush();
  assert.ok(!current.document.body.textContent.includes('Old disposed offer'));
  assert.equal(checks(rows).length, 1, 'only the reopened panel can dispatch its fresh public check');
  assert.ok(fresh);
  assert.equal(button(current).disabled, true);
  complete(fresh, checked('Current checked offer'));
  await flush();
  assert.ok(current.document.body.textContent.includes('Current checked offer'));
});

test('a late initial list cannot overwrite a newer connection check', async (t) => {
  const rows = processes(t, () => true);
  const owner = new ConnectionsPanel(host.workspaceState);
  const opening = owner.show();
  const panel = host.panels.at(-1);
  t.after(() => panel.dispose());
  panel.webview.receive({ type: 'ready' });
  await flush();
  assert.equal(rows.length, 2);
  complete(rows[1], list);
  await flush();
  panel.webview.receive({ type: 'action', provider: 'kilocode', action: 'check' });
  await flush();
  complete(rows[2], list);
  await flush();
  complete(checks(rows)[0], checked('Fresh check'));
  await flush();
  complete(rows[0], list);
  await opening;
  await flush();
  assert.equal(panel.webview.messages.at(-1).connections[0].detail, 'Fresh check');
});

test('revealing Connections during a check does not launch a competing list', async (t) => {
  const rows = processes(t);
  const ui = await open(t);
  button(ui).click(); await flush();
  const child = checks(rows)[0];
  const count = rows.length;
  await ui.owner.show();
  assert.equal(rows.length, count);
  complete(child, checked('Checked while revealed'));
  await flush();
  assert.ok(ui.document.body.textContent.includes('Checked while revealed'));
});
