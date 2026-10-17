const assert = require('node:assert/strict');
const { test, afterEach, mock } = require('node:test');
const { JSDOM } = require('jsdom');
const { editor, flush } = require('./helpers/editor.cjs');

const host = editor();
const cli = host.load('core/cli.js');
const { RunController } = host.load('controller.js');
const { RunPanel } = host.load('panel.js');
const { RunItem } = host.load('runsView.js');
const { activate } = host.load('extension.js');
const controller = new RunController(host.log);
const run = { workspace: '/ws', runDir: '/ws/.autocode/runs/test' };
const ok = { code: 0, stdout: '', stderr: '', rejected: false };

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
  const show = t.mock.method(RunPanel, 'show', () => {});
  const context = { subscriptions: [] };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const other = { ...run, runDir: '/ws/.autocode/runs/other' };
  const row = new RunItem({ ...other, id: 'other', availability: 'available', status: 'RUNNING' }, other.workspace);

  await host.commands.get('autocode.takeAction')(row);
  assert.deepEqual(show.mock.calls.at(-1).arguments[1], other);
  await host.commands.get('autocode.openRun')(run);
  await host.commands.get('autocode.takeAction')(row);
  assert.deepEqual(show.mock.calls.at(-1).arguments[1], other);
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
