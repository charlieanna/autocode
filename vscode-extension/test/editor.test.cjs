const assert = require('node:assert/strict');
const { test, afterEach, mock } = require('node:test');
const { JSDOM } = require('jsdom');
const { editor, flush } = require('./helpers/editor.cjs');

const host = editor();
const cli = host.load('core/cli.js');
const { RunController } = host.load('controller.js');
const { RunPanel, panelHtml } = host.load('panel.js');
const { ChatView } = host.load('chat.js');
const { RunItem, RunsProvider } = host.load('runsView.js');
const { activate } = host.load('extension.js');
const { ConnectionsPanel, connectionsHtml } = host.load('connections.js');
const controller = new RunController(host.log);
const run = { workspace: '/ws', runDir: '/ws/.autocode/runs/test' };
const ok = { code: 0, stdout: '', stderr: '', rejected: false };

const connectionRows = [
  { id: 'kilocode', label: 'Kilo', connection_status: 'not_checked', detail: 'Installed', actions: [
    { id: 'login', label: 'Sign in / Reconnect', next_command: ['connections', 'login', '--provider', 'kilocode'] },
    { id: 'check', label: 'Check connection', next_command: ['connections', 'check', '--provider', 'kilocode', '--json'] },
  ] },
  { id: 'company', label: 'Company', connection_status: 'not_checked', detail: 'Installed', actions: [] },
];

test('AutoCode setup is offered without Kilo and opens only the core setup terminal', async (t) => {
  const direct = { id: 'autocode', label: 'AutoCode · Direct connection', connection_status: 'setup_required',
    detail: 'No external coding agent required', actions: [{ id: 'setup', label: 'Set up AutoCode', next_command: ['connections', 'setup'] }] };
  t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: JSON.stringify({ version: 1, connections: [direct] }) }));
  const terminal = t.mock.method(host.vscode.window, 'createTerminal', () => ({ show() {} }));
  const connections = new ConnectionsPanel(host.workspaceState);
  t.after(() => connections.dispose());
  await connections.show();
  const panel = host.panels.at(-1);
  panel.webview.receive({ type: 'select', provider: 'autocode' }); await flush();
  assert.ok(panel.webview.messages.at(-1).error.includes('Finish'));
  panel.webview.receive({ type: 'action', provider: 'autocode', action: 'setup' }); await flush();
  const options = terminal.mock.calls[0].arguments[0];
  assert.equal(options.shellPath, 'autocode');
  assert.deepEqual(options.shellArgs, ['connections', 'setup']);
  assert.equal(host.config.get('provider'), undefined, 'setup does not silently replace the selected backend');
});

test('connections delegates login using only the core offer and no shell text', async (t) => {
  const calls = [];
  t.mock.method(cli, 'invoke', async (_settings, argv) => {
    calls.push(argv);
    return { ...ok, stdout: JSON.stringify({ version: 1, connections: connectionRows }) };
  });
  const terminal = t.mock.method(host.vscode.window, 'createTerminal', (options) => ({ show() {}, options }));
  host.config.set('cliPath', '/python path/python'); host.config.set('cliArgs', ['/core path/autocode.py']);
  const connections = new ConnectionsPanel(host.workspaceState);
  t.after(() => connections.dispose());
  await connections.show();
  const panel = host.panels.at(-1);
  panel.webview.receive({ type: 'action', provider: 'company', action: 'login', next_command: ['evil'] });
  await flush();
  assert.equal(terminal.mock.callCount(), 0, 'unoffered login must not run');
  panel.webview.receive({ type: 'action', provider: 'kilocode', action: 'login', next_command: ['evil'] });
  await flush();
  assert.equal(terminal.mock.callCount(), 1);
  const options = terminal.mock.calls[0].arguments[0];
  assert.equal(options.shellPath, '/python path/python');
  assert.deepEqual(options.shellArgs, ['/core path/autocode.py', 'connections', 'login', '--provider', 'kilocode']);
  assert.ok(calls.every((args) => args.join(' ') === 'connections list --json'), 'login is not captured as a noninteractive call');
});

test('connection switching restores Kilo routes and cannot reroute an existing chat', async (t) => {
  t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: JSON.stringify({ version: 1, connections: connectionRows }) }));
  host.config.set('provider', 'kilocode');
  const routes = ['--requirements-model', 'zai-coding-plan/glm-5.3', '--astra-model=xiaomi-token-plan-sgp/mimo-v2.6-pro', '--pin-model-role', 'astra', '--role-transport', 'terra=companytool', '--allow-uncontained-tools'];
  host.config.set('extraOptions', ['--max-cost-usd', '3', ...routes]);
  const storage = new Map();
  const connections = new ConnectionsPanel({ get: (key) => storage.get(key), update: async (key, value) => { storage.set(key, value); } });
  t.after(() => connections.dispose());
  await connections.show();
  const panel = host.panels.at(-1);
  panel.webview.receive({ type: 'select', provider: 'company' }); await flush();
  assert.equal(host.config.get('provider'), 'company');
  assert.deepEqual(host.config.get('extraOptions'), ['--max-cost-usd', '3']);
  const prepared = controller.prepare({ status: 'RUNNING', needs: { kind: 'continue' } }, run, {});
  assert.deepEqual(prepared.argv, ['--no-chat'], 'saved run must not inherit the picker backend or new options');
  host.config.set('extraOptions', ['--max-cost-usd', '5']);
  panel.webview.receive({ type: 'select', provider: 'kilocode' }); await flush();
  assert.equal(host.config.get('provider'), 'kilocode');
  assert.deepEqual(host.config.get('extraOptions'), ['--max-cost-usd', '5', ...routes]);
});

test('toolbar buttons are icons with accessible names and unchanged ids', async (t) => {
  const ui = await openChat(t, chatView());
  for (const [id, label] of [['connections', 'Connections'], ['history', 'History'], ['details', 'Run details'], ['new-chat', 'New chat']]) {
    const button = ui.document.getElementById(id);
    assert.ok(button, `#${id} is missing`);
    assert.equal(button.getAttribute('aria-label'), label);
    assert.equal(button.textContent.trim(), '', `#${id} must render no text`);
    assert.ok(button.querySelector('svg'), `#${id} has no icon`);
  }
});

test('engine chip shows the engine new chats will use and opens the picker', async (t) => {
  host.config.set('extraOptions', ['--engine', 'codex', '--max-cost-usd', '3']);
  const ui = await openChat(t, chatView());
  assert.equal(ui.document.getElementById('engine-name').textContent, 'codex');
  const picker = t.mock.method(host.vscode.window, 'showQuickPick', async (items) => items.find((item) => item.engine === 'codex'));
  ui.document.getElementById('engine-picker').click();
  await flush();
  assert.equal(ui.sent.at(-1).type, 'engine');
  const [items, options] = picker.mock.calls[0].arguments;
  assert.deepEqual(items.map((item) => item.label), ['OpenCode', 'Codex', 'Claude Code', 'Connections…', 'Configure in Settings…']);
  assert.ok(options.placeHolder.includes('Codex'), 'names the current engine');
});

test('picking an engine applies the CLI consistency rules to new-chat settings', async (t) => {
  const ui = await openChat(t, chatView());
  const pick = async (engine) => {
    t.mock.method(host.vscode.window, 'showQuickPick', async (items) => items.find((item) => item.engine === engine));
    ui.document.getElementById('engine-picker').click();
    await flush();
  };
  host.config.set('provider', 'kilocode');
  host.config.set('extraOptions', ['--engine', 'claude', '--requirements-model', 'zai-coding-plan/glm-5.3']);
  await pick('codex');
  assert.deepEqual(host.config.get('extraOptions'), ['--requirements-model', 'zai-coding-plan/glm-5.3', '--engine', 'codex']);
  assert.equal(host.config.get('provider'), '', 'codex refuses a provider');
  await pick('claude');
  assert.deepEqual(host.config.get('extraOptions'), ['--requirements-model', 'zai-coding-plan/glm-5.3', '--engine', 'claude']);
  assert.equal(host.config.get('provider'), 'claude', 'claude is the built-in Claude Code connection');
  await pick('opencode');
  assert.equal(host.config.get('provider'), 'claude', 'opencode leaves the chosen backend alone');
});

test('connections renders backend text safely and sends only provider and action IDs', () => {
  const sent = [];
  const dom = new JSDOM(connectionsHtml(), { runScripts: 'dangerously', beforeParse(window) {
    window.acquireVsCodeApi = () => ({ postMessage: (message) => sent.push(message) });
  } });
  try {
    const row = { ...connectionRows[0], label: '<img src=x onerror=alert(1)>', detail: '<script>evil()</script>' };
    dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data: { connections: [row], selected: 'company' } }));
    assert.equal(dom.window.document.querySelectorAll('img').length, 0);
    assert.ok(dom.window.document.querySelector('main').textContent.includes('<script>evil()</script>'));
    [...dom.window.document.querySelectorAll('button')].find((button) => button.textContent === 'Sign in / Reconnect').click();
    assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1))), { type: 'action', provider: 'kilocode', action: 'login' });
  } finally { dom.window.close(); }
});

test('status polling uses the core-recognized read-only command grammar', async (t) => {
  const { EventEmitter } = require('node:events');
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  const spawn = t.mock.method(require('node:child_process'), 'spawn', () => {
    setImmediate(() => { child.stdout.emit('data', Buffer.from('{"view":{"status":"RUNNING"}}')); child.emit('close', 0); });
    return child;
  });
  const result = await cli.readStatus({ command: 'python3', prefix: ['/core/autocode.py'] }, run.workspace, run.runDir, JSON.parse);
  assert.equal(result.view.status, 'RUNNING');
  assert.deepEqual(spawn.mock.calls[0].arguments[1], ['/core/autocode.py', '--status', '--workspace', run.workspace, '--run-dir', run.runDir]);
  assert.equal(spawn.mock.calls[0].arguments[2].shell, false);
});

afterEach(() => {
  RunPanel.disposeAll();
  mock.restoreAll();
  host.config.clear();
  host.panels.length = 0;
  host.errors.length = 0;
});

test('start passes the selected workspace to the CLI regardless of the host directory', async (t) => {
  host.config.set('provider', 'chosen-route');
  const revealLog = t.mock.method(host.log, 'show', () => {});
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const selected = '/different workspace/project';
  const created = await controller.start(selected, 'Build the requested CLI');
  const [options, args] = launch.mock.calls[0].arguments;
  assert.equal(args[args.indexOf('--workspace') + 1], selected);
  assert.equal(options.cwd, selected);
  assert.equal(args[args.indexOf('--new-run-dir') + 1], created.runDir);
  assert.ok(created.runDir.startsWith(`${selected}/.autocode/runs/`));
  assert.equal(args[args.indexOf('--provider') + 1], 'chosen-route');
  assert.ok(args.includes('--joint-planning'));
  assert.ok(args.includes('--spec-first'), 'chat must gather and confirm requirements before planning');
  assert.equal(revealLog.mock.callCount(), 0, 'starting chat must not open the diagnostic console');
});

test('start reports an input rejection instead of returning a nonexistent run', async (t) => {
  t.mock.method(cli, 'stream', async () => ({ ...ok, code: 2, rejected: true, stderr: 'Input rejected: invalid destination' }));
  await assert.rejects(controller.start('/ws', 'A task'), /invalid destination/);
});

test('tree actions use the clicked run with no active run and with another run active', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  t.mock.method(RunController.prototype, 'status', async () => ({ status: 'RUNNING', done: false, needs: { kind: 'continue' } }));
  const take = t.mock.method(RunController.prototype, 'take', async () => ok);
  const show = t.mock.method(ChatView.prototype, 'show', () => {});
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const other = { ...run, runDir: '/ws/.autocode/runs/other' };
  const row = new RunItem({ ...other, id: 'other', availability: 'available', status: 'RUNNING' }, other.workspace);

  await host.commands.get('autocode.takeAction')(row);
  assert.deepEqual(show.mock.calls.at(-1).arguments[0], other);
  await host.commands.get('autocode.openRun')(run);
  await host.commands.get('autocode.takeAction')(row);
  assert.deepEqual(show.mock.calls.at(-1).arguments[0], other);
  await host.commands.get('autocode.openRun')(run);
  await host.commands.get('autocode.continueRun')(row);
  assert.deepEqual(take.mock.calls.at(-1).arguments[1], other);
  await flush();
});

function approvalView() {
  return {
    status: 'AWAITING_GOAL_APPROVAL', done: false,
    needs: { kind: 'approve_plan', token: 'r1:abc' },
    // Public displayed_plan shape from tests/test_taskrun.py, including fields
    // which the original string-array fixture accidentally hid from the renderer.
    displayed_plan: {
      revision: 1, hash: 'abc', token: 'r1:abc',
      acceptance_criteria: [
        { id: 'AC1', criterion: 'Preserve user data <script>bad()</script>', verification_method: 'Run migration test', human_review: true },
        { id: 'AC2', criterion: 'Keep export stable', verification_method: 'Compare export fixture', human_review: false },
      ],
      constraints: ['Never delete the original data'],
      permission_boundaries: ['Only edit app.py'],
    },
    next_command: `AUTOCODE_APPROVE_GOAL_TOKEN=r1:abc autocode --workspace ${run.workspace} --run-dir ${run.runDir} --approve-goal -`,
  };
}

function questionView(model = false) {
  return {
    status: 'PAUSED', done: false, turn: 1,
    needs: {
      kind: 'answer', request_id: 'request-1', resolver_token: 'resolver-1',
      questions: [{ id: 'q1', question: 'Which behavior?', options: [], proposed_default: null }],
      ...(model ? { route: { question_id: 'q1', role: 'Builder', candidates: ['model-a', 'model-b'] } } : {}),
    },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --answer q1=${model ? 'MODEL' : 'ANSWER'}`,
  };
}

async function openPanel(t, initial) {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let view = initial;
  t.mock.method(controller, 'status', async () => structuredClone(view));
  const panel = RunPanel.show(1, run, controller);
  const webview = host.panels.at(-1).webview;
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  const messages = [];
  dom.window.acquireVsCodeApi = () => ({ postMessage: (message) => { messages.push(message); webview.receive(message); } });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await panel.refresh();
  t.after(() => dom.window.close());
  return {
    document: dom.window.document, window: dom.window, messages,
    take: () => [...dom.window.document.querySelectorAll('button')].find((button) => button.textContent !== 'Refresh'),
    async poll(next = view) { view = next; t.mock.timers.tick(5000); await flush(); },
    setView(next) { view = next; },
  };
}

test('approval renders structured criteria, verification, human review, constraints and permissions as text', async (t) => {
  const ui = await openPanel(t, approvalView());
  const text = ui.document.body.textContent;
  for (const expected of ['AC1', 'Preserve user data <script>bad()</script>', 'Run migration test', 'Human review: required',
    'AC2', 'Compare export fixture', 'Human review: not required', 'Never delete the original data', 'Only edit app.py']) {
    assert.ok(text.includes(expected), `missing approval content: ${expected}`);
  }
  const headings = [...ui.document.querySelectorAll('h2')].map((node) => node.textContent);
  assert.ok(headings.includes('Plan to approve · revision 1'), headings.join(' | '));
  assert.ok(!text.includes('bound to'));
  assert.ok(!text.includes('[object Object]'));
  assert.equal(ui.document.querySelectorAll('script').length, 1, 'model text must not become markup');
  assert.equal(ui.take().disabled, false);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.take().click();
  await flush();
  assert.equal(launch.mock.callCount(), 1);
  const [, args, env] = launch.mock.calls[0].arguments;
  assert.equal(env.AUTOCODE_APPROVE_GOAL_TOKEN, 'r1:abc');
  assert.ok(!args.includes('r1:abc'));
});

for (const surface of ['chat', 'Run details']) {
  test(`the approved local commit is visible in ${surface}`, async (t) => {
    const view = approvalView();
    view.displayed_plan.git_delivery = { kind: 'atomic_commit', message: 'Add JSON export' };
    const ui = surface === 'chat' ? await openChat(t, chatView(view)) : await openPanel(t, view);
    assert.ok(ui.document.body.textContent.includes('Create one local atomic commit: Add JSON export'));
  });
}

test('approval stays disabled for missing, malformed or stale displayed plans', async (t) => {
  const base = approvalView();
  const ui = await openPanel(t, { ...base, displayed_plan: undefined });
  assert.equal(ui.take().disabled, true);
  await ui.poll({ ...base, displayed_plan: { ...base.displayed_plan, acceptance_criteria: ['not a structured criterion'] } });
  assert.equal(ui.take().disabled, true);
  await ui.poll({ ...base, displayed_plan: { ...base.displayed_plan, constraints: undefined } });
  assert.equal(ui.take().disabled, true);
  await ui.poll({ ...base, needs: { kind: 'approve_plan', token: 'r2:def' } });
  assert.equal(ui.take().disabled, true);
});

test('chat recovery retry requires confirmation and keeps the exact job token out of argv', async (t) => {
  const retry = {
    status: 'PAUSED_JOB_FAILURE', done: false,
    needs: { kind: 'retry_job', reason: 'The Investigator stopped unexpectedly', job_retry_token: 'jr:exact-secret' },
    next_command: `AUTOCODE_JOB_RETRY_TOKEN=jr:exact-secret autocode --resume-paused --retry-failed-stage --job-retry-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
  };
  const ui = await openChat(t, retry);
  const prompt = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const action = ui.document.querySelector('#take-action');
  assert.equal(action.disabled, false);

  action.click();
  await flush();
  assert.equal(prompt.mock.callCount(), 1);
  assert.equal(launch.mock.callCount(), 0, 'declining the recovery confirmation must leave the stopped run untouched');

  prompt.mock.mockImplementation(async () => 'Continue');
  ui.document.querySelector('#take-action').click();
  await flush();
  assert.equal(prompt.mock.callCount(), 2);
  assert.equal(launch.mock.callCount(), 1);
  const [, args, env] = launch.mock.calls[0].arguments;
  assert.ok(args.includes('--retry-failed-stage'));
  assert.ok(args.includes('--job-retry-token'));
  assert.ok(args.includes('-'));
  assert.ok(!args.includes('jr:exact-secret'));
  assert.equal(env.AUTOCODE_JOB_RETRY_TOKEN, 'jr:exact-secret');
});

test('chat does not guess an unresolved recovery allowance', async (t) => {
  const recovery = {
    status: 'PAUSED_TIMEOUT_RECOVERY', done: false,
    needs: { kind: 'resume', reason: 'Automatic timeout recoveries are exhausted' },
    next_command: `autocode --resume-paused --grant-recovery N --workspace ${run.workspace} --run-dir ${run.runDir}`,
  };
  const ui = await openChat(t, recovery);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  assert.match(ui.document.querySelector('#app').textContent, /leaves N open/);
  assert.equal(ui.document.querySelector('#take-action'), null);
  assert.equal(launch.mock.callCount(), 0);
});

test('a click on an old displayed plan cannot approve a newly fetched revision', async (t) => {
  const base = approvalView();
  const ui = await openPanel(t, base);
  ui.setView({ ...base, needs: { kind: 'approve_plan', token: 'r2:def' } });
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.take().click();
  await flush();
  assert.equal(launch.mock.callCount(), 0);
  assert.match(host.errors.at(-1), /plan changed/);
});

test('status polling preserves a draft, focus and selection and submits the exact answer', async (t) => {
  const view = questionView();
  const ui = await openPanel(t, view);
  const input = ui.document.querySelector('input');
  input.focus();
  input.value = 'keep existing behavior';
  input.setSelectionRange(5, 13);
  input.dispatchEvent(new ui.window.Event('input'));
  await ui.poll();
  await ui.poll({ ...view, usage: { tokens: 10 } });
  const refreshed = ui.document.querySelector('input');
  assert.equal(refreshed.value, 'keep existing behavior');
  assert.equal(ui.document.activeElement, refreshed);
  assert.equal(refreshed.selectionStart, 5);
  assert.equal(refreshed.selectionEnd, 13);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.take().click();
  await flush();
  assert.ok(launch.mock.calls[0].arguments[1].includes('q1=keep existing behavior'));
});

test('a new request reusing the question ID does not inherit the previous answer', async (t) => {
  const view = questionView();
  const ui = await openPanel(t, view);
  const input = ui.document.querySelector('input');
  input.value = 'old request answer';
  input.dispatchEvent(new ui.window.Event('input'));
  await ui.poll({ ...view, needs: { ...view.needs, request_id: 'request-2', resolver_token: 'resolver-2' } });
  assert.equal(ui.document.querySelector('input').value, '');
  assert.equal(ui.take().disabled, true);
});

test('reopening a hidden run resumes polling while preserving its answer draft', async (t) => {
  const view = questionView();
  const ui = await openPanel(t, view);
  const input = ui.document.querySelector('input');
  input.value = 'keep this draft';
  input.dispatchEvent(new ui.window.Event('input'));
  const panel = host.panels.at(-1);
  panel.setVisible(false);
  await ui.poll({ ...view, usage: { tokens: 123 } });
  assert.ok(!ui.document.body.textContent.includes('123 tokens'));

  RunPanel.show(1, run, controller);
  await flush();
  assert.ok(ui.document.body.textContent.includes('123 tokens'));
  await ui.poll({ ...view, usage: { tokens: 456 } });
  assert.ok(ui.document.body.textContent.includes('456 tokens'));
  assert.equal(ui.document.querySelector('input').value, 'keep this draft');
});

test('model selection enables the action immediately, survives polling and disabling follows clearing', async (t) => {
  const ui = await openPanel(t, questionView(true));
  assert.equal(ui.take().disabled, true);
  let select = ui.document.querySelector('select');
  select.value = 'model-b';
  select.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.take().disabled, false);
  await ui.poll();
  select = ui.document.querySelector('select');
  assert.equal(select.value, 'model-b');
  assert.equal(ui.take().disabled, false);
  select.value = '';
  select.dispatchEvent(new ui.window.Event('change'));
  assert.equal(ui.take().disabled, true);
  select.value = 'model-a';
  select.dispatchEvent(new ui.window.Event('change'));
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.take().click();
  await flush();
  assert.ok(launch.mock.calls[0].arguments[1].includes('q1=model-a'));
});


test('VS Code tree event replaces an initially empty sidebar after registry refresh', async () => {
  let listed = [];
  const provider = new RunsProvider({ list: async () => ({ runs: listed, diagnostics: [] }) });
  let rendered = provider.getChildren();
  assert.equal(rendered.length, 0);
  const subscription = provider.onDidChangeTreeData(() => { rendered = provider.getChildren(); });
  try {
    listed = [{ ...run, id: 'run-one', availability: 'available', status: 'RUNNING' }];
    await provider.refresh();
    assert.equal(rendered.length, 1);
    assert.equal(provider.getChildren(rendered[0])[0].run.runDir, run.runDir);
    listed = [{ ...listed[0], status: 'AWAITING_GOAL_APPROVAL' }];
    await provider.refresh();
    assert.equal(provider.getChildren(rendered[0])[0].contextValue, 'autocode.runNeeds');
  } finally {
    subscription.dispose();
    provider.dispose();
  }
});

function chatView(base = approvalView(), kind = 'feedback', context = 'chat-1') {
  return { ...base, chat: {
    context,
    messages: [{ id: 'initial', role: 'user', text: 'Build the requested CLI <script>bad()</script>' }],
    input: { kind, label: kind === 'feedback' ? 'Send plan feedback' : 'Send follow-up',
      next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} ${kind === 'feedback' ? '--feedback' : '--follow-up'} MESSAGE` },
  } };
}

async function openChat(t, initial, selected = run, options = {}) {
  if (!options.timersEnabled) t.mock.timers.enable({ apis: ['setInterval'] });
  let value = initial;
  const chatController = options.controller || controller;
  t.mock.method(chatController, 'status', async () => structuredClone(value));
  let receive;
  let visibility;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const view = { visible: true, webview, onDidChangeVisibility: (callback) => { visibility = callback; }, onDidDispose() {} };
  const saved = options.storageValues || new Map();
  const storage = options.storage || { get: (key) => saved.get(key), update: async (key, value) => { saved.set(key, value); } };
  let persisted = options.webviewState;
  const chat = new ChatView(chatController, storage, {
    pickWorkspace: async () => run.workspace, confirmStart: async () => true, changed() {}, selected() {},
  });
  chat.resolveWebviewView(view);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  const sent = [];
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => { sent.push(message); receive(message); },
    getState: () => persisted, setState: (value) => { persisted = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();
  if (selected !== false) chat.show(selected);
  await flush();
  t.after(() => { chat.dispose(); dom.window.close(); });
  return {
    chat, document: dom.window.document, window: dom.window, sent, saved,
    get webviewState() { return persisted; },
    setView(next) { value = next; },
    async poll(next = value) { value = next; t.mock.timers.tick(5000); await flush(); },
    async visible(visible) { view.visible = visible; visibility(); await flush(); },
    type(text) { const field = dom.window.document.querySelector('#message'); field.value = text; field.dispatchEvent(new dom.window.Event('input')); },
    submit() { dom.window.document.querySelector('#composer').dispatchEvent(new dom.window.Event('submit', { cancelable: true })); },
  };
}

test('sidebar sends core-named feedback verbatim then continues, and retains the saved transcript', async (t) => {
  const ui = await openChat(t, chatView());
  const text = 'Keep quotes " and $(literal) <b>text</b>\nsecond line';
  const launch = t.mock.method(cli, 'stream', async (_options, args) => {
    if (args.includes('--feedback')) ui.setView({ status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
      context: 'chat-2', input: null, messages: [...chatView().chat.messages, { id: 'feedback', role: 'user', text }],
    } });
    return ok;
  });
  ui.type(text); ui.submit(); ui.submit();
  await flush(); await flush();
  assert.equal(launch.mock.callCount(), 2, 'one saved message and one explicit advance; duplicate click ignored');
  assert.deepEqual(launch.mock.calls[0].arguments[1], ['--feedback', text, '--workspace', run.workspace, '--run-dir', run.runDir]);
  assert.ok(launch.mock.calls[1].arguments[1].includes('--no-chat'));
  assert.equal(ui.document.querySelector('#message').value, '');
  assert.ok(ui.document.querySelector('#messages').textContent.includes(text));
  assert.equal(ui.document.querySelectorAll('script').length, 1);
  assert.equal(ui.document.querySelectorAll('#messages b').length, 0);
});

test('chat approval uses the displayed plan token and advances only when the core offers continue', async (t) => {
  const approval = approvalView();
  approval.pause_category = 'your_decision';
  approval.pause_category_label = 'Your decision';
  const ui = await openChat(t, approval);
  const planText = ui.document.querySelector('#app').textContent;
  for (const expected of ['Plan to approve', 'Preserve user data', 'You check this one yourself', 'Never delete the original data', 'Only edit app.py']) {
    assert.ok(planText.includes(expected), expected);
  }
  assert.ok(!planText.includes('Plan the approval is bound to'));
  assert.ok(!planText.includes('Human review: not required'));
  assert.ok(!planText.includes('Run migration test'));
  assert.ok(!planText.includes('Compare export fixture'));
  assert.ok(!planText.includes('I couldn’t finish this step.'));
  assert.ok(!planText.includes('Open Run details to see what needs attention.'));
  assert.equal(ui.document.querySelector('#take-action').textContent, 'Approve and build');
  const launch = t.mock.method(cli, 'stream', async (_options, args) => {
    if (args.includes('--approve-goal')) {
      ui.setView({ status: 'RUNNING', done: false, needs: { kind: 'continue' } });
    } else {
      ui.setView({ status: 'PAUSED', done: false, needs: { kind: 'resume', reason: 'The core reached a person-only stop' } });
    }
    return ok;
  });

  ui.document.querySelector('#take-action').click();
  await flush(); await flush();
  assert.equal(launch.mock.callCount(), 2);
  const [, approveArgs, approveEnv] = launch.mock.calls[0].arguments;
  assert.ok(approveArgs.includes('--approve-goal'));
  assert.equal(approveEnv.AUTOCODE_APPROVE_GOAL_TOKEN, 'r1:abc');
  assert.ok(!approveArgs.includes('r1:abc'));
  assert.ok(launch.mock.calls[1].arguments[1].includes('--no-chat'));
  assert.equal(ui.document.querySelector('#send-message').disabled, true, 'a later person-only stop is not bypassed');
});

test('chat shows the plan to approve in plain words, without revision, criterion IDs or verification commands', async (t) => {
  const ui = await openChat(t, chatView());
  const app = ui.document.querySelector('#app');
  const headings = [...app.querySelectorAll('h2')].map((node) => node.textContent);
  assert.ok(headings.includes('Plan to approve'), headings.join(' | '));
  const text = app.textContent;
  for (const expected of ['Preserve user data <script>bad()</script>', 'Keep export stable', 'You check this one yourself',
    'Never delete the original data', 'Only edit app.py']) {
    assert.ok(text.includes(expected), `missing plan content: ${expected}`);
  }
  for (const hidden of ['AC1', 'AC2', 'revision', 'bound to', 'Run migration test', 'Compare export fixture',
    'Verification', 'Human review', 'acceptance criteria', 'permission boundaries']) {
    assert.ok(!text.includes(hidden), `chat shows plan internals: ${hidden}`);
  }
  assert.equal(ui.document.querySelectorAll('script').length, 1, 'model text must not become markup');
  assert.equal(ui.document.querySelector('#take-action').disabled, false);
});

test('chat shows review criteria but keeps internal problem diagnostics out of the conversation', async (t) => {
  const ui = await openChat(t, { ...chatView(), status: 'WAITING_FOR_USER',
    needs: { kind: 'review', criteria: [{ id: 'AC7', criterion: 'Export matches the saved fixture' }] },
    progress: { headline: 'Review output', problems: { items: [
      { id: 'F12', finding: 'Export drops the header row', severity: 'high', blocking: true },
    ] } },
  });
  const text = ui.document.querySelector('#app').textContent;
  assert.ok(text.includes('Export matches the saved fixture'));
  assert.ok(!text.includes('Export drops the header row'));
  assert.ok(!text.includes('Open problems'));
  assert.ok(!text.includes('AC7'), 'criterion ID shown in chat');
  assert.ok(!text.includes('F12'), 'finding ID shown in chat');
});

test('chat explains an unloaded plan without token jargon or a CLI command', async (t) => {
  const ui = await openChat(t, { ...chatView(), displayed_plan: undefined });
  const text = ui.document.querySelector('#app').textContent;
  assert.equal(ui.document.querySelector('#take-action').disabled, true);
  assert.match(text, /plan has not loaded here yet/i);
  assert.ok(!/token|binds to|--status/.test(text), text);
});

test('stale chat submission is rejected and preserves the unsent draft', async (t) => {
  const ui = await openChat(t, chatView());
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.type('my unsent correction');
  ui.setView(chatView(approvalView(), 'feedback', 'chat-2'));
  ui.submit(); await flush();
  assert.equal(launch.mock.callCount(), 0);
  assert.match(ui.document.querySelector('#chat-error').textContent, /conversation changed/);
  assert.equal(ui.document.querySelector('#message').value, 'my unsent correction');
});

test('follow-up submits to the same finished run and never invents another start', async (t) => {
  const ui = await openChat(t, chatView({ status: 'TASK_COMPLETE', done: true, needs: { kind: 'done' } }, 'follow_up'));
  const start = t.mock.method(controller, 'start', async () => run);
  const launch = t.mock.method(cli, 'stream', async () => {
    ui.setView({ status: 'RUNNING', done: false, needs: { kind: 'continue' } });
    return ok;
  });
  ui.type('Review what you built'); ui.submit(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 0);
  assert.equal(launch.mock.calls[0].arguments[1][0], '--follow-up');
  assert.equal(launch.mock.calls[0].arguments[1].at(-1), run.runDir);
});

test('chat drafts survive polling, hidden view restoration and switching runs', async (t) => {
  const ui = await openChat(t, chatView());
  ui.type('a draft for this run');
  const field = ui.document.querySelector('#message');
  field.focus(); field.setSelectionRange(2, 7);
  await ui.poll({ ...chatView(), usage: { tokens: 10 } });
  assert.equal(ui.document.activeElement, field);
  assert.equal(field.selectionStart, 2);
  await ui.visible(false);
  ui.setView({ ...chatView(), usage: { tokens: 20 } });
  await ui.visible(true);
  assert.ok(ui.document.body.textContent.includes('20 tokens'));
  ui.chat.show({ workspace: '/other', runDir: '/other/run' }); await flush();
  assert.equal(field.value, '');
  ui.type('other run draft');
  ui.chat.show(run); await flush();
  assert.equal(field.value, 'a draft for this run');
  assert.equal(ui.webviewState.selectedRun.runDir, run.runDir);
  assert.equal(ui.saved.get('autocode.chat.run'), undefined, 'selected runs no longer race through workspace state');
});

test('separate VS Code windows keep independent selected tasks and drafts', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-window' };
  const workspaceValues = new Map([['autocode.chat.run', run]]);
  const workspaceStorage = {
    get: (key) => workspaceValues.get(key),
    update: async (key, value) => { workspaceValues.set(key, value); },
  };
  const firstController = new RunController(host.log);
  const secondController = new RunController(host.log);
  const first = await openChat(t, chatView(), run, { storage: workspaceStorage, controller: firstController });
  const second = await openChat(t, chatView(), other, { storage: workspaceStorage, controller: secondController, timersEnabled: true });

  first.type('Draft for the first VS Code window');
  second.type('Draft for the second VS Code window');
  assert.deepEqual(first.webviewState.selectedRun, run);
  assert.deepEqual(second.webviewState.selectedRun, other);
  assert.equal(first.webviewState.drafts[`${run.workspace}\n${run.runDir}`], 'Draft for the first VS Code window');
  assert.equal(second.webviewState.drafts[`${other.workspace}\n${other.runDir}`], 'Draft for the second VS Code window');
  assert.equal(workspaceValues.get('autocode.chat.run'), undefined);

  const restoredFirst = await openChat(t, chatView(), false, {
    storage: workspaceStorage, controller: new RunController(host.log), webviewState: first.webviewState, timersEnabled: true,
  });
  assert.deepEqual(restoredFirst.chat.run, run);
  assert.equal(restoredFirst.document.querySelector('#message').value, 'Draft for the first VS Code window');
  assert.deepEqual(second.chat.run, other);
  assert.equal(second.document.querySelector('#message').value, 'Draft for the second VS Code window');
});

test('composer model, variant and usage update on polling without losing the draft or open breakdown', async (t) => {
  const q = (value) => ({ value, known: value, complete: true, known_items: 1 });
  const initial = { ...chatView(), provider: 'kilocode', usage: { accounting: {
    attempts: [{ model: 'zai-coding-plan/glm-5.3', active: true,
      launch_route: { engine: 'opencode', reasoning_effort: 'medium' } }],
    tokens: { input_tokens: q(1000), output_tokens: q(200), cached_input_tokens: q(500), reasoning_output_tokens: q(80) },
    cost: { historical_estimated_usd: q(0.05) },
  } } };
  const ui = await openChat(t, initial);
  ui.type('Keep my unsent feedback');
  const field = ui.document.querySelector('#message');
  field.focus(); field.setSelectionRange(5, 10);
  const details = ui.document.querySelector('#usage-details');
  assert.equal(details.open, false, 'model details start collapsed');
  assert.match(ui.document.querySelector('#model-info').textContent, /Variant: medium.*zai-coding-plan\/glm-5.3.*Provider: kilocode/);
  assert.match(ui.document.querySelector('#usage-summary').textContent, /1,200 tokens.*\$0.0500.*est\./);
  const next = structuredClone(initial);
  next.usage.accounting.attempts[0] = { model: 'xiaomi-token-plan-sgp/mimo-v2.6-pro<script>bad()</script>', active: true,
    launch_route: { engine: 'opencode', reasoning_effort: 'high' } };
  next.usage.accounting.tokens.output_tokens = q(400);
  await ui.poll(next);
  assert.match(ui.document.querySelector('#usage-summary').textContent, /1,400 tokens/);
  assert.match(ui.document.querySelector('#model-info').textContent, /Variant: high/);
  assert.equal(ui.document.querySelector('#model-info script'), null);
  assert.equal(ui.document.activeElement, field);
  assert.equal(field.value, 'Keep my unsent feedback');
  assert.equal(field.selectionStart, 5);
  assert.equal(details.open, false);
  ui.chat.show(); await flush();
  assert.match(ui.document.querySelector('#model-info').textContent, /when a run starts/);
  assert.match(ui.document.querySelector('#usage-summary').textContent, /Tokens not reported/);
});

test('all configured jobs render before use and polling keeps historical models separate', async (t) => {
  const initial = { ...chatView(), provider: 'kilocode', routes: {
    requirements: { job: 'Requirements', model: 'zai/glm', reasoning_effort: 'medium', engine: 'opencode' },
    plan_reviewer: { job: 'Plan Reviewer', model: 'xiaomi/mimo', reasoning_effort: 'high', engine: 'opencode' },
    terra: { job: 'Builder', model: 'xiaomi/mimo', reasoning_effort: 'medium', engine: 'opencode' },
    sol: { job: 'Tester', model: 'zai/glm', reasoning_effort: 'high', engine: 'opencode' },
    completion: { job: 'Completion Reviewer', model: 'zai/glm', reasoning_effort: 'medium', engine: 'opencode' },
  } };
  const ui = await openChat(t, initial);
  assert.deepEqual([...ui.document.querySelectorAll('#configured-models .model-job')].map((node) => node.textContent),
    ['Requirements', 'Plan Reviewer', 'Builder', 'Tester', 'Completion Reviewer']);
  assert.match(ui.document.querySelector('#model-summary').textContent, /5 configured roles/);
  assert.equal(ui.document.querySelector('#used-models'), null);
  ui.type('Keep my draft while all models update');
  const disclosure = ui.document.querySelector('#usage-details');
  disclosure.open = false;
  disclosure.dispatchEvent(new ui.window.Event('toggle'));
  const next = structuredClone(initial);
  next.routes.terra.model = 'next-model<script>bad()</script>';
  next.usage = { accounting: { attempts: [{ job: 'Builder', model: 'xiaomi/mimo', active: true,
    launch_route: { provider: 'kilocode', reasoning_effort: 'high' } }] } };
  await ui.poll(next);
  assert.match(ui.document.querySelector('#configured-models').textContent, /next-model<script>/);
  assert.match(ui.document.querySelector('#used-models').textContent, /BuilderVariant: highxiaomi\/mimo.*Active/);
  assert.match(ui.document.querySelector('#model-summary').textContent, /Builder · mimo · high.*5 configured roles/);
  assert.equal(ui.document.querySelector('#configured-models script'), null);
  assert.equal(disclosure.open, false);
  assert.equal(ui.document.querySelector('#message').value, 'Keep my draft while all models update');
});

test('model details remember expansion across polling, draft edits and webview recreation', async (t) => {
  const ui = await openChat(t, chatView());
  const disclosure = ui.document.querySelector('#usage-details');
  assert.equal(disclosure.open, false);
  for (const open of [true, false]) {
    disclosure.open = open;
    disclosure.dispatchEvent(new ui.window.Event('toggle'));
    ui.type('Keep both the draft and my display preference');
    await ui.poll({ ...chatView(), usage: { tokens: 42 } });
    assert.equal(disclosure.open, open);
    assert.equal(ui.webviewState.modelDetailsOpen, open);
    assert.ok(Object.values(ui.webviewState.drafts).includes('Keep both the draft and my display preference'));
    const restored = new JSDOM(panelHtml(true), { runScripts: 'outside-only' });
    restored.window.acquireVsCodeApi = () => ({ getState: () => ui.webviewState, setState() {}, postMessage() {} });
    restored.window.eval(restored.window.document.querySelector('script').textContent);
    assert.equal(restored.window.document.querySelector('#usage-details').open, open);
    restored.window.close();
  }
});

test('history replaces chat instead of splitting the sidebar and returning preserves the draft', async (t) => {
  const contexts = new Map();
  const views = require('../package.json').contributes.views.autocode;
  const visible = () => views.filter((view) => {
    if (!view.when) return true;
    return view.when.startsWith('!') ? !contexts.get(view.when.slice(1)) : !!contexts.get(view.when);
  }).map((view) => view.id);
  let focused;
  t.mock.method(host.vscode.commands, 'executeCommand', async (command, key, value) => {
    if (command === 'setContext') contexts.set(key, value);
    else if (command.endsWith('.focus')) {
      focused = command.slice(0, -'.focus'.length);
      assert.ok(visible().includes(focused), 'make the view visible before focusing it');
    }
  });
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const ui = await openChat(t, chatView());
  ui.type('Keep this unsent plan feedback');
  assert.deepEqual(visible(), ['autocode.chat']);
  ui.document.querySelector('#history').click(); await flush();
  assert.deepEqual(visible(), ['autocode.runs']);
  assert.equal(focused, 'autocode.runs');
  await ui.chat.focus();
  assert.deepEqual(visible(), ['autocode.chat']);
  assert.equal(focused, 'autocode.chat');
  assert.equal(ui.document.querySelector('#message').value, 'Keep this unsent plan feedback');
  assert.deepEqual(ui.webviewState.selectedRun, run);
  assert.equal(launch.mock.callCount(), 0, 'browsing history must never advance a run');
});

test('new chat shows the selected run while its first provider call is still pending', async (t) => {
  const ui = await openChat(t, null, undefined);
  ui.chat.show(); await flush();
  let finish;
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => {
    created(run);
    return new Promise((resolve) => { finish = () => resolve(run); });
  });
  ui.type('Build something'); ui.submit(); await flush();
  assert.equal(start.mock.callCount(), 1);
  assert.equal(start.mock.calls[0].arguments[0], run.workspace);
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Build something'));
  assert.ok(ui.document.body.textContent.includes('Thinking…'));
  ui.setView(chatView()); finish(); await flush(); await flush();
  assert.ok(ui.document.body.textContent.includes('Approve and build'));
});

test('a greeting waits for Requirements to answer instead of a canned reply', async (t) => {
  const ui = await openChat(t, null, undefined);
  ui.chat.show(); await flush();
  t.mock.method(controller, 'start', async (workspace, brief, created) => {
    created(run);
    return new Promise(() => {});
  });
  ui.type('hi'); ui.submit(); await flush();
  const rows = [...ui.document.querySelectorAll('#messages .message')].map((row) => row.textContent);
  assert.deepEqual(rows, ['Youhi']);
  assert.ok(ui.document.querySelector('#app').textContent.includes('Thinking…'));
});

test('requirements chat streams replies without an empty task dashboard and keeps typed text', async (t) => {
  const value = { status: 'RUNNING', done: false, phase: 'DISCOVERING', needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [{ id: 'u1', role: 'user', text: 'A book app' }, { id: 'live', role: 'assistant', text: 'Who will', streaming: true }],
  } };
  const ui = await openChat(t, value);
  ui.type('It is just for me');
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Who will'));
  assert.equal(ui.document.querySelectorAll('#app h1, #app h2').length, 0);
  assert.ok(!ui.document.querySelector('#app').textContent.includes('nothing needed'));
  const next = structuredClone(value); next.chat.messages[1].text = 'Who will use it?';
  await ui.poll(next);
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Who will use it?'));
  assert.equal(ui.document.querySelector('#message').value, 'It is just for me');
});

test('requirements chat offers Continue when no step is running instead of thinking forever', async (t) => {
  const value = { status: 'RUNNING', done: false, phase: 'DISCOVERING', needs: { kind: 'continue' },
    liveness: { kind: 'unknown' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`, chat: {
      phase: 'requirements', context: 'stopped', input: null,
      messages: [{ id: 'u1', role: 'user', text: 'A book app' }] } };
  const ui = await openChat(t, value);
  assert.ok(!ui.document.querySelector('#app').textContent.includes('Thinking…'));
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const resume = [...ui.document.querySelectorAll('#app button')].find((button) => button.textContent === 'Continue');
  assert.ok(resume, 'a stopped requirements step needs a way to continue');
  resume.click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1);
  assert.ok(!launch.mock.calls[0].arguments[1].includes('--approve-goal'));
});

test('requirements chat shows only Thinking while a step is running', async (t) => {
  const value = { status: 'RUNNING', done: false, phase: 'DISCOVERING', needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, chat: { phase: 'requirements', context: 'live', input: null,
      messages: [{ id: 'u1', role: 'user', text: 'A book app' }] } };
  const ui = await openChat(t, value);
  assert.ok(ui.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(ui.document.querySelectorAll('#app button').length, 0);
});

test('requirements approval sends only the core-offered answer and never approves a build', async (t) => {
  const value = { status: 'WAITING_FOR_USER', done: false, pause_category: 'your_decision', pause_category_label: 'Your decision', needs: { kind: 'answer', resolver_token: 'bound-summary',
    questions: [{ id: 'requirements-review-current', question: 'Is this what you want?', options: [], proposed_default: 'plan' }] },
    next_command: `autocode --answer requirements-review-current=ANSWER --resolver-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: 'requirements', context: 'summary', input: null,
      review: { question_id: 'requirements-review-current', answer: 'plan', label: 'Approve understanding and prepare plan' },
      messages: [{ id: 'summary', role: 'assistant', text: 'A personal reading list. Is that right?' }] },
  };
  const ui = await openChat(t, value);
  assert.equal(ui.document.querySelectorAll('#app h1, #app h2, #app input').length, 0);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const approve = [...ui.document.querySelectorAll('#app button')].find((button) => button.textContent.includes('Approve understanding'));
  approve.click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1);
  const args = launch.mock.calls[0].arguments[1];
  assert.ok(args.includes('requirements-review-current=plan'));
  assert.ok(!args.includes('--approve-goal'));
});

test('missing message action disables free text instead of bypassing a person-only pause', async (t) => {
  const ui = await openChat(t, { status: 'PAUSED', done: false, needs: { kind: 'resume' } });
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.type('go ahead'); ui.submit(); await flush();
  assert.equal(ui.document.querySelector('#send-message').disabled, true);
  assert.equal(launch.mock.callCount(), 0);
});

test('chat replaces pause dashboards with a useful explanation and keeps recovery gated', async (t) => {
  const ui = await openChat(t, {
    status: 'PAUSED_TRANSPORT_CHANGED', done: false, turn: 1,
    pause_category_label: 'Your environment',
    progress: { headline: 'Paused', line: 'Paused · no task list yet · no requirements yet · 0 open problems · inspect the pause, then resume', needs_you: 'inspect the pause, then resume' },
    needs: { kind: 'resume', reason: 'A joint-planning CLI/auth/provider configuration changed' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --resume-paused`,
    chat: { messages: [], context: 'connection-stop', input: null,
      notice: 'Your model connection settings changed. Check Connections before continuing.' },
  });
  const text = ui.document.querySelector('#app').textContent;
  assert.ok(text.includes('Check Connections before continuing.'));
  for (const internal of ['Paused', 'no task list', 'no requirements', '0 open problems', 'turn 1', 'Your environment', 'Waiting for']) {
    assert.ok(!text.includes(internal), internal);
  }
  assert.equal(ui.document.querySelector('#send-message').disabled, true);
  assert.equal(ui.document.querySelector('#take-action').textContent, 'Continue');
  const prompt = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.document.querySelector('#take-action').click(); await flush(); await flush();
  assert.equal(prompt.mock.calls[0].arguments[0], 'Continue AutoCode?');
  assert.match(prompt.mock.calls[0].arguments[1].detail, /Check Connections/);
  assert.equal(launch.mock.callCount(), 0, 'cancelling still leaves recovery gated');
});

test('message actions cannot name another run or append approval flags', async (t) => {
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const view = chatView();
  view.chat.input.next_command = 'autocode --workspace /other --run-dir /other/run --feedback MESSAGE';
  await assert.rejects(controller.sendMessage(view, run, 'hello', 'chat-1'), /not this run/);
  view.chat.input.next_command = 'autocode --feedback MESSAGE --approve-goal -';
  await assert.rejects(controller.sendMessage(view, run, 'hello', 'chat-1'), /Unsupported message action/);
  view.chat.input = { kind: 'unknown', next_command: 'autocode --follow-up MESSAGE' };
  await assert.rejects(controller.sendMessage(view, run, 'hello', 'chat-1'), /Unsupported message action/);
  assert.equal(launch.mock.callCount(), 0);
});

test('operational reply offer shows a usable composer without a false no-action warning', async (t) => {
  const view = chatView({ status: 'WAITING_FOR_USER', needs: { kind: 'answer', resolver_scope: 'blocker' },
    next_command: 'autocode --resolver-message \"WHAT CHANGED\"' });
  view.chat.input = { kind: 'resolver_response', label: 'Send recovery reply', next_command: 'bound offer' };
  const ui = await openChat(t, view);
  assert.ok(!ui.document.querySelector('#app').textContent.includes('No action the client can take'));
  ui.type('Leave this run blocked');
  assert.equal(ui.document.querySelector('#send-message').disabled, false);
});

test('operational chat reply uses only the current named Resolver binding', async (t) => {
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const view = chatView({ status: 'WAITING_FOR_USER', needs: {
    kind: 'answer', resolver_scope: 'blocker', resolver_request_id: 'req-1', resolver_token: 'current-token',
  } });
  const command = 'AUTOCODE_RESOLVER_TOKEN=current-token autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message MESSAGE';
  view.chat.input = { kind: 'resolver_response', label: 'Send recovery reply', next_command: command };
  await controller.sendMessage(view, run, 'I corrected the fixture', 'chat-1');
  assert.deepEqual(launch.mock.calls[0].arguments[1], [
    '--resolver-request', 'req-1', '--resolver-token', '-', '--resolver-response', 'provide_information',
    '--resolver-message', 'I corrected the fixture', '--workspace', run.workspace, '--run-dir', run.runDir,
  ]);
  assert.equal(launch.mock.calls[0].arguments[2].AUTOCODE_RESOLVER_TOKEN, 'current-token');
  for (const bad of [command.replace('req-1', 'req-old'), command.replace('current-token', 'old-token'),
    command + ' --resume-paused', command.replace('provide_information', 'retry')]) {
    view.chat.input.next_command = bad;
    await assert.rejects(controller.sendMessage(view, run, 'reply', 'chat-1'), /Unsupported message action/);
  }
  assert.equal(launch.mock.callCount(), 1);
});

test('a CLI failure is surfaced instead of continuing as if the action succeeded', async (t) => {
  t.mock.method(cli, 'stream', async () => ({ ...ok, code: 1, stderr: 'provider unavailable' }));
  await assert.rejects(controller.take({ status: 'RUNNING', done: false, needs: { kind: 'continue' } }, run), /provider unavailable/);
});

test('a rejected chat start retains the request and original error across polling', async (t) => {
  const ui = await openChat(t, null);
  ui.chat.show(); await flush();
  t.mock.method(controller, 'start', async (_workspace, _text, created) => { created(run); throw new Error('Input rejected: invalid option'); });
  ui.type('Do not lose this request'); ui.submit(); await flush(); await flush();
  t.mock.method(controller, 'status', async () => { throw new Error('Run not found'); });
  await ui.poll();
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Do not lose this request'));
  assert.match(ui.document.querySelector('#chat-error').textContent, /Input rejected: invalid option/);
});

test('assistant replies render Markdown while HTML and executable links stay inert', async (t) => {
  const view = chatView({ status: 'TASK_COMPLETE', done: true, needs: { kind: 'done' } }, 'follow_up');
  view.chat.messages.push({ id: 'reply', role: 'assistant', text: '**Done**\n\n- Added tests\n- Updated docs\n\n```python\nprint("hello")\n```\n\n<script>window.pwned=true</script>\n\n[unsafe](command:workbench.action.closeWindow) [docs](https://example.com/docs)' });
  const ui = await openChat(t, view);
  assert.equal(ui.document.querySelector('.assistant strong').textContent, 'Done');
  assert.equal(ui.document.querySelectorAll('.assistant li').length, 2);
  assert.equal(ui.document.querySelector('.assistant pre code').textContent, 'print("hello")');
  assert.equal(ui.document.querySelectorAll('.assistant script').length, 0);
  assert.equal(ui.window.pwned, undefined);
  assert.equal(ui.document.querySelectorAll('.assistant a').length, 1);
  assert.equal(ui.document.querySelector('.assistant a').href, 'https://example.com/docs');
  assert.ok(ui.document.querySelector('.assistant').textContent.includes('<script>'));
});

test('Kilo transcript scrolling preserves reading position and resumes on Latest', async (t) => {
  const ui = await openChat(t, chatView());
  const thread = ui.document.querySelector('#thread');
  Object.defineProperties(thread, { scrollHeight: { value: 2000, configurable: true }, clientHeight: { value: 500 } });
  thread.scrollTop = 1500;
  thread.dispatchEvent(new ui.window.WheelEvent('wheel', { deltaY: -100 }));
  thread.scrollTop = 900;
  thread.dispatchEvent(new ui.window.Event('scroll'));
  const next = chatView();
  next.chat.messages.push({ id: 'new', role: 'assistant', text: 'A new result arrives.' });
  await ui.poll(next);
  assert.equal(thread.scrollTop, 900, 'polling must not pull the reader to the bottom');
  const latest = ui.document.querySelector('#latest');
  assert.equal(latest.hidden, false);
  latest.click();
  assert.equal(thread.scrollTop, 2000);
  assert.equal(latest.hidden, true);
});

test('Kilo composer grows with text and keeps undo inside the webview', async (t) => {
  const ui = await openChat(t, chatView());
  const field = ui.document.querySelector('#message');
  Object.defineProperty(field, 'scrollHeight', { value: 400 });
  ui.type('A long draft\nwith multiple lines');
  assert.equal(field.style.height, '200px');
  let action;
  ui.document.execCommand = (value) => { action = value; };
  let escaped = false;
  ui.window.addEventListener('keydown', () => { escaped = true; });
  field.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'z', metaKey: true, bubbles: true, cancelable: true }));
  assert.equal(action, 'undo');
  assert.equal(escaped, false);
  const submit = t.mock.method(cli, 'stream', async () => ok);
  field.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true }));
  field.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }));
  await flush();
  assert.equal(submit.mock.callCount(), 0, 'newline and IME confirmation are not submissions');
});

function checkpointView() {
  return {
    status: 'PAUSED_REQUESTED', done: false,
    needs: { kind: 'resume', reason: 'A person asked the run to pause.' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --resume-paused`,
    code_checkpoints: {
      version: 1, blocked_reason: null,
      rows: [
        { id: 'cp-1', label: 'Fixture code', at: '2026-10-17T22:00:00Z', changed_files: ['app.txt', 'run.sh'],
          available: true, recorded_check: { verdict: 'PASS' }, actions: [
            { id: 'compare', label: 'Show what changed since here',
              next_command: ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--compare', 'cp-1'] },
            { id: 'restore', label: 'Go back to here',
              next_command: ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--restore', 'cp-1', '--expected-token', '-', '--request-id', 'REQUEST_ID'] },
          ] },
        { id: 'cp-legacy', label: 'Old step', changed_files: [], available: false,
          reason: 'no restorable source checkpoint', actions: [] },
      ],
    },
    chat: { context: 'ctx-1', messages: [], input: null },
  };
}

test('saved code changes render with only the actions the view named', async (t) => {
  const ui = await openChat(t, checkpointView());
  const text = ui.document.body.textContent;
  assert.ok(text.includes('Saved code changes'), 'section missing');
  assert.ok(text.includes('Fixture code · PASS'));
  assert.ok(text.includes('2 file(s) changed'));
  assert.ok(text.includes('no restorable source checkpoint'), 'unavailable rows explain themselves');
  const buttons = [...ui.document.querySelectorAll('[data-checkpoint-action]')];
  assert.deepEqual(
    buttons.map((button) => button.dataset.checkpointId + ':' + button.dataset.checkpointAction),
    ['cp-1:compare', 'cp-1:restore'],
    'unavailable rows offer no actions',
  );
});

test('compare runs exactly the named argv and renders the returned diff', async (t) => {
  const ui = await openChat(t, checkpointView());
  const calls = [];
  t.mock.method(cli, 'invoke', async (_settings, argv) => {
    calls.push(argv);
    return { ...ok, stdout: JSON.stringify({ checkpoint_id: 'cp-1', expected_token: 'checkpoint-v1:t', changed_files: ['app.txt'],
      patch: '--- checkpoint/app.txt\n+++ current/app.txt\n-later work\n', truncated: false, blocked_reason: null }) };
  });
  ui.document.querySelector('[data-checkpoint-action="compare"]').click();
  await flush();
  assert.deepEqual(calls, [['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--compare', 'cp-1']]);
  const text = ui.document.body.textContent;
  assert.ok(text.includes('1 file(s) differ from this checkpoint'));
  assert.ok(text.includes('+++ current/app.txt'));
  assert.ok(!text.includes('checkpoint-v1:t'), 'the inspection token never reaches the conversation');
});

test('restore asks the person and replays one stable request id', async (t) => {
  const ui = await openChat(t, checkpointView());
  const comparison = { checkpoint_id: 'cp-1', expected_token: 'checkpoint-v1:t', changed_files: ['app.txt'],
    patch: 'diff', blocked_reason: null, source_branch: 'main' };
  const restore = t.mock.method(controller, 'restoreCheckpoint', async () => {
    throw new Error('network died');
  });
  t.mock.method(controller, 'compareCheckpoint', async () => comparison);
  const modal = t.mock.method(host.vscode.window, 'showWarningMessage', async (_message, _options, choice) => choice);
  ui.document.querySelector('[data-checkpoint-action="restore"]').click();
  await flush();
  assert.equal(modal.mock.callCount(), 1, 'restore is gated on a person confirming the comparison');
  assert.equal(restore.mock.calls[0].arguments[2], 'checkpoint-v1:t', 'the token a fresh compare returned');
  assert.match(String(restore.mock.calls[0].arguments[3]), /^[A-Za-z0-9_-]{8,100}$/);
  const first = restore.mock.calls[0].arguments[3];
  restore.mock.restore();
  const retry = t.mock.method(controller, 'restoreCheckpoint', async () => ({ branch: 'autocode/checkpoint-1',
    workspace: run.workspace, run_dir: run.runDir + '-child', status: 'PAUSED_REQUESTED', message: 'ok' }));
  ui.document.querySelector('[data-checkpoint-action="restore"]').click();
  await flush();
  assert.equal(retry.mock.calls[0].arguments[3], first, 'a failed restore retries the same request id');
  const saved = ui.saved.get('autocode.checkpoint.requests');
  assert.ok(!saved || !saved[run.workspace + '\n' + run.runDir], 'success clears the request id');
  const text = ui.document.body.textContent;
  assert.ok(text.includes('Restored on a new branch: autocode/checkpoint-1'));
  assert.ok(text.includes('Open restored candidate'));
});

test('a checkpoint action the view did not name exactly is refused without a CLI call', async (t) => {
  const view = checkpointView();
  view.code_checkpoints.rows[0].actions[0].next_command.push('--extra');
  t.mock.method(controller, 'status', async () => structuredClone(view));
  const invoke = t.mock.method(cli, 'invoke', async () => ok);
  await assert.rejects(controller.compareCheckpoint(run, 'cp-1'), /Unsupported checkpoint action/);
  assert.equal(invoke.mock.callCount(), 0);
  await assert.rejects(controller.compareCheckpoint(run, 'cp-missing'), /named no compare action/);
});

test('opening the restored candidate switches the chat to it', async (t) => {
  const ui = await openChat(t, checkpointView());
  const show = t.mock.method(ChatView.prototype, 'show', () => {});
  ui.window.dispatchEvent(new ui.window.MessageEvent('message', { data: { type: 'checkpoint_result', runKey: run.workspace + '\n' + run.runDir, checkpoint: 'cp-1',
    result: { branch: 'b', workspace: run.workspace, run_dir: '/ws/.autocode/runs/child' } } }));
  await flush();
  ui.document.getElementById('new-chat'); // webview still alive
  const open = [...ui.document.querySelectorAll('button')].find((button) => button.textContent === 'Open restored candidate');
  open.click();
  await flush();
  assert.deepEqual(show.mock.calls.at(-1).arguments[0], { workspace: run.workspace, runDir: '/ws/.autocode/runs/child' });
});

test('chat renders decision card and sticky action strip without button restlessness on poll', async (t) => {
  const ui = await openChat(t, approvalView());
  const card = ui.document.querySelector('#app .decision-card');
  assert.ok(card, 'decision card renders in app');
  assert.equal(card.querySelector('.speaker').textContent, 'AutoCode');
  assert.ok(card.querySelector('.message-content').textContent.includes('Plan to approve'));

  const strip = ui.document.querySelector('#app #action-strip');
  assert.ok(strip, 'action strip renders in app');
  assert.equal(strip.querySelector('.action-prompt').textContent, 'AutoCode is waiting for you');
  const actionButton = strip.querySelector('#take-action');
  assert.equal(actionButton.textContent, 'Approve and build');

  // Polling usage and telemetry must not recreate or move the action button
  const pollView = structuredClone(approvalView());
  pollView.usage = { accounting: { tokens: { input_tokens: 5000, output_tokens: 200 } } };
  await ui.poll(pollView);
  assert.equal(ui.document.querySelector('#take-action'), actionButton, 'polling usage preserves action button identity');
});

test('chat renders review criteria in decision card with sticky action strip', async (t) => {
  const ui = await openChat(t, {
    ...chatView(),
    status: 'WAITING_FOR_USER',
    needs: { kind: 'review', criteria: [{ id: 'AC1', criterion: 'All unit tests pass' }] },
  });
  const card = ui.document.querySelector('#app .decision-card');
  assert.ok(card, 'decision card renders');
  assert.ok(card.querySelector('.message-content').textContent.includes('All unit tests pass'));
  assert.equal(ui.document.querySelector('#action-strip .action-prompt').textContent, 'AutoCode is waiting for you');
  assert.equal(ui.document.querySelector('#take-action').textContent, 'Accept the reviewed criteria');
});

test('non-requirements running stage shows Thinking in chat without offering Continue (#1530)', async (t) => {
  const runningView = {
    status: 'RUNNING', done: false,
    liveness: { kind: 'supervised' },
    needs: { kind: 'continue' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { context: 'ctx-1', messages: [{ id: 'm1', role: 'assistant', text: 'Building it now.' }], input: null },
    progress: { line: 'Builder · writing the export' },
  };
  const chatUi = await openChat(t, runningView);
  assert.ok(chatUi.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(chatUi.document.querySelector('#take-action'), null, 'active build step must not offer Continue');
  assert.equal(chatUi.document.querySelectorAll('#app button').length, 0);
  assert.equal(chatUi.document.querySelector('#send-hint').textContent, 'AutoCode is working. You can keep a draft here.');

  // Once the step stops, Continue is offered again
  await chatUi.poll({ ...runningView, liveness: { kind: 'unknown' } });
  assert.ok(!chatUi.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(chatUi.document.querySelector('#take-action').textContent, 'Continue');
  assert.equal(chatUi.document.querySelector('#take-action').disabled, false);
});

test('Run details disables Take action while a stage is actively running (#1530)', async (t) => {
  const runningView = {
    status: 'RUNNING', done: false,
    liveness: { kind: 'supervised' },
    needs: { kind: 'continue' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    progress: { line: 'Builder · writing the export' },
  };
  const panelUi = await openPanel(t, runningView);
  assert.equal(panelUi.take().textContent, 'Working…');
  assert.equal(panelUi.take().disabled, true);
  await panelUi.poll({ ...runningView, liveness: { kind: 'unknown' } });
  assert.equal(panelUi.take().textContent, 'Take the named action');
  assert.equal(panelUi.take().disabled, false);
});

test('Chat #details toolbar button is disabled without an active run and enabled with one (#1543)', async (t) => {
  const ui = await openChat(t, chatView(), run);
  const detailsBtn = ui.document.querySelector('#details');
  assert.equal(detailsBtn.disabled, false, '#details must be enabled when a run is active');

  detailsBtn.click();
  await flush();
  assert.equal(ui.sent.filter((m) => m.type === 'details').length, 1);

  // Switching to new chat disables #details
  ui.chat.show();
  await flush();
  assert.equal(detailsBtn.disabled, true, '#details must become disabled on new chat');

  // Clicking while disabled sends nothing
  detailsBtn.click();
  await flush();
  assert.equal(ui.sent.filter((m) => m.type === 'details').length, 1);

  // Switching back to active run enables #details again
  ui.chat.show(run);
  await flush();
  assert.equal(detailsBtn.disabled, false, '#details must become enabled when run is active again');
});

test('Command palette run commands show "Select a run first." when no run is selected (#1543)', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  const messages = [];
  t.mock.method(host.vscode.window, 'showInformationMessage', (msg) => { messages.push(msg); });
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));

  await host.commands.get('autocode.openDetails')();
  assert.equal(messages.at(-1), 'Select a run first.');

  await host.commands.get('autocode.showCliCommand')();
  assert.equal(messages.at(-1), 'Select a run first.');

  await host.commands.get('autocode.continueRun')();
  assert.equal(messages.at(-1), 'Select a run first.');

  await host.commands.get('autocode.watchRun')();
  assert.equal(messages.at(-1), 'Select a run first.');

  await host.commands.get('autocode.openRun')();
  assert.equal(messages.at(-1), 'Select a run first.');

  await host.commands.get('autocode.takeAction')();
  assert.equal(messages.at(-1), 'Select a run first.');
});

test('autocode.watchRun toggles watching off/on, switches watched runs, and updates status bar immediately (#1543)', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let statusVisible = false;
  let statusText = '';
  const mockStatus = {
    show() { statusVisible = true; },
    hide() { statusVisible = false; },
    dispose() {},
    get text() { return statusText; },
    set text(val) { statusText = val; },
    command: undefined,
    tooltip: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'status', async (target) => ({
    status: 'RUNNING',
    done: false,
    needs: { kind: 'continue' },
    progress: { headline: target.runDir.endsWith('other') ? 'Other running' : 'Main running' },
  }));

  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));

  const other = { ...run, runDir: '/ws/.autocode/runs/other' };

  // 1. Open run: status bar is shown for run
  await host.commands.get('autocode.openRun')(run);
  await flush();
  assert.equal(statusVisible, true);
  assert.ok(mockStatus.text.includes('Main running'));

  // 2. Watch/unwatch toggles watching off on same run
  await host.commands.get('autocode.watchRun')(run);
  assert.equal(statusVisible, false, 'status bar must hide immediately when unwatched');

  // 3. active selection is preserved after unwatching
  const copied = [];
  t.mock.method(host.vscode.env.clipboard, 'writeText', async (text) => { copied.push(text); });
  await host.commands.get('autocode.showCliCommand')();
  assert.equal(copied.length, 1, 'active run target must be preserved for commands when unwatched');

  // 4. Invoking watchRun again without arguments watches the active run
  await host.commands.get('autocode.watchRun')();
  await flush();
  assert.equal(statusVisible, true);
  assert.ok(mockStatus.text.includes('Main running'));

  // 5. Switching watched run to other
  await host.commands.get('autocode.watchRun')(other);
  await flush();
  assert.equal(statusVisible, true);
  assert.ok(mockStatus.text.includes('Other running'));

  // 6. Unwatching other
  await host.commands.get('autocode.watchRun')(other);
  assert.equal(statusVisible, false);
});

test('delayed status resolution does not re-show unwatched run or replace newly watched run (#1543)', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let statusVisible = false;
  let statusText = '';
  const mockStatus = {
    show() { statusVisible = true; },
    hide() { statusVisible = false; },
    dispose() {},
    get text() { return statusText; },
    set text(val) { statusText = val; },
    command: undefined,
    tooltip: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);

  let resolveMain;
  t.mock.method(RunController.prototype, 'status', async (target) => {
    if (target.runDir.endsWith('other')) {
      return { status: 'RUNNING', done: false, progress: { headline: 'Other running' } };
    }
    return new Promise((resolve) => {
      resolveMain = () => resolve({ status: 'RUNNING', done: false, progress: { headline: 'Main running' } });
    });
  });

  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));

  // 1. Open run: status fetch starts and is pending
  await host.commands.get('autocode.openRun')(run);
  assert.equal(statusVisible, false, 'status should not be visible while pending');

  // 2. Unwatch while status read is still in-flight
  await host.commands.get('autocode.watchRun')(run);
  assert.equal(statusVisible, false);

  // 3. In-flight status resolves: must NOT show unwatched run
  resolveMain();
  await flush();
  assert.equal(statusVisible, false, 'delayed status resolution must not re-show unwatched run');

  // 4. Test switching watched runs during delayed status
  const other = { ...run, runDir: '/ws/.autocode/runs/other' };
  // Start in-flight for run again
  await host.commands.get('autocode.watchRun')(run);
  // Switch to other before main resolves
  await host.commands.get('autocode.watchRun')(other);
  await flush();
  assert.equal(statusVisible, true);
  assert.ok(mockStatus.text.includes('Other running'));

  // Main resolves late: must NOT replace Other
  resolveMain();
  await flush();
  assert.equal(statusVisible, true);
  assert.ok(mockStatus.text.includes('Other running'), 'late resolution must not replace newer watched run');
});
