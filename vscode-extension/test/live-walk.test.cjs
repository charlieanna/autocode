// A walk of a real chat against the real `autocode` CLI: the client's own code and webview,
// every read and action a real subprocess. Set AUTOCODE_WALK_CLI (the interpreter) and
// AUTOCODE_WALK_SCRIPT (tools/autocode.py of a checkout) to run it; it is skipped otherwise.
//
// The flows the walk covers are the ones only a live chat can prove together: a reply
// that starts streaming after a finished-only thread, the running Builder's live diff, a
// confirmation dialog that parks while a person answers it elsewhere, and the registry's own
// archive and delete over real runs.
const assert = require('node:assert/strict');
const { test, afterEach, mock } = require('node:test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { editor, flush } = require('./helpers/editor.cjs');

const CLI = process.env.AUTOCODE_WALK_CLI;
const SCRIPT = process.env.AUTOCODE_WALK_SCRIPT;
const skip = !CLI || !SCRIPT ? 'set AUTOCODE_WALK_CLI and AUTOCODE_WALK_SCRIPT to walk against a real AutoCode' : false;

const host = editor();
const { RunController } = host.load('controller.js');
const { ChatView } = host.load('chat.js');
const { RunItem } = host.load('runsView.js');
const { activate } = host.load('extension.js');

// The sealed goal contract the Builder leg works under: contract_identity.sealed() of this body.
const SEALED = 'a391f94a2611978d74b9dacc0df7a4c1f1a03f3a084aebbc19cbb2096c763066';

afterEach(() => { mock.restoreAll(); host.config.clear(); });

/** Poll until `ok` or the deadline: the real CLI takes wall-clock seconds per call. */
async function waitFor(ok, ms = 15000) {
  const until = Date.now() + ms;
  while (!ok() && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 50));
  return ok();
}

/** A git workspace with three runs: one mid-Requirements-reply, one mid-Build, one paused on a bound. */
function buildWorkspace() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'walk-')));
  const git = (...args) => execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
  git('init', '-q');
  git('config', 'user.email', 'walk@test');
  git('config', 'user.name', 'Walk');
  fs.writeFileSync(path.join(root, 'greet.py'), "print('hi')\n");
  git('add', 'greet.py');
  git('commit', '-qm', 'seed');
  const run = (name) => {
    const dir = path.join(root, '.autocode', 'runs', name);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
  };
  const base = {
    version: 3, task: 'Build a greeting CLI', workspace: root,
    iteration: 1, sessions: {}, stages: [], history: [], user_events: [], turns: [],
    created_at: '2026-11-11T08:00:00Z', settings: { provider: 'fake', engine: 'opencode' },
  };
  // A Requirements reply that is still arriving: the events file grows between reads.
  const stream = run('stream');
  fs.writeFileSync(path.join(stream, 'events.jsonl'), '');
  fs.writeFileSync(path.join(stream, 'state.json'), JSON.stringify({
    ...base, status: 'RUNNING', next_stage: 'requirements_gather',
    settings: { ...base.settings, spec_first: true },
    active_stage: { stage: 'requirements_gather', events: path.join(stream, 'events.jsonl'), active: true,
      iteration: 1, output: path.join(stream, 'attempt.log') },
  }));
  // A Builder mid-step, on a sealed approved goal contract: the view names its live diff.
  const builder = run('builder');
  const body = { task_id: 't1', summary: 'Build it', open_blocking_questions: false, acceptance_criteria: [] };
  const contract = { task_id: 't1', revision: 1, body, origin: { kind: 'chat' }, hash: SEALED };
  const approval = { kind: 'goal_approval', token: `r1:${SEALED}`, actor: 'user_cli' };
  contract.approval_status = 'approved';
  contract.approval_event = approval;
  fs.writeFileSync(path.join(builder, 'state.json'), JSON.stringify({
    ...base, status: 'RUNNING', next_stage: 'builder', goal_contract: contract,
    user_events: [approval], active_stage: { stage: 'builder', active: true, iteration: 1, output: path.join(builder, 'attempt.log') },
    code_checkpoints: [],
  }));
  fs.writeFileSync(path.join(root, 'greet.py'), "print('hello, world')\n"); // the Builder's change so far
  // A run paused on its cost bound: its Resume is a person's decision, and opens a dialog.
  const paused = run('paused');
  fs.writeFileSync(path.join(paused, 'state.json'), JSON.stringify({
    ...base, status: 'PAUSED_COST_LIMIT', next_stage: 'builder',
    settings: { ...base.settings, limits: { max_cost_usd: 2 } },
    inherited_spend: { usd: 2.37 },
    recovery: { token: 'cost:current', category: 'paused' },
  }));
  const restore = run('restore');
  writeRestoreRun(root, restore);
  return { root, stream, builder, paused, restore };
}


/** A run paused at a person's request, with one saved step whose receipt matches the real tree. */
function writeRestoreRun(root, runDir) {
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import datetime, hashlib, json, pathlib, secrets, subprocess, sys
sys.path.insert(0, ${JSON.stringify(tools)})
from autocode_code_checkpoints import tree_files
from autocode_util import digest
root = ${JSON.stringify(root)}
run = ${JSON.stringify(runDir)}
seed = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
body = {"task_id": "t1", "summary": "Build it", "open_blocking_questions": False, "acceptance_criteria": []}
contract = {"task_id": "t1", "revision": 1, "body": body, "origin": {"kind": "chat"}}
contract["hash"] = digest({k: contract[k] for k in ("task_id", "revision", "body")})
token = f"r{contract['revision']}:{contract['hash']}"
approval = {"kind": "goal_approval", "token": token, "actor": "user_cli"}
contract["approval_status"] = "approved"
contract["approval_event"] = approval
receipt = {"id": "cp-1", "commit": seed, "run_dir": run, "contract_hash": contract["hash"],
           "source": {"head": seed, "files": tree_files(root, seed)}}
path = pathlib.Path(run, "cp-1.json")
path.write_text(json.dumps(receipt))
state = {
  "version": 3, "task": "Build a greeting CLI", "workspace": root,
  "status": "PAUSED_REQUESTED", "iteration": 1, "sessions": {}, "stages": [], "history": [],
  "user_events": [approval], "turns": [], "next_stage": "builder",
  "created_at": "2026-11-11T08:00:00Z", "settings": {"provider": "fake", "engine": "opencode"},
  "goal_contract": contract,
  "code_checkpoints": [{
    "id": "cp-1", "label": "Fixture step", "at": "2026-11-11T08:30:00Z", "changed_files": ["greet.py"],
    "available": True, "receipt": str(path), "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "commit": seed, "recorded_check": {"verdict": "PASS"},
  }],
}
pathlib.Path(run, "state.json").write_text(json.dumps(state, indent=2))
`], { stdio: 'pipe' });
}


/** A supervised Builder: live owner/keeper/provider processes and the supervision record naming them. */
function claimSupervision(runDir) {
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import datetime, json, pathlib, secrets, subprocess, sys
sys.path.insert(0, ${JSON.stringify(tools)})
import autocode_process as processes
run = ${JSON.stringify(runDir)}
def spawn():
    p = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return processes.identity(processes.process_table({p.pid})[p.pid])
owner, keeper, provider = spawn(), spawn(), spawn()
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
receipt = {"schema": 1, "nonce": secrets.token_hex(16), "owner": owner, "keeper": keeper, "provider": provider,
           "phase": "armed", "cause": None, "cleanup_error": None, "observed_at": now,
           "processes": [owner, keeper, provider]}
path = pathlib.Path(run, "supervision.json")
path.write_text(json.dumps(receipt))
state = json.loads(pathlib.Path(run, "state.json").read_text())
state["active_stage"]["supervision"] = {"schema": 1, "nonce": receipt["nonce"], "receipt": str(path),
                                        "owner": owner, "keeper": keeper, "provider": provider}
pathlib.Path(run, "state.json").write_text(json.dumps(state, indent=2))
`], { stdio: 'pipe' });
}

/** A live controller receipt for a run, so the view's liveness says a stage really works. */
function claimController(runDir) {
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import datetime, json, pathlib, secrets, subprocess, sys
sys.path.insert(0, ${JSON.stringify(tools)})
import autocode_process as processes
p = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
row = processes.process_table({p.pid})[p.pid]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
receipt = {
  "schema": 1, "id": secrets.token_hex(16), "identity": processes.identity(row),
  "started_at": now, "last_checkpoint_at": now, "last_stage": "builder",
  "checkpoint": secrets.token_hex(32), "disposition": "running",
  "exit_code": None, "exited_at": None, "reason": None,
}
pathlib.Path(${JSON.stringify(runDir)}, "controller-owner.json").write_text(json.dumps(receipt))
`], { stdio: 'pipe' });
}

/** A chat against the real CLI: no controller mocks at all, just the webview shell. */
async function openWalkChat(t, root, runDir) {
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  const controller = new RunController(host.log);
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const view = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  const chat = new ChatView(controller, { get: () => undefined, update: async () => {} },
    { pickWorkspace: async () => root, confirmStart: async () => true, changed() {}, selected() {} });
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(view);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.acquireVsCodeApi = () => ({ postMessage: (message) => receive(message), getState: () => undefined, setState() {} });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();
  chat.show({ workspace: root, runDir });
  await chat.refresh();          // the first real --status read lands here
  await flush();
  return { chat, dom, window: dom.window, document: dom.window.document };
}

test('walk: a reply that starts streaming shows at once and grows in place', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  const ui = await openWalkChat(t, root, stream);
  const live = () => ui.document.querySelector('#messages .message.streaming');
  assert.equal(live(), null, 'the thread starts without a provisional row');
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will"}' } }) + '\n');
  await ui.chat.refresh();
  await flush();
  assert.ok(live(), 'a reply that starts streaming after a finished-only thread is shown at once');
  assert.equal(live().querySelector('.message-content').textContent, 'Who will');
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will use it? Are you bui' } }) + '\n');
  await ui.chat.refresh();
  await flush();
  assert.equal(live().querySelector('.message-content').textContent, 'Who will use it? Are you bui',
    'the extract grows in place');
});

test("walk: the running Builder's live diff is offered and shows its real change", { skip }, async (t) => {
  const { root, builder } = buildWorkspace();
  claimController(builder);
  const ui = await openWalkChat(t, root, builder);
  const show = ui.document.querySelector('#working-diff');
  assert.ok(show, "the view names the Builder's live diff while it works");
  show.click();
  await waitFor(() => ui.document.querySelector('#app').textContent.includes('+print'));
  const text = ui.document.querySelector('#app').textContent;
  assert.ok(text.includes('1 file(s) changed'), text.slice(0, 200));
  assert.ok(text.includes("+print('hello, world')"), 'the diff is the real change against the checked-out commit');
});

test('walk: a confirmation that parks names itself in the chat until it is answered', { skip }, async (t) => {
  host.config.set('confirmPersonOnly', true);
  const { root, paused } = buildWorkspace();
  const ui = await openWalkChat(t, root, paused);
  // The bound is attached to the named action: the take button IS the raise.
  const field = ui.document.querySelector('[data-question="max_cost_usd"]');
  const take = ui.document.querySelector('#take-action');
  assert.ok(field && take, 'the bound holding the run names its own raise');
  assert.equal(take.textContent, 'Raise limit and continue');
  field.value = '5';
  field.dispatchEvent(new ui.window.Event('input'));
  let release;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => new Promise((resolve) => { release = resolve; }));
  take.click();
  // The confirmation follows a real --status read; wait for the dialog to open before looking.
  await waitFor(() => release);
  assert.ok(release, 'the confirmation dialog opened');
  const hint = () => ui.document.querySelector('#send-hint').textContent;
  assert.ok(hint().includes('answer the dialog that opened'), `the chat names the open dialog: ${hint()}`);
  release(undefined);
  await flush(); await flush();
  assert.ok(!hint().includes('answer the dialog that opened'), `the hint reverts once the dialog is answered: ${hint()}`);
});

test('walk: archiving and deleting a chat run the named registry commands and stop the status line', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  const home = path.join(root, 'registry-home');
  process.env.AUTOCODE_HOME = home;
  t.after(() => { delete process.env.AUTOCODE_HOME; });
  execFileSync(CLI, [SCRIPT, 'registry', 'import', root], { env: process.env, stdio: 'pipe' });
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  const controller = new RunController(host.log);
  const first = await controller.list();
  // A running chat has no cleanup to name (the core refuses it); the paused one does.
  const pausedRow = first.runs.find((row) => row.runDir.endsWith('paused'));
  assert.ok(pausedRow, 'the imported chat is in the real registry list');
  assert.ok((pausedRow.actions || []).some((a) => a.id === 'archive'), 'the registry names its cleanup');
  assert.equal((first.runs.find((row) => row.runDir.endsWith('builder')) || {}).actions?.length || 0, 0,
    'a running chat offers no cleanup');

  // Archive: the exact command the row named, and the list reflects it.
  await controller.registryAction({ workspace: pausedRow.workspace, runDir: pausedRow.runDir },
    pausedRow.actions.find((a) => a.id === 'archive'));
  // The default list hides archived chats; the archived list marks them.
  assert.equal((await controller.list()).runs.find((row) => row.runDir.endsWith('paused')), undefined,
    'an archived chat is hidden from the default list');
  const after = await controller.list(true);
  const archived = after.runs.find((row) => row.runDir.endsWith('paused'));
  assert.equal(archived && archived.archived, true, 'the archived list marks it');

  // Delete through the extension's own cleanup: its confirmation, the real command, and the
  // status line that must stop naming a chat that no longer exists.
  let statusVisible = false;
  const mockStatus = { show() { statusVisible = true; }, hide() { statusVisible = false; }, dispose() {},
    text: '', tooltip: '', command: undefined };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const deleteRow = archived;
  const item = new RunItem(deleteRow, deleteRow.workspace);
  await host.commands.get('autocode.openRun')(item);
  await waitFor(() => statusVisible);
  assert.equal(statusVisible, true, 'the status line follows the opened chat');
  t.mock.method(host.vscode.window, 'showWarningMessage', async (question, _options, button) => {
    assert.match(question, /Delete the chat/, 'the delete asks first');
    return button;
  });
  await host.commands.get('autocode.deleteRun')(item);
  await waitFor(() => !statusVisible);
  assert.equal(statusVisible, false, 'the status line stops following a chat that no longer exists');
  const gone = (await controller.list()).runs.find((row) => row.runDir.endsWith('paused'));
  assert.equal(gone, undefined, 'the deleted chat is gone from the real registry');
});

test('walk: a saved step compares for real and its restore is named, confirmed and taken', { skip }, async (t) => {
  const { root, restore } = buildWorkspace();
  const ui = await openWalkChat(t, root, restore);
  const compare = ui.document.querySelector('[data-checkpoint-action="compare"]');
  assert.ok(compare, 'the saved step offers its compare');
  compare.click();
  // Wait for the compare's own diff (the row's counts appear without it).
  await waitFor(() => ui.document.querySelector('#app').textContent.includes('+print'));
  // The buttons re-render when the action ends; click the live, enabled one.
  await waitFor(() => {
    const btn = ui.document.querySelector('[data-checkpoint-action="restore"]');
    return Boolean(btn && !btn.disabled);
  });
  const restoreBtn = ui.document.querySelector('[data-checkpoint-action="restore"]');
  assert.ok(restoreBtn && !restoreBtn.disabled, 'the saved step offers its restore');
  let release;
  t.mock.method(host.vscode.window, 'showWarningMessage', (question, _options, button) => {
    assert.match(question, /Restore "Fixture step"/, 'the dialog names the step');
    return new Promise((resolve) => { release = (answer) => resolve(answer); });
  });
  restoreBtn.click();
  await waitFor(() => release);
  assert.ok(release, 'the restore confirmation opened');
  const hint = () => ui.document.querySelector('#send-hint').textContent;
  assert.ok(hint().includes('answer the dialog that opened'), `the chat names the open dialog: ${hint()}`);
  const before = execFileSync('git', ['-C', root, 'branch', '--list'], { encoding: 'utf8' });
  release('Restore');
  const done = await waitFor(() => ui.document.querySelector('#app').textContent.includes('Restored on a new branch'));
  if (!done) console.error('DEBUG app tail:', ui.document.querySelector('#app').textContent.slice(-400), '| errors:', host.errors);
  const after = execFileSync('git', ['-C', root, 'branch', '--list'], { encoding: 'utf8' });
  if (after === before) console.error('DEBUG branches:', JSON.stringify(after));
  assert.notEqual(after, before, 'the restore put the saved step on a real branch');
});

test('walk: closing a chat mid-reply returns every poll to the ordinary interval', { skip }, async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const { root, stream, paused } = buildWorkspace();
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will"}' } }) + '\n');
  const ui = await openWalkChat(t, root, stream);
  ui.chat.show({ workspace: root, runDir: paused });
  // Real reads take wall-clock seconds; drain on the call count, never on a mocked timer.
  const cli = host.load('core/cli.js');
  const counted = t.mock.method(cli, 'readStatus');
  const reads = () => counted.mock.callCount();
  const drain = async (ok, ms = 20000) => {
    const until = Date.now() + ms;
    while (!ok() && Date.now() < until) await new Promise((resolve) => setImmediate(resolve));
    return ok();
  };
  // Real reads take wall-clock seconds on a busy machine: wait for the count to stop
  // moving, never for a fixed pause.
  const settle = async () => {
    let last = -1;
    while (last !== reads()) { last = reads(); await drain(() => false, 800); }
  };
  // The reply grows again: an unchanged extract would quiet the pace back to the ordinary
  // interval after a few reads, which is the core's rule, not the bug being walked here.
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will use it?"} ' } }) + '\n');
  // A cycle still in flight from the setup would swallow the tick's refresh (the joinable
  // refresh is right); wait for idle and take one settled cycle that sees the growth.
  await drain(() => !ui.chat.refreshing);
  await ui.chat.refresh();
  await settle();
  const start = reads();
  t.mock.timers.tick(250);
  await drain(() => reads() > start);
  assert.ok(reads() > start, 'a growing reply polls at the streaming pace');
  // The streaming chat's tab is closed while its reply still grows.
  [...ui.document.querySelectorAll('.chat-tab .close')][0].click();
  await drain(() => !ui.chat.refreshing);
  await ui.chat.refresh();                   // a settled cycle after the close
  await settle();                            // and the counts stop moving
  t.mock.timers.tick(250); await settle();
  const settled = reads();
  t.mock.timers.tick(250);
  await settle();
  assert.equal(reads(), settled, 'a closed chat must not hold every poll at the streaming pace');
  t.mock.timers.tick(5000);
  await drain(() => reads() > settled);
  assert.ok(reads() > settled, 'the ordinary interval still polls the chat that is open');
});

test("walk: the Builder's live log streams and its named diff is still offered", { skip }, async (t) => {
  const { root, builder } = buildWorkspace();
  claimSupervision(builder);
  const events = path.join(builder, 'events.jsonl');
  const row = (item) => JSON.stringify({ type: 'item.updated', item }) + '\n';
  fs.writeFileSync(events,
    row({ type: 'command_execution', id: 'c1', command: 'python3 -m unittest -q', status: 'done' })
    + row({ type: 'file_change', id: 'f1', changes: [{ path: path.join(root, 'greet.py'), kind: 'edit' }], status: 'done' }));
  const statePath = path.join(builder, 'state.json');
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  state.active_stage.events = events;
  fs.writeFileSync(statePath, JSON.stringify(state));
  const ui = await openWalkChat(t, root, builder);
  const text = () => ui.document.querySelector('#app').textContent;
  await waitFor(() => text().includes('python3 -m unittest -q'));
  assert.ok(text().includes('edited greet.py'), 'the log shows what the Builder did: ' + text().slice(0, 200));
  assert.ok(text().includes('Builder'), 'the log names its agent');
  // The point of the walk: the named diff is offered WHILE the log streams (the log once
  // took the early return that skipped the diff offer entirely).
  const show = ui.document.querySelector('#working-diff');
  assert.ok(show, "the Builder's named diff is offered while its log streams");
  // The log grows while it works.
  fs.appendFileSync(events, row({ type: 'tool_call', id: 't1', name: 'Read', arguments: { path: 'greet.py' }, status: 'running' }));
  await ui.chat.refresh();
  await waitFor(() => text().includes('Read greet.py'));
  assert.ok(text().includes('reading files'), 'the header says what it does now');
  show.click();
  await waitFor(() => text().includes('+print'));
  assert.ok(text().includes("+print('hello, world')"), 'the diff is the real change against the checked-out commit');
});

test('walk: a CLI that dies mid-reply leaves the reply standing, says why, and recovers', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will"}' } }) + '\n');
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  let statusVisible = false;
  const mockStatus = { show() { statusVisible = true; }, hide() { statusVisible = false; }, dispose() {},
    text: '', tooltip: '', command: undefined };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const chat = host.providers.get('autocode.chat');
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.acquireVsCodeApi = () => ({ postMessage: (message) => receive(message), getState: () => undefined, setState() {} });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  await host.commands.get('autocode.openRun')({ workspace: root, runDir: stream });
  const text = () => dom.window.document.querySelector('#app').textContent;
  const live = () => dom.window.document.querySelector('#messages .message.streaming .message-content');
  await waitFor(() => live() && live().textContent === 'Who will');
  await waitFor(() => statusVisible);
  assert.ok(mockStatus.text.includes('AutoCode'), 'the status line follows the chat: ' + mockStatus.text);

  // The CLI dies mid-reply: the run's state stops reading (a half-written state.json).
  const statePath = path.join(stream, 'state.json');
  const saved = fs.readFileSync(statePath, 'utf8');
  fs.writeFileSync(statePath, '{ not json');
  await waitFor(() => text().includes('read the latest status'), 30000);
  assert.ok(text().includes('I couldn'), 'the chat says it could not read the status');
  assert.equal(live().textContent, 'Who will', 'the reply that was arriving is left standing');
  const take = dom.window.document.querySelector('#take-action');
  const diff = dom.window.document.querySelector('#working-diff');
  assert.ok((!take || take.disabled) && (!diff || diff.disabled), 'actions are paused while the status cannot be read');
  await waitFor(() => mockStatus.text.includes('status unavailable'), 30000);
  assert.ok(mockStatus.text.includes('status unavailable'), 'the status line says so too: ' + mockStatus.text);

  // The CLI comes back: the reply resumes growing and both surfaces recover.
  fs.writeFileSync(statePath, saved);
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will use it?"} ' } }) + '\n');
  await waitFor(() => live() && live().textContent.startsWith('Who will use it?'), 30000);
  assert.ok(!text().includes('read the latest status'), 'the notice clears when the status reads again');
  await waitFor(() => statusVisible && !mockStatus.text.includes('status unavailable'), 30000);
  assert.ok(mockStatus.text.includes('AutoCode'), 'the status line is back on the run: ' + mockStatus.text);
});

test('walk: the status line keeps its liveness without the poll interval, and goes loud when reads are slow', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  host.config.set('pollSeconds', 1);
  t.mock.timers.enable({ apis: ['setInterval'] });   // the poll interval is silenced: the round-14 wart
  let statusText = '';
  const mockStatus = {
    show() {}, hide() {}, dispose() {},
    get text() { return statusText; }, set text(val) { statusText = val; },
    tooltip: undefined, command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  await host.commands.get('autocode.openRun')({ workspace: root, runDir: stream });
  await waitFor(() => statusText.includes('AutoCode'), 30000);

  // The run changes with no interval driving the line: the line's own watchdog keeps it live.
  const statePath = path.join(stream, 'state.json');
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  state.inherited_spend = { usd: 5 };
  fs.writeFileSync(statePath, JSON.stringify(state));
  await waitFor(() => statusText.includes('$5'), 30000);
  assert.ok(statusText.includes('$5'), 'the line tracks the run with the poll interval silenced: ' + statusText);

  // Now the CLI itself is slow (a wedged read takes longer than the line's threshold):
  // the line must say it is stale rather than present the old content as current.
  const slow = path.join(root, 'slow-cli.sh');
  fs.writeFileSync(slow, '#!/bin/sh\nsleep 3\nexec "$@"\n');
  fs.chmodSync(slow, 0o755);
  host.config.set('cliPath', slow);
  host.config.set('cliArgs', [CLI, SCRIPT]);
  await waitFor(() => statusText.includes('stale'), 30000);
  assert.ok(statusText.includes('stale'), 'the line goes loud while a read is slow: ' + statusText);

  // And it comes back to the run when the reads land again.
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  await waitFor(() => statusText.includes('$5') && !statusText.includes('stale'), 30000);
  assert.ok(statusText.includes('$5'), 'the line recovers: ' + statusText);
});

test("walk: two runs racing on one project see each other's work, and a restore leaves the tree alone", { skip }, async (t) => {
  const { root, builder, restore } = buildWorkspace();
  claimSupervision(builder);
  // One project, one tree: A (the saved step) changed greet.py, B (the Builder) wrote helper.py.
  fs.writeFileSync(path.join(root, 'greet.py'), "print('A changed this')\n");
  fs.writeFileSync(path.join(root, 'helper.py'), "print('B wrote this')\n");
  const ui = await openWalkChat(t, root, restore);
  const text = () => ui.document.querySelector('#app').textContent;

  // A's compare against its saved step names BOTH runs' work: one tree, every diff sees it all.
  ui.document.querySelector('[data-checkpoint-action="compare"]').click();
  await waitFor(() => text().includes('+print'));
  assert.ok(text().includes('helper.py'), "A's changes since its checkpoint include B's file: " + text().slice(-200));

  // B's own diff, in its tab, names A's work just the same.
  ui.chat.show({ workspace: root, runDir: builder });
  await ui.chat.refresh();
  const show = await waitFor(() => {
    const btn = ui.document.querySelector('#working-diff');
    return btn && !btn.disabled ? btn : false;
  });
  assert.ok(show, "B's diff is offered while it works");
  show.click();
  await waitFor(() => text().includes('+print'));
  assert.ok(text().includes('greet.py') || text().includes("A changed this"),
    "B's changes so far include A's file: " + text().slice(-200));

  // A restores while B builds: the candidate lands on its own branch and the tree stays put.
  ui.chat.show({ workspace: root, runDir: restore });
  await ui.chat.refresh();
  await waitFor(() => {
    const btn = ui.document.querySelector('[data-checkpoint-action="restore"]');
    return btn && !btn.disabled ? btn : false;
  });
  let release;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => new Promise((resolve) => { release = resolve; }));
  ui.document.querySelector('[data-checkpoint-action="restore"]').click();
  await waitFor(() => release);
  release('Restore');
  await waitFor(() => text().includes('Restored on a new branch'));
  const branches = execFileSync('git', ['-C', root, 'branch', '--list'], { encoding: 'utf8' });
  assert.ok(branches.includes('autocode/checkpoint-'), 'the candidate is on its own branch: ' + branches.trim());
  const head = execFileSync('git', ['-C', root, 'branch', '--show-current'], { encoding: 'utf8' }).trim();
  assert.equal(head, 'master', "the restore leaves the shared checkout where it was: B's tree is untouched");
  assert.ok(fs.existsSync(path.join(root, 'helper.py')), "B's file survives the restore");
});
