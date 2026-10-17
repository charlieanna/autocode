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
const controller = new RunController(host.log);
const run = { workspace: '/ws', runDir: '/ws/.autocode/runs/test' };
const ok = { code: 0, stdout: '', stderr: '', rejected: false };

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

async function openChat(t, initial, selected = run) {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let value = initial;
  t.mock.method(controller, 'status', async () => structuredClone(value));
  let receive;
  let visibility;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const view = { visible: true, webview, onDidChangeVisibility: (callback) => { visibility = callback; }, onDidDispose() {} };
  const saved = new Map();
  const storage = { get: () => undefined, update: async (key, value) => { saved.set(key, value); } };
  const chat = new ChatView(controller, storage, {
    pickWorkspace: async () => run.workspace, confirmStart: async () => true, changed() {}, selected() {},
  });
  chat.resolveWebviewView(view);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  let persisted;
  const sent = [];
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => { sent.push(message); receive(message); },
    getState: () => persisted, setState: (value) => { persisted = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  chat.show(selected);
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
  assert.equal(ui.saved.get('autocode.chat.run').runDir, run.runDir);
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
  assert.equal(details.open, true, 'model details start expanded');
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
  assert.equal(details.open, true);
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
  assert.equal(disclosure.open, true);
  for (const open of [false, true]) {
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
  assert.deepEqual(ui.saved.get('autocode.chat.run'), run);
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
  assert.ok(ui.document.body.textContent.includes('Starting AutoCode'));
  ui.setView(chatView()); finish(); await flush(); await flush();
  assert.ok(ui.document.body.textContent.includes('Approve and build'));
});

test('missing message action disables free text instead of bypassing a person-only pause', async (t) => {
  const ui = await openChat(t, { status: 'PAUSED', done: false, needs: { kind: 'resume' } });
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.type('go ahead'); ui.submit(); await flush();
  assert.equal(ui.document.querySelector('#send-message').disabled, true);
  assert.equal(launch.mock.callCount(), 0);
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
