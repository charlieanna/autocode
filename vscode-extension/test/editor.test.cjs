const assert = require('node:assert/strict');
const { test, beforeEach, afterEach, mock } = require('node:test');
const { JSDOM } = require('jsdom');
const { editor, flush } = require('./helpers/editor.cjs');

const host = editor();
const cli = host.load('core/cli.js');
const { RunController } = host.load('controller.js');
const { RunPanel, panelHtml, stateFor } = host.load('panel.js');
const { ChatView } = host.load('chat.js');
const { RunItem, RunsProvider } = host.load('runsView.js');
const { activate } = host.load('extension.js');
const { ConnectionsPanel, connectionsHtml } = host.load('connections.js');
const { StagesPanel, stagesHtml } = host.load('stages.js');
const { readStages, currentRoute } = host.load('core/stages.js');
const { engineOf, routeKeyOf, spendTarget, sanitizeRoutes, readSettings, applyEngine } = host.load('settings.js');
let controller = new RunController(host.log);
const run = { workspace: '/ws', runDir: '/ws/.autocode/runs/test' };
const ok = { code: 0, stdout: '', stderr: '', rejected: false };

function totalTimeView(token = 'time:current') {
  return {
    status: 'PAUSED_TIME_LIMIT', done: false, recovery: { token },
    needs: { kind: 'resume', command_inputs: [{ id: 'max_seconds', kind: 'integer', flag: '--max-seconds', placeholder: 'N',
      arg_index: 2, label: 'Total active time (seconds)', current: 2700, elapsed: 3010.885, minimum: 3011, maximum: Number.MAX_SAFE_INTEGER }] },
    next_command: `AUTOCODE_EXPECTED_RECOVERY_TOKEN=${token} autocode --run-dir ${run.runDir} --workspace ${run.workspace} --resume-paused --max-seconds N --expected-recovery-token -`,
  };
}

function planningAllowanceView(saveOnly = false, token = 'planning:current') {
  return {
    status: 'PAUSED_PLANNING_BUDGET', done: false, recovery: { token },
    needs: { kind: 'planning_budget', command_inputs: [{ id: 'planning_review_call_limit', kind: 'integer',
      flag: '--planning-review-call-limit', placeholder: 'N', arg_index: saveOnly ? 1 : 2,
      label: 'Total plan-review calls', current: 2, used: 2, minimum: 3, maximum: Number.MAX_SAFE_INTEGER }] },
    next_command: `AUTOCODE_EXPECTED_RECOVERY_TOKEN=${token} autocode --run-dir ${run.runDir} --workspace ${run.workspace} ${saveOnly ? '' : '--resume-paused '}--planning-review-call-limit N --expected-recovery-token -`,
  };
}

function recoveryGrantView(token = 'recovery:current') {
  return { status: 'PAUSED_TIMEOUT_RECOVERY', done: false, recovery: { token },
    needs: { kind: 'resume', command_inputs: [{ id: 'grant_recovery', kind: 'integer', flag: '--grant-recovery', placeholder: 'N',
      arg_index: 2, label: 'Additional recoveries', current: 3, used: 3, minimum: 1, maximum: 3 }] },
    next_command: `AUTOCODE_EXPECTED_RECOVERY_TOKEN=${token} autocode --run-dir ${run.runDir} --workspace ${run.workspace} --resume-paused --grant-recovery N --expected-recovery-token -` };
}

for (const surface of ['chat', 'Run details']) {
  test(`finite recovery grant in ${surface} is blank, bounded and explicitly confirmed`, async (t) => {
    host.config.set('confirmPersonOnly', false);
    const ownController = new RunController(host.log);
    const value = recoveryGrantView();
    value.needs.confirmation_required = false; // A display hint cannot waive numeric authority.
    const ui = surface === 'chat' ? await openChat(t, value, run, { controller: ownController }) : await openPanel(t, value, undefined, ownController);
    const field = ui.document.querySelector('[data-question="grant_recovery"]');
    const take = () => ui.document.querySelector('#take-action');
    assert.equal(field.value, ''); assert.equal(take().disabled, true);
    for (const text of ['Automatic recovery allowance: 3', 'Counted recoveries used: 3', '1 to 3 additional recoveries']) assert.ok(ui.document.body.textContent.includes(text), text);
    for (const value of ['0', '4', '1.5', '1e2', '9007199254740992']) {
      field.value = value; field.dispatchEvent(new ui.window.Event('input')); assert.equal(take().disabled, true);
    }
    field.value = '1'; field.dispatchEvent(new ui.window.Event('input'));
    assert.equal(take().disabled, false);
    const launch = t.mock.method(cli, 'stream', async () => ok);
    const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
    take().click(); await flush(); assert.equal(launch.mock.callCount(), 0);
    const [title, options, button] = confirm.mock.calls[0].arguments;
    assert.ok(title.includes('1 recovery slot')); assert.equal(button, 'Grant and continue');
    for (const text of ['fixed allowance', 'history is retained', 'excess is not banked', 'does not approve a plan']) assert.ok(options.detail.includes(text), text);
    confirm.mock.mockImplementation(async () => button);
    take().click(); await flush(); assert.equal(launch.mock.callCount(), 1);
    const [, args, env] = launch.mock.calls[0].arguments;
    assert.deepEqual(args, ['--resume-paused', '--grant-recovery', '1', '--expected-recovery-token', '-', '--workspace', run.workspace, '--run-dir', run.runDir]);
    assert.deepEqual(env, { AUTOCODE_EXPECTED_RECOVERY_TOKEN: 'recovery:current' });
    ui.setView(recoveryGrantView('recovery:new'));
    take().click(); await flush(); assert.equal(launch.mock.callCount(), 1);
  });
}

for (const surface of ['chat', 'Run details']) {
  for (const saveOnly of [false, true]) {
    test(`typed review allowance in ${surface}, saveOnly=${saveOnly}, is explicit and preserves the named action`, async (t) => {
      host.config.set('confirmPersonOnly', false);
      const ownController = new RunController(host.log);
      const view = planningAllowanceView(saveOnly);
      const ui = surface === 'chat' ? await openChat(t, view, run, { controller: ownController }) : await openPanel(t, view, undefined, ownController);
      const input = ui.document.querySelector('[data-question="planning_review_call_limit"]');
      const take = () => ui.document.querySelector('#take-action');
      assert.equal(input.value, ''); assert.equal(take().disabled, true);
      for (const text of ['Saved allowance: 2', 'calls used: 2', 'at least 3 calls']) assert.ok(ui.document.body.textContent.includes(text), text);
      for (const value of ['0', '2', '-1', '1.5', '4e2', '9007199254740992']) {
        input.value = value; input.dispatchEvent(new ui.window.Event('input')); assert.equal(take().disabled, true, value);
      }
      input.value = '4'; input.dispatchEvent(new ui.window.Event('input'));
      assert.equal(take().disabled, false);
      const launch = t.mock.method(cli, 'stream', async () => ok);
      const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
      take().click(); await flush(); assert.equal(launch.mock.callCount(), 0);
      const [title, options, button] = confirm.mock.calls[0].arguments;
      assert.ok(title.includes('4 calls')); assert.equal(title.includes('and continue'), false);
      assert.ok(options.detail.includes('Used calls stay counted'));
      assert.ok(options.detail.includes('does not approve a plan'));
      assert.equal(button, 'Set review allowance');
      confirm.mock.mockImplementation(async () => button);
      take().click(); await flush(); assert.equal(launch.mock.callCount(), 1);
      const [, args, env] = launch.mock.calls[0].arguments;
      assert.deepEqual(args, [...(saveOnly ? [] : ['--resume-paused']), '--planning-review-call-limit', '4', '--expected-recovery-token', '-', '--workspace', run.workspace, '--run-dir', run.runDir]);
      assert.deepEqual(env, { AUTOCODE_EXPECTED_RECOVERY_TOKEN: 'planning:current' });
      ui.setView(planningAllowanceView(saveOnly, 'planning:new'));
      take().click(); await flush(); assert.equal(launch.mock.callCount(), 1, 'stale input cannot execute again');
    });
  }
}

for (const surface of ['chat', 'Run details']) {
  test(`typed total in ${surface} is blank, validates, confirms exact total and sends only its bound action`, async (t) => {
    host.config.set('confirmPersonOnly', false);
    const ownController = new RunController(host.log);
    const ui = surface === 'chat' ? await openChat(t, totalTimeView(), run, { controller: ownController }) : await openPanel(t, totalTimeView(), undefined, ownController);
    const input = ui.document.querySelector('[data-question="max_seconds"]');
    const take = () => ui.document.querySelector('#take-action');
    assert.equal(input.value, '');
    assert.equal(input.getAttribute('aria-label'), 'Total active time (seconds)');
    assert.equal(input.inputMode, 'numeric');
    for (const text of ['Saved total: 2700', 'Active time used: 3010.885', 'at least 3011']) assert.ok(ui.document.body.textContent.includes(text), text);
    assert.equal(take().disabled, true);
    const enter = (value) => { input.value = value; input.dispatchEvent(new ui.window.Event('input')); };
    for (const value of ['3010', '0', '-1', '1.5', '5e3', 'Infinity', '9007199254740992']) {
      enter(value); assert.equal(take().disabled, true, value);
    }
    enter('5400');
    assert.equal(take().disabled, false);
    const launch = t.mock.method(cli, 'stream', async () => ok);
    const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
    take().click(); await flush();
    assert.equal(launch.mock.callCount(), 0, 'declining must leave the cap untouched');
    const [title, options, button] = confirm.mock.calls[0].arguments;
    assert.ok(title.includes('5400 seconds'));
    assert.ok(options.detail.includes('Other saved limits and model routes stay the same'));
    assert.ok(options.detail.includes('does not approve a plan'));
    assert.equal(button, 'Set total and continue');
    confirm.mock.mockImplementation(async () => button);
    take().click(); await flush();
    assert.equal(launch.mock.callCount(), 1, 'numeric resume must not add another advance');
    const [, args, env] = launch.mock.calls[0].arguments;
    assert.deepEqual(args, ['--resume-paused', '--max-seconds', '5400', '--expected-recovery-token', '-', '--workspace', run.workspace, '--run-dir', run.runDir]);
    assert.deepEqual(env, { AUTOCODE_EXPECTED_RECOVERY_TOKEN: 'time:current' });
    assert.ok(!args.includes('time:current'));
  });

  test(`typed total in ${surface} rejects a stale recovery token and clears its draft on a new offer`, async (t) => {
    const ownController = new RunController(host.log);
    const ui = surface === 'chat' ? await openChat(t, totalTimeView(), run, { controller: ownController }) : await openPanel(t, totalTimeView(), undefined, ownController);
    const field = ui.document.querySelector('[data-question="max_seconds"]');
    field.value = '5400'; field.dispatchEvent(new ui.window.Event('input'));
    const launch = t.mock.method(cli, 'stream', async () => ok);
    const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => 'Set total and continue');
    ui.setView(totalTimeView('time:new'));
    ui.document.querySelector('#take-action').click(); await flush();
    assert.equal(launch.mock.callCount(), 0);
    assert.equal(confirm.mock.callCount(), 0, 'stale click is rejected before confirmation');
    await ui.poll(totalTimeView('time:new'));
    assert.equal(ui.document.querySelector('[data-question="max_seconds"]').value, '');
    assert.equal(ui.document.querySelector('#take-action').disabled, true);
  });
}

// A cost cap held behind a plain resume (#2044): the view names the raise as the row's own command.
function heldCostView(token = 'cost:current', operational = false) {
  const raise = `AUTOCODE_EXPECTED_RECOVERY_TOKEN=${token} autocode --workspace ${run.workspace} --run-dir ${run.runDir} --resume-paused --max-cost-usd N --expected-recovery-token -`;
  const view = { status: 'PAUSED_COST_LIMIT', done: false, recovery: { token },
    needs: { kind: 'resume', command_inputs: [{ id: 'max_cost_usd', kind: 'integer', flag: '--max-cost-usd', placeholder: 'N',
      arg_index: 2, label: 'Cost cap (whole USD)', current: 2, used: 2.37, minimum: 3, maximum: Number.MAX_SAFE_INTEGER, command: raise }] },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --resume-paused` };
  if (!operational) return view;
  // AutoResolver's operational request for the same bound: the conversation takes a reply, the row the raise.
  const reply = 'AUTOCODE_RESOLVER_TOKEN=resolver autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message MESSAGE';
  return { ...view, status: 'WAITING_FOR_USER', needs: { ...view.needs, kind: 'answer', resolver_scope: 'operational_exhaustion',
    resolver_request_id: 'req-1', resolver_token: 'resolver' }, next_command: reply.replace('MESSAGE', '"WHAT CHANGED"'),
    chat: { context: 'chat-1', messages: [], input: { kind: 'resolver_response', label: 'Send recovery reply', next_command: reply } } };
}

for (const surface of ['chat', 'Run details']) {
  for (const operational of [false, true]) {
    test(`a limit held ${operational ? 'by an AutoResolver request' : 'behind a plain resume'} in ${surface} takes a typed raise through its named command`, async (t) => {
      host.config.set('confirmPersonOnly', false);
      const ownController = new RunController(host.log);
      const view = heldCostView('cost:current', operational);
      const ui = surface === 'chat' ? await openChat(t, view, run, { controller: ownController }) : await openPanel(t, view, undefined, ownController);
      const field = ui.document.querySelector('[data-question="raise:max_cost_usd"]');
      const raise = () => ui.document.querySelector('#raise-bound');
      assert.ok(field, 'the separate raise is shown');
      assert.equal(field.value, ''); assert.equal(raise().disabled, true);
      assert.equal(field.getAttribute('aria-label'), 'Cost cap (whole USD)');
      for (const text of ['Saved cost cap: $2', 'Known spend: $2.37', 'at least $3']) assert.ok(ui.document.body.textContent.includes(text), text);
      for (const value of ['2', '0', '-1', '3.5', '5e2', '9007199254740992']) {
        field.value = value; field.dispatchEvent(new ui.window.Event('input')); assert.equal(raise().disabled, true, value);
      }
      field.value = '5'; field.dispatchEvent(new ui.window.Event('input'));
      assert.equal(raise().disabled, false);
      const launch = t.mock.method(cli, 'stream', async () => ok);
      const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
      raise().click(); await flush();
      assert.equal(launch.mock.callCount(), 0, 'declining must leave the cap untouched');
      const [title, options, button] = confirm.mock.calls[0].arguments;
      assert.equal(title, 'Set the cost cap to $5 and continue?');
      assert.equal(button, 'Raise limit and continue');
      for (const text of ['only the cost cap', 'Other saved limits', 'does not approve a plan']) assert.ok(options.detail.includes(text), text);
      confirm.mock.mockImplementation(async () => button);
      raise().click(); await flush();
      assert.equal(launch.mock.callCount(), 1);
      const [, args, env] = launch.mock.calls[0].arguments;
      assert.deepEqual(args, ['--resume-paused', '--max-cost-usd', '5', '--expected-recovery-token', '-', '--workspace', run.workspace, '--run-dir', run.runDir]);
      assert.deepEqual(env, { AUTOCODE_EXPECTED_RECOVERY_TOKEN: 'cost:current' });
      ui.setView(heldCostView('cost:new', operational));
      raise().click(); await flush();
      assert.equal(launch.mock.callCount(), 1, 'a click on the old offer cannot raise the new pause');
    });
  }

  test(`a malformed separate raise in ${surface} is not offered`, async (t) => {
    const view = heldCostView();
    view.needs.command_inputs[0].command = view.needs.command_inputs[0].command.replace(run.runDir, '/other/run');
    const ui = surface === 'chat' ? await openChat(t, view) : await openPanel(t, view);
    assert.equal(ui.document.querySelector('#raise-bound'), null);
  });

  test(`an attached cost raise in ${surface} names the cost cap, not active time`, async (t) => {
    host.config.set('confirmPersonOnly', false);
    const ownController = new RunController(host.log);
    const view = heldCostView();
    view.next_command = view.needs.command_inputs[0].command;
    delete view.needs.command_inputs[0].command;
    const ui = surface === 'chat' ? await openChat(t, view, run, { controller: ownController }) : await openPanel(t, view, undefined, ownController);
    assert.equal(ui.document.querySelector('#raise-bound'), null, 'an attached row is the named action itself');
    const field = ui.document.querySelector('[data-question="max_cost_usd"]');
    field.value = '4'; field.dispatchEvent(new ui.window.Event('input'));
    const launch = t.mock.method(cli, 'stream', async () => ok);
    const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
    ui.document.querySelector('#take-action').click(); await flush();
    assert.equal(launch.mock.callCount(), 0);
    const [title, options, button] = confirm.mock.calls[0].arguments;
    assert.equal(title, 'Set the cost cap to $4 and continue?');
    assert.equal(button, 'Raise limit and continue');
    assert.ok(options.detail.includes('only the cost cap'));
  });
}

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

test('a relocated run takes the command the view names, not the start handle', () => {
  const relocated = { workspace: '/real/project', runDir: '/real/project/.autocode/runs/test' };
  const view = {
    status: 'AWAITING_GOAL_APPROVAL', done: false,
    workspace: relocated.workspace, run_dir: relocated.runDir,
    needs: { kind: 'approve_plan', token: 'r1:abc' },
    next_command: `AUTOCODE_APPROVE_GOAL_TOKEN=r1:abc autocode --workspace ${relocated.workspace} --run-dir ${relocated.runDir} --approve-goal -`,
  };
  const prepared = controller.prepare(view, run, {});
  assert.deepEqual(prepared.argv, ['--approve-goal', '-']);
  assert.deepEqual(prepared.env, { AUTOCODE_APPROVE_GOAL_TOKEN: 'r1:abc' });
  assert.throws(() => controller.prepare({ ...view, workspace: '/elsewhere' }, run, {}), /not this run/);
});

test('a relocated run accepts the checkpoint action the view names', async (t) => {
  const relocated = { workspace: '/real/project', runDir: '/real/project/.autocode/runs/test' };
  const checkpoint = (flag, extra = []) => [
    'checkpoint', '--workspace', relocated.workspace, '--run-dir', relocated.runDir, flag, 'cp1', ...extra,
  ];
  const view = {
    status: 'PAUSED', done: false, workspace: relocated.workspace, run_dir: relocated.runDir,
    code_checkpoints: { rows: [{ id: 'cp1', actions: [
      { id: 'compare', next_command: checkpoint('--compare') },
      { id: 'restore', next_command: checkpoint('--restore', ['--expected-token', '-', '--request-id', 'REQUEST_ID']) },
    ] }] },
  };
  const invoked = t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: '{}' }));
  t.mock.method(RunController.prototype, 'status', async () => view);
  assert.deepEqual(await controller.compareCheckpoint(run, 'cp1'), {});
  assert.deepEqual(invoked.mock.calls[0].arguments[1], checkpoint('--compare'));
});

test('a relocated run sends its message to the paths the view names', async (t) => {
  const relocated = { workspace: '/real/project', runDir: '/real/project/.autocode/runs/test' };
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const view = chatView();
  view.workspace = relocated.workspace;
  view.run_dir = relocated.runDir;
  view.chat.input.next_command =
    `autocode --workspace ${relocated.workspace} --run-dir ${relocated.runDir} --feedback MESSAGE`;
  await controller.sendMessage(view, run, 'hello', 'chat-1');
  assert.deepEqual(launch.mock.calls[0].arguments[1], [
    '--feedback', 'hello', '--workspace', relocated.workspace, '--run-dir', relocated.runDir,
  ]);
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

test('a narrow sidebar hides the keyboard hint and lets the composer row wrap so Send stays reachable', async (t) => {
  const ui = await openChat(t, chatView());
  const rules = [...ui.document.styleSheets].flatMap((sheet) => [...sheet.cssRules]);
  const narrow = rules.find((rule) => rule.conditionText === '(max-width: 240px)');
  assert.ok(narrow, 'chat styles need a narrow-composer rule');
  const nested = [...narrow.cssRules];
  assert.equal(nested.find((rule) => rule.selectorText === '.keyboard-hint').style.display, 'none');
  assert.equal(nested.find((rule) => rule.selectorText === '#composer .actions').style.getPropertyValue('flex-wrap'), 'wrap');
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
  assert.deepEqual(items.map((item) => item.label), ['OpenCode', 'Codex', 'Claude Code', 'Connections…', 'Stage models…', 'Configure in Settings…']);
  assert.ok(options.placeHolder.includes('Codex'), 'names the current engine');
  const run = t.mock.method(host.vscode.commands, 'executeCommand', async () => {});
  picker.mock.mockImplementation(async (choices) => choices.find((item) => item.label === 'Stage models…'));
  ui.document.getElementById('engine-picker').click();
  await flush();
  assert.deepEqual(run.mock.calls.at(-1).arguments, ['autocode.stages'], 'the chat opens the stage dropdowns');
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

function stageView(chosen = null) {
  const set = ['stages', 'set', 'terra', '--backend', 'claude', '--json', '--model', 'MODEL', '--effort', 'EFFORT'];
  return { version: 1, backend: 'claude', label: 'Claude Code', options_stop: null, stages: [{
    id: 'terra', label: 'Builder', does: 'Writes the code.', advice: 'A fast model at medium.',
    default: { model: 'haiku', effort: 'medium', offered: true }, chosen,
    options: [
      { model: 'haiku', effort_choices: ['low', 'medium'], family: 'claude', unavailable: null },
      { model: 'opus', effort_choices: ['low', 'high'], family: 'claude', unavailable: 'the Completion Reviewer runs opus' },
      { model: 'sonnet', effort_choices: ['low', 'high'], family: 'claude', unavailable: null },
    ],
    set: { next_command: set }, reset: chosen ? { next_command: ['stages', 'reset', 'terra', '--backend', 'claude', '--json'] } : null,
  }] };
}

test('stage models lists the backend new chats run on and saves only the stage view\'s own command', async (t) => {
  const calls = [];
  let chosen = null;
  t.mock.method(cli, 'invoke', async (_settings, argv) => {
    calls.push(argv);
    if (argv[1] !== 'list') chosen = argv[1] === 'set' ? { model: argv.at(-3), effort: argv.at(-1) } : null;
    return { ...ok, stdout: JSON.stringify(stageView(chosen)) };
  });
  t.mock.timers.enable({ apis: ['setInterval'] });
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  host.config.set('extraOptions', ['--engine', 'claude']);
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  await host.commands.get('autocode.stages')();
  const panel = host.panels.at(-1);
  assert.equal(panel.title, 'AutoCode Stage Models');
  assert.deepEqual(calls, [['stages', 'list', '--json', '--backend', 'claude']]);
  assert.equal(panel.webview.messages.at(-1).view.stages[0].now, 'haiku at medium (default)');
  for (const [model, effort, error] of [['evil', 'low', 'not offered evil'], ['opus', 'low', 'cannot run opus'], ['sonnet', 'max', 'sonnet takes low, high']]) {
    panel.webview.receive({ type: 'set', stage: 'terra', model, effort, next_command: ['evil'] }); await flush();
    assert.ok(panel.webview.messages.at(-1).error.includes(error), error);
  }
  assert.ok(calls.every((argv) => argv[1] === 'list'), 'nothing the view does not offer is sent');
  panel.webview.receive({ type: 'set', stage: 'terra', model: 'sonnet', effort: 'high' }); await flush();
  assert.deepEqual(calls.at(-1), ['stages', 'set', 'terra', '--backend', 'claude', '--json', '--model', 'sonnet', '--effort', 'high']);
  const saved = panel.webview.messages.at(-1);
  assert.equal(saved.note, 'New chats run the Builder on sonnet at high.');
  assert.equal(saved.view.stages[0].now, 'sonnet at high (your choice)');
  panel.webview.receive({ type: 'reset', stage: 'terra' }); await flush();
  assert.deepEqual(calls.at(-1), ['stages', 'reset', 'terra', '--backend', 'claude', '--json']);
  assert.equal(panel.webview.messages.at(-1).note, 'The Builder is back to its default.');
});

test('stage models shows the core\'s refusal and lists without a backend when none was chosen', async (t) => {
  const calls = [];
  t.mock.method(cli, 'invoke', async (_settings, argv) => {
    calls.push(argv);
    return argv[1] === 'set' ? { ...ok, code: 2, stderr: 'autocode stages: the Tester runs sonnet\n' } : { ...ok, stdout: JSON.stringify(stageView()) };
  });
  const stages = new StagesPanel();
  t.after(() => stages.dispose());
  await stages.show();
  const panel = host.panels.at(-1);
  assert.deepEqual(calls[0], ['stages', 'list', '--json']);
  panel.webview.receive({ type: 'set', stage: 'terra', model: 'sonnet', effort: 'low' }); await flush();
  assert.ok(panel.webview.messages.at(-1).error.includes('the Tester runs sonnet'));
  assert.equal(panel.webview.messages.at(-1).busy, false);
});

test('stage models renders core text safely, one model and level dropdown per stage, and sends only the choice', () => {
  const sent = [];
  const dom = new JSDOM(stagesHtml(), { runScripts: 'dangerously', beforeParse(window) {
    window.acquireVsCodeApi = () => ({ postMessage: (message) => sent.push(message) });
  } });
  try {
    const raw = stageView({ model: 'sonnet', effort: 'high' });
    raw.stages[0].does = '<img src=x onerror=alert(1)>';
    const view = readStages(JSON.stringify(raw));
    view.stages[0].now = currentRoute(view.stages[0]);
    dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data: { view, busy: false } }));
    const document = dom.window.document;
    assert.equal(document.querySelectorAll('img').length, 0);
    assert.ok(document.querySelector('main').textContent.includes('<img src=x onerror=alert(1)>'));
    const [models, levels] = document.querySelectorAll('select');
    assert.deepEqual([...models.options].map((row) => [row.value, row.disabled]), [['haiku', false], ['opus', true], ['sonnet', false]]);
    assert.ok(models.options[1].textContent.includes('not available: the Completion Reviewer runs opus'));
    assert.equal(models.value, 'sonnet', 'the dropdown opens on what the stage runs now');
    assert.deepEqual([[...levels.options].map((row) => row.value), levels.value], [['low', 'high'], 'high']);
    models.value = 'haiku'; models.dispatchEvent(new dom.window.Event('change'));
    assert.deepEqual([...levels.options].map((row) => row.value), ['low', 'medium'], 'levels follow the chosen model');
    levels.value = 'medium';
    [...document.querySelectorAll('button')].find((button) => button.textContent === 'Save').click();
    assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1))), { type: 'set', stage: 'terra', model: 'haiku', effort: 'medium' });
    [...document.querySelectorAll('button')].find((button) => button.textContent === 'Use the default').click();
    assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1))), { type: 'reset', stage: 'terra' });
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

beforeEach(() => { controller = new RunController(host.log); });

afterEach(() => {
  RunPanel.disposeAll();
  mock.restoreAll();
  host.config.clear();
  host.panels.length = 0;
  host.errors.length = 0;
  host.notices.length = 0;
});

test('start passes the selected workspace to the CLI regardless of the host directory', async (t) => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vscode-start-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  host.config.set('provider', 'chosen-route');
  const revealLog = t.mock.method(host.log, 'show', () => {});
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const selected = fs.realpathSync(folder);
  const created = await controller.start(folder, 'Build the requested CLI');
  const [options, args] = launch.mock.calls[0].arguments;
  assert.equal(args[args.indexOf('--workspace') + 1], selected, 'the core resolves the workspace; the client sends the resolved path');
  assert.equal(options.cwd, selected);
  assert.equal(args[args.indexOf('--new-run-dir') + 1], created.runDir);
  assert.ok(created.runDir.startsWith(`${selected}/.autocode/runs/`));
  assert.equal(args[args.indexOf('--provider') + 1], 'chosen-route');
  assert.ok(args.includes('--joint-planning'));
  assert.ok(args.includes('--spec-first'), 'chat must gather and confirm requirements before planning');
  assert.equal(revealLog.mock.callCount(), 0, 'starting chat must not open the diagnostic console');
});

test('start resolves a symlinked workspace so the core accepts the run destination (#1555)', async (t) => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vscode-symlink-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'real'));
  const real = fs.realpathSync(path.join(root, 'real'));
  const link = path.join(root, 'link');
  fs.symlinkSync(real, link);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const created = await controller.start(link, 'A task');
  const [, args] = launch.mock.calls[0].arguments;
  assert.equal(args[args.indexOf('--workspace') + 1], real);
  assert.ok(args[args.indexOf('--new-run-dir') + 1].startsWith(`${real}/.autocode/runs/`),
    'the destination must be an immediate child of the resolved workspace/.autocode/runs');
  assert.equal(created.workspace, real);
});

test('an action still completes when the child exits while a grandchild holds stdio open (#1556)', async (t) => {
  const { EventEmitter } = require('node:events');
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  const spawn = t.mock.method(require('node:child_process'), 'spawn', () => child);
  const action = cli.stream({ command: 'autocode', prefix: [] }, ['--resume-paused'], {}, undefined, 20);
  child.emit('exit', 2);
  const result = await action;
  assert.equal(spawn.mock.callCount(), 1);
  assert.equal(result.code, 2, 'exit alone settles the action after the grace period');
  assert.equal(result.rejected, false, 'a pause boundary is not a rejection');
  child.emit('close', 2);
  await new Promise((resolve) => setTimeout(resolve, 10));
});

test('start reports an input rejection instead of returning a nonexistent run', async (t) => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vscode-reject-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  t.mock.method(cli, 'stream', async () => ({ ...ok, code: 2, rejected: true, stderr: 'Input rejected: invalid destination' }));
  await assert.rejects(controller.start(folder, 'A task'), /invalid destination/);
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
      scope_exclusions: ['billing-api: the server response is not covered <script>excluded()</script>'],
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

async function openPanel(t, initial, onAction, panelController = controller) {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let view = initial;
  t.mock.method(panelController, 'status', async () => structuredClone(view));
  const panel = RunPanel.show(1, run, panelController, onAction);
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
  test(`excluded repositories are visible before approval in ${surface}`, async (t) => {
    const view = approvalView();
    const ui = surface === 'chat' ? await openChat(t, chatView(view)) : await openPanel(t, view);
    assert.ok(ui.document.body.textContent.includes('Not covered'));
    assert.ok(ui.document.body.textContent.includes(view.displayed_plan.scope_exclusions[0]));
    assert.equal(ui.document.querySelectorAll('script').length, 1, 'excluded work stays text');
  });
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
  await ui.poll({ ...base, displayed_plan: { ...base.displayed_plan, scope_exclusions: 'unshowable' } });
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

for (const surface of ['chat', 'details']) for (const [label, status, confirmation, prompts] of [
  ['clean requested pause', 'PAUSED_REQUESTED', false, 0],
  ['legacy requested pause', 'PAUSED_REQUESTED', undefined, 1],
  ['explicit confirmation', 'PAUSED_INTERVENTION', true, 1],
  ['failure pause', 'PAUSED_INVALID_OUTPUT', undefined, 1],
]) test(`${surface}: ${label} keeps the core resume-confirmation policy`, async (t) => {
  host.config.set('confirmPersonOnly', true);
  t.after(() => host.config.delete('confirmPersonOnly'));
  const value = { status, done: false, needs: { kind: 'resume',
    ...(confirmation === undefined ? {} : { confirmation_required: confirmation }) },
    next_command: `AUTOCODE_EXPECTED_RECOVERY_TOKEN=resume:exact autocode --resume-paused --expected-recovery-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
  };
  const ui = surface === 'chat' ? await openChat(t, value) : await openPanel(t, value);
  const prompt = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  assert.equal(launch.mock.callCount(), 0, 'a pause still waits for an explicit click');
  ui.document.querySelector('#take-action').click();
  await flush(); await flush();
  assert.equal(prompt.mock.callCount(), prompts);
  assert.equal(launch.mock.callCount(), prompts ? 0 : 1, 'declining a required dialog must keep the run paused');
  if (!prompts) {
    const [, args, env] = launch.mock.calls[0].arguments;
    assert.ok(args.includes('--resume-paused'));
    assert.ok(args.includes('--expected-recovery-token'));
    assert.equal(env.AUTOCODE_EXPECTED_RECOVERY_TOKEN, 'resume:exact');
    assert.ok(!args.includes('resume:exact'));
  }
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
  assert.match(host.errors.at(-1), /plan changed|action changed/);
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
  if (!options.timersEnabled) t.mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  let value = initial;
  const chatController = options.controller || controller;
  // UI fixtures choose a deterministic no-modes response unless the case has
  // supplied one. They must not race an unmocked external modes subprocess.
  if (!chatController.modes.mock) t.mock.method(chatController, 'modes', async () => ({ modes: [] }));
  t.mock.method(chatController, 'status', async (target) =>
    structuredClone(options.statusFor ? options.statusFor(target) : value));
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
    ...options.actions,
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

for (const [label, finalState, extraNotice] of [
  ['working', workingView(), 0],
  ['stopped', { status: 'PAUSED', done: false, needs: { kind: 'continue' } }, 1],
]) {
  test(`follow-up automatic continue stays quiet until ${label} result`, async (t) => {
    const ui = await openChat(t, chatView({ status: 'TASK_COMPLETE', done: true, needs: null }, 'follow_up'));
    assert.equal(host.notices.length, 1, 'completed turn has its own notification');
    let finishAdvance;
    const launch = t.mock.method(cli, 'stream', async (_options, args) => {
      if (args.includes('--follow-up')) {
        ui.setView({ status: 'RUNNING', done: false, needs: { kind: 'continue' },
          controller: { kind: 'ready', checked: true, alive: false },
          progress: { needs_you: 'nothing needed from you' } });
      } else {
        await new Promise((resolve) => { finishAdvance = resolve; });
        ui.setView(finalState);
      }
      return ok;
    });
    ui.type('Add peek to the same queue'); ui.submit();
    await flush(); await flush();
    assert.equal(launch.mock.callCount(), 2, 'one save and one automatic continuation');
    assert.equal(typeof finishAdvance, 'function');
    try {
      await ui.poll(); await ui.poll();
      assert.equal(host.notices.length, 1, 'client-owned continuation is not a person wait');
    } finally {
      finishAdvance(); await flush(); await flush();
    }
    assert.equal(host.notices.length, 1 + extraNotice, 'a genuine stopped checkpoint must still notify');
    await ui.poll();
    assert.equal(host.notices.length, 1 + extraNotice, 'settled result is announced only once');
  });
}

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
  assert.deepEqual(rows, ['AutoCodeHi! What would you like to work on today?', 'Youhi']);
  assert.ok(ui.document.querySelector('#app').textContent.includes('Thinking…'));
});

const RUN_MODES = {
  defaultId: 'full',
  modes: [
    { id: 'full', label: 'Full', description: 'Requirements first.', startArgs: ['--joint-planning', '--spec-first'] },
    // The core lists only Full since 2026-10-22; a made-up second mode checks the picker still offers what it lists.
    { id: 'other', label: 'Other mode', description: 'Another way.', startArgs: ['--joint-planning', '--spec-first', '--max-iterations=2'] },
  ],
};

test('a new chat starts with the run mode picked from the core list and passes its flags unchanged', async (t) => {
  t.mock.method(controller, 'modes', async () => structuredClone(RUN_MODES));
  const ui = await openChat(t, null, undefined);
  ui.chat.show(); await flush(); await flush();
  const chip = ui.document.getElementById('mode-picker');
  assert.equal(chip.hidden, false, 'a fresh chat offers a mode');
  assert.equal(ui.document.getElementById('mode-name').textContent, 'Full', 'the core default until the person picks');
  const picker = t.mock.method(host.vscode.window, 'showQuickPick', async (items) => items.find((item) => item.id === 'other'));
  chip.click(); await flush(); await flush();
  assert.equal(ui.sent.at(-1).type, 'mode');
  const [items, options] = picker.mock.calls[0].arguments;
  assert.deepEqual(items.map((item) => item.label), ['Full', 'Other mode']);
  assert.equal(items[1].detail, 'Another way.', 'the core describes each mode');
  assert.ok(options.placeHolder.includes('Full'));
  assert.equal(ui.saved.get('autocode.chat.mode'), 'other');
  assert.equal(ui.document.getElementById('mode-name').textContent, 'Other mode');
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build something'); ui.submit(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 1);
  assert.deepEqual(start.mock.calls[0].arguments[3], ['--joint-planning', '--spec-first', '--max-iterations=2']);
  assert.equal(chip.hidden, true, 'a started chat keeps the flow it started with');
  const rules = [...ui.document.styleSheets].flatMap((sheet) => [...sheet.cssRules]);
  assert.ok(rules.some((rule) => rule.selectorText === '#composer .chip[hidden]' && rule.style.display === 'none'),
    'the chip style must not override hidden');
});

test('a saved mode the core no longer lists starts new chats in its default (quick, removed 2026-10-22)', async (t) => {
  const modes = { defaultId: 'full', modes: [{ ...structuredClone(RUN_MODES.modes[0]), spendConfirmation: 'planning' }] };
  t.mock.method(controller, 'modes', async () => structuredClone(modes));
  const ui = await openChat(t, null, false, { storageValues: new Map([['autocode.chat.mode', 'quick']]) });
  ui.chat.show(); await flush(); await flush();
  assert.equal(ui.document.getElementById('mode-name').textContent, 'Full');
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build something'); ui.submit(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 1, 'the unlisted choice does not stop the chat starting');
  assert.deepEqual(start.mock.calls[0].arguments[3], ['--joint-planning', '--spec-first']);
});

test('Full chat starts a greeting without the spend dialog when the core defers it to planning', async (t) => {
  const modes = structuredClone(RUN_MODES); modes.modes[0].spendConfirmation = 'planning';
  t.mock.method(controller, 'modes', async () => modes);
  let confirmations = 0;
  const ui = await openChat(t, null, false, { actions: { confirmStart: async () => { confirmations++; return false; } } });
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('hi'); ui.submit(); await flush(); await flush();
  assert.equal(confirmations, 0);
  assert.equal(start.mock.callCount(), 1);
  assert.equal(start.mock.calls[0].arguments[1], 'hi');
  assert.deepEqual(start.mock.calls[0].arguments[3], ['--joint-planning', '--spec-first']);
  assert.ok(ui.document.querySelector('#app').textContent.includes('Thinking…'), 'actual work can show activity');
});

for (const policy of ['start', undefined]) test(`pre-launch ${policy || 'legacy'} confirmation waits without Thinking and cancellation keeps the draft`, async (t) => {
  const modes = structuredClone(RUN_MODES); modes.modes[0].spendConfirmation = policy;
  t.mock.method(controller, 'modes', async () => modes);
  let finish;
  let confirmations = 0;
  const ui = await openChat(t, null, false, { actions: {
    confirmStart: () => { confirmations++; return new Promise((resolve) => { finish = resolve; }); },
  } });
  const start = t.mock.method(controller, 'start', async () => run);
  ui.type('hi'); ui.submit(); await flush(); await flush();
  assert.equal(confirmations, 1);
  assert.equal(start.mock.callCount(), 0);
  assert.ok(ui.document.querySelector('#app').textContent.includes('Waiting to start'));
  assert.ok(!ui.document.querySelector('#app').textContent.includes('Thinking…'));
  ui.submit(); await flush();
  assert.equal(confirmations, 1, 'duplicate sends cannot open another prompt');
  finish(false); await flush(); await flush();
  assert.equal(start.mock.callCount(), 0);
  assert.equal(ui.document.querySelector('#message').value, 'hi');
  assert.equal(ui.document.querySelector('#send-message').disabled, false);
});

test('the core prepare-plan match includes normalized approvals and excludes corrections', () => {
  const { planningSpendConfirmation } = host.load('panel.js');
  const confirmation = { title: 'Prepare?', detail: 'Uses selected models.', label: 'Prepare', match: 'trim_lower' };
  const value = { chat: { review: { question_id: 'review', answer: 'plan', spend_confirmation: confirmation } } };
  for (const answer of ['plan', ' PLAN ', '\u0085PLAN\u001f']) {
    assert.equal(planningSpendConfirmation(value, { review: answer }), confirmation);
  }
  assert.equal(planningSpendConfirmation(value, { review: 'Use Ruby instead' }), null);
  assert.equal(planningSpendConfirmation(value, { another: 'plan' }), null);
});

for (const surface of ['chat', 'details']) test(`${surface}: spend is confirmed at the core prepare-plan answer and cancellation launches nothing`, async (t) => {
  host.config.set('confirmPersonOnly', false);
  t.after(() => host.config.delete('confirmPersonOnly'));
  const confirmation = { title: 'Prepare a reviewed plan?', detail: 'Planning uses your selected models and saved limits.', label: 'Prepare plan' };
  const value = { status: 'WAITING_FOR_USER', done: false,
    needs: { kind: 'answer', resolver_token: 'review:exact', questions: [
      { id: 'requirements-review-current', question: 'Is this what you want?', options: [], proposed_default: 'plan' },
    ] },
    next_command: `autocode --answer requirements-review-current=ANSWER --resolver-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: 'requirements', context: 'review', input: null, messages: [], review: {
      question_id: 'requirements-review-current', answer: 'plan', label: 'Approve understanding and prepare plan', spend_confirmation: confirmation,
    } },
  };
  const ui = surface === 'chat' ? await openChat(t, value) : await openPanel(t, value);
  const take = () => ui.document.querySelector('#take-action');
  if (surface === 'details') {
    const field = ui.document.querySelector('[data-question="requirements-review-current"]');
    field.value = 'plan'; field.dispatchEvent(new ui.window.Event('input'));
  }
  const prompt = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  take().click(); await flush(); await flush();
  assert.equal(prompt.mock.callCount(), 1);
  assert.equal(prompt.mock.calls[0].arguments[0], confirmation.title);
  assert.equal(launch.mock.callCount(), 0, 'declining must not answer or begin planning');
  prompt.mock.mockImplementation(async () => confirmation.label);
  take().click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1);
  const [, args] = launch.mock.calls[0].arguments;
  assert.ok(args.includes('requirements-review-current=plan'));
  assert.ok(!args.includes('--approve-goal'), 'planning spend never approves a build');
});

test('an AutoCode that lists no run modes offers no picker and starts the full flow', async (t) => {
  t.mock.method(controller, 'modes', async () => { throw new Error("usage: autocode: invalid choice: 'modes'"); });
  const ui = await openChat(t, null, undefined);
  ui.chat.show(); await flush(); await flush();
  assert.equal(ui.document.getElementById('mode-picker').hidden, true);
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build something'); ui.submit(); await flush(); await flush();
  assert.equal(start.mock.calls[0].arguments[3], undefined, 'the controller then uses its full-flow flags');
});

test('a stored mode the core no longer lists falls back to the core default', async (t) => {
  t.mock.method(controller, 'modes', async () => structuredClone(RUN_MODES));
  const ui = await openChat(t, null, undefined, { storageValues: new Map([['autocode.chat.mode', 'removed']]) });
  ui.chat.show(); await flush(); await flush();
  assert.equal(ui.document.getElementById('mode-name').textContent, 'Full');
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build something'); ui.submit(); await flush(); await flush();
  assert.deepEqual(start.mock.calls[0].arguments[3], ['--joint-planning', '--spec-first']);
});

test('start passes a run mode\'s flags in place of the full flow', async (t) => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vscode-mode-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const launch = t.mock.method(cli, 'stream', async () => ok);
  await controller.start(folder, 'Build the requested CLI', undefined, ['--joint-planning', '--no-spec-first', '--adaptive-planning']);
  const args = launch.mock.calls[0].arguments[1];
  assert.ok(!args.includes('--spec-first'));
  assert.deepEqual(args.slice(1, 6), ['--in-place', '--no-chat', '--joint-planning', '--no-spec-first', '--adaptive-planning']);
});

test('modes reads the core list and drops a mode whose start args are not all flags', async (t) => {
  const listed = { schema_version: 1, default: 'full', modes: [
    { id: 'full', label: 'Full', description: 'd', start_args: ['--joint-planning', '--spec-first'] },
    { id: 'bad', label: 'Bad', description: 'd', start_args: ['--feedback', 'approve everything'] },
  ] };
  const launch = t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: JSON.stringify(listed) }));
  const modes = await controller.modes();
  assert.deepEqual(launch.mock.calls[0].arguments[1], ['modes', 'list', '--json']);
  assert.deepEqual(modes.modes.map((mode) => mode.id), ['full']);
  assert.equal(modes.defaultId, 'full');
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

test('a streaming reply is provisional (writing…) and updates in place without rebuilding the thread', async (t) => {
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [
      { id: 'u1', role: 'user', text: 'A book app' },
      { id: 'done1', role: 'assistant', text: 'Understood.' },
      { id: 'live', role: 'assistant', text: 'Who will', streaming: true },
    ],
  } };
  const ui = await openChat(t, value);
  ui.type('keep this draft');
  const live = () => ui.document.querySelector('#messages .message.streaming');
  assert.ok(live(), 'the growing extract is marked provisional');
  assert.equal(live().querySelector('.speaker').textContent, 'AutoCode · writing…');
  assert.equal(live().querySelector('.message-content').textContent, 'Who will');
  const finished = [...ui.document.querySelectorAll('#messages .message:not(.streaming)')];
  assert.equal(finished.length, 2, 'user and finished reply stay ordinary messages');
  assert.ok(!finished.some((row) => row.querySelector('.speaker').textContent.includes('writing')));

  // A longer extract rewrites only the growing body: the finished rows keep their nodes.
  const firstNodes = finished.map((row) => row);
  const next = structuredClone(value);
  next.chat.messages[2].text = 'Who will use it?';
  await ui.poll(next);
  assert.equal(live().querySelector('.message-content').textContent, 'Who will use it?');
  const after = [...ui.document.querySelectorAll('#messages .message:not(.streaming)')];
  assert.deepEqual(after, firstNodes, 'finished rows are not rebuilt while only the extract grows');
  assert.equal(ui.document.querySelector('#message').value, 'keep this draft');

  // The finished message replaces the provisional one and is marked as written.
  const done = structuredClone(next);
  done.chat.messages = [
    { id: 'u1', role: 'user', text: 'A book app' },
    { id: 'done1', role: 'assistant', text: 'Understood.' },
    { id: 'ask', role: 'assistant', text: 'Who will use it?' },
  ];
  await ui.poll(done);
  assert.equal(ui.document.querySelector('#messages .message.streaming'), null);
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Who will use it?'));
  assert.ok([...ui.document.querySelectorAll('#messages .speaker')].some((row) => row.textContent === 'AutoCode'));
});

test('status polls speed up while a reply is streaming and slow down once it is quiet', async (t) => {
  const growing = (text) => ({ status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [{ id: 'u1', role: 'user', text: 'A book app' }, { id: 'live', role: 'assistant', text, streaming: true }],
  } });
  const ui = await openChat(t, growing('Who'));
  const reads = () => controller.status.mock.callCount();
  const before = reads();
  // The configured interval is 5s; a growing extract polls at the streaming pace (250ms).
  t.mock.timers.tick(250); await flush();
  assert.equal(reads(), before + 1, 'a changing extract is read at the streaming pace');
  ui.setView(growing('Who will'));
  t.mock.timers.tick(250); await flush();
  assert.equal(reads(), before + 2, 'each new extract is read at once');
  // The extract stops changing: after STREAM_QUIET_POLLS quiet reads the ordinary pace returns.
  for (let i = 0; i < 8; i++) { t.mock.timers.tick(250); await flush(); }
  const quiet = reads();
  t.mock.timers.tick(250); await flush();
  assert.equal(reads(), quiet, 'an unchanged extract no longer forces the short pace');
  t.mock.timers.tick(5000); await flush();
  assert.equal(reads(), quiet + 1, 'the ordinary poll interval is back');
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

test('a reader scrolled up sees the running step change on the Latest button without being moved', async (t) => {
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: null, context: 'live-step', input: null,
      messages: [{ id: 'a1', role: 'assistant', text: 'Builder working on the follow-up' }],
      progress: ['Builder working · task M1'] } };
  const ui = await openChat(t, value);
  ui.type('Keep this draft');
  const latest = () => ui.document.querySelector('#latest').textContent;
  assert.equal(latest(), '↓ Now: Builder working · task M1');
  const next = structuredClone(value);
  next.chat.progress = ['Tester working'];
  await ui.poll(next);
  assert.equal(latest(), '↓ Now: Tester working', 'the transcript is unchanged, the current step is not');
  assert.equal(ui.document.querySelector('#message').value, 'Keep this draft');
  await ui.poll({ ...structuredClone(next), status: 'TASK_COMPLETE', done: true, needs: { kind: 'none' } });
  assert.equal(latest(), '↓ Latest messages', 'no step is named once none is running');
});

test('the progress card is already current in the DOM while follow is paused; Latest only scrolls (#1711)', async (t) => {
  // Root-cause regression for the Builder→Tester "stale card" report: the card
  // lives inside #thread-content, so a reader who scrolled up cannot see it
  // update. The data is never stale — Latest does not refetch.
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: null, context: 'live-step', input: null,
      messages: [{ id: 'a1', role: 'assistant', text: 'Builder working on the follow-up' }],
      progress: ['Builder working · task M1'] } };
  const ui = await openChat(t, value);
  const card = () => ui.document.querySelector('#app').textContent;
  assert.ok(card().includes('Builder working · task M1'));

  // The conversation row still names the Builder; it is history, not the live card.
  const next = structuredClone(value);
  next.chat.progress = ['Tester working'];
  await ui.poll(next);
  assert.ok(card().includes('Tester working'), 'the live card updated on the poll alone');
  assert.ok(!card().includes('Builder working'), 'the live card no longer names the Builder');
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Builder working on the follow-up'),
    'the historical row is unchanged and can still be on screen above the card');

  // Latest scrolls to the bottom; it issues no status read and no named action.
  const reads = () => controller.status.mock.callCount();
  const before = reads();
  const invoked = t.mock.method(cli, 'invoke', async () => ok);
  const streamed = t.mock.method(cli, 'stream', async () => ok);
  ui.document.querySelector('#latest').click();
  await flush();
  assert.equal(reads(), before, 'Latest does not refetch status');
  assert.equal(invoked.mock.callCount(), 0);
  assert.equal(streamed.mock.callCount(), 0);
  assert.ok(card().includes('Tester working'), 'the card Latest reveals is the one the poll already wrote');
});

test('requirements chat shows only Thinking while a step is running', async (t) => {
  const value = { status: 'RUNNING', done: false, phase: 'DISCOVERING', needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, chat: { phase: 'requirements', context: 'live', input: null,
      messages: [{ id: 'u1', role: 'user', text: 'A book app' }] } };
  const ui = await openChat(t, value);
  assert.ok(ui.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(ui.document.querySelectorAll('#app button').length, 0);
});

for (const phase of ['requirements', null]) test(`chat renders core step progress during ${phase || 'build'} and updates it on poll`, async (t) => {
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase, context: 'live-step', input: null, messages: [], progress: [
      'Builder working · task M1', '1 of 3 requirements checked (1 from earlier checks)',
      'Recent steps: Planner attempt finished · Tester attempt needs correction',
    ] } };
  const ui = await openChat(t, value);
  ui.type('Keep this draft');
  const text = () => ui.document.querySelector('#app').textContent;
  for (const line of value.chat.progress) assert.ok(text().includes(line));
  assert.ok(!text().includes('Thinking…'));
  assert.equal(ui.document.querySelectorAll('#app button').length, 0);
  const next = structuredClone(value);
  next.chat.progress = ['Completion Reviewer working', '3 of 3 requirements checked', 'Recent steps: Tester attempt finished'];
  await ui.poll(next);
  for (const line of next.chat.progress) assert.ok(text().includes(line));
  assert.ok(!text().includes('Builder working'));
  assert.equal(ui.document.querySelector('#message').value, 'Keep this draft');
});

test('chat shows the running step\'s activity and the live diff the view names (#1643)', async (t) => {
  const working = ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--working'];
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    code_checkpoints: { rows: [], working: { id: 'working', label: 'Show what the Builder has changed so far', next_command: working } },
    chat: { phase: null, context: 'live-step', input: null, messages: [], progress: ['Builder working · task M1'],
      activity: [
        { kind: 'command', text: 'python3 -m unittest -q', status: 'done' },
        { kind: 'edit', text: 'edited greet.py', paths: ['greet.py'], status: 'running' },
        { kind: 'command', text: 'false', status: 'failed' },
      ] } };
  const ui = await openChat(t, value);
  const text = () => ui.document.querySelector('#app').textContent;
  assert.ok(text().includes('$python3 -m unittest -q'));
  assert.ok(text().includes('±edited greet.py'));
  assert.ok(text().includes('✗false'), 'a failed command is marked as failed, not as done');
  const calls = [];
  t.mock.method(cli, 'invoke', async (_settings, argv) => {
    calls.push(argv);
    return { ...ok, stdout: JSON.stringify({ since: 'start', changed_files: ['greet.py'],
      patch: '--- checkpoint/greet.py\n+++ current/greet.py\n+print(1)\n', truncated: false }) };
  });
  ui.document.querySelector('#working-diff').click();
  await flush();
  assert.deepEqual(calls, [working], 'only the named argv runs');
  assert.ok(text().includes('1 file(s) changed since the run started'));
  assert.ok(text().includes('+print(1)'));
  const done = structuredClone(value);
  done.code_checkpoints.working = null;
  done.chat.activity = [];
  await ui.poll(done);
  assert.equal(ui.document.querySelector('#working-diff'), null, 'no offer once the Builder step ends');
  assert.ok(!text().includes('+print(1)'), 'a diff of an ended step is not left on screen');
});

test('chat shows the live agent log (chat.log) with agent names and thinking', async (t) => {
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: null, context: 'live-step', input: null, messages: [], progress: ['Builder working · task M2'],
      // The log already carries tool marks; the bare activity list is not shown beside it.
      activity: [{ kind: 'command', text: 'duplicate of the log', status: 'done' }],
      log: [
        { id: 'a1', agent: 'Builder · task M2', kind: 'thinking', text: 'I should check the tests first' },
        { id: 'a2', agent: 'Builder · task M2', kind: 'message', text: 'Running the greeting checks now.' },
        { id: 'a3', agent: 'Builder · task M2', kind: 'command', text: 'python3 -m unittest -q', status: 'done' },
        { id: 'a4', agent: 'Plan Reviewer', kind: 'command', text: 'false', status: 'failed' },
      ] } };
  const ui = await openChat(t, value);
  const text = () => ui.document.querySelector('#app').textContent;
  assert.ok(text().includes('Builder · task M2'), 'each line names the agent');
  assert.ok(text().includes('Plan Reviewer'));
  assert.ok(text().includes('I should check the tests first'), 'reasoning is shown');
  assert.ok(text().includes('Running the greeting checks now.'), 'agent prose is shown');
  assert.ok(text().includes('python3 -m unittest -q'));
  assert.ok(text().includes('false'));
  assert.ok(!text().includes('duplicate of the log'), 'the log supersedes the bare activity list');
  assert.equal(ui.document.querySelector('#app .live-log').getAttribute('aria-label'), 'Agent log');
  const thinking = ui.document.querySelector('#app .activity-row.thinking');
  assert.ok(thinking, 'thinking rows are marked');
  assert.equal(thinking.querySelector('.activity-mark').textContent, '∴');
  assert.equal(thinking.querySelector('.agent').textContent, 'Builder · task M2');
});

test('the live agent log keeps entries by id across reads as the view moves on', async (t) => {
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: null, context: 'live-step', input: null, messages: [], progress: ['Builder working'],
      log: [{ id: 'a1', agent: 'Builder', kind: 'message', text: 'First extract' }] } };
  const ui = await openChat(t, value);
  const text = () => ui.document.querySelector('#app').textContent;
  assert.ok(text().includes('First extract'));
  // The view only holds the newest entries; earlier ones stay available by id.
  const next = structuredClone(value);
  next.chat.log = [
    { id: 'a1', agent: 'Builder', kind: 'message', text: 'First extract, completed' },
    { id: 'a2', agent: 'Tester', kind: 'command', text: 'pytest -q', status: 'running' },
  ];
  await ui.poll(next);
  assert.ok(text().includes('First extract, completed'), 'a later read of the same id updates in place');
  assert.ok(text().includes('Tester'));
  assert.ok(text().includes('pytest -q'));
});

test('a live diff the view names differently is refused before anything runs', async (t) => {
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' }, liveness: { kind: 'supervised' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    code_checkpoints: { rows: [], working: { id: 'working', next_command: ['checkpoint', '--working'] } },
    chat: { phase: null, context: 'live-step', input: null, messages: [], progress: ['Builder working'] } };
  const ui = await openChat(t, value);
  const invoked = t.mock.method(cli, 'invoke', async () => ok);
  ui.document.querySelector('#working-diff').click();
  await flush();
  assert.equal(invoked.mock.callCount(), 0);
  assert.ok(ui.document.querySelector('#app').textContent.includes('Unsupported working-diff action'));
});

function requirementsModelQuestion(token = 'model-question:current') {
  // Native0a8 public shape: core offers only its main route-glm=MODEL action,
  // with no free-text chat input or Requirements-understanding review.
  return { status: 'WAITING_FOR_USER', done: false, phase: 'DISCOVERING',
    needs: { kind: 'answer', resolver_token: token,
      questions: [{ id: 'route-glm', question: "Requirements's response exhausted its output limit; name another model to continue on", options: [] }],
      route: { question_id: 'route-glm', role: 'glm', candidates: [] } },
    next_command: `AUTOCODE_RESOLVER_TOKEN=${token} autocode --answer route-glm=MODEL --resolver-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: 'requirements', context: 'requirements-model-choice', input: null, review: null,
      messages: [{ id: 'request', role: 'user', text: 'Fix the queue ordering bug' }] },
  };
}

test('requirements model-choice renders a blank decision and sends only the named answer', async (t) => {
  const value = requirementsModelQuestion();
  const ui = await openChat(t, value);
  const field = ui.document.querySelector('[data-question="route-glm"]');
  assert.ok(field, 'the core model-choice question must remain visible in Requirements chat');
  assert.equal(field.value, '', 'the client must not choose a model');
  assert.ok(ui.document.querySelector('#app').textContent.includes(value.needs.questions[0].question));
  assert.equal(ui.document.querySelector('#take-action').disabled, true);
  assert.equal(ui.document.querySelector('#send-message').disabled, true, 'chat.input=null is not a free-text offer');
  field.value = 'fixture/alternate'; field.dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.document.querySelector('#take-action').disabled, false);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.document.querySelector('#take-action').click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1);
  const [, args, env] = launch.mock.calls[0].arguments;
  assert.deepEqual(args, ['--answer', 'route-glm=fixture/alternate', '--resolver-token', '-', '--workspace', run.workspace, '--run-dir', run.runDir]);
  assert.deepEqual(env, { AUTOCODE_RESOLVER_TOKEN: 'model-question:current' });
  const take = ui.sent.find((message) => message.type === 'take');
  assert.equal(take.runKey, `${run.workspace}\n${run.runDir}`);
  assert.ok(take.decisionContext, 'the answer retains the displayed question binding');
  assert.ok(!args.includes('--approve-goal') && !args.includes('--grant-recovery') && !args.includes('--resume-paused'));
});

test('requirements model-choice disables stale controls after a failed status read', async (t) => {
  const value = requirementsModelQuestion();
  let failing = false;
  const ui = await openChat(t, value, run, { statusFor: () => { if (failing) throw new Error('status unavailable'); return value; } });
  const field = ui.document.querySelector('[data-question="route-glm"]');
  assert.ok(field);
  field.value = 'fixture/alternate'; field.dispatchEvent(new ui.window.Event('input'));
  assert.equal(ui.document.querySelector('#take-action').disabled, false);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  failing = true; await ui.poll();
  assert.equal(ui.document.querySelector('#take-action').disabled, true);
  ui.document.querySelector('#take-action').click(); await flush();
  assert.equal(launch.mock.callCount(), 0);
  failing = false; await ui.poll();
  assert.equal(ui.document.querySelector('[data-question="route-glm"]').value, 'fixture/alternate');
  assert.equal(ui.document.querySelector('#take-action').disabled, false);
});

test('requirements model-choice rejects an answer bound to the previous question', async (t) => {
  const ui = await openChat(t, requirementsModelQuestion());
  const field = ui.document.querySelector('[data-question="route-glm"]');
  assert.ok(field);
  field.value = 'fixture/alternate'; field.dispatchEvent(new ui.window.Event('input'));
  ui.setView(requirementsModelQuestion('model-question:new'));
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.document.querySelector('#take-action').click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 0, 'a fresh read must reject the old question binding');
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
  assert.equal(ui.document.querySelector('#send-message').getAttribute('aria-label'), 'Send recovery reply');
  assert.equal(ui.document.querySelector('#send-hint').textContent, 'Send recovery reply');
});

for (const surface of ['chat', 'Run details']) {
  test(`durable Stop is explained in ${surface} without offering a resume`, async (t) => {
    const view = {
      status: 'PAUSED_INTERVENTION', done: false, needs: { kind: 'resume' }, next_command: null,
      recovery: { category: 'stopped', title: 'Stopped at your request',
        what_happened: 'The current step finished and saved. This conversation will launch no more stages.',
        retained: 'Saved plan, messages and evidence remain available.',
        actions: [{ kind: 'inspect', label: 'Inspect saved work' },
          { kind: 'new_conversation', label: 'Start a new conversation' }] },
    };
    const ui = surface === 'chat' ? await openChat(t, view) : await openPanel(t, view);
    const text = ui.document.querySelector('#app').textContent;
    for (const expected of ['Stopped at your request', 'launch no more stages', 'evidence remain available']) {
      assert.ok(text.includes(expected), expected);
    }
    assert.ok(!text.includes('No action the client can take'));
    assert.ok(!text.includes('I couldn’t finish this step'));
    assert.equal(ui.document.querySelector('#take-action'), null);
    const launch = t.mock.method(cli, 'stream', async () => ok);
    const execute = t.mock.method(host.vscode.commands, 'executeCommand', async () => {});
    ui.document.querySelector('#start-new-conversation').click(); await flush();
    if (surface === 'chat') assert.equal(ui.chat.run, undefined);
    else assert.equal(execute.mock.calls.at(-1).arguments[0], 'autocode.startRun');
    assert.equal(launch.mock.callCount(), 0, 'opening a fresh chat must launch no provider');
  });

  test(`${surface} applies polling interval changes without reload and disposes the listener`, async (t) => {
    const view = { status: 'RUNNING', done: false, needs: { kind: 'continue' } };
    const ui = surface === 'chat' ? await openChat(t, view) : await openPanel(t, view);
    await host.vscode.workspace.getConfiguration('autocode').update('pollSeconds', 30);
    const before = controller.status.mock.callCount();
    t.mock.timers.tick(5000); await flush();
    assert.equal(controller.status.mock.callCount(), before, 'the old five-second timer was replaced');
    t.mock.timers.tick(25000); await flush();
    assert.equal(controller.status.mock.callCount(), before + 1, 'the new interval polls once');
    if (surface === 'chat') ui.chat.dispose();
    else RunPanel.disposeAll();
    await host.vscode.workspace.getConfiguration('autocode').update('pollSeconds', 1);
    const disposed = controller.status.mock.callCount();
    t.mock.timers.tick(30000); await flush();
    assert.equal(controller.status.mock.callCount(), disposed, 'configuration cannot restart a disposed view');
  });
}

test('chat distinguishes saved-attempt reconciliation from the separately confirmed resume', async (t) => {
  const base = { status: 'PAUSED_PROVIDER_UNCERTAIN', done: false, needs: { kind: 'resume' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --abandon-stage 001/requirements-01`,
    recovery: { actions: [{ kind: 'abandon', args: ['--abandon-stage', '001/requirements-01'],
      label: 'Recover saved work', effect: 'Keep partial work. Resume is a separate action.' }] } };
  const ui = await openChat(t, base);
  assert.equal(ui.document.querySelector('#take-action').textContent, 'Recover saved work');
  const prompt = t.mock.method(host.vscode.window, 'showWarningMessage', async () => 'Continue');
  const launch = t.mock.method(cli, 'stream', async () => {
    ui.setView({ ...base, status: 'PAUSED_STAGE_ABANDONED',
      next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --resume-paused`,
      recovery: { actions: [{ kind: 'resume', args: ['--resume-paused'],
        label: 'Continue from saved pause', effect: 'Launch the next step under the saved limits.' }] } });
    return ok;
  });
  ui.document.querySelector('#take-action').click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1, 'reconciliation cannot silently resume the run');
  assert.deepEqual(launch.mock.calls[0].arguments[1], ['--abandon-stage', '001/requirements-01', '--workspace', run.workspace, '--run-dir', run.runDir]);
  assert.equal(prompt.mock.calls[0].arguments[0], 'Recover saved work?');
  assert.match(prompt.mock.calls[0].arguments[1].detail, /Resume is a separate action/);
  assert.equal(ui.document.querySelector('#take-action').textContent, 'Continue from saved pause');
  ui.document.querySelector('#take-action').click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 2);
  assert.equal(prompt.mock.calls[1].arguments[0], 'Continue from saved pause?');
  assert.ok(launch.mock.calls[1].arguments[1].includes('--resume-paused'));
});

test('a recovery description cannot relabel a different named command', async (t) => {
  const view = { status: 'PAUSED_PROVIDER_UNCERTAIN', done: false, needs: { kind: 'resume' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --abandon-stage 001/current`,
    recovery: { actions: [{ kind: 'resume', args: ['--resume-paused'], label: 'Incorrect resume label' }] } };
  const ui = await openChat(t, view);
  assert.equal(ui.document.querySelector('#take-action').textContent, 'Continue');
  assert.ok(!ui.document.querySelector('#app').textContent.includes('Incorrect resume label'));
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

test('Run details points to the bound operational reply instead of claiming no action exists', async (t) => {
  const view = chatView({ status: 'WAITING_FOR_USER', done: false, needs: {
    kind: 'answer', resolver_scope: 'operational_exhaustion', resolver_request_id: 'req-1', resolver_token: 'current-token',
  }, next_command: 'AUTOCODE_RESOLVER_TOKEN=current-token autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message "WHAT CHANGED"' });
  view.chat.input = { kind: 'resolver_response', label: 'Send recovery reply',
    next_command: 'AUTOCODE_RESOLVER_TOKEN=current-token autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message MESSAGE' };
  const ui = await openPanel(t, view);
  const text = ui.document.querySelector('#app').textContent;
  assert.ok(text.includes('conversation can accept a recovery reply'));
  assert.ok(!text.includes('No action the client can take'));
  assert.equal(ui.document.querySelector('#take-action'), null);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const execute = t.mock.method(host.vscode.commands, 'executeCommand', async () => {});
  ui.document.querySelector('#open-recovery-conversation').click(); await flush();
  assert.deepEqual(execute.mock.calls.at(-1).arguments, ['autocode.openRun', run]);
  assert.equal(launch.mock.callCount(), 0, 'navigation cannot submit a reply or launch a provider');
});

test('a CLI failure is surfaced instead of continuing as if the action succeeded', async (t) => {
  t.mock.method(cli, 'stream', async () => ({ ...ok, code: 1, stderr: 'provider unavailable' }));
  await assert.rejects(controller.take({ status: 'RUNNING', done: false, needs: { kind: 'continue' } }, run), /provider unavailable/);
});

test('a rejected chat start retains the request and original error across polling', async (t) => {
  const ui = await openChat(t, null);
  ui.chat.show(); await flush();
  t.mock.method(controller, 'status', async () => { throw new Error('Run not found'); });
  t.mock.method(controller, 'start', async (_workspace, _text, created) => { created(run); throw new Error('Input rejected: invalid option'); });
  ui.type('Do not lose this request'); ui.submit(); await flush(); await flush();
  await ui.poll();
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Do not lose this request'));
  assert.match(ui.document.querySelector('#chat-error').textContent, /Input rejected: invalid option/);
  assert.equal(ui.document.querySelector('#message').value, 'Do not lose this request');
  assert.equal(ui.document.querySelector('#send-message').disabled, false);
  assert.equal(ui.webviewState?.selectedRun, null);
});

test('a failed start leaves the thread holding the greeting, the request, and the failure (#1550)', async (t) => {
  const ui = await openChat(t, null);
  ui.chat.show(); await flush();
  t.mock.method(controller, 'status', async () => { throw new Error('Run not found'); });
  t.mock.method(controller, 'start', async (_workspace, _text, created) => { created(run); throw new Error('Input rejected: invalid option'); });
  ui.type('Do not lose this request'); ui.submit(); await flush(); await flush();
  const rows = [...ui.document.querySelectorAll('#messages .message')];
  assert.equal(rows.length, 3, 'greeting, the request, and the failure notice');
  assert.match(rows[0].textContent, /Hi! What would you like to work on today\?/);
  assert.match(rows[1].textContent, /Do not lose this request/);
  assert.ok(rows[2].classList.contains('notice'), 'the failure is a thread notice');
  assert.match(rows[2].textContent, /Input rejected: invalid option/);
  assert.equal(ui.document.querySelector('#send-message').disabled, false, 'the restored draft stays sendable');
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

for (const outcome of ['success', 'failure']) {
  test(`saved-code actions explain a pending chat command and recover after ${outcome}`, async (t) => {
    const ui = await openChat(t, checkpointView());
    let finish;
    const launch = t.mock.method(cli, 'stream', () => new Promise((resolve) => {
      finish = () => resolve(outcome === 'success' ? ok : { ...ok, code: 1, stderr: 'Fixture command failed' });
    }));
    t.after(() => finish?.());
    const compare = t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: JSON.stringify({
      checkpoint_id: 'cp-1', changed_files: [], patch: '', blocked_reason: null,
    }) }));
    const actions = () => [...ui.document.querySelectorAll('[data-checkpoint-action]')];
    assert.ok(actions().every((button) => !button.disabled), 'named actions start available');

    ui.document.querySelector('#take-action').click();
    await flush(); await flush();
    assert.equal(launch.mock.callCount(), 1, 'the existing command is still pending');
    for (const button of actions()) button.click();
    await flush();
    assert.equal(compare.mock.callCount(), 0, 'checkpoint clicks must not interfere with the pending command');
    assert.equal(launch.mock.callCount(), 1, 'no second run action starts');
    assert.ok(actions().every((button) => button.disabled), 'busy controls must not silently accept a click');
    assert.ok(ui.document.querySelector('#app').textContent.includes('Saved-code actions are available after the current AutoCode action finishes.'));

    finish(); await flush(); await flush();
    assert.ok(actions().every((button) => !button.disabled), 'the named actions return when the command settles');
    assert.ok(!ui.document.querySelector('#app').textContent.includes('Saved-code actions are available after the current AutoCode action finishes.'));
    ui.document.querySelector('[data-checkpoint-action="compare"]').click();
    await flush();
    assert.deepEqual(compare.mock.calls[0].arguments[1],
      ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--compare', 'cp-1']);
    assert.ok(ui.document.querySelector('#app').textContent.includes('The files match this checkpoint.'));
  });
}

test('a pending saved-code comparison explains its own busy state without duplicate reads', async (t) => {
  const ui = await openChat(t, checkpointView());
  let finish;
  const compare = t.mock.method(cli, 'invoke', () => new Promise((resolve) => {
    finish = () => resolve({ ...ok, stdout: JSON.stringify({
      checkpoint_id: 'cp-1', changed_files: [], patch: '', blocked_reason: null,
    }) });
  }));
  t.after(() => finish?.());
  const button = () => ui.document.querySelector('[data-checkpoint-action="compare"]');
  button().click(); await flush();
  assert.equal(compare.mock.callCount(), 1);
  button().click(); await flush();
  assert.equal(compare.mock.callCount(), 1, 'the pending comparison is not dispatched again');
  assert.equal(button().disabled, true, 'a pending comparison must publish its busy state immediately');
  assert.ok(ui.document.querySelector('#app').textContent.includes('Saved-code actions are available after the current AutoCode action finishes.'));
  finish(); await flush(); await flush();
  assert.equal(button().disabled, false);
  assert.ok(ui.document.querySelector('#app').textContent.includes('The files match this checkpoint.'));
});

test('a named saved-code comparison stays available during work not owned by this chat', async (t) => {
  const view = checkpointView();
  view.status = 'RUNNING';
  view.liveness = { kind: 'supervised' };
  view.needs = { kind: 'continue' };
  view.code_checkpoints.rows[0].actions = [view.code_checkpoints.rows[0].actions[0]];
  const ui = await openChat(t, view);
  const compare = t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: JSON.stringify({
    checkpoint_id: 'cp-1', changed_files: [], patch: '', blocked_reason: null,
  }) }));
  assert.ok(ui.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(ui.document.querySelector('[data-checkpoint-action="compare"]').disabled, false);
  ui.document.querySelector('[data-checkpoint-action="compare"]').click();
  await flush();
  assert.equal(compare.mock.callCount(), 1, 'working status alone must not suppress the named read-only action');
  assert.ok(ui.document.querySelector('#app').textContent.includes('The files match this checkpoint.'));
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

test('non-requirements running stage shows Thinking in chat without offering Continue (#1530, #1538)', async (t) => {
  const runningView = {
    status: 'RUNNING', done: false,
    liveness: { kind: 'supervised' },
    needs: { kind: 'continue' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: {
      context: 'ctx-1',
      messages: [{ id: 'm1', role: 'assistant', text: 'Building it now.' }],
      input: null,
      notice: 'Working on your request…',
    },
    progress: { line: 'Builder · writing the export' },
  };
  const chatUi = await openChat(t, runningView);
  assert.ok(chatUi.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(chatUi.document.querySelector('.decision-card .notice'), null, 'active build step must not duplicate Working on your request notice in decision card');
  assert.equal(chatUi.document.querySelector('#take-action'), null, 'active build step must not offer Continue');
  assert.equal(chatUi.document.querySelectorAll('#app button').length, 0);
  assert.equal(chatUi.document.querySelector('#send-hint').textContent, 'AutoCode is working. You can keep a draft here.');

  // Unsupervised running stage also renders one progress indicator without offering Continue
  await chatUi.poll({ ...runningView, liveness: { kind: 'unsupervised' } });
  assert.ok(chatUi.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(chatUi.document.querySelector('.decision-card .notice'), null);
  assert.equal(chatUi.document.querySelector('#take-action'), null);

  // Once the step stops, Continue is offered and no contradictory working notice is displayed
  await chatUi.poll({ ...runningView, liveness: { kind: 'unknown' }, chat: { ...runningView.chat, notice: null } });
  assert.ok(!chatUi.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(chatUi.document.querySelector('.decision-card .notice'), null, 'stopped continue checkpoint must not show working notice');
  assert.equal(chatUi.document.querySelector('#take-action').textContent, 'Continue');
  assert.equal(chatUi.document.querySelector('#take-action').disabled, false);
  assert.equal(chatUi.document.querySelector('#action-strip .action-prompt').textContent, 'AutoCode is waiting for you');

  // Even if a stale view retains a working notice string at a stopped checkpoint, the client suppresses it
  await chatUi.poll({ ...runningView, liveness: { kind: 'unknown' }, chat: { ...runningView.chat, notice: 'Working on your request…' } });
  assert.equal(chatUi.document.querySelector('.decision-card .notice'), null);
  assert.equal(chatUi.document.querySelector('#take-action').textContent, 'Continue');

  // Existing pause explanations are preserved
  await chatUi.poll({
    ...runningView,
    status: 'PAUSED_TRANSPORT_CHANGED',
    pause_category: 'environment',
    pause_category_label: 'Your environment',
    liveness: { kind: 'unknown' },
    chat: { ...runningView.chat, notice: 'Your model connection settings changed. Check Connections before continuing.' },
  });
  const noticeEl = chatUi.document.querySelector('.decision-card .notice');
  assert.ok(noticeEl, 'pause notice should be rendered');
  assert.equal(noticeEl.textContent, 'Your model connection settings changed. Check Connections before continuing.');
});

test('Run details disables Take action while a stage is actively running (#1530, #1538)', async (t) => {
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
  assert.ok(panelUi.document.querySelector('#app').textContent.includes('a stage is currently running'));
  assert.ok(!panelUi.document.querySelector('#app').textContent.includes('nothing; the run can simply proceed'));

  await panelUi.poll({ ...runningView, liveness: { kind: 'unsupervised' } });
  assert.equal(panelUi.take().textContent, 'Working…');
  assert.equal(panelUi.take().disabled, true);
  assert.ok(panelUi.document.querySelector('#app').textContent.includes('a stage is currently running'));

  await panelUi.poll({ ...runningView, liveness: { kind: 'unknown' } });
  assert.equal(panelUi.take().textContent, 'Take the named action');
  assert.equal(panelUi.take().disabled, false);
  assert.ok(panelUi.document.querySelector('#app').textContent.includes('nothing; the run can simply proceed'));
  assert.ok(!panelUi.document.querySelector('#app').textContent.includes('a stage is currently running'));

  await panelUi.poll({ ...runningView, liveness: { kind: 'stopped' } });
  assert.equal(panelUi.take().textContent, 'Take the named action');
  assert.equal(panelUi.take().disabled, false);
  assert.ok(panelUi.document.querySelector('#app').textContent.includes('nothing; the run can simply proceed'));
});

test('unestablished start failure rolls back provisional run, restores editable retry, and stops polling (#1539)', async (t) => {
  const ui = await openChat(t, null);
  ui.chat.show();
  await flush();

  let startCount = 0;
  t.mock.method(controller, 'start', async (_workspace, text, created) => {
    startCount++;
    if (startCount === 1) {
      created(run);
      const err = new Error('spawn autocode ENOENT');
      err.code = 'ENOENT';
      throw err;
    }
    created(run);
    return run;
  });

  let statusCalls = 0;
  t.mock.method(controller, 'status', async () => {
    statusCalls++;
    throw new Error('Run not found');
  });

  ui.type('Draft my first task');
  ui.submit();
  await flush();
  await flush();

  // 1. Provisional run was rolled back
  assert.equal(ui.chat.run, undefined, 'unestablished run must be rolled back');
  assert.equal(ui.webviewState?.selectedRun, null, 'persisted selection must be cleared');

  // 2. Draft is restored in composer and Send is enabled
  assert.equal(ui.document.querySelector('#message').value, 'Draft my first task');
  assert.equal(ui.document.querySelector('#send-message').disabled, false, 'send button must be enabled');
  assert.equal(ui.document.querySelector('#send-hint').textContent, 'Message AutoCode');
  assert.match(ui.document.querySelector('#chat-error').textContent, /ENOENT/);

  // 3. Polling does not poll the unestablished run
  const callsBeforePoll = statusCalls;
  await ui.poll();
  assert.equal(statusCalls, callsBeforePoll, 'polling must not query nonexistent run');

  // 4. User can edit the prompt and retry successfully
  ui.type('Draft my edited task');
  ui.submit();
  await flush();
  await flush();
  assert.equal(startCount, 2, 'retrying must invoke start again');
});

test('established run with subsequent status failure does not roll back run (#1539)', async (t) => {
  const ui = await openChat(t, null);
  ui.chat.show();
  await flush();

  const establishedView = { status: 'RUNNING', done: false, needs: { kind: 'continue' } };
  t.mock.method(controller, 'start', async (_workspace, _text, created) => {
    created(run);
    return run;
  });

  let shouldFail = false;
  t.mock.method(controller, 'status', async () => {
    if (shouldFail) throw new Error('Temporary provider outage');
    return establishedView;
  });

  ui.type('Task with transient status failure');
  ui.submit();
  await flush();
  await flush();

  assert.deepEqual(ui.chat.run, run, 'established run must be retained');

  // Subsequent transient status failure
  shouldFail = true;
  await ui.poll();
  assert.deepEqual(ui.chat.run, run, 'temporary read failure must not roll back an established run');
});

test('a failed status read keeps the transcript but disables chat actions until a good read (#1593)', async (t) => {
  let failing = false;
  const value = chatView();
  const ui = await openChat(t, value, run, { statusFor: () => { if (failing) throw new Error('status unavailable'); return value; } });
  const take = () => ui.document.querySelector('#take-action');
  const send = () => ui.document.querySelector('#send-message');
  ui.type('Please keep the export format');
  assert.equal(take().disabled, false);
  assert.equal(send().disabled, false);
  failing = true;
  await ui.poll();
  assert.equal(take().disabled, true, 'an action from an older read must not look live');
  assert.equal(send().disabled, true, 'feedback is bound to the input the older read offered');
  assert.ok(ui.document.querySelector('#app').textContent.includes('couldn’t read the latest status'));
  assert.ok(ui.document.querySelector('#messages').textContent.includes('Build the requested CLI'), 'transcript stays');
  assert.equal(ui.document.querySelector('#message').value, 'Please keep the export format', 'draft stays');
  assert.ok(ui.document.querySelector('#chat-error').textContent.includes('status unavailable'));
  const refresh = [...ui.document.querySelectorAll('#app button')].find((button) => button.textContent === 'Refresh');
  assert.equal(refresh.disabled, false, 'Refresh is how the reader retries');
  failing = false;
  await ui.poll();
  assert.equal(take().disabled, false);
  assert.equal(send().disabled, false);
  assert.ok(!ui.document.querySelector('#app').textContent.includes('couldn’t read the latest status'));
  assert.equal(ui.document.querySelector('#chat-error').textContent, '');
});

test('a failed status read disables requirements Continue (#1593)', async (t) => {
  let failing = false;
  const value = { status: 'RUNNING', done: false, phase: 'DISCOVERING', needs: { kind: 'continue' },
    liveness: { kind: 'unknown' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`, chat: {
      phase: 'requirements', context: 'stopped', input: null,
      messages: [{ id: 'u1', role: 'user', text: 'A book app' }] } };
  const ui = await openChat(t, value, run, { statusFor: () => { if (failing) throw new Error('status unavailable'); return value; } });
  assert.equal(ui.document.querySelector('#take-action').disabled, false);
  failing = true;
  await ui.poll();
  assert.equal(ui.document.querySelector('#take-action').disabled, true);
});

function pauseView(extra = {}) {
  return { status: 'RUNNING', done: false, liveness: { kind: 'supervised' }, needs: { kind: 'continue' },
    pause: { label: 'Pause after this step', effect: 'The current step finishes and is saved, then the run pauses.',
      requested: false, next_command: ['intervention', 'submit', '--workspace', run.workspace, '--run-dir', run.runDir,
        '--request-id', 'REQUEST_ID', '--kind', 'pause', '--json'] }, ...extra };
}

test('while the run works, Pause takes Send\'s place and runs only the named pause (#1598)', async (t) => {
  const ui = await openChat(t, pauseView());
  const pause = () => ui.document.querySelector('#pause-run');
  const send = () => ui.document.querySelector('#send-message');
  assert.equal(pause().hidden, false);
  assert.equal(pause().disabled, false);
  assert.equal(pause().textContent, 'Pause');
  assert.equal(pause().getAttribute('aria-label'), 'Pause after this step');
  assert.equal(send().hidden, true, 'Pause sits where Send was');
  ui.type('a draft');
  assert.equal(send().disabled, true, 'Enter cannot send through the hidden button');
  const launch = t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: '{"receipt":{"kind":"pause"}}' }));
  pause().click(); pause().click();
  await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1, 'a second click while submitting is ignored');
  const [, argv] = launch.mock.calls[0].arguments;
  assert.deepEqual(argv.slice(0, 6), ['intervention', 'submit', '--workspace', run.workspace, '--run-dir', run.runDir]);
  assert.match(argv[7], /^pause-[0-9a-f]{16}$/, 'the placeholder becomes this request\'s id');
  assert.deepEqual(argv.slice(8), ['--kind', 'pause', '--json']);
  await ui.poll(pauseView({ pause: { label: 'Pause after this step', requested: true, next_command: null } }));
  assert.equal(pause().disabled, true);
  assert.equal(pause().textContent, 'Pausing after this step…');
  assert.ok(ui.document.querySelector('#send-hint').textContent.includes('pauses once this step is saved'));
  await ui.poll({ status: 'PAUSED_INTERVENTION', done: false, liveness: { kind: 'unknown' }, needs: { kind: 'resume' }, pause: null,
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --resume-paused` });
  assert.equal(pause().hidden, true, 'a paused run has nothing to pause');
  assert.equal(send().hidden, false);
  assert.equal(ui.document.querySelector('#message').value, 'a draft', 'the draft survives the pause');
});

test('Pause works while this chat\'s own Continue is still running the stage (#1598)', async (t) => {
  host.config.set('confirmPersonOnly', false);
  const idle = { status: 'RUNNING', done: false, liveness: { kind: 'unknown' }, needs: { kind: 'continue' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}` };
  const ui = await openChat(t, idle);
  t.mock.method(cli, 'stream', () => new Promise(() => {}));
  ui.document.querySelector('#take-action').click();
  await flush();
  await ui.poll(pauseView());
  const pause = ui.document.querySelector('#pause-run');
  assert.equal(pause.disabled, false, 'the stage this chat launched is exactly what Pause is for');
  const launch = t.mock.method(cli, 'invoke', async () => ok);
  pause.click(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1);
});

test('Pause stays beside Send when the working run also takes a message (#1598)', async (t) => {
  const ui = await openChat(t, { ...chatView(pauseView()), status: 'RUNNING' });
  assert.equal(ui.document.querySelector('#pause-run').hidden, false);
  assert.equal(ui.document.querySelector('#send-message').hidden, false);
});

test('Pause refuses a command the view did not name in the exact pause grammar (#1598)', async (t) => {
  const changed = pauseView();
  changed.pause.next_command = changed.pause.next_command.map((word) => (word === 'pause' ? 'stop' : word));
  const ui = await openChat(t, changed);
  const launch = t.mock.method(cli, 'invoke', async () => ok);
  ui.document.querySelector('#pause-run').click();
  await flush(); await flush();
  assert.equal(launch.mock.callCount(), 0);
  assert.ok(ui.document.querySelector('#chat-error').textContent.includes('Unsupported pause action'));
});

test('no Pause without a named offer, or once the run is done (#1598)', async (t) => {
  const ui = await openChat(t, workingView());
  assert.equal(ui.document.querySelector('#pause-run').hidden, true);
  await ui.poll(pauseView({ done: true }));
  assert.equal(ui.document.querySelector('#pause-run').hidden, true);
});

test('a background tab keeps its own read error when it is shown again (#1593)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other' };
  let failing = false;
  const value = chatView();
  const ui = await openChat(t, value, run, { statusFor: (target) => {
    if (failing && target.runDir === run.runDir) throw new Error('status unavailable');
    return value;
  } });
  ui.chat.show(other);
  await flush();
  failing = true;
  await ui.poll();
  const reads = [];
  // Hold the re-read that showing the tab starts, so the first frame is the stored state.
  t.mock.method(controller, 'status', () => new Promise((resolve) => reads.push(resolve)));
  ui.chat.show(run);
  await flush();
  assert.equal(ui.document.querySelector('#take-action').disabled, true, 'shown before its re-read returns');
  assert.ok(ui.document.querySelector('#chat-error').textContent.includes('status unavailable'));
  reads.forEach((resolve) => resolve(structuredClone(value)));
});

test('in-flight start does not block New Chat or actions on other runs (#1540)', async (t) => {
  const runA = { workspace: '/ws', runDir: '/ws/.autocode/runs/run-a' };
  const runB = { workspace: '/ws', runDir: '/ws/.autocode/runs/run-b' };

  const ui = await openChat(t, null);
  ui.chat.show();
  await flush();

  let resolveStartA;
  let rejectStartA;
  t.mock.method(controller, 'start', async (_workspace, _text, created) => {
    created(runA);
    return new Promise((resolve, reject) => {
      resolveStartA = () => resolve(runA);
      rejectStartA = (err) => reject(err);
    });
  });

  // 1. Submit run A
  ui.type('Long running task A');
  ui.submit();
  await flush();

  assert.equal(ui.chat.run.runDir, runA.runDir);
  assert.equal(ui.document.querySelector('#send-hint').textContent, 'AutoCode is working. You can keep a draft here.');

  // 2. Click New Chat (+) while run A is in flight: allowed!
  ui.document.getElementById('new-chat').click();
  await flush();
  assert.equal(ui.chat.run, undefined, 'New Chat must open even while run A is in flight');
  assert.equal(ui.document.querySelector('#send-hint').textContent, 'Message AutoCode');

  // 3. Switch to existing run B
  const viewB = {
    ...questionView(),
    next_command: `autocode --workspace ${runB.workspace} --run-dir ${runB.runDir} --answer q1=ANSWER`,
  };
  t.mock.method(controller, 'status', async (r) => {
    if (r.runDir === runB.runDir) return viewB;
    throw new Error('Not found');
  });
  ui.chat.show(runB);
  await flush();
  await flush();

  assert.equal(ui.chat.run.runDir, runB.runDir);
  // Run B is NOT marked busy by run A's in-flight turn!
  const take = ui.document.querySelector('#take-action');
  assert.ok(take, 'take action exists on run B');
  assert.notEqual(take.textContent, 'Working…');

  // 4. Action on run B is not blocked
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const input = ui.document.querySelector('input');
  if (input) {
    input.value = 'answer for B';
    input.dispatchEvent(new ui.window.Event('input'));
  }
  take.click();
  await flush();
  assert.equal(launch.mock.callCount(), 1, 'action on run B must execute while run A is in flight');

  // 5. Run A fails: error must NOT leak onto run B!
  rejectStartA(new Error('Run A startup rejected: bad config'));
  await flush();
  await flush();

  assert.equal(ui.chat.run.runDir, runB.runDir, 'run B remains selected');
  assert.equal(ui.document.querySelector('#chat-error').textContent, '', 'run A error must not leak onto run B');
});

test('aborted launch when selection changes during workspace or confirmation (#1540)', async (t) => {
  const runB = { workspace: '/ws', runDir: '/ws/.autocode/runs/run-b' };
  let resolveWorkspace;
  const actions = {
    pickWorkspace: () => new Promise((resolve) => { resolveWorkspace = () => resolve(run.workspace); }),
  };

  const ui = await openChat(t, null, false, { actions });
  ui.chat.show();
  await flush();

  let startCalled = false;
  t.mock.method(controller, 'start', async () => { startCalled = true; return run; });

  ui.type('Task aborted during prompt');
  ui.submit();
  await flush();

  // User switches to run B before picking workspace completes
  ui.chat.show(runB);
  await flush();

  // Workspace prompt finishes
  resolveWorkspace();
  await flush();
  await flush();

  assert.equal(startCalled, false, 'start must not launch when selection changed during pre-launch');
  assert.deepEqual(ui.chat.run, runB, 'run B remains selected');
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

test('Run details guards against concurrent clicks during status and modal confirmation (#1542)', async (t) => {
  const view = questionView();
  view.needs.resolver_scope = 'blocker'; // Triggers person-only confirmation modal
  let resolveStatus;
  let blockStatus = false;
  const statusCalled = [];
  t.mock.method(controller, 'status', async () => {
    if (blockStatus) {
      statusCalled.push(Date.now());
      return new Promise((resolve) => { resolveStatus = () => resolve(structuredClone(view)); });
    }
    return structuredClone(view);
  });
  const panel = RunPanel.show(1, run, controller);
  const webview = host.panels.at(-1).webview;
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  const messages = [];
  dom.window.acquireVsCodeApi = () => ({ postMessage: (message) => { messages.push(message); webview.receive(message); } });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  t.after(() => dom.window.close());

  // Deliver state manually to simulate loaded panel
  const initial = { ...stateFor(view, run, controller), decisions: [{ questionId: 'q1', kind: 'answer', text: 'Which?', options: [], suggested: null }] };
  webview.deliver({ type: 'state', state: initial });
  await flush();

  blockStatus = true;
  const take = [...dom.window.document.querySelectorAll('button')].find((b) => b.textContent !== 'Refresh');
  assert.ok(take, 'take action button exists');
  const input = dom.window.document.querySelector('input');
  input.value = 'my answer';
  input.dispatchEvent(new dom.window.Event('input'));

  const launch = t.mock.method(cli, 'stream', async () => ok);
  let resolveModal;
  const modalCalled = [];
  t.mock.method(host.vscode.window, 'showWarningMessage', () => {
    modalCalled.push(Date.now());
    return new Promise((resolve) => { resolveModal = () => resolve('Take it anyway'); });
  });

  // First click: begins status check
  take.click();
  await flush();
  assert.equal(statusCalled.length, 1);

  // Second and third clicks while status is pending: dropped by taking guard
  take.click();
  take.click();
  await flush();
  assert.equal(statusCalled.length, 1, 'concurrent clicks during status must be dropped');

  // Resolve status -> enters warning modal
  resolveStatus();
  await flush();
  assert.equal(modalCalled.length, 1);

  // Fourth click while modal is open: dropped by taking guard
  take.click();
  await flush();
  assert.equal(modalCalled.length, 1, 'clicks while modal is open must be dropped');

  // Resolve modal -> proceeds to take
  blockStatus = false; // The final display refresh must settle too.
  resolveModal();
  await flush();
  await flush();
  assert.equal(launch.mock.callCount(), 1, 'only one take call executed');
});

test('Run details releases in-flight taking guard on cancellation or error (#1542)', async (t) => {
  const view = questionView();
  view.needs.resolver_scope = 'blocker';
  let modalResponse = 'Cancel';
  t.mock.method(host.vscode.window, 'showWarningMessage', async () => modalResponse);
  const panelUi = await openPanel(t, view);
  const input = panelUi.document.querySelector('input');
  input.value = 'answer 1';
  input.dispatchEvent(new panelUi.window.Event('input'));

  const launch = t.mock.method(cli, 'stream', async () => ok);

  // First attempt: user cancels modal
  panelUi.take().click();
  await flush();
  await flush();
  assert.equal(launch.mock.callCount(), 0, 'cancelled modal does not launch command');

  // Second attempt: user confirms modal -> taking guard was released, so click succeeds
  modalResponse = 'Take it anyway';
  panelUi.take().click();
  await flush();
  await flush();
  assert.equal(launch.mock.callCount(), 1, 'taking guard was released and second attempt succeeded');
});

test('Run details validates decisionContext and rejects changed retry bindings (#1542)', async (t) => {
  const retryView = {
    status: 'PAUSED_JOB_FAILURE', done: false,
    needs: { kind: 'retry_job', reason: 'Stopped', job_retry_token: 'secret-token-1' },
    next_command: `AUTOCODE_JOB_RETRY_TOKEN=secret-token-1 autocode --resume-paused --retry-failed-stage --job-retry-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
  };
  const panelUi = await openPanel(t, retryView);
  const launch = t.mock.method(cli, 'stream', async () => ok);

  // While panel is open displaying secret-token-1, the run updates with secret-token-2
  const updatedView = {
    ...retryView,
    needs: { ...retryView.needs, job_retry_token: 'secret-token-2' },
    next_command: `AUTOCODE_JOB_RETRY_TOKEN=secret-token-2 autocode --resume-paused --retry-failed-stage --job-retry-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
  };
  panelUi.setView(updatedView);

  panelUi.take().click();
  await flush();
  assert.equal(launch.mock.callCount(), 0, 'stale retry token must not be taken');
  assert.match(host.errors.at(-1), /action changed/);
});

test('Run details notifies host refresh callback after taking action (#1542)', async (t) => {
  const view = questionView();
  const refreshed = [];
  const panelUi = await openPanel(t, view, () => { refreshed.push('refreshed'); });
  const input = panelUi.document.querySelector('input');
  input.value = 'choice';
  input.dispatchEvent(new panelUi.window.Event('input'));

  t.mock.method(cli, 'stream', async () => ok);
  panelUi.take().click();
  await flush();
  await flush();
  assert.equal(refreshed.length, 1, 'onAction host refresh callback was called on take');
});


test('engineOf and routeKeyOf support equals syntax, CLI precedence, and registered providers (#1541)', () => {
  assert.equal(engineOf({ runOptions: ['--engine=codex'] }), 'codex');
  assert.equal(routeKeyOf({ runOptions: ['--engine=codex'] }), 'codex');
  assert.equal(engineOf({ runOptions: ['--engine=claude'] }), 'claude');
  assert.equal(routeKeyOf({ runOptions: ['--engine=claude'] }), 'claude');
  assert.equal(engineOf({ runOptions: ['--engine=opencode'] }), 'opencode');
  assert.equal(routeKeyOf({ runOptions: ['--engine=opencode'] }), 'opencode');

  // Provider-only Claude Code maps to Claude engine and route identity
  assert.equal(engineOf({ runOptions: ['--provider', 'claude'] }), 'claude');
  assert.equal(routeKeyOf({ runOptions: ['--provider', 'claude'] }), 'claude');
  assert.equal(engineOf({ runOptions: ['--provider=claude'] }), 'claude');
  assert.equal(routeKeyOf({ runOptions: ['--provider=claude'] }), 'claude');

  // Provider-only selections (including user-level registered providers) surface their provider name
  assert.equal(engineOf({ runOptions: ['--provider', 'kilocode'] }), 'kilocode');
  assert.equal(routeKeyOf({ runOptions: ['--provider', 'kilocode'] }), 'kilocode');
  assert.equal(engineOf({ runOptions: ['--provider', 'kilo_glm_mimo_gpt'] }), 'kilo_glm_mimo_gpt');
  assert.equal(routeKeyOf({ runOptions: ['--provider', 'kilo_glm_mimo_gpt'] }), 'kilo_glm_mimo_gpt');

  // Explicit --engine on top of a provider shows the explicit engine while keeping the provider route key
  assert.equal(engineOf({ runOptions: ['--provider', 'kilocode', '--engine', 'opencode'] }), 'opencode');
  assert.equal(routeKeyOf({ runOptions: ['--provider', 'kilocode', '--engine', 'opencode'] }), 'kilocode');

  // CLI precedence: later flag overrides earlier flag
  assert.equal(engineOf({ runOptions: ['--engine=codex', '--engine=opencode'] }), 'opencode');
  assert.equal(engineOf({ runOptions: ['--engine=opencode', '--engine=codex'] }), 'codex');
  assert.equal(engineOf({ runOptions: ['--engine', 'codex', '--engine=claude'] }), 'claude');
});

test('picking an engine or configuring a registered provider immediately updates the chat engine chip and QuickPick (#1541)', async (t) => {
  host.config.set('provider', '');
  host.config.set('extraOptions', []);
  const ui = await openChat(t, chatView());
  assert.equal(ui.document.getElementById('engine-name').textContent, 'opencode');

  // Registered provider with no --engine displays the provider in #engine-name and QuickPick
  host.config.set('provider', 'kilo_glm_mimo_gpt');
  ui.chat.post();
  await flush();
  assert.equal(ui.document.getElementById('engine-name').textContent, 'kilo_glm_mimo_gpt');
  let placeholder = '';
  t.mock.method(host.vscode.window, 'showQuickPick', async (_items, opts) => { placeholder = opts.placeHolder; return undefined; });
  ui.document.getElementById('engine-picker').click();
  await flush();
  assert.ok(placeholder.includes('kilo_glm_mimo_gpt'), `placeholder "${placeholder}" must name registered provider as current`);

  // Pick Codex
  t.mock.method(host.vscode.window, 'showQuickPick', async (items) => items.find((item) => item.engine === 'codex'));
  ui.document.getElementById('engine-picker').click();
  await flush();

  // Engine chip is immediately updated to codex without waiting for poll
  assert.equal(ui.document.getElementById('engine-name').textContent, 'codex');
  assert.equal(host.config.get('extraOptions').includes('--engine'), true);

  // Subsequent click names Codex as current
  placeholder = '';
  t.mock.method(host.vscode.window, 'showQuickPick', async (_items, opts) => { placeholder = opts.placeHolder; return undefined; });
  ui.document.getElementById('engine-picker').click();
  await flush();
  assert.ok(placeholder.includes('Codex'), `placeholder "${placeholder}" must name Codex as current`);
});

test('Connections round-trip: Codex -> Kilo -> OpenCode preserves routes and prevents contamination (#1541)', async (t) => {
  const connections = new ConnectionsPanel(host.workspaceState);
  const rows = [...connectionRows, { id: 'opencode', label: 'OpenCode', connection_status: 'not_checked', detail: 'Default', actions: [] }];
  t.mock.method(cli, 'invoke', async () => ({ code: 0, stdout: JSON.stringify({ version: 1, connections: rows }), stderr: '' }));
  t.after(() => connections.dispose());

  // 1. Start on Codex
  host.config.set('extraOptions', ['--engine', 'codex', '--max-cost-usd', '10']);
  host.config.set('provider', '');
  await connections.show();
  const panel = host.panels.at(-1);

  // Connections post should have selected: 'codex'
  const posted = panel.webview.messages.at(-1);
  assert.equal(posted.selected, 'codex');

  // 2. Select Kilo
  panel.webview.receive({ type: 'select', provider: 'kilocode' });
  await flush();
  assert.equal(host.config.get('provider'), 'kilocode');
  // Extra options for Kilo must NOT include --engine codex
  assert.deepEqual(host.config.get('extraOptions'), ['--max-cost-usd', '10']);
  // Codex routes must be preserved in storage under codex, NOT opencode
  const saved = host.workspaceState.get('autocode.connection.routes');
  assert.deepEqual(saved.codex, ['--engine', 'codex']);
  assert.ok(!saved.opencode || !saved.opencode.includes('codex'), 'opencode routes must not contain codex');

  // 3. Select OpenCode
  panel.webview.receive({ type: 'select', provider: 'opencode' });
  await flush();
  assert.equal(host.config.get('provider'), 'opencode');
  // OpenCode options must remain clean without --engine codex
  assert.deepEqual(host.config.get('extraOptions'), ['--max-cost-usd', '10']);
});

test('Direct Codex -> OpenCode switch works and cleans contaminated storage (#1541)', async (t) => {
  const connections = new ConnectionsPanel(host.workspaceState);
  const rows = [...connectionRows, { id: 'opencode', label: 'OpenCode', connection_status: 'not_checked', detail: 'Default', actions: [] }];
  t.mock.method(cli, 'invoke', async () => ({ code: 0, stdout: JSON.stringify({ version: 1, connections: rows }), stderr: '' }));
  t.after(() => connections.dispose());

  // Seed storage with previously contaminated opencode routes
  await host.workspaceState.update('autocode.connection.routes', {
    opencode: ['--engine', 'codex', '--model', 'custom/plan'],
  });

  // Start on Codex
  host.config.set('extraOptions', ['--engine', 'codex', '--max-cost-usd', '5']);
  host.config.set('provider', '');
  await connections.show();
  const panel = host.panels.at(-1);

  // Directly select OpenCode from Codex
  panel.webview.receive({ type: 'select', provider: 'opencode' });
  await flush();

  assert.equal(host.config.get('provider'), 'opencode');
  assert.deepEqual(host.config.get('extraOptions'), ['--max-cost-usd', '5', '--model', 'custom/plan']);

  // Saved routes in storage are cleansed: codex routes rescued, opencode routes clean
  const saved = host.workspaceState.get('autocode.connection.routes');
  assert.deepEqual(saved.codex, ['--engine', 'codex']);
  assert.deepEqual(saved.opencode, ['--model', 'custom/plan']);
});

test('Claude through Connections and Engine picker updates engine chip and settings (#1541)', async (t) => {
  const connections = new ConnectionsPanel(host.workspaceState);
  const rows = [...connectionRows, { id: 'claude', label: 'Claude Code', connection_status: 'not_checked', detail: 'Built-in', actions: [] }];
  t.mock.method(cli, 'invoke', async () => ({ code: 0, stdout: JSON.stringify({ version: 1, connections: rows }), stderr: '' }));
  t.after(() => connections.dispose());

  // Select Claude in Connections
  await connections.show();
  const panel = host.panels.at(-1);
  panel.webview.receive({ type: 'select', provider: 'claude' });
  await flush();
  assert.equal(host.config.get('provider'), 'claude');

  // Chat view reflects claude engine
  const ui = await openChat(t, chatView());
  assert.equal(ui.document.getElementById('engine-name').textContent, 'claude');

  // Picker names Claude Code as current
  let placeholder = '';
  t.mock.method(host.vscode.window, 'showQuickPick', async (_items, opts) => { placeholder = opts.placeHolder; return undefined; });
  ui.document.getElementById('engine-picker').click();
  await flush();
  assert.ok(placeholder.includes('Claude Code'), `placeholder "${placeholder}" must name Claude Code`);
});

test('confirmSpend names codex, claude, provider or its default correctly (#1541)', () => {
  // 1. Explicit --engine codex
  assert.equal(spendTarget({ runOptions: ['--engine', 'codex'] }), 'codex');
  assert.equal(spendTarget({ runOptions: ['--engine=codex'] }), 'codex');

  // 2. Explicit --engine claude
  assert.equal(spendTarget({ runOptions: ['--engine', 'claude'] }), 'claude');
  assert.equal(spendTarget({ runOptions: ['--engine=claude'] }), 'claude');

  // 3. Provider kilocode
  host.config.set('provider', 'kilocode');
  assert.equal(spendTarget({ runOptions: ['--provider', 'kilocode'] }), 'kilocode');

  // 4. Provider claude
  host.config.set('provider', 'claude');
  assert.equal(spendTarget({ runOptions: ['--provider', 'claude'] }), 'claude');

  // 5. Default
  host.config.set('provider', '');
  assert.equal(spendTarget({ runOptions: [] }), 'its default');
});

test('two runs open as separate tabs with names and busy marks (#1558)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-run' };
  const ui = await openChat(t, chatView());
  ui.chat.show(other);
  await flush();
  const tabs = [...ui.document.querySelectorAll('.chat-tab')];
  assert.deepEqual(tabs.map((tab) => tab.querySelector('.name').textContent), ['test', 'other-run']);
  assert.equal(tabs[0].classList.contains('active'), false);
  assert.equal(tabs[1].classList.contains('active'), true);
  assert.equal(ui.chat.run.runDir, other.runDir, 'switching tabs switches the active run');
  const active = ui.document.querySelector('.chat-tab.active .name').textContent;
  assert.equal(active, 'other-run');
});

test('one hung status read does not freeze the other tabs (#1711)', async (t) => {
  // ChatView.refresh used to await tabs in order: a hung --status on tab 1
  // blocked every later tab until it returned. Reads now start together.
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-run' };
  const ui = await openChat(t, chatView());
  ui.chat.show(other);
  await flush();
  const calls = [];
  let releaseHung;
  const hung = new Promise((resolve) => { releaseHung = resolve; });
  t.mock.method(controller, 'status', (target) => {
    calls.push(target.runDir);
    if (target.runDir === run.runDir) return hung;
    return Promise.resolve({ status: 'RUNNING', done: false, needs: { kind: 'continue' },
      chat: { phase: null, context: 'c', input: null, messages: [], progress: ['Tester working'] } });
  });
  void ui.chat.refresh();
  await flush();
  assert.deepEqual(calls, [run.runDir, other.runDir], 'both tabs start reading at once');
  // The healthy tab already landed even though the first read is still in flight.
  await flush();
  releaseHung({ status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: { phase: null, context: 'c', input: null, messages: [] } });
  await flush();
});

test('a run tab is named after the request it works on and stays in view (#1528)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/20261019-063752-vscode-bea9dd' };
  const ui = await openChat(t, chatView(), run, {
    statusFor: (target) => target.runDir === other.runDir
      ? { ...chatView(), task: 'Add a --shout flag\n\nThis follows up an earlier request' }
      : chatView(),
  });
  const scrolled = [];
  ui.window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.textContent); };
  ui.chat.show(other);
  await flush();
  const names = [...ui.document.querySelectorAll('.chat-tab .name')].map((node) => node.textContent);
  assert.deepEqual(names, ['test', 'Add a --shout flag'], 'the first line of the request, not the run directory');
  assert.ok(scrolled.some((text) => text.startsWith('Add a --shout flag')), 'the active tab is scrolled into view');
});

test('switching tabs preserves each tab draft and closes to the neighbor (#1558)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-run' };
  const ui = await openChat(t, chatView());
  ui.chat.show(other);
  await flush();
  ui.type('draft for the first run');
  [...ui.document.querySelectorAll('.chat-tab')][0].click();
  await flush();
  assert.equal(ui.chat.run.runDir, run.runDir);
  assert.equal(ui.document.querySelector('#message').value, '', 'the other tab starts with its own draft');
  ui.type('draft for the second run');
  [...ui.document.querySelectorAll('.chat-tab')][1].click();
  await flush();
  assert.equal(ui.document.querySelector('#message').value, 'draft for the first run', 'returning restores the first tab draft');
  [...ui.document.querySelectorAll('.chat-tab .close')][1].click();
  await flush();
  const tabs = [...ui.document.querySelectorAll('.chat-tab .name')].map((node) => node.textContent);
  assert.deepEqual(tabs, ['test'], 'closing the active tab removes it');
  assert.equal(ui.chat.run.runDir, run.runDir, 'the neighbor becomes active');
});

test('opening a run that already has a tab focuses it instead of duplicating (#1558)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-run' };
  const ui = await openChat(t, chatView());
  ui.chat.show(other);
  await flush();
  ui.chat.show(run);
  await flush();
  const names = [...ui.document.querySelectorAll('.chat-tab .name')].map((node) => node.textContent);
  assert.deepEqual(names, ['test', 'other-run']);
  assert.equal(ui.chat.run.runDir, run.runDir);
});

test('a second new chat opens its own tab while another run stays open (#1558)', async (t) => {
  const ui = await openChat(t, chatView());
  ui.chat.show();
  await flush();
  const names = [...ui.document.querySelectorAll('.chat-tab .name')].map((node) => node.textContent);
  assert.deepEqual(names, ['test', 'New chat']);
  assert.equal(ui.chat.run, undefined);
  const state = ui.webviewState;
  ui.type('a draft in the fresh tab');
  [...ui.document.querySelectorAll('.chat-tab')][0].click();
  await flush();
  assert.equal(ui.chat.run.runDir, run.runDir, 'the run tab is untouched');
  [...ui.document.querySelectorAll('.chat-tab')][1].click();
  await flush();
  assert.equal(ui.document.querySelector('#message').value, 'a draft in the fresh tab');
  void state;
});

test('a recreated webview restores its tabs and active chat (#1558)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-run' };
  const first = await openChat(t, chatView());
  first.chat.show(other);
  await flush();
  const restored = await openChat(t, chatView(), false, { webviewState: first.webviewState, timersEnabled: true });
  assert.deepEqual(restored.chat.run, other, 'the active tab survives webview recreation');
  const names = [...restored.document.querySelectorAll('.chat-tab .name')].map((node) => node.textContent);
  assert.deepEqual(names, ['test', 'other-run']);
});

test('switching tabs preserves each tab reading position (#1558)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-run' };
  const ui = await openChat(t, chatView());
  const thread = ui.document.querySelector('#thread');
  Object.defineProperties(thread, { scrollHeight: { value: 2000, configurable: true }, clientHeight: { value: 500 } });
  thread.scrollTop = 900;
  thread.dispatchEvent(new ui.window.WheelEvent('wheel', { deltaY: -100 }));
  ui.chat.show(other);
  await flush();
  assert.equal(thread.scrollTop, 2000, 'a tab opened for the first time starts at the latest messages');
  ui.chat.show(run);
  await flush();
  assert.equal(thread.scrollTop, 900, 'returning restores the tab reading position');
  const next = chatView();
  next.chat.messages.push({ id: 'new', role: 'assistant', text: 'A new result arrives.' });
  await ui.poll(next);
  assert.equal(thread.scrollTop, 900, 'polling the restored tab does not pull the reader to the bottom');
});

test('a busy tab shows its mark in the strip (#1558)', async (t) => {
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other-run' };
  const ui = await openChat(t, chatView());
  ui.chat.show(other);
  await flush();
  let release;
  const done = new Promise((resolve) => { release = resolve; });
  t.mock.method(controller, 'sendMessage', async () => { await done; });
  ui.type('a message that stays in flight');
  ui.submit();
  await flush();
  assert.ok(ui.document.querySelector('.chat-tab.active .dot'), 'the working tab carries a busy mark');
  release();
  await flush();
  // The busy mark only: a run left waiting on Continue earns the separate waiting mark.
  assert.equal(ui.document.querySelector('.chat-tab .dot:not(.needs):not(.finished)'), null, 'the mark clears when the action settles');
});

test('a second run starts beside an open run and both tabs remain (#1558)', async (t) => {
  const second = { workspace: '/ws', runDir: '/ws/.autocode/runs/second-run' };
  const ui = await openChat(t, chatView());
  t.mock.method(controller, 'start', async (_workspace, _brief, created) => { created(second); });
  ui.chat.show();
  await flush();
  ui.type('a second task while the first run stays open');
  ui.submit();
  await flush();
  assert.deepEqual([...ui.document.querySelectorAll('.chat-tab .name')].map((node) => node.textContent), ['test', 'second-run']);
  assert.equal(ui.chat.run.runDir, second.runDir, 'the new run\'s tab is active');
});

const alpha = { workspace: '/ws', runDir: '/ws/.autocode/runs/alpha' };
const beta = { workspace: '/ws', runDir: '/ws/.autocode/runs/beta' };

function workingView() {
  return { status: 'RUNNING', done: false, liveness: { kind: 'supervised' }, needs: { kind: 'continue' } };
}

function approveView(token = 'r1:abc') {
  return { status: 'AWAITING_GOAL_APPROVAL', done: false, needs: { kind: 'approve_plan', token } };
}

test('test_ac1_background_tab_need_notifies_once', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 1);
  assert.equal(host.notices[0].message, 'AutoCode: alpha is waiting for you - approval of the plan AutoCode displayed');
  assert.deepEqual(host.notices[0].items, ['Open chat']);
});

test('test_ac2_same_need_second_poll_does_not_repeat', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  await ui.poll();
  assert.equal(host.notices.length, 1);
});

test('test_ac3_new_need_notifies_again', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  views.set(alpha.runDir, {
    status: 'PAUSED', done: false,
    needs: { kind: 'answer', request_id: 'request-2', questions: [{ id: 'q1' }, { id: 'q2' }] },
  });
  await ui.poll();
  assert.equal(host.notices.length, 2);
  assert.equal(host.notices[1].message, 'AutoCode: alpha is waiting for you - 2 question(s)');
});

test('test_ac4_done_notifies_once_with_outcome', async (t) => {
  const views = new Map([[alpha.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  views.set(alpha.runDir, { status: 'COMPLETED', done: true });
  await ui.poll();
  assert.equal(host.notices.length, 2);
  assert.equal(host.notices[1].message, 'AutoCode: alpha finished.');
  await ui.poll();
  assert.equal(host.notices.length, 2);
});

test('test_ac5_working_and_nonwaiting_transitions_do_not_notify', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  await ui.poll();
  views.set(alpha.runDir, {
    status: 'REVIEWING', done: false, liveness: { kind: 'supervised' }, needs: { kind: 'continue' },
    progress: { line: 'Reviewing the change' },
  });
  await ui.poll();
  assert.equal(host.notices.length, 0);
});

test('test_ac6_open_chat_action_selects_the_notifying_tab', async (t) => {
  const exec = t.mock.method(host.vscode.commands, 'executeCommand', async () => {});
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 1);
  assert.equal(host.notices[0].message, 'AutoCode: alpha is waiting for you - approval of the plan AutoCode displayed');
  host.notices[0].resolve('Open chat');
  await flush();
  assert.equal(ui.chat.run.runDir, '/ws/.autocode/runs/alpha');
  assert.ok(exec.mock.calls.some((call) => call.arguments[0] === 'autocode.chat.focus'));
});

test('test_ac7_notify_false_silences_every_notification', async (t) => {
  host.config.set('notify', false);
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  views.set(alpha.runDir, { status: 'COMPLETED', done: true });
  await ui.poll();
  assert.equal(host.notices.length, 0);
});

test('test_ac8_notify_setting_registered_in_package_json', () => {
  const pkg = require('../package.json');
  const properties = pkg.contributes.configuration.properties;
  const setting = properties['autocode.notify'];
  assert.ok(properties['autocode.pollSeconds'], 'pollSeconds lives in the same properties object');
  assert.ok(properties['autocode.confirmPersonOnly'], 'confirmPersonOnly lives in the same properties object');
  assert.ok(setting, 'autocode.notify is registered');
  assert.equal(setting.type, 'boolean');
  assert.equal(setting.default, true);
  assert.equal(typeof setting.markdownDescription, 'string');
  assert.ok(setting.markdownDescription.trim().length > 0);
});

test('test_ac12_working_to_done_task_complete_notifies_once', async (t) => {
  const views = new Map([[alpha.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  views.set(alpha.runDir, { status: 'TASK_COMPLETE', done: true, needs: null });
  await ui.poll();
  assert.equal(host.notices.length, 1);
  assert.equal(host.notices[0].message, 'AutoCode: alpha finished.');
  await ui.poll();
  assert.equal(host.notices.length, 1);
});

test('test_ac13_renewed_identity_notifies_and_prior_identity_stays_silent', async (t) => {
  const views = new Map([[alpha.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  views.set(alpha.runDir, approveView('r2:def'));
  await ui.poll();
  assert.equal(host.notices.length, 2);
  assert.equal(host.notices[1].message, 'AutoCode: alpha is waiting for you - approval of the plan AutoCode displayed');
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 2);
});

test('test_ac14_other_run_same_need_notifies_independently', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 1);
  views.set(beta.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 2);
  assert.equal(host.notices[1].message, 'AutoCode: beta is waiting for you - approval of the plan AutoCode displayed');
});

test('test_ac15_failed_background_read_preserves_dedupe_without_error', async (t) => {
  let failAlpha = false;
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, {
    statusFor: (target) => {
      if (failAlpha && target.runDir === alpha.runDir) throw new Error('unavailable');
      return views.get(target.runDir);
    },
  });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 1);
  ui.chat.show();
  await flush();
  assert.equal(ui.chat.run, undefined);
  failAlpha = true;
  await ui.poll();
  assert.equal(host.notices.length, 1);
  assert.deepEqual(host.errors, []);
  failAlpha = false;
  await ui.poll();
  assert.equal(host.notices.length, 1);
  views.set(alpha.runDir, approveView('r2:def'));
  await ui.poll();
  assert.equal(host.notices.length, 2);
  assert.equal(host.notices[1].message, 'AutoCode: alpha is waiting for you - approval of the plan AutoCode displayed');
});

test('test_ac16_background_tab_notifies_while_fresh_chat_active', async (t) => {
  const views = new Map([[alpha.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show();
  await flush();
  assert.equal(ui.chat.run, undefined);
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 1);
  assert.equal(host.notices[0].message, 'AutoCode: alpha is waiting for you - approval of the plan AutoCode displayed');
  assert.deepEqual(host.notices[0].items, ['Open chat']);
});

test('test_ac17_hidden_view_background_tab_still_notifies', async (t) => {
  const views = new Map([[alpha.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  await ui.visible(false);
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 1);
  assert.equal(host.notices[0].message, 'AutoCode: alpha is waiting for you - approval of the plan AutoCode displayed');
  await ui.visible(true);
  await ui.poll();
  assert.equal(host.notices.length, 1);
});

test('test_ac18_reopened_tab_same_run_does_not_renotify', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  views.set(alpha.runDir, approveView('r1:abc'));
  await ui.poll();
  assert.equal(host.notices.length, 1);
  [...ui.document.querySelectorAll('.chat-tab .close')][0].click();
  await flush();
  ui.chat.show(alpha);
  await flush();
  await ui.poll();
  assert.equal(host.notices.length, 1);
  views.set(alpha.runDir, approveView('r2:def'));
  await ui.poll();
  assert.equal(host.notices.length, 2);
});

test('test_ac19_idle_continue_need_notifies_once', async (t) => {
  const views = new Map([[alpha.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  views.set(alpha.runDir, { status: 'PAUSED', done: false, needs: { kind: 'continue' } });
  await ui.poll();
  assert.equal(host.notices.length, 1);
  assert.equal(host.notices[0].message, 'AutoCode: alpha is waiting for you - nothing; the run can simply proceed');
  await ui.poll();
  assert.equal(host.notices.length, 1);
});

test('test_ac20_unsupervised_live_continue_does_not_notify', async (t) => {
  const views = new Map([[alpha.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  views.set(alpha.runDir, { status: 'RUNNING', done: false, liveness: { kind: 'unsupervised' }, needs: { kind: 'continue' } });
  await ui.poll();
  await ui.poll();
  assert.equal(host.notices.length, 0);
});

// Source-derived preflight boundary: autocode_preflight_attempts records probes in
// stages, not active_stage. Stage liveness is unknown, while controller_owner.inspect
// independently reports the birth-bound invocation running. No live raw preflight
// snapshot was captured in #1590; this is the public contract that reproduces it.
function controllerPreflightView() {
  return {
    status: 'RUNNING', done: false, next_stage: 'astra_challenge',
    liveness: { kind: 'unknown', reason: 'metadata_missing' },
    controller: { kind: 'running', checked: true, alive: true },
    needs: { kind: 'continue' },
    progress: { line: 'Plan Reviewer ready', needs_you: 'nothing needed from you' },
    next_command: `autocode --workspace ${alpha.workspace} --run-dir ${alpha.runDir}`,
  };
}

test('live controller preflight stays quiet and working until a real person-wait', async (t) => {
  const preflight = controllerPreflightView();
  const ui = await openChat(t, preflight, alpha);
  assert.equal(host.notices.length, 0, 'first observed access probe is work, not a person-wait');
  assert.ok(ui.document.querySelector('#app').textContent.includes('Thinking…'));
  assert.equal(ui.document.querySelector('#take-action'), null);
  await ui.poll();
  await ui.poll({ ...preflight, liveness: { kind: 'supervised' } });
  assert.equal(host.notices.length, 0, 'preflight-to-stage transition stays quiet');

  // A controller can still be returning after publishing a meaningful checkpoint.
  const approval = { ...approveView(), controller: preflight.controller };
  await ui.poll(approval);
  assert.equal(host.notices.length, 1);
  assert.match(host.notices[0].message, /approval of the plan AutoCode displayed$/);
  await ui.poll();
  assert.equal(host.notices.length, 1);
  await ui.poll({ ...approval, status: 'WAITING_FOR_USER', needs: { kind: 'answer', request_id: 'controller-question', questions: [{ id: 'q1' }] } });
  assert.equal(host.notices.length, 2, 'live controller does not hide a real question');
  await ui.poll({ status: 'TASK_COMPLETE', done: true, needs: null, controller: preflight.controller });
  assert.equal(host.notices.length, 3, 'completion remains observable while controller returns');
  await ui.poll();
  assert.equal(host.notices.length, 3);
});

test('Run details keeps live controller preflight working and releases stopped continue', async (t) => {
  const preflight = {
    ...controllerPreflightView(), progress: { line: 'Plan Reviewer ready' },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
  };
  const ui = await openPanel(t, preflight);
  assert.equal(ui.take().textContent, 'Working…');
  assert.equal(ui.take().disabled, true);
  assert.ok(ui.document.querySelector('#app').textContent.includes('a stage is currently running'));
  await ui.poll({ ...preflight, controller: { kind: 'ready', checked: true, alive: false } });
  assert.equal(ui.take().textContent, 'Take the named action');
  assert.equal(ui.take().disabled, false);
  assert.ok(ui.document.querySelector('#app').textContent.includes('nothing; the run can simply proceed'));
});

for (const [label, controllerState] of [
  ['ready', { kind: 'ready', checked: true, alive: false }],
  ['ended', { kind: 'ended', checked: true, alive: false }],
  ['unchecked', { kind: 'running', checked: false, alive: true }],
  ['dead', { kind: 'running', checked: true, alive: false }],
  ['missing', undefined],
]) {
  test(`live controller to ${label} continue notifies once and remains actionable`, async (t) => {
    const preflight = controllerPreflightView();
    const ui = await openChat(t, preflight, alpha);
    assert.equal(host.notices.length, 0);
    await ui.poll({ ...preflight, controller: controllerState });
    assert.equal(host.notices.length, 1, 'stopped or unverified ownership is not work');
    assert.match(host.notices[0].message, /waiting for you - nothing needed from you$/);
    const take = ui.document.querySelector('#take-action');
    assert.equal(take.textContent, 'Continue');
    assert.equal(take.disabled, false);
    assert.ok(!ui.document.querySelector('#app').textContent.includes('Thinking…'));
    await ui.poll();
    assert.equal(host.notices.length, 1);
  });
}

test('test_ac21_first_observed_waiting_run_notifies_once', async (t) => {
  const views = new Map([[alpha.runDir, approveView('r1:abc')]]);
  const ui = await openChat(t, approveView('r1:abc'), alpha, { statusFor: (target) => views.get(target.runDir) });
  assert.equal(host.notices.length, 1);
  assert.equal(host.notices[0].message, 'AutoCode: alpha is waiting for you - approval of the plan AutoCode displayed');
  assert.deepEqual(host.notices[0].items, ['Open chat']);
  await ui.poll();
  assert.equal(host.notices.length, 1);
});


test('cap-counted cost renders above historical cost and polling retains its unknown coverage', async (t) => {
  const initial = { ...chatView(), cost: { spent_usd: 1.966683, cap_usd: 2, complete: false, unpriced_stages: 10 },
    usage: { accounting: { cost: { historical_estimated_usd: { value: null, known: 1.5335513, complete: false, known_items: 15 } } } } };
  const ui = await openChat(t, initial);
  ui.type('Keep this unsent feedback');
  const summary = () => ui.document.querySelector('#usage-summary').textContent;
  const details = ui.document.querySelector('#usage-breakdown');
  assert.match(summary(), /Cost counted toward cap: at least \$1\.97 \/ \$2\.00 cap \(partial; 10 unpriced stages\)/);
  assert.ok(!summary().includes('$1.53'), 'the cap uses its own accounting, not the historical subtotal');
  const rows = [...details.querySelectorAll('.telemetry-row')].map((row) => row.textContent);
  assert.match(rows[0], /^Cost counted toward cap/);
  assert.ok(rows.findIndex((row) => row.startsWith('Historical cost estimate')) > 0);
  assert.match(details.textContent, /Historical cost estimate\$1\.53 \(partial\)/);
  const next = structuredClone(initial);
  next.cost.spent_usd = null;
  await ui.poll(next);
  assert.match(summary(), /Cost counted toward cap: unknown \/ \$2\.00 cap/);
  assert.ok(!summary().includes('$0.00'));
  assert.equal(ui.document.querySelector('#message').value, 'Keep this unsent feedback');
  delete next.cost;
  await ui.poll(next);
  assert.match(summary(), /\$1\.53 \(partial\) historical est\./);
  assert.ok(!summary().includes('no configured cap'), 'an older core missing cost does not mean a free or uncapped run');
});

function changesetSummary(token) {
  const plan = (id) => ({ revision: 1, acceptance_criteria: [{ id, criterion: `criterion ${id}`, verification_method: 'test', human_review: false }], constraints: [], permission_boundaries: [], scope_exclusions: [`${id}: billing-api integration is excluded`] });
  return {
    changeset: 'changeset-1',
    runs: [
      { role: 'source', target: 'ws', workspace: run.workspace, run_dir: run.runDir, status: 'AWAITING_GOAL_APPROVAL', needs: 'approve_plan', plan: plan('AC1') },
      { role: 'member', target: 'b', workspace: '/b', run_dir: '/b/.autocode/runs/m', status: 'AWAITING_GOAL_APPROVAL', needs: 'approve_plan', plan: plan('BC1') },
    ],
    approval: { set_token: token, waiting_on: [], next_command: ['autocode', 'changeset', 'approve', '--run-dir', run.runDir, '--set-token', token] },
    verification: { waiting_on: ['ws', 'b'], done: false, merge_order: ['ws', 'b'], next_command: null },
  };
}

test('Run details shows the change set and approves only the set token it showed (#2066)', async (t) => {
  host.config.set('confirmPersonOnly', false);
  const ownController = new RunController(host.log);
  let summary = changesetSummary('set-1');
  t.mock.method(cli, 'invoke', async (_settings, args) => (args[0] === 'changeset'
    ? { code: 0, stdout: JSON.stringify(summary), stderr: '', rejected: false } : ok));
  const view = { status: 'AWAITING_GOAL_APPROVAL', done: false,
    scope: { linked: [{ changeset: 'changeset-1', target: 'b', role: 'member', workspace: '/b', run_dir: '/b/.autocode/runs/m' }] } };
  const ui = await openPanel(t, view, undefined, ownController);
  const text = ui.document.body.textContent;
  for (const expected of ['Change set changeset-1', 'b (linked run): AWAITING_GOAL_APPROVAL', 'AC1: criterion AC1', 'BC1: criterion BC1', 'AC1: billing-api integration is excluded', 'BC1: billing-api integration is excluded']) {
    assert.ok(text.includes(expected), expected);
  }
  const action = () => ui.document.querySelector('#changeset-action');
  assert.equal(action().textContent, 'Approve all 2 plans');
  const launch = t.mock.method(cli, 'stream', async () => ok);
  t.mock.method(host.vscode.window, 'showWarningMessage', async () => 'Approve all');
  summary = changesetSummary('set-2'); // a plan was revised after the card was drawn
  action().click(); await flush();
  assert.equal(launch.mock.callCount(), 0, 'a stale set token approves nothing');
  await ui.poll();
  action().click(); await flush();
  assert.equal(launch.mock.callCount(), 1);
  assert.deepEqual(launch.mock.calls[0].arguments[1], ['changeset', 'approve', '--run-dir', run.runDir, '--set-token', 'set-2']);
});

// Stage meter, cost header, closing card, welcome and tab marks: the terminal chat's #2610 features.
function meteredView(extra = {}) {
  const tried = (stage, job, more = {}) => ({
    stage, job, launch_route: { model: 'claude-sonnet-5-5', reasoning_effort: 'medium' }, duration_seconds: 30, exit_code: 0,
    attributed_tokens: { input_tokens: { value: 1000, complete: true }, output_tokens: { value: 200, complete: true } }, ...more,
  });
  return {
    ...workingView(),
    chat: { context: 'meter', messages: [{ id: 'initial', role: 'user', text: 'Add a greeting CLI' }], input: null, progress: ['Tester is checking the change'] },
    usage: { accounting: {
      attempts: [
        tried('requirements_gather', 'Requirements'), tried('astra_discovery', 'Planner'), tried('astra_challenge', 'Plan Reviewer'),
        tried('terra', 'Builder', { launch_route: { model: 'claude-haiku-4-5', reasoning_effort: 'medium' } }),
        tried('sol', 'Tester', { rejected: true }), tried('sol', 'Tester', { active: true, duration_seconds: 12 }),
      ],
      tokens: { input_tokens: { value: 610000, complete: true }, output_tokens: { value: 41000, complete: true } },
    } },
    cost: { spent_usd: 1.21, cap_usd: 5, complete: true, breakdown: { by_stage: [{ stage: 'terra', spent_usd: 0.31 }, { stage: 'sol', spent_usd: 0.2 }] } },
    ...extra,
  };
}

test('the chat lists the newest stages with time, cost, model and tokens, and expands to all (#2610 parity)', async (t) => {
  const ui = await openChat(t, meteredView());
  const section = ui.document.getElementById('stages');
  assert.equal(section.hidden, false);
  const rows = [...ui.document.querySelectorAll('#stage-rows .stage')];
  assert.deepEqual(rows.map((row) => row.dataset.stage), ['astra_discovery', 'astra_challenge', 'terra', 'sol'], 'the newest four');
  const builder = rows[2];
  assert.equal(builder.querySelector('.mark').textContent, '✓');
  assert.equal(builder.querySelector('.head').textContent, 'Builder · 30s · $0.31');
  assert.equal(builder.querySelector('.detail').textContent, 'claude-haiku-4-5 medium · 1.2k tokens');
  const tester = rows[3];
  assert.ok(tester.classList.contains('retrying'));
  assert.equal(tester.querySelector('.mark').textContent, '↻');
  assert.equal(tester.querySelector('.head').textContent, 'Tester · 42s · $0.20 · attempt 2');
  assert.match(tester.querySelector('.detail').textContent, /answer rejected/);
  const more = ui.document.getElementById('stages-more');
  assert.equal(more.textContent, '1 earlier');
  more.click();
  assert.equal(ui.document.querySelectorAll('#stage-rows .stage').length, 5);
  assert.equal(more.textContent, 'Show fewer');
});

test('a stage on a later attempt shows a struggling warning in the working card', async (t) => {
  const ui = await openChat(t, meteredView());
  const warn = ui.document.getElementById('struggling');
  assert.ok(warn, 'the working card carries the warning');
  assert.equal(warn.textContent, '⚠ Tester · attempt 2 · 42s · last one answer rejected');
  const calm = meteredView();
  calm.usage.accounting.attempts = calm.usage.accounting.attempts.filter((row) => !row.rejected);
  await ui.poll(calm);
  assert.equal(ui.document.getElementById('struggling'), null, 'a first attempt is not struggling');
});

test('the toolbar shows the run cost against its cap, and a total once two chats hold runs', async (t) => {
  const views = new Map([[alpha.runDir, meteredView()], [beta.runDir, meteredView({ cost: { spent_usd: 0.5, complete: true } })]]);
  const ui = await openChat(t, meteredView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  const cost = ui.document.getElementById('run-cost');
  assert.equal(cost.textContent, '$1.21 of $5.00 · 651k tokens');
  ui.chat.show(beta);
  await flush();
  await ui.poll();
  assert.equal(cost.textContent, '$0.50 · 651k tokens · all chats $1.71 · 1.3M tokens');
});

test('a finished run closes with its result, requirements, changed files, cost and a way to the changes', async (t) => {
  const done = meteredView({
    status: 'TASK_COMPLETE', done: true, needs: { kind: 'done' }, liveness: null,
    progress: { headline: 'Complete; current delivery verified', requirements: { checked: 8, failed: 0, unchecked: 0, awaiting_review: 0 } },
    code_checkpoints: { rows: [{ id: 'c1', label: 'Builder step 1', changed_files: ['greet.py', 'README.md'], available: true, actions: [] }] },
    acceptance_tests: { files: ['test_greet.py'] },
  });
  done.usage.accounting.attempts = done.usage.accounting.attempts.map((row) => ({ ...row, active: false }));
  const ui = await openChat(t, done);
  const card = ui.document.getElementById('finish-summary');
  assert.ok(card, 'the closing card is drawn');
  assert.match(card.textContent, /✓ Complete; current delivery verified/);
  assert.match(card.textContent, /8 of 8 checked/);
  assert.match(card.textContent, /3 files: greet\.py, README\.md, test_greet\.py/);
  assert.match(card.textContent, /\$1\.21 of \$5\.00/);
  assert.ok(ui.document.getElementById('finish-show-changes'), 'saved changes exist, so it offers them');
  assert.ok(ui.document.getElementById('saved-changes'));
  ui.document.getElementById('finish-details').click();
  assert.ok(ui.sent.some((message) => message.type === 'details'));
  assert.equal(ui.document.getElementById('struggling'), null);
});

test('a finished run with no follow-up offer says so instead of pointing at an action that is not there', async (t) => {
  const ui = await openChat(t, { status: 'TASK_COMPLETE', done: true, needs: { kind: 'done' }, chat: { context: 'x', messages: [], input: null } });
  assert.equal(ui.document.getElementById('send-hint').textContent, 'This run is finished. Start a new chat with +.');
});

test('a new chat welcomes with what AutoCode does, and an example only fills the message box', async (t) => {
  const ui = await openChat(t, null, false);
  const welcome = ui.document.getElementById('welcome');
  assert.ok(welcome, 'a new chat shows the welcome');
  assert.match(welcome.textContent, /Fix.*a bug/);
  const examples = [...welcome.querySelectorAll('button.example')];
  assert.equal(examples.length, 3);
  const launched = t.mock.method(cli, 'stream', async () => ok);
  examples[1].click();
  assert.equal(ui.document.getElementById('message').value, 'Add a --json option to the export command');
  assert.equal(ui.document.getElementById('send-message').disabled, false);
  await flush();
  assert.equal(launched.mock.callCount(), 0, 'choosing an example starts nothing');
  assert.equal(ui.document.getElementById('stages').hidden, true);
  assert.equal(ui.document.getElementById('run-cost').textContent, '');
});

test('a tab whose run waits for you, or has finished, carries its own mark', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  assert.equal(ui.document.querySelector('.chat-tab .dot.needs'), null, 'working runs wait for nobody');
  views.set(alpha.runDir, approveView());
  views.set(beta.runDir, { status: 'TASK_COMPLETE', done: true, needs: { kind: 'done' } });
  await ui.poll();
  const tabs = [...ui.document.querySelectorAll('.chat-tab')];
  assert.equal(tabs[0].querySelector('.dot').className, 'dot needs');
  assert.equal(tabs[0].querySelector('.dot').title, 'Waiting for you');
  assert.equal(tabs[1].querySelector('.dot').className, 'dot finished');
});
