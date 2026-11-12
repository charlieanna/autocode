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
  return { root, stream, builder, paused };
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
