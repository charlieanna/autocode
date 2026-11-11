const assert = require('node:assert/strict');
const { test, beforeEach, afterEach, mock } = require('node:test');
// jsdom is loaded on first use, so a copy without it still loads this file.
function JSDOM(...args) {
  try { return new (require('jsdom').JSDOM)(...args); } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    return new MiniDom(...args);
  }
}
const { editor, flush } = require('./helpers/editor.cjs');

const host = editor();
useTypeScriptSourcesWhenNotCompiled(host);
const cli = host.load('core/cli.js');
const { RunController } = host.load('controller.js');
const { RunPanel, panelHtml, stateFor } = host.load('panel.js');
const { ChatView } = host.load('chat.js');
const { RunItem, RunsProvider } = host.load('runsView.js');
const { activate, pickWorkspace, NO_FOLDER } = host.load('extension.js');
const { ConnectionsPanel, connectionsHtml } = host.load('connections.js');
const { StagesPanel, stagesHtml, runStages } = host.load('stages.js');
const { readStages, currentRoute } = host.load('core/stages.js');
const { StageListCache } = host.load('core/stage-cache.js');
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
  const reply = 'autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message MESSAGE';
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
  const routes = ['--requirements-model', 'zai-coding-plan/glm-5.3', '--astra-model=xiaomi-token-plan-sgp/mimo-v2.6-pro', '--pin-model-role', 'astra', '--role-transport', 'terra=companytool', '--builder-model', 'openai/gpt-6-luna', '--tester-reasoning-effort=high', '--allow-uncontained-tools'];
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
  const narrow = rules.find((rule) => rule.conditionText === '(max-width: 480px)');
  assert.ok(narrow, 'chat styles need a narrow-composer rule');
  const nested = [...narrow.cssRules];
  assert.equal(nested.find((rule) => rule.selectorText === '.keyboard-hint').style.display, 'none');
  assert.equal(nested.find((rule) => rule.selectorText === '#composer .actions').style.getPropertyValue('flex-wrap'), 'wrap');
  const chipLabel = rules.find((rule) => rule.selectorText === '#composer .chip span');
  assert.ok(chipLabel, 'chip labels need an ellipsis rule');
  assert.equal(chipLabel.style.getPropertyValue('text-overflow'), 'ellipsis');
  const engineLabel = rules.find((rule) => rule.selectorText === '#engine-picker span');
  assert.ok(engineLabel, 'the engine chip label needs a width cap so a long provider id cannot push Send out of the sidebar');
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
  const folders = [];
  let chosen = null;
  // The core resolves the backend for its working directory, so every stage call runs in the folder new chats start in.
  t.after(() => { host.vscode.workspace.workspaceFolders = undefined; });
  host.vscode.workspace.workspaceFolders = [{ name: 'shop', uri: { fsPath: '/work/shop' } }, { name: 'docs', uri: { fsPath: '/work/docs' } }];
  t.mock.method(cli, 'invoke', async (settings, argv) => {
    calls.push(argv);
    folders.push(settings.cwd);
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
  assert.deepEqual([...new Set(folders)], ['/work/shop'], 'list, set and reset all run in the first open folder');
});

test('stage models shows the core\'s refusal and lists without a backend when none was chosen', async (t) => {
  const calls = [];
  host.vscode.workspace.workspaceFolders = undefined;
  t.mock.method(cli, 'invoke', async (settings, argv) => {
    assert.equal(settings.cwd, undefined, 'with no folder open the CLI keeps its own directory');
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

test('a chat\'s own stage models list and change that run in its folder, and refuse while a stage works', async (t) => {
  const runList = ['stages', 'list', '--run-dir', '/work/shop/.autocode/runs/r', '--json'];
  const set = ['--run-dir', '/work/shop/.autocode/runs/r', '--workspace', '/work/shop', '--stage-model', 'terra', 'MODEL', 'EFFORT'];
  const calls = [];
  let current = { model: 'haiku', effort: 'medium' };
  let changeable = true;
  t.mock.method(cli, 'invoke', async (settings, argv) => {
    calls.push([settings.cwd, argv]);
    if (argv[0] === '--run-dir') {
      current = { model: argv.at(-2), effort: argv.at(-1) };
      return { ...ok, stdout: 'The Builder now runs sonnet at high in this run (was haiku). Saved; no agent launched.\n' };
    }
    const view = stageView(current);
    view.stages[0].set.next_command = set; view.stages[0].reset = null;
    return { ...ok, stdout: JSON.stringify({ ...view, run_dir: '/work/shop/.autocode/runs/r', changeable,
      reason: changeable ? null : 'A stage is working.' }) };
  });
  assert.equal(runStages({ list: ['stages', 'set', 'terra'], cwd: '/work' }), undefined, 'only a run\'s read-only list');
  assert.equal(runStages({ list: runList }), undefined, 'and only with the run\'s folder');
  const stages = new StagesPanel();
  t.after(() => stages.dispose());
  await stages.show(runStages({ list: runList, cwd: '/work/shop' }));
  const panel = host.panels.at(-1);
  assert.equal(panel.title, "AutoCode: This Chat's Models");
  assert.deepEqual(calls, [['/work/shop', runList]]);
  const shown = panel.webview.messages.at(-1);
  assert.equal(shown.scope, 'run');
  assert.equal(shown.view.stages[0].now, 'haiku at medium (this chat)');
  panel.webview.receive({ type: 'set', stage: 'terra', model: 'sonnet', effort: 'high' }); await flush();
  assert.deepEqual(calls.slice(-2), [
    ['/work/shop', ['--run-dir', '/work/shop/.autocode/runs/r', '--workspace', '/work/shop', '--stage-model', 'terra', 'sonnet', 'high']],
    ['/work/shop', runList],
  ], 'the run\'s own set array, then its list again');
  const saved = panel.webview.messages.at(-1);
  assert.equal(saved.note, 'This chat runs the Builder on sonnet at high from its next launch.');
  assert.equal(saved.view.stages[0].now, 'sonnet at high (this chat)');
  changeable = false;
  panel.webview.receive({ type: 'refresh' }); await flush();
  const before = calls.length;
  panel.webview.receive({ type: 'set', stage: 'terra', model: 'haiku', effort: 'low' }); await flush();
  assert.ok(calls.slice(before).every(([, argv]) => argv[0] === 'stages'), 'nothing is changed while a stage works');
  assert.equal(panel.webview.messages.at(-1).error, 'A stage is working.');
  await stages.show();
  assert.equal(panel.title, 'AutoCode Stage Models', 'the same panel goes back to new chats\' stage models');
  assert.equal(panel.webview.messages.at(-1).scope, 'new');
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

test('a start that exits 2 before saving its run reports its own output, not a refused status read', async (t) => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vscode-unsaved-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  t.mock.method(cli, 'stream', async () => ({ ...ok, code: 2, stderr: 'Claude Code is not signed in' }));
  t.mock.method(controller, 'status', async (target) => { throw new Error(`--run-dir ${target.runDir} has no state.json`); });
  await assert.rejects(controller.start(folder, 'hi'), (error) =>
    error.message.includes('AutoCode stopped before it saved the run') && error.message.includes('Claude Code is not signed in')
    && !error.message.includes('state.json'));
});

test('a start that exits 2 at a saved pause returns the run', async (t) => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vscode-paused-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  t.mock.method(cli, 'stream', async () => ({ ...ok, code: 2 }));
  const status = t.mock.method(controller, 'status', async () => chatView());
  const created = await controller.start(folder, 'hi');
  assert.deepEqual(status.mock.calls[0].arguments[0], created);
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
    async poll(next = view) { view = next; t.mock.timers.tick(5000); await flush(); await panel.refresh(); },
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

test('the Runs list puts the most recently active chats on top under Recent', async () => {
  const listed = [
    { id: 'old', workspace: '/alpha', runDir: '/alpha/.autocode/runs/20261001-old', availability: 'available',
      status: 'COMPLETE', title: 'Old alpha chat', updatedAt: '2026-10-01T10:00:00Z' },
    { id: 'idle', workspace: '/beta', runDir: '/beta/.autocode/runs/20261109-idle', availability: 'available',
      status: 'COMPLETE', title: null, updatedAt: '2026-11-09T10:00:00Z' },
    { id: 'today', workspace: '/alpha', runDir: '/alpha/.autocode/runs/20260901-today', availability: 'available',
      status: 'AWAITING_GOAL_APPROVAL', title: 'Fix the login page', updatedAt: '2026-11-11T11:00:00Z' },
    { id: 'gone', workspace: '/gamma', runDir: '/gamma/.autocode/runs/x', availability: 'workspace_missing',
      diagnostic: 'Workspace no longer exists' },
  ];
  const provider = new RunsProvider({ list: async () => ({ runs: listed, diagnostics: [] }) });
  const now = mock.method(Date, 'now', () => Date.parse('2026-11-11T12:00:00Z'));
  try {
    await provider.refresh();
    const [recent, ...projects] = provider.getChildren();
    assert.equal(recent.label, 'Recent');
    assert.equal(provider.getTreeItem(recent).collapsibleState, 2);
    const rows = provider.getChildren(recent);
    assert.deepEqual(rows.map((row) => row.run.id), ['today', 'idle', 'old']);
    assert.deepEqual(rows.map((row) => row.description), ['1h ago · alpha', '2d ago · beta', '2026-10-01 · alpha']);
    assert.equal(rows[0].label, 'Fix the login page');
    assert.equal(rows[1].label, '20261109-idle');
    assert.equal(rows[0].id, 'recent:today');
    // Projects follow, newest first and folded, each with its own chats newest first.
    assert.deepEqual(projects.map((node) => node.label), ['alpha', 'beta', 'gamma']);
    assert.ok(projects.every((node) => provider.getTreeItem(node).collapsibleState === 1));
    const alpha = provider.getChildren(projects[0]);
    assert.deepEqual(alpha.map((row) => row.run.id), ['today', 'old']);
    assert.equal(alpha[0].id, 'today');
    assert.equal(alpha[0].description, '1h ago · AWAITING_GOAL_APPROVAL');
  } finally {
    now.mock.restore();
    provider.dispose();
  }
});

test('a finished chat shows the check icon, not the working sync icon', async () => {
  const listed = [
    { id: 'done', workspace: '/alpha', runDir: '/alpha/.autocode/runs/done', availability: 'available',
      status: 'TASK_COMPLETE', title: 'Done chat', updatedAt: '2026-11-11T11:00:00Z' },
    { id: 'running', workspace: '/alpha', runDir: '/alpha/.autocode/runs/running', availability: 'available',
      status: 'RUNNING', title: 'Running chat', updatedAt: '2026-11-11T11:05:00Z' },
    { id: 'paused', workspace: '/alpha', runDir: '/alpha/.autocode/runs/paused', availability: 'available',
      status: 'PAUSED_REQUESTED', title: 'Paused chat', updatedAt: '2026-11-11T11:03:00Z' },
  ];
  const provider = new RunsProvider({ list: async () => ({ runs: listed, diagnostics: [] }) });
  try {
    await provider.refresh();
    const [recent] = provider.getChildren();
    const rows = provider.getChildren(recent);
    const icon = (id) => rows.find((row) => row.run.id === id)?.iconPath?.id;
    assert.equal(icon('done'), 'check', 'TASK_COMPLETE is a finished chat');
    assert.equal(icon('running'), 'sync');
    assert.equal(icon('paused'), 'report');
  } finally {
    provider.dispose();
  }
});

test('registry rows carry the archived flag and only named cleanup actions', () => {
  const { readRegistryList } = host.load('core/view.js');
  const list = readRegistryList(JSON.stringify({ runs: [{
    id: 'r1', workspace: '/ws', run_dir: '/ws/.autocode/runs/r1', task_id: null,
    availability: 'available', archived: true, diagnostic: { status: 'TASK_COMPLETE' },
    actions: [
      { id: 'unarchive', label: 'Unarchive',
        next_command: ['registry', 'unarchive', '--workspace', '/ws', '--run-dir', '/ws/.autocode/runs/r1'] },
      { id: 'broken', label: 'Broken', next_command: 'not-an-array' },
    ],
  }], diagnostics: [] }));
  assert.equal(list.runs[0].archived, true);
  assert.equal(list.runs[0].status, 'TASK_COMPLETE');
  assert.deepEqual(list.runs[0].actions, [{
    id: 'unarchive', label: 'Unarchive',
    nextCommand: ['registry', 'unarchive', '--workspace', '/ws', '--run-dir', '/ws/.autocode/runs/r1'],
  }]);
});

test('archived chats are marked and shown only when the list asks for them', async () => {
  const calls = [];
  const listed = [{
    id: 'archived', workspace: '/alpha', runDir: '/alpha/.autocode/runs/archived',
    availability: 'available', status: 'TASK_COMPLETE', title: 'Old finished chat',
    updatedAt: '2026-10-01T10:00:00Z', archived: true,
    actions: [{ id: 'unarchive', label: 'Unarchive',
      nextCommand: ['registry', 'unarchive', '--workspace', '/alpha', '--run-dir', '/alpha/.autocode/runs/archived'] }],
  }];
  const provider = new RunsProvider({ list: async (includeArchived) => {
    calls.push(includeArchived);
    return { runs: listed, diagnostics: [] };
  } });
  try {
    await provider.refresh();
    assert.deepEqual(calls, [false]);
    const [archivedGroup, project] = provider.getChildren();
    assert.equal(archivedGroup.label, 'Archived');
    assert.equal(provider.getTreeItem(archivedGroup).collapsibleState, 2, 'archived chats are visible');
    assert.notEqual(project.label, 'Recent', 'an archived chat is never in Recent');
    const [row] = provider.getChildren(archivedGroup);
    assert.equal(row.contextValue, 'autocode.runArchived');
    assert.ok(row.description.includes('archived'));
    await provider.setShowArchived(true);
    assert.deepEqual(calls, [false, true]);
  } finally {
    provider.dispose();
  }
});

test('a registry cleanup action runs only the exact command the row named', async (t) => {
  const launch = t.mock.method(cli, 'invoke', async () => ok);
  const controller = new RunController(host.log);
  const archive = ['registry', 'archive', '--workspace', run.workspace, '--run-dir', run.runDir];
  await controller.registryAction(run, { id: 'archive', label: 'Archive', nextCommand: archive });
  assert.deepEqual(launch.mock.calls[0].arguments[1], archive);
  const remove = ['registry', 'delete', '--workspace', run.workspace, '--run-dir', run.runDir, '--yes'];
  await controller.registryAction(run, { id: 'delete', label: 'Delete', nextCommand: remove });
  assert.deepEqual(launch.mock.calls[1].arguments[1], remove);
  await assert.rejects(
    () => controller.registryAction(run, { id: 'archive', label: 'Archive',
      nextCommand: ['registry', 'archive', '--workspace', '/elsewhere', '--run-dir', run.runDir] }),
    /Unsupported registry action from AutoCode/,
  );
  assert.equal(launch.mock.callCount(), 2, 'a command outside the grammar runs nothing');
});

test('deleting the chat the status line follows stops the status line', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  let statusVisible = false;
  const mockStatus = {
    show() { statusVisible = true; }, hide() { statusVisible = false; }, dispose() {},
    text: '', tooltip: '', command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  t.mock.method(RunController.prototype, 'status', async () => ({
    status: 'RUNNING', done: false, needs: { kind: 'continue' }, progress: { headline: 'Builder working' } }));
  const acted = t.mock.method(RunController.prototype, 'registryAction', async () => {});
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));

  await host.commands.get('autocode.openRun')(run);
  await flush();
  assert.equal(statusVisible, true, 'the status line follows the opened chat');

  const row = new RunItem({
    id: 'r1', workspace: run.workspace, runDir: run.runDir, taskId: null,
    availability: 'available', status: 'RUNNING',
    actions: [{ id: 'delete', label: 'Delete',
      nextCommand: ['registry', 'delete', '--workspace', run.workspace, '--run-dir', run.runDir, '--yes'] }],
  }, run.workspace);
  t.mock.method(host.vscode.window, 'showWarningMessage', async () => 'Delete');
  await host.commands.get('autocode.deleteRun')(row);
  await flush();
  assert.equal(acted.mock.callCount(), 1, 'the named delete action ran');
  assert.equal(statusVisible, false, 'the status line stops following a chat that no longer exists');
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
  // A chat that has not started reads the stages it will run. Unless the case supplies a list,
  // the core answers an empty one, so no external `autocode stages` subprocess is raced.
  if (!cli.invoke.mock) {
    const original = cli.invoke;
    const stages = options.stages || (() => ({ ...ok, stdout: JSON.stringify({ version: 1, stages: [] }) }));
    // Where a new chat works: unless the case supplies a list, an AutoCode without one answers, so the
    // chat starts in the folder as it did before and no external `autocode worktrees` subprocess is raced.
    const worktrees = options.worktrees || (() => ({ ...ok, code: 2, stdout: '', stderr: 'unknown command' }));
    // A copy loaded from TypeScript sources has read-only module namespaces: the mock cannot be
    // installed there, and the chat then reads the missing external `autocode` as unavailable.
    try {
      t.mock.method(cli, 'invoke', async (settings, argv, ...rest) => (argv[0] === 'stages' ? stages(argv, settings, rest[1])
        : argv[0] === 'worktrees' ? worktrees(argv, settings) : original(settings, argv, ...rest)));
    } catch (error) { if (!(error instanceof TypeError)) throw error; }
  }
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
  }, options.stageLists);
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
    async poll(next = value) { value = next; t.mock.timers.tick(5000); await flush(); await chat.refresh(); },
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
  assert.match(ui.document.querySelector('#model-info').textContent, /Nothing has run yet\. A new chat runs the models in Stage models\./,
    'without a per-chat list the new chat names where its models come from');
  assert.equal(ui.document.querySelector('#models-picker').hidden, true, 'no list, no Models chip');
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
  // A started chat keeps its routes: the section says so, so a Stage models change is not expected here.
  assert.equal(ui.document.querySelector('#configured-models .model-caption').textContent, "This chat's models");
  assert.match(ui.document.querySelector('#configured-models .model-note').textContent, /Fixed when this chat started; a change in Stage models applies to new chats/);
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

test('a started chat offers to change its own models only between stages, through the list its view names', async (t) => {
  const list = ['stages', 'list', '--run-dir', run.runDir, '--json'];
  const routes = { builder: { job: 'Builder', model: 'gpt-5.6-terra', reasoning_effort: 'medium', engine: 'codex' } };
  const initial = { ...chatView(), routes, stage_models: { changeable: true, reason: null, next_command: list } };
  const ui = await openChat(t, initial);
  const change = ui.document.querySelector('#configured-models #change-stage-models');
  assert.ok(change, 'the chat offers the change beside its own models');
  assert.equal(change.disabled, false);
  assert.match(ui.document.querySelector('#configured-models .model-note').textContent, /until you change one here/);
  const opened = t.mock.method(host.vscode.commands, 'executeCommand', async () => {});
  change.click(); await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.sent.at(-1))), { type: 'stage_models' }, 'the webview names no command');
  assert.deepEqual(JSON.parse(JSON.stringify(opened.mock.calls.at(-1).arguments)), ['autocode.stages', { list, cwd: run.workspace }]);
  const working = 'A stage is working. Pause the chat, or wait until it asks you something, to change its models.';
  await ui.poll({ ...initial, stage_models: { changeable: false, reason: working, next_command: list } });
  assert.equal(ui.document.querySelector('#change-stage-models').disabled, true);
  assert.ok(ui.document.querySelector('#configured-models').textContent.includes(working), 'the core says why not');
  await ui.poll({ ...initial, stage_models: null });
  assert.equal(ui.document.querySelector('#change-stage-models'), null, 'an older core offers nothing to change');
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

function headerView(extra = {}) {
  const step = (job, label, state, more = {}) => ({ job, label, state, model: null, reasoning_effort: null, spent_usd: null, reason: null, ...more });
  return { ...chatView(), status: 'RUNNING', needs: { kind: 'continue' }, next_command: null,
    summary: { state: 'working', label: 'Working', sentence: 'Builder working.' },
    explanation: { what_happened: 'The run is RUNNING.', what_it_means: 'Work is in progress.', what_the_command_does: 'Use --status to follow progress.' },
    pipeline: [
      step('requirements', 'Requirements', 'ran', { model: 'claude-sonnet-5-5', reasoning_effort: 'medium', spent_usd: 0.08 }),
      step('planner', 'Planner', 'ran'),
      step('plan_reviewer', 'Plan Reviewer', 'skipped', { reason: 'approved at the first review' }),
      step('builder', 'Builder', 'working', { model: 'opus<img src=x onerror=bad()>' }),
      step('tester', 'Tester', 'pending'),
      step('completion', 'Completion Reviewer', 'pending'),
    ],
    recoveries: [{ id: 'r1', at: '2026-11-02T05:33:44+00:00', headline: 'Builder sent back', problem: 'A test failed', action: 'It tried again.' }],
    run_metrics: { schema: 1, spent_usd: 2.5, complete: true, by_job: [{ job: 'Builder', attempts: 1, spent_usd: 2.5, complete: true, share: 1 }],
      tokens: {}, waste: {}, single_model: { builder_usd: 2.5, builder_share: 1, checking_usd: 0, produced: {} } },
    ...extra };
}

test('the run header sits above the conversation with the pipeline, where the run is and its spend', async (t) => {
  const ui = await openChat(t, headerView());
  const header = ui.document.querySelector('#run-header');
  assert.equal(header.hidden, false);
  // Always visible: it is outside the scrolling thread, between the tabs and the conversation.
  assert.equal(header.previousElementSibling.id, 'chat-tabs');
  assert.equal(header.nextElementSibling.id, 'thread');
  // One aligned row per stage: its mark, name, model and level, time, tokens and cost.
  const cells = (row) => [...row.querySelectorAll('td')].filter((cell) => !cell.classList.contains('later'))
    .map((cell) => (cell.classList.contains('name') ? cell.firstChild.textContent : cell.textContent));
  assert.deepEqual([...ui.document.querySelectorAll('#pipeline tbody tr')].map((row) => [row.className, ...cells(row)]), [
    ['ran', '✓', 'Requirements', 'claude-sonnet-5-5 medium', '', '', '$0.08'],
    ['ran', '✓', 'Planner', ''],
    ['skipped', '–', 'Plan Reviewer', 'skipped'],
    ['working', '●', 'Builder', 'opus<img src=x onerror=bad()>'],
    ['pending', '·', 'Tester', ''], ['pending', '·', 'Completion Reviewer', '']]);
  assert.deepEqual([...ui.document.querySelectorAll('#pipeline thead th')].map((cell) => cell.textContent), ['State', 'Stage', 'Model', 'Time', 'Tokens', 'Cost']);
  assert.equal(ui.document.querySelector('#pipeline tr.working').getAttribute('aria-current'), 'step');
  assert.equal(ui.document.querySelector('#pipeline tr.skipped').title, 'Plan Reviewer · skipped: approved at the first review');
  // A stage with no figures yet (still to come, or skipped) keeps its model where a narrow sidebar shows it.
  assert.equal(ui.document.querySelector('#pipeline tr.skipped td.later').colSpan, 3);
  assert.equal(ui.document.querySelector('#pipeline tfoot').textContent, '', 'no total before the run has worked or spent');
  assert.equal(ui.document.querySelector('#header-label').textContent, 'Working');
  assert.equal(ui.document.querySelector('#header-sentence').textContent, 'Builder working.');
  assert.equal(ui.document.querySelector('#header-facts').textContent, 'Builder 100% · checking 0% · 1 thing went wrong, handled');
  assert.equal(ui.document.querySelector('#header-details').hidden, true, 'details start folded');
  assert.equal(ui.document.querySelector('#run-header img'), null);
  assert.doesNotMatch(header.textContent, /RUNNING|--status/);
});

test('the run header unfolds its details and keeps them open across polling, tabs and webview recreation', async (t) => {
  const ui = await openChat(t, headerView());
  const toggle = ui.document.querySelector('#header-toggle');
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  toggle.click();
  const details = ui.document.querySelector('#header-details');
  assert.equal(details.hidden, false);
  assert.equal(toggle.textContent, 'Hide details');
  assert.equal(ui.webviewState.headerOpen, true);
  assert.deepEqual([...details.querySelectorAll('.detail-section')].map((node) => node.dataset.section), ['where', 'pipeline', 'wrong', 'spend']);
  const wrong = details.querySelector('[data-section="wrong"]');
  assert.deepEqual([...wrong.querySelectorAll('.detail-line')].map((node) => [node.className, node.textContent]), [
    ['detail-line', '05:33:44 Builder sent back'], ['detail-line sub', 'What went wrong: A test failed'], ['detail-line sub', 'Then: It tried again.']]);
  assert.match(details.querySelector('[data-section="pipeline"]').textContent, /● Builder: opus<img src=x onerror=bad\(\)>/);
  assert.equal(details.querySelector('img'), null, 'model text is shown as text, never as markup');
  ui.type('Keep my draft while the header updates');
  let envelope = null;
  ui.window.addEventListener('message', (event) => { if (event.data && event.data.type === 'chat') envelope = event.data; });
  const next = headerView({ summary: { state: 'needs_you', label: 'Needs you', sentence: 'Approve the plan.' } });
  await ui.poll(next);
  assert.equal(details.hidden, false, 'a new read keeps the details open');
  assert.equal(ui.document.querySelector('#run-header').className, 'needs_you');
  assert.equal(ui.document.querySelector('#header-label').textContent, 'Needs you');
  assert.equal(ui.document.querySelector('#message').value, 'Keep my draft while the header updates');
  const restored = new JSDOM(panelHtml(true), { runScripts: 'outside-only' });
  restored.window.acquireVsCodeApi = () => ({ getState: () => ui.webviewState, setState() {}, postMessage() {} });
  restored.window.eval(restored.window.document.querySelector('script').textContent);
  restored.window.dispatchEvent(new restored.window.MessageEvent('message', { data: envelope }));
  assert.equal(restored.window.document.querySelector('#run-header').hidden, false);
  assert.equal(restored.window.document.querySelector('#header-details').hidden, false, 'the choice survives a recreated webview');
  restored.window.close();
  toggle.click();
  assert.equal(details.hidden, true);
  assert.equal(ui.webviewState.headerOpen, false);
});

test('the run header shows the working stage and what it does, and its details the run accounting, as /details does', async (t) => {
  const ui = await openChat(t, headerView({
    pipeline: [{ job: 'requirements', label: 'Requirements', state: 'working', model: null, reasoning_effort: null, spent_usd: null, reason: null }],
    cost: { spent_usd: 0, running_usd: 0.04, cap_usd: null, complete: false, unpriced_stages: 0, breakdown: {} },
    usage: { accounting: { complete: true, active_attempts: 1,
      tokens: { input_tokens: { value: null, known: 9000, complete: false }, output_tokens: { value: null, known: 400, complete: false } },
      attempts: [{ stage: 'requirements_gather', job: 'Requirements', active: true, started_at: new Date(Date.now() - 75_000).toISOString(),
        launch_route: { model: 'claude-sonnet-5-5', reasoning_effort: 'medium', provider: 'anthropic' },
        attributed_tokens: { input_tokens: { value: null, known: 9000, complete: false }, output_tokens: { value: null, known: 400, complete: false } } }] } },
    chat: { ...chatView().chat, now: [{ agent: 'Requirements', text: 'reading the issue list' }] },
  }));
  // Visible without opening anything: the working stage's row, highlighted, and what it is doing under the table.
  const row = ui.document.querySelector('#pipeline tr.working');
  assert.deepEqual([...row.querySelectorAll('td.model, td.num')].map((cell) => cell.textContent).slice(0, 1), ['claude-sonnet-5-5 medium']);
  assert.match(row.querySelector('td.time').textContent, /^1m1\ds$/);
  assert.deepEqual([row.querySelector('td.tokens').textContent, row.querySelector('td.cost').textContent], ['9.4k+', '$0.04']);
  assert.equal(row.querySelector('td.name .sub').textContent, 'claude-sonnet-5-5 medium', 'a narrow sidebar shows the model under the name');
  assert.deepEqual([...ui.document.querySelectorAll('#pipeline tfoot td')].map((cell) => cell.textContent), ['', 'Total', '', '', '9.4k+', '$0.04+']);
  assert.equal(ui.document.querySelector('#header-stage').textContent, 'Requirements: reading the issue list');
  ui.document.querySelector('#header-toggle').click();
  const details = ui.document.querySelector('#header-details');
  assert.deepEqual([...details.querySelectorAll('.detail-section')].map((node) => node.dataset.section), ['where', 'pipeline', 'wrong', 'spend', 'usage']);
  const usage = [...details.querySelectorAll('[data-section="usage"] .detail-line')].map((node) => [node.className, node.textContent]);
  assert.deepEqual(usage.slice(0, 2), [['detail-line', 'Accounted so far: $0 (1 running)'], ['detail-line sub', 'Running stage so far: $0.04']]);
  assert.ok(usage.some(([cls, line]) => cls === 'detail-line sub sub2' && line === 'claude-sonnet-5-5 · medium · anthropic'), 'an attempt\'s route sits under it');
  assert.equal(ui.document.querySelector('#header-stage').hidden, false);
});

test('a new chat with no run shows no run header', async (t) => {
  const ui = await openChat(t, headerView(), false);
  assert.equal(ui.document.querySelector('#run-header').hidden, true);
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

/**
 * What a row of This chat's models shows, in the words the core's route uses: the model and level
 * from its dropdowns and, beside them, only where they come from. The full words are its tooltip.
 */
function shownRoute(row) {
  const now = row.querySelector('.now');
  const model = row.querySelector('select.model');
  if (!model) return now.textContent;
  const shown = `${model.value} at ${row.querySelector('select.level').value} (${now.textContent})`;
  assert.equal(now.title, shown, 'the tooltip names the same route');
  return shown;
}

/** `autocode stages list --json` for a new chat, trimmed to the Builder and Tester, with any picks it was given. */
function chatStageView(argv) {
  const base = ['stages', 'list', '--json', '--backend', 'claude'];
  const picks = {};
  for (let i = argv.indexOf('--chat-stage'); i >= 0; i = argv.indexOf('--chat-stage', i + 1)) picks[argv[i + 1]] = [argv[i + 2], argv[i + 3]];
  const others = (role) => Object.entries(picks).filter(([id]) => id !== role).flatMap(([id, [model, effort]]) => ['--chat-stage', id, model, effort]);
  const option = (model, unavailable = null) => ({ model, levels: ['low', 'medium', 'high'], family: 'claude', context: null, account: null,
    effort_choices: ['low', 'medium', 'high'], unavailable });
  const builderModel = picks.builder ? picks.builder[0] : 'haiku';
  const stage = (id, label, chosen, options) => ({
    id, label, does: `${label} does its job.`, advice: '', flag: `--${id}-model`, effort_flag: `--${id}-reasoning-effort`,
    default: { model: 'haiku', effort: 'medium', offered: true }, chosen, options,
    set: { next_command: ['stages', 'set', id, '--backend', 'claude', '--json', '--model', 'MODEL', '--effort', 'EFFORT'] }, reset: null,
    chat: picks[id] ? { model: picks[id][0], effort: picks[id][1] } : null,
    chat_set: { next_command: [...base, ...others(id), '--chat-stage', id, 'MODEL', 'EFFORT'] },
    chat_reset: picks[id] ? { next_command: [...base, ...others(id)] } : null,
  });
  return { ...ok, stdout: JSON.stringify({ version: 1, backend: 'claude', label: 'Claude Code', options_stop: null, stages: [
    stage('builder', 'Builder', null, [option('haiku'), option('sonnet'), option('opus', 'the Tester runs opus')]),
    stage('tester', 'Tester', { model: 'opus', effort: 'high' }, [option('haiku'), option('sonnet'), option(builderModel === 'opus' ? 'none' : 'opus')]
      .map((row) => (row.model === builderModel ? { ...row, unavailable: `the Builder runs ${builderModel}` } : row))),
  ], chat: { next_command: [...base, ...others(null)],
    start_args: Object.entries(picks).flatMap(([id, [model, effort]]) => [`--${id}-model`, model, `--${id}-reasoning-effort`, effort]) } }) };
}

test('a new chat shows the stages it will run and changes one for itself alone before it starts', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const calls = [];
  const ui = await openChat(t, null, false, { stages: (argv) => { calls.push(argv); return chatStageView(argv); } });
  ui.chat.show(); await flush(); await flush();
  const section = ui.document.querySelector('#chat-models');
  assert.equal(section.hidden, false, 'the stages are there before the first message');
  assert.deepEqual(calls[0], ['stages', 'list', '--json', '--backend', 'claude'], 'the list new chats here run on');
  const rows = [...section.querySelectorAll('.chat-model')];
  assert.deepEqual(rows.map(shownRoute),
    ['haiku at medium (default)', 'opus at high (Stage models)']);
  const builder = section.querySelector('[data-stage="builder"] select.model');
  assert.equal(builder.value, 'haiku');
  assert.equal(builder.querySelector('option[value="opus"]').disabled, true, 'a model a checker runs is not offered');
  builder.value = 'sonnet'; builder.dispatchEvent(new ui.window.Event('change')); await flush(); await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.sent.at(-1))), { type: 'chat_stage', stage: 'builder', model: 'sonnet', effort: 'medium' },
    'the webview names a pick, never a command');
  assert.deepEqual(calls.at(-1), ['stages', 'list', '--json', '--backend', 'claude', '--chat-stage', 'builder', 'sonnet', 'medium']);
  assert.ok(calls.every((argv) => argv[1] === 'list'), 'nothing is saved to Stage models');
  assert.equal(shownRoute(section.querySelector('[data-stage="builder"]')), 'sonnet at medium (this chat)');
  assert.match(ui.document.querySelector('#chat-models-summary').textContent, /Claude Code · 1 changed for this chat/);
  assert.equal(section.querySelector('[data-stage="tester"] select.model option[value="sonnet"]').disabled, true,
    'the Tester is judged against this chat\'s Builder');
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 1);
  assert.deepEqual(calls.at(-1), ['stages', 'list', '--json', '--backend', 'claude', '--chat-stage', 'builder', 'sonnet', 'medium'],
    'the picks are checked again where the chat starts');
  assert.deepEqual(start.mock.calls[0].arguments[4], ['--builder-model', 'sonnet', '--builder-reasoning-effort', 'medium']);
  assert.equal(section.hidden, true, 'a started chat lists its own models in Models & usage');
});

/** Answer the Models chip's two steps: the stage, then the model and level ("model · level"), by label; record what was offered. */
function pickInOrder(t, labels) {
  const offered = [];
  const picker = t.mock.method(host.vscode.window, 'showQuickPick', async (items, options) => {
    offered.push({ labels: items.map((item) => item.label), placeHolder: options.placeHolder, items });
    return items.find((item) => item.label === labels[offered.length - 1]);
  });
  return { offered, picker };
}

test('the chat box Models chip changes a new chat\'s model, not only its level, for that chat alone', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const calls = [];
  const ui = await openChat(t, null, false, { stages: (argv) => { calls.push(argv); return chatStageView(argv); } });
  ui.chat.show(); await flush(); await flush();
  const chip = ui.document.getElementById('models-picker');
  assert.equal(chip.hidden, false, 'the chat box offers the models before the first message');
  assert.equal(chip.disabled, false);
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models');
  const rules = [...ui.document.styleSheets].flatMap((sheet) => [...sheet.cssRules]);
  for (const selector of ['.chat-model select', '.chat-model select.level']) {
    assert.equal(rules.find((rule) => rule.selectorText === selector).style.width, 'auto',
      `${selector} must not take the form-wide width: 100%, which hid the model dropdown behind the level`);
  }
  assert.equal(ui.document.querySelector('#model-info').textContent,
    "Nothing has run yet. Choose this chat's models with Models under the message box.");
  assert.equal(ui.document.querySelector('#usage-breakdown').textContent, 'Tokens and cost are shown here once the chat starts.');
  const { offered } = pickInOrder(t, ['Builder', 'sonnet · high']);
  chip.click(); await flush(); await flush(); await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.sent.at(-1))), { type: 'models' }, 'the webview names no command');
  assert.deepEqual(offered[0].labels, ['Builder', 'Tester']);
  assert.equal(offered[0].items[0].description, 'haiku at medium (default)');
  assert.equal(offered.length, 2, 'a stage, then its model and level in one list');
  assert.deepEqual(offered[1].labels, ['haiku · medium', 'haiku · low', 'haiku · high', 'sonnet · low', 'sonnet · medium', 'sonnet · high'],
    'what the stage runs now first; a model the core marks unavailable is not offered');
  assert.equal(offered[1].items[0].description, 'current · claude');
  assert.equal(offered[1].items[1].description, 'claude');
  assert.deepEqual(calls.at(-1), ['stages', 'list', '--json', '--backend', 'claude', '--chat-stage', 'builder', 'sonnet', 'high'],
    'only the stage\'s own chat_set, filled with the pick');
  assert.ok(calls.every((argv) => argv[1] === 'list'), 'nothing is saved to Stage models');
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models · 1 changed');
  assert.equal(shownRoute(ui.document.querySelector('#chat-models [data-stage="builder"]')), 'sonnet at high (this chat)');
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.deepEqual(start.mock.calls[0].arguments[4], ['--builder-model', 'sonnet', '--builder-reasoning-effort', 'high']);
});

test('the chat box Models picker cancelled at any step changes nothing', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const calls = [];
  const ui = await openChat(t, null, false, { stages: (argv) => { calls.push(argv); return chatStageView(argv); } });
  ui.chat.show(); await flush(); await flush();
  const before = calls.length;
  pickInOrder(t, ['Tester']);
  ui.document.getElementById('models-picker').click(); await flush(); await flush(); await flush();
  assert.equal(calls.length, before, 'a cancelled model and level sends nothing');
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models');
});

/** A started chat's own list (`stages list --run-dir RUN --json`): its `set` is the run's `--stage-model`. */
function runStageView(changeable = true) {
  const option = (model, unavailable = null) => ({ model, levels: ['low', 'high'], family: null, context: null, account: null,
    effort_choices: ['low', 'high'], unavailable });
  const stage = (id, label, chosen, options) => ({ id, label, does: `${label} does its job.`, advice: '',
    default: { model: 'gpt-5.6-terra', effort: 'low', offered: true }, chosen, options,
    set: { next_command: ['--run-dir', run.runDir, '--workspace', run.workspace, '--stage-model', id, 'MODEL', 'EFFORT'] }, reset: null });
  return { ...ok, stdout: JSON.stringify({ version: 1, backend: 'codex', label: 'Codex', options_stop: null, changeable,
    reason: changeable ? null : 'A stage is working.', run_dir: run.runDir, stages: [
      stage('builder', 'Builder', { model: 'gpt-5.6-terra', effort: 'high' }, [option('gpt-5.6-terra'), option('gpt-6-sol'), option('gpt-6', 'the Tester runs gpt-6')]),
    ] }) };
}

test('a started chat changes a stage\'s model from the chat box between stages, through the list its view names', async (t) => {
  const list = ['stages', 'list', '--run-dir', run.runDir, '--json'];
  const routes = { builder: { job: 'Builder', model: 'gpt-5.6-terra', reasoning_effort: 'high', engine: 'codex' } };
  const initial = { ...chatView(), routes, stage_models: { changeable: true, reason: null, next_command: list } };
  const calls = [];
  const sets = [];
  // One stand-in for the CLI, set before the chat opens: the run's list and its `--stage-model` change.
  t.mock.method(cli, 'invoke', async (settings, argv) => {
    if (argv.includes('--stage-model')) { sets.push({ argv, cwd: settings.cwd }); return { ...ok, stdout: 'saved\n' }; }
    calls.push({ argv, cwd: settings.cwd });
    return argv[0] === 'stages' ? runStageView() : { ...ok, stdout: JSON.stringify({ version: 1, stages: [] }) };
  });
  const ui = await openChat(t, initial, run);
  const chip = ui.document.getElementById('models-picker');
  assert.equal(chip.hidden, false, 'a started chat offers its own models in the chat box');
  assert.equal(chip.disabled, false);
  const { offered } = pickInOrder(t, ['Builder', 'gpt-6-sol · low']);
  chip.click(); await flush(); await flush(); await flush(); await flush();
  assert.deepEqual(calls.at(-1), { argv: list, cwd: run.workspace }, 'the list the status view names, in the chat\'s folder');
  assert.equal(offered[0].items[0].description, 'gpt-5.6-terra at high (this chat)');
  assert.deepEqual(offered[1].labels, ['gpt-5.6-terra · high', 'gpt-5.6-terra · low', 'gpt-6-sol · low', 'gpt-6-sol · high']);
  assert.equal(offered[1].items[0].description, 'current');
  assert.deepEqual(sets, [{ argv: ['--run-dir', run.runDir, '--workspace', run.workspace, '--stage-model', 'builder', 'gpt-6-sol', 'low'], cwd: run.workspace }]);
  assert.ok(host.notices.some((notice) => /Builder on gpt-6-sol at low from its next stage/.test(notice.message)));
  const working = 'A stage is working. Pause the chat, or wait until it asks you something, to change its models.';
  await ui.poll({ ...initial, stage_models: { changeable: false, reason: working, next_command: list } });
  assert.equal(chip.disabled, true, 'only between stages');
  assert.equal(chip.title, working, 'the core says why not');
  await ui.poll({ ...initial, stage_models: null });
  assert.equal(chip.hidden, true, 'an older core offers nothing to change');
});

test('a started chat\'s Models chip says it is reading the chat\'s models, and comes back when the pick is cancelled', async (t) => {
  const list = ['stages', 'list', '--run-dir', run.runDir, '--json'];
  const initial = { ...chatView(), stage_models: { changeable: true, reason: null, next_command: list } };
  const waiting = [];
  const timeouts = [];
  t.mock.method(cli, 'invoke', async (settings, argv, env, timeout) => {
    // Only the chat's own list waits; anything else (the new-chat list) answers an empty one.
    if (!argv.includes('--run-dir')) return { ...ok, stdout: JSON.stringify({ version: 1, stages: [] }) };
    timeouts.push(timeout);
    return new Promise((resolve) => { waiting.push(() => resolve(runStageView())); });
  });
  const ui = await openChat(t, initial, run);
  const chip = ui.document.getElementById('models-picker');
  chip.click(); await flush(); await flush();
  assert.equal(waiting.length, 1);
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models · Reading models…', 'not a chip that seems to do nothing');
  assert.equal(chip.disabled, true, 'a second click does not read the list twice');
  assert.deepEqual(timeouts, [60000]);
  waiting.shift()(); await flush(); await flush(); await flush(); await flush();
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models', 'a cancelled pick gives the chip back');
  assert.equal(chip.disabled, false);
});

/** `autocode worktrees list --json`: a new branch by default, two existing branches (one taken), and the folder with its warning. */
function worktreeList() {
  return { ...ok, stdout: JSON.stringify({ schema: 1, folder: run.workspace, branch: 'main', default: 'new_branch', places: [
    { id: 'new_branch', label: 'New worktree and branch', does: 'Plans and builds on a new branch.', start_args: ['--build-in-worktree'], warning: null },
    { id: 'branch', label: 'Existing branch', does: 'Plans and builds on a branch you pick.', branches: [
      { name: 'feature', start_args: ['--build-in-worktree', '--worktree-branch', 'feature'], unavailable: null },
      { name: 'autocode/other-chat', start_args: null, unavailable: 'The chat “Fix <b>it</b>” plans and builds on it.' },
    ] },
    { id: 'folder', label: 'This folder (branch main)', does: 'Changes this folder\'s files.', start_args: ['--in-place'],
      warning: 'Your own edits and 1 other chat working in this folder can conflict with it.' },
  ], active: [] }) };
}

test('a new chat works on a new branch of its own unless the person picks a branch or the folder', async (t) => {
  const reads = [];
  const ui = await openChat(t, null, false, { worktrees: (argv, settings) => { reads.push([argv, settings.cwd]); return worktreeList(); } });
  ui.chat.show(); await flush(); await flush();
  const section = ui.document.querySelector('#chat-place');
  assert.equal(section.hidden, false, 'where it works is there before the first message');
  assert.deepEqual(reads[0][0], ['worktrees', 'list', '--json'], 'the window has no folder here: the list for where it runs');
  assert.equal(section.querySelector('input[value="new_branch"]').checked, true, 'a new worktree and branch is the default');
  assert.equal(ui.document.querySelector('#chat-place-summary').textContent, 'New worktree and branch');
  assert.equal(section.querySelector('.chat-place-warning'), null);
  const taken = section.querySelector('select.branch option[value="autocode/other-chat"]');
  assert.equal(taken.disabled, true, 'a branch another chat has is not offered');
  assert.match(taken.textContent, /not available: The chat “Fix <b>it<\/b>” plans/, 'core text is shown as text');

  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  const select = section.querySelector('select.branch');
  select.value = 'feature'; select.dispatchEvent(new ui.window.Event('change')); await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.sent.at(-1))), { type: 'chat_place', place: 'branch', branch: 'feature' },
    'the webview names a place, never a flag');
  assert.equal(ui.document.querySelector('#chat-place-summary').textContent, 'branch feature');
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 1);
  assert.deepEqual(reads.at(-1), [['worktrees', 'list', '--json', '--workspace', run.workspace], run.workspace],
    'the list is read again in the folder the chat starts in');
  assert.deepEqual(start.mock.calls[0].arguments[5], ['--build-in-worktree', '--worktree-branch', 'feature']);
  assert.equal(section.hidden, true, 'a started chat says where it works in its header');
});

test('a chat in the folder itself starts only once the person confirms the warning', async (t) => {
  const ui = await openChat(t, null, false, { worktrees: () => worktreeList() });
  ui.chat.show(); await flush(); await flush();
  const folder = ui.document.querySelector('#chat-place input[value="folder"]');
  folder.checked = true; folder.dispatchEvent(new ui.window.Event('change')); await flush();
  assert.match(ui.document.querySelector('#chat-place .chat-place-warning').textContent, /1 other chat working in this folder can conflict/);
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => undefined);
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.equal(confirm.mock.callCount(), 1);
  assert.equal(confirm.mock.calls[0].arguments[1].modal, true);
  assert.match(confirm.mock.calls[0].arguments[1].detail, /can conflict/);
  assert.equal(start.mock.callCount(), 0, 'declined: nothing starts');
  confirm.mock.mockImplementation(async () => 'Start in this folder');
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 1);
  assert.deepEqual(start.mock.calls[0].arguments[5], ['--in-place']);
});

test('a chosen branch the fresh list no longer offers is not still claimed by the picker', async (t) => {
  const without = (name) => {
    const list = JSON.parse(worktreeList().stdout);
    for (const place of list.places) place.branches = (place.branches || []).filter((branch) => branch.name !== name);
    return { ...ok, stdout: JSON.stringify(list) };
  };
  const ui = await openChat(t, null, false, {
    worktrees: (argv) => (argv.includes('--workspace') ? without('feature') : worktreeList()),
  });
  ui.chat.show(); await flush(); await flush();
  const select = ui.document.querySelector('#chat-place select.branch');
  select.value = 'feature'; select.dispatchEvent(new ui.window.Event('change')); await flush();
  assert.equal(ui.document.querySelector('#chat-place-summary').textContent, 'branch feature');
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.match(ui.document.querySelector('#chat-error').textContent, /Pick the branch/);
  assert.equal(start.mock.callCount(), 0, 'a branch the list does not offer cannot start a chat');
  const summary = ui.document.querySelector('#chat-place-summary').textContent;
  assert.notEqual(summary, 'branch feature', `the picker must not claim a branch the list dropped: ${summary}`);
});

test('a chat in the folder itself names the open warning in the chat, never a setup step', async (t) => {
  const ui = await openChat(t, null, false, { worktrees: () => worktreeList() });
  ui.chat.show(); await flush(); await flush();
  const folder = ui.document.querySelector('#chat-place input[value="folder"]');
  folder.checked = true; folder.dispatchEvent(new ui.window.Event('change')); await flush();
  t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  let answer;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => new Promise((resolve) => { answer = resolve; }));
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  const app = () => ui.document.querySelector('#app').textContent;
  assert.ok(app().includes('Start this chat in your folder? Answer the dialog that opened.'), app());
  assert.ok(!app().includes('Complete setup'));
  answer(undefined); await flush(); await flush(); await flush();
  assert.ok(!app().includes('Answer the dialog'), 'a declined warning is no longer named');
});

test('a first message reads the modes, its model picks and where it works at once, saying it is starting', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const pending = [];
  const later = (argv, answer) => new Promise((resolve) => { pending.push({ argv, done: () => resolve(answer()) }); });
  let reading = false;
  const ui = await openChat(t, null, false, {
    stages: (argv) => (reading ? later(argv, () => chatStageView(argv)) : chatStageView(argv)),
    worktrees: (argv) => later(argv, () => worktreeList()),
  });
  ui.chat.show(); await flush(); await flush();
  // A pick for this chat alone, so its start checks the picks again.
  const builder = ui.document.querySelector('#chat-models [data-stage="builder"] select.model');
  builder.value = 'sonnet'; builder.dispatchEvent(new ui.window.Event('change')); await flush(); await flush(); await flush();
  pending.splice(0).forEach((row) => row.done()); await flush(); await flush();
  reading = true;
  let modesDone;
  controller.modes.mock.mockImplementation(() => new Promise((resolve) => { modesDone = () => resolve({ modes: [] }); }));
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.equal(typeof modesDone, 'function', 'the modes are being read');
  assert.deepEqual(pending.map((row) => row.argv[0]).sort(), ['stages', 'worktrees'],
    'the picks and the places are read while the modes are, not one after another');
  const app = () => ui.document.querySelector('#app').textContent;
  assert.ok(app().includes('Starting your chat…'), app());
  assert.ok(!app().includes('Complete setup'), 'nothing for the person to set up');
  assert.equal(start.mock.callCount(), 0);
  modesDone(); pending.splice(0).forEach((row) => row.done()); await flush(); await flush(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 1);
  assert.deepEqual(start.mock.calls[0].arguments[4], ['--builder-model', 'sonnet', '--builder-reasoning-effort', 'medium']);
  assert.deepEqual(start.mock.calls[0].arguments[5], ['--build-in-worktree']);
});

test('a chat started with an AutoCode that cannot list places works in the folder, as before', async (t) => {
  const ui = await openChat(t, null, false);
  ui.chat.show(); await flush(); await flush();
  assert.match(ui.document.querySelector('#chat-place .chat-place-note').textContent, /works in your folder\. Update AutoCode/);
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.deepEqual(start.mock.calls[0].arguments[5], ['--in-place']);
});

test('start passes only a placement the core named, and refuses any other flag', async (t) => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vscode-place-'));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  const launch = t.mock.method(cli, 'stream', async () => ok);
  await controller.start(folder, 'A task', undefined, [], [], ['--build-in-worktree', '--worktree-branch', 'feature']);
  const [, args] = launch.mock.calls[0].arguments;
  assert.deepEqual(args.slice(1, 4), ['--build-in-worktree', '--worktree-branch', 'feature']);
  assert.ok(!args.includes('--in-place'));
  await assert.rejects(controller.start(folder, 'A task', undefined, [], [], ['--in-place', '--yes']), /Unsupported placement/);
  await assert.rejects(controller.start(folder, 'A task', undefined, [], [], ['--build-in-worktree', '--worktree-branch', '--yolo']), /Unsupported placement/);
  assert.equal(launch.mock.callCount(), 1);
});

test('a chat says its worktree and branch in the header and its details', async (t) => {
  const view = { ...approvalView(), summary: { state: 'working', label: 'Working', sentence: 'Builder working.' }, worktree: {
    kind: 'worktree', path: '/p/.autocode/worktrees/add-x-1', branch: 'autocode/add-x-1', folder: '/p', existing_branch: false,
    moves_to_branch: null, label: 'Own worktree · branch autocode/add-x-1', sentence: 'This chat changes files only in its own worktree.' } };
  const ui = await openChat(t, view);
  const place = ui.document.querySelector('#header-place');
  assert.equal(place.textContent, 'Own worktree · branch autocode/add-x-1', 'always shown, folded or not');
  assert.match(place.title, /Worktree: \/p\/\.autocode\/worktrees\/add-x-1/);
  ui.document.querySelector('#header-toggle').click(); await flush();
  const details = ui.document.querySelector('#header-details').textContent;
  assert.match(details, /Where it works/);
  assert.match(details, /Branch: autocode\/add-x-1/);
  assert.match(details, /Your folder: \/p/);
});

test('a new chat drops a pick back to Stage models and starts with none', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const calls = [];
  const ui = await openChat(t, null, false, { stages: (argv) => { calls.push(argv); return chatStageView(argv); } });
  ui.chat.show(); await flush(); await flush();
  const level = ui.document.querySelector('[data-stage="tester"] select.level');
  level.value = 'low'; level.dispatchEvent(new ui.window.Event('change')); await flush(); await flush();
  assert.deepEqual(calls.at(-1).slice(-4), ['--chat-stage', 'tester', 'opus', 'low']);
  const picked = calls.length;
  ui.document.querySelector('[data-stage="tester"] button.link').click(); await flush(); await flush();
  assert.equal(calls.length, picked, 'without its pick the chat shows the list every new chat shares, read a moment ago');
  assert.equal(shownRoute(ui.document.querySelector('[data-stage="tester"]')), 'opus at high (Stage models)');
  const start = t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  const before = calls.length;
  ui.type('Build it'); ui.submit(); await flush(); await flush();
  assert.equal(calls.length, before, 'without a pick nothing is read again');
  assert.deepEqual(start.mock.calls[0].arguments[4], []);
});

test('a new chat keeps a model pick in its row, marked as being checked, until the core answers', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const waiting = [];
  const timeouts = [];
  // The pick's own list is slow (OpenCode lists its accounts' models): it answers only when released.
  const ui = await openChat(t, null, false, { stages: (argv, _settings, timeout) => {
    timeouts.push(timeout);
    if (!argv.includes('--chat-stage')) return chatStageView(argv);
    return new Promise((resolve) => { waiting.push(() => resolve(chatStageView(argv))); });
  } });
  ui.chat.show(); await flush(); await flush();
  const row = () => ui.document.querySelector('#chat-models [data-stage="builder"]');
  const chip = ui.document.getElementById('models-picker');
  row().querySelector('select.model').value = 'sonnet';
  row().querySelector('select.model').dispatchEvent(new ui.window.Event('change')); await flush(); await flush();
  assert.equal(waiting.length, 1, 'the core is checking the pick');
  assert.equal(row().querySelector('select.model').value, 'sonnet', 'the dropdown keeps the pick, never the old model');
  assert.equal(row().querySelector('select.level').value, 'medium');
  assert.equal(row().querySelector('.now').textContent, 'Checking…');
  assert.equal(row().querySelector('.now').getAttribute('role'), 'status');
  assert.ok(row().classList.contains('checking'));
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models · checking…');
  assert.equal(chip.disabled, true);
  assert.ok(timeouts.length && timeouts.every((timeout) => timeout === 60000), 'a wedged list read cannot grey the box out for good');
  waiting.shift()(); await flush(); await flush(); await flush();
  assert.equal(shownRoute(row()), 'sonnet at medium (this chat)');
  assert.ok(!row().classList.contains('checking'));
  assert.equal(row().querySelector('select.model').disabled, false, 'the box answers again');
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models · 1 changed');
});

test('a new chat whose pick the core refuses goes back to what it runs and says why', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const ui = await openChat(t, null, false, { stages: (argv) => (argv.includes('--chat-stage')
    ? { ...ok, code: 2, stdout: '', stderr: 'the Tester runs sonnet' } : chatStageView(argv)) });
  ui.chat.show(); await flush(); await flush();
  const row = () => ui.document.querySelector('#chat-models [data-stage="builder"]');
  row().querySelector('select.model').value = 'sonnet';
  row().querySelector('select.model').dispatchEvent(new ui.window.Event('change'));
  for (let i = 0; i < 5; i += 1) await flush();
  assert.equal(shownRoute(row()), 'haiku at medium (default)');
  assert.equal(row().querySelector('select.model').disabled, false);
  assert.match(ui.document.querySelector('#chat-models .chat-models-error').textContent, /the Tester runs sonnet/);
  assert.equal(ui.document.getElementById('models-name').textContent, 'Models');
});

test('a row of This chat\'s models names its model once: in the dropdown, with only where it comes from beside it', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const ui = await openChat(t, null, false, { stages: chatStageView });
  ui.chat.show(); await flush(); await flush();
  const rows = [...ui.document.querySelectorAll('#chat-models .chat-model')];
  assert.deepEqual(rows.map((row) => row.querySelector('.now').textContent), ['default', 'Stage models']);
  for (const row of rows) {
    const model = row.querySelector('select.model').value;
    assert.doesNotMatch(row.querySelector('.head').textContent, new RegExp(model), 'the model is not repeated beside its dropdown');
  }
});

/** `chatStageView` with the Stage models choices in `chosen` (stage ID to model and level). */
function withChosen(argv, chosen) {
  const answer = chatStageView(argv);
  const parsed = JSON.parse(answer.stdout);
  for (const stage of parsed.stages) if (chosen[stage.id]) stage.chosen = chosen[stage.id];
  return { ...answer, stdout: JSON.stringify(parsed) };
}

const nowText = (ui) => [...ui.document.querySelectorAll('#chat-models .chat-model')].map(shownRoute);

test('every new chat after the first shows its models at once, from the list the first one read', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const calls = [];
  let answer = (argv) => chatStageView(argv);
  const ui = await openChat(t, null, false, { stages: (argv) => { calls.push(argv); return answer(argv); } });
  ui.chat.show(); await flush(); await flush();
  assert.equal(calls.length, 1, 'the first new chat reads the list');
  // From here the CLI never answers: a chat that waited for it would show no models.
  answer = () => new Promise(() => {});
  for (let chat = 0; chat < 3; chat++) {
    ui.chat.show(); await flush();
    assert.equal(ui.document.querySelector('#chat-models').hidden, false);
    assert.deepEqual(nowText(ui), ['haiku at medium (default)', 'opus at high (Stage models)']);
    assert.doesNotMatch(ui.document.querySelector('#chat-models-body').textContent, /Reading the stages/);
    const chip = ui.document.getElementById('models-picker');
    assert.equal(chip.hidden, false); assert.equal(chip.disabled, false, 'the Models chip works at once');
  }
  assert.equal(calls.length, 1, 'a list read a moment ago is not read again for each chat');
});

test('a list older than a minute is shown at once and read again in the background', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  let clock = 1_000_000;
  const stageLists = new StageListCache(undefined, () => clock);
  const chosen = {};
  const calls = [];
  let release;
  let answer = (argv) => withChosen(argv, chosen);
  const ui = await openChat(t, null, false, { stageLists, stages: (argv) => { calls.push(argv); return answer(argv); } });
  ui.chat.show(); await flush(); await flush();
  ui.chat.show(); await flush();
  assert.equal(calls.length, 1);
  clock += 61_000;
  chosen.tester = { model: 'sonnet', effort: 'low' };
  answer = (argv) => new Promise((resolve) => { release = () => resolve(withChosen(argv, chosen)); });
  ui.chat.show(); await flush();
  assert.deepEqual(nowText(ui), ['haiku at medium (default)', 'opus at high (Stage models)'], 'the last list, without waiting');
  assert.equal(calls.length, 2, 'read again in the background');
  ui.chat.show(); await flush();
  assert.equal(calls.length, 2, 'one read at a time, shared by every new chat');
  release(); await flush(); await flush();
  assert.deepEqual(nowText(ui), ['haiku at medium (default)', 'sonnet at low (Stage models)'], 'the fresh list replaces it');
});

test('after the editor restarts, a new chat shows the saved list at once, then the fresh one', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const storageValues = new Map();
  const chosen = {};
  const calls = [];
  let release;
  let answer = (argv) => withChosen(argv, chosen);
  const before = await openChat(t, null, false, { storageValues, stages: (argv) => { calls.push(argv); return answer(argv); } });
  before.chat.show(); await flush(); await flush();
  before.chat.dispose();
  chosen.builder = { model: 'sonnet', effort: 'high' };
  answer = (argv) => new Promise((resolve) => { release = () => resolve(withChosen(argv, chosen)); });
  const ui = await openChat(t, null, false, { storageValues, timersEnabled: true });
  ui.chat.show(); await flush();
  assert.deepEqual(nowText(ui), ['haiku at medium (default)', 'opus at high (Stage models)'], 'kept in the workspace, shown before the CLI answers');
  assert.equal(calls.length, 2, 'a saved list is read again once');
  release(); await flush(); await flush();
  assert.deepEqual(nowText(ui), ['sonnet at high (Stage models)', 'opus at high (Stage models)']);
});

test('a choice saved in Stage models reaches a new chat that is already open', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  const stageLists = new StageListCache();
  const chosen = {};
  const calls = [];
  const ui = await openChat(t, null, false, { stageLists, stages: (argv) => {
    calls.push(argv);
    if (argv[1] === 'set') chosen[argv[2]] = { model: argv[7], effort: argv[9] };
    return withChosen(argv, chosen);
  } });
  ui.chat.show(); await flush(); await flush();
  const panel = new StagesPanel(stageLists);
  t.after(() => panel.dispose());
  await panel.show();
  const page = host.panels.at(-1);
  assert.equal(page.webview.messages.at(-1).view.stages[0].now, 'haiku at medium (default)', 'the panel opens on the shared list');
  page.webview.receive({ type: 'set', stage: 'builder', model: 'sonnet', effort: 'high' }); await flush(); await flush(); await flush();
  assert.deepEqual(calls.filter((argv) => argv[1] === 'set').length, 1);
  assert.deepEqual(calls.at(-1), ['stages', 'list', '--json', '--backend', 'claude'], 'the chat reads its list again');
  assert.deepEqual(nowText(ui), ['sonnet at high (Stage models)', 'opus at high (Stage models)']);
});

test('a pick the core refuses where the chat starts keeps the draft and starts nothing', async (t) => {
  host.config.set('extraOptions', ['--engine', 'claude']);
  let refuse = false;
  const ui = await openChat(t, null, false, { stages: (argv) => (refuse && argv.includes('--chat-stage')
    ? { ...ok, code: 2, stderr: 'autocode stages: the Builder cannot run sonnet: the Tester runs sonnet\n' } : chatStageView(argv)) });
  ui.chat.show(); await flush(); await flush();
  const builder = ui.document.querySelector('[data-stage="builder"] select.model');
  builder.value = 'sonnet'; builder.dispatchEvent(new ui.window.Event('change')); await flush(); await flush();
  refuse = true;
  const start = t.mock.method(controller, 'start', async () => run);
  ui.type('Build it'); ui.submit(); await flush(); await flush(); await flush();
  assert.equal(start.mock.callCount(), 0);
  assert.match(ui.document.querySelector('#messages').textContent, /This chat's models: autocode stages: the Builder cannot run sonnet/);
  assert.equal(ui.document.querySelector('#message').value, 'Build it');
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

test('while a new run is still starting the chat says what it is doing, not that the status could not be read', async (t) => {
  const { RunStarting } = host.load('core/view.js');
  const ui = await openChat(t, null, false, { statusFor: () => { throw new RunStarting('resolving which model each stage runs'); } });
  t.mock.method(controller, 'start', async (workspace, brief, created) => { created(run); return new Promise(() => {}); });
  ui.type('hi'); ui.submit(); await flush(); await flush();
  await ui.poll(); await flush();
  assert.ok(ui.document.querySelector('#app').textContent.includes('Resolving which model each stage runs…'));
  assert.equal(ui.document.querySelector('#chat-error').textContent, '', 'a start in progress is not a read error');
});

test('a run its own start has not saved yet is starting, not a status notice, until the start returns', async (t) => {
  // A core older than its launch note (#3079), or a read before the core reserves the directory,
  // refuses --status for the chat's own run while its first message is still starting it.
  const unsaved = `usage: autocode [TASK] [OPTIONS]\nautocode: error: --run-dir ${run.runDir} has no state.json. A saved run is PROJECT/.autocode/runs/RUN`;
  const ui = await openChat(t, null, false, { statusFor: () => { throw new Error(unsaved); } });
  let finish;
  t.mock.method(controller, 'start', async (workspace, brief, created) => {
    created(run);
    return new Promise((resolve, reject) => { finish = { resolve, reject }; });
  });
  ui.type('any bugs?'); ui.submit(); await flush(); await flush();
  await ui.poll(); await flush();
  const app = () => ui.document.querySelector('#app').textContent;
  assert.ok(app().includes('Starting the run…'), app());
  assert.ok(!app().includes('has no state.json'), 'the first message shows no refused status read');
  assert.equal(ui.document.querySelector('#chat-error').textContent, '');
  // The start's own failure is still the person's to read once its command returns.
  finish.reject(new Error('AutoCode stopped before it saved the run:\nno engine is connected'));
  await flush(); await flush(); await flush();
  assert.ok(ui.document.querySelector('#chat-error').textContent.includes('no engine is connected'));
});

test('a refused command shows the core\'s reason without the argparse usage line', () => {
  const refused = { code: 2, stdout: '', rejected: false,
    stderr: 'usage: autocode [TASK] [OPTIONS]\nautocode: error: --run-dir /ws/.autocode/runs/x has no state.json' };
  assert.equal(cli.tail(refused), '--run-dir /ws/.autocode/runs/x has no state.json');
  assert.equal(cli.tail({ code: 1, stdout: '', stderr: 'Run: /ws\nsomething failed', rejected: false }), 'Run: /ws\nsomething failed');
  assert.equal(cli.tail({ code: 2, stdout: '', stderr: 'usage: autocode [TASK] [OPTIONS]', rejected: false }), 'usage: autocode [TASK] [OPTIONS]');
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
  const app = ui.document.querySelector('#app').textContent;
  assert.ok(app.includes('Start a live AutoCode run? Answer the dialog that opened.'), app);
  assert.ok(!app.includes('Complete setup'), 'a start waits on the dialog it names, not on a setup step');
  assert.ok(!app.includes('Thinking…'));
  ui.submit(); await flush();
  assert.equal(confirmations, 1, 'duplicate sends cannot open another prompt');
  finish(false); await flush(); await flush();
  assert.equal(start.mock.callCount(), 0);
  assert.equal(ui.document.querySelector('#message').value, 'hi');
  assert.equal(ui.document.querySelector('#send-message').disabled, false);
  assert.ok(!ui.document.querySelector('#app').textContent.includes('Answer the dialog'), 'an answered dialog is no longer named');
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

test('a pending spend confirmation says in the chat that a dialog is open', async (t) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
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
  const ui = await openChat(t, value);
  t.mock.method(host.vscode.window, 'showWarningMessage', () => gate);
  const launch = t.mock.method(cli, 'stream', async () => ok);
  ui.document.querySelector('#take-action').click(); await flush(); await flush();
  // #app, not body: the webview's own script source also contains the notice's words.
  assert.ok(ui.document.getElementById('app').textContent.includes('Prepare a reviewed plan? — answer the dialog that opened'),
    'the chat names the dialog while it waits');
  assert.equal(launch.mock.callCount(), 0, 'nothing runs until the dialog is answered');
  release(confirmation.label); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1);
  assert.ok(!ui.document.getElementById('app').textContent.includes('answer the dialog that opened'), 'the notice leaves with the dialog');
});

// A live chat showed "Here's what I understand..." as a message and then the same text again as a
// raw block in the approval card under it (2026-11-11). review.summary is that question's own text.
const understanding = 'Here’s what I understand. Does this match what you want?\n\nOutcome: Greet by name';
function understandingView(messages) {
  return { status: 'WAITING_FOR_USER', done: false,
    needs: { kind: 'answer', resolver_token: 'review:exact', questions: [
      { id: 'requirements-review-current', question: understanding, options: [], proposed_default: 'plan' },
    ] },
    next_command: `autocode --answer requirements-review-current=ANSWER --resolver-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: 'requirements', context: 'review', input: null, messages, review: {
      question_id: 'requirements-review-current', answer: 'plan', label: 'Approve understanding and prepare plan', summary: understanding,
    } },
  };
}

test('the Requirements understanding shows once: the thread holds it, the approval card does not repeat it', async (t) => {
  const asked = [{ id: 'initial', role: 'user', text: 'greet by name' },
    { id: 'requirements-question-abc123', role: 'assistant', text: understanding }];
  const ui = await openChat(t, understandingView(asked));
  const thread = ui.document.getElementById('messages').textContent;
  assert.equal(thread.split('Outcome: Greet by name').length - 1, 1, 'the thread shows the understanding once');
  assert.equal(ui.document.querySelector('#app pre.understanding'), null, 'the card does not repeat it');
  assert.ok(!ui.document.getElementById('app').textContent.includes('Outcome: Greet by name'));
  assert.ok(ui.document.querySelector('#take-action').textContent.includes('Approve understanding'), 'the approval is still offered');
});

test('the approval card shows the understanding when the thread does not hold it', async (t) => {
  // An older core sent no requirements-question message: the card is then the only place it is shown.
  const ui = await openChat(t, understandingView([{ id: 'initial', role: 'user', text: 'greet by name' }]));
  assert.ok(ui.document.querySelector('#app pre.understanding').textContent.includes('Outcome: Greet by name'));
});

test('a send dropped for a stale chat key keeps the draft and says so', async (t) => {
  const ui = await openChat(t, chatView());
  ui.type('Do the thing');
  const launch = t.mock.method(cli, 'stream', async () => ok);
  await ui.chat.receive({ type: 'send', text: 'Do the thing', runKey: 'new-stale' });
  await flush();
  assert.ok(ui.document.getElementById('chat-error').textContent.includes('send it again'),
    'a dropped send is reported, never silent');
  assert.equal(ui.document.getElementById('message').value, 'Do the thing', 'the typed message stays in the composer');
  assert.equal(launch.mock.callCount(), 0);
});

test('a fresh chat the host re-keyed keeps the typed brief', async (t) => {
  const ui = await openChat(t, null, false);
  ui.type('Fix the bug in tools/x.py');
  ui.window.dispatchEvent(new ui.window.MessageEvent('message', { data: { type: 'chat', runKey: 'new-second', busy: false,
    tabs: [{ key: 'new-second', name: 'New chat', run: null }], activeKey: 'new-second', state: null } }));
  await flush();
  assert.equal(ui.document.getElementById('message').value, 'Fix the bug in tools/x.py',
    'a host re-key of a chat that never started must not wipe an unsent brief');
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

test('closing a chat that was mid-reply returns every poll to the ordinary interval', async (t) => {
  const growing = (text) => ({ status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [{ id: 'u1', role: 'user', text: 'A book app' }, { id: 'live', role: 'assistant', text, streaming: true }],
  } });
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other' };
  const views = new Map([
    [run.runDir, growing('Who')],
    [other.runDir, { status: 'RUNNING', done: false, needs: { kind: 'continue' } }],
  ]);
  const ui = await openChat(t, growing('Who'), run, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(other);
  await flush();
  // The streaming chat's tab is closed while its reply still grows.
  [...ui.document.querySelectorAll('.chat-tab .close')][0].click();
  await flush();
  const reads = () => controller.status.mock.callCount();
  t.mock.timers.tick(250); await flush();
  t.mock.timers.tick(250); await flush();
  const settled = reads();
  t.mock.timers.tick(250); await flush();
  assert.equal(reads(), settled, 'a closed chat must not hold every poll at the streaming pace');
  t.mock.timers.tick(5000); await flush();
  assert.equal(reads(), settled + 1, 'the ordinary interval still polls the chat that is open');
});

test('deleting the only chat while it was mid-reply returns the next chat to the ordinary interval', async (t) => {
  const growing = (text) => ({ status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [{ id: 'u1', role: 'user', text: 'A book app' }, { id: 'live', role: 'assistant', text, streaming: true }],
  } });
  const other = { workspace: '/ws', runDir: '/ws/.autocode/runs/other' };
  const views = new Map([
    [run.runDir, growing('Who')],
    [other.runDir, { status: 'RUNNING', done: false, needs: { kind: 'continue' } }],
  ]);
  const ui = await openChat(t, growing('Who'), run, { statusFor: (target) => views.get(target.runDir) });
  // The only tab is deleted while its reply still grows: a fresh chat replaces it without closing it.
  ui.chat.forget(run);
  await flush();
  ui.chat.show(other);
  await flush();
  const reads = () => controller.status.mock.callCount();
  t.mock.timers.tick(250); await flush();
  t.mock.timers.tick(250); await flush();
  const settled = reads();
  t.mock.timers.tick(250); await flush();
  t.mock.timers.tick(250); await flush();
  assert.equal(reads(), settled, 'a deleted chat must not hold every poll at the streaming pace');
  t.mock.timers.tick(5000); await flush();
  assert.equal(reads(), settled + 1, 'the ordinary interval still polls the chat that is open');
});

test('a second refresh joins the running poll cycle instead of returning before it lands', async (t) => {
  const own = new RunController(host.log);
  const base = { status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [{ id: 'u1', role: 'user', text: 'A book app' }],
  } };
  const ui = await openChat(t, base, run, { controller: own });
  let release;
  t.mock.method(own, 'pollStatus', () => new Promise((resolve) => { release = () => resolve(structuredClone(base)); }));
  const first = ui.chat.refresh();
  await flush();
  let joined = false;
  const second = ui.chat.refresh().then(() => { joined = true; });
  await flush();
  assert.equal(joined, false, 'joining refresh() must wait for the running cycle, not resolve at once');
  release();
  await Promise.all([first, second]);
  assert.equal(joined, true);
});

test('a reply that starts streaming after a finished-only thread is shown while it grows', async (t) => {
  const base = { status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [{ id: 'u1', role: 'user', text: 'A book app' }],
  } };
  const ui = await openChat(t, base);
  // The assistant row arrives already streaming on a later read. The finished set
  // is unchanged, so only the live extract changed: the in-place path must still
  // show the provisional row, which it never rendered before.
  const next = structuredClone(base);
  next.chat.messages.push({ id: 'live', role: 'assistant', text: 'Who will', streaming: true });
  await ui.poll(next);
  const live = ui.document.querySelector('#messages .message.streaming');
  assert.ok(live, 'a newly started reply shows as provisional at once');
  assert.equal(live.querySelector('.message-content').textContent, 'Who will');
  const grown = structuredClone(next);
  grown.chat.messages[1].text = 'Who will use it?';
  await ui.poll(grown);
  assert.equal(ui.document.querySelector('#messages .message.streaming .message-content').textContent, 'Who will use it?');
});

test('one chat\'s slow status read does not hold back another chat\'s display', async (t) => {
  const own = new RunController(host.log);
  const slowRun = { workspace: '/ws', runDir: '/ws/.autocode/runs/slow' };
  const base = { status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
    phase: 'requirements', context: 'turn-one', input: null,
    messages: [{ id: 'u1', role: 'user', text: 'A book app' }, { id: 'a1', role: 'assistant', text: 'first answer' }],
  } };
  const ui = await openChat(t, base, run, { controller: own });
  ui.chat.show(slowRun);
  await flush();
  ui.chat.show(run);
  await flush();
  let releaseSlow;
  t.mock.method(own, 'pollStatus', (target) => {
    if (target.runDir === slowRun.runDir) return new Promise((resolve) => { releaseSlow = () => resolve(structuredClone(base)); });
    return Promise.resolve({ status: 'RUNNING', done: false, needs: { kind: 'continue' }, chat: {
      phase: 'requirements', context: 'turn-one', input: null,
      messages: [{ id: 'u1', role: 'user', text: 'A book app' }, { id: 'a1', role: 'assistant', text: 'fresh answer' }],
    } });
  });
  t.mock.timers.tick(5000);
  await flush();
  assert.ok(ui.document.querySelector('#messages').textContent.includes('fresh answer'),
    'the chat whose read landed is shown while another chat\'s read is still out');
  releaseSlow();
  await flush();
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

// Two chats in one folder (2026-11-11): after "Looks right. Prepare the plan." the card said "Next: Planner",
// then fell back to a bare Continue because the other chat held the folder; Continue found it busy again.
const folderWait = 'Planner is waiting for this folder: another chat is working in it, and two chats would change the same files at once. Nothing was changed.';
function waitingForFolderView(wait = true) {
  return { status: 'RUNNING', done: false, phase: 'DISCOVERING', next_stage: 'draft_plan', iteration: 1, needs: { kind: 'continue' },
    liveness: { kind: 'unknown' }, controller: { kind: 'ready', checked: true, alive: false },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --no-chat`,
    checkout_wait: wait ? { run_dir: '/project/.autocode/runs/other-chat', since: null, text: folderWait } : null,
    chat: { phase: 'requirements', context: 'ready', input: null,
      messages: [{ id: 'u1', role: 'user', text: 'Greet by name' }, { id: 'u2', role: 'user', text: 'Looks right. Prepare the plan.' }],
      progress: wait ? ['Next: Planner', folderWait] : ['Next: Planner'] } };
}

test('a run waiting for another chat to leave its folder keeps its progress card and offers no Continue', async (t) => {
  const ui = await openChat(t, waitingForFolderView());
  const app = () => ui.document.querySelector('#app').textContent;
  assert.ok(app().includes('Next: Planner'), app());
  assert.ok(app().includes(folderWait), 'the card says what the run waits for');
  assert.equal([...ui.document.querySelectorAll('#app button')].filter((button) => button.textContent === 'Continue').length, 0,
    'no button the person would press only to find the folder still busy');
  assert.ok(!app().includes('AutoCode is waiting for you'));
  assert.equal(host.notices.length, 0, 'a wait the client settles itself is not announced as waiting for the person');
});

test('once the other chat leaves the folder, the chat takes the named continue itself, once', async (t) => {
  const ui = await openChat(t, waitingForFolderView());
  const launch = t.mock.method(cli, 'stream', async () => { ui.setView(workingView()); return ok; });
  await ui.poll();
  assert.equal(launch.mock.callCount(), 0, 'nothing is taken while the folder is busy');
  ui.setView(waitingForFolderView(false));
  await ui.poll(); await flush(); await flush();
  assert.equal(launch.mock.callCount(), 1, 'the run goes on as soon as the folder is free');
  const [, args] = launch.mock.calls[0].arguments;
  assert.ok(args.includes('--no-chat') && !args.includes('--approve-goal') && !args.some((arg) => arg.startsWith('--answer')),
    'only the plain continue the view names');
  await ui.poll(); await flush();
  assert.equal(launch.mock.callCount(), 1, 'taken once, not on every poll');
});

test('a Continue on a ready run that never waited for its folder is still the person\'s to press', async (t) => {
  const ui = await openChat(t, waitingForFolderView(false));
  const launch = t.mock.method(cli, 'stream', async () => ok);
  await ui.poll(); await flush();
  assert.equal(launch.mock.callCount(), 0, 'no wait was seen, so nothing is taken on a poll');
  assert.ok([...ui.document.querySelectorAll('#app button')].some((button) => button.textContent === 'Continue'));
});

test('approving the understanding goes on through a ready boundary until the Planner is working', async (t) => {
  // One continue can end at a ready boundary that needs only the next (a chat moving to its own worktree
  // before planning, or a step that saved its phase and stopped). Taking just one left the person a bare Continue after "Looks right. Prepare the plan."
  host.config.set('confirmPersonOnly', false);
  t.after(() => host.config.delete('confirmPersonOnly'));
  const confirmation = { title: 'Prepare a reviewed plan?', detail: 'Planning uses your selected models.', label: 'Prepare plan' };
  const review = { status: 'WAITING_FOR_USER', done: false,
    needs: { kind: 'answer', resolver_token: 'review:exact', questions: [
      { id: 'requirements-review-current', question: 'Is this what you want?', options: [], proposed_default: 'plan' },
    ] },
    next_command: `autocode --answer requirements-review-current=ANSWER --resolver-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: 'requirements', context: 'review', input: null, messages: [], review: {
      question_id: 'requirements-review-current', answer: 'plan', label: 'Approve understanding and prepare plan', spend_confirmation: confirmation,
    } } };
  const ui = await openChat(t, review);
  t.mock.method(host.vscode.window, 'showWarningMessage', async () => confirmation.label);
  const ready = (phase) => ({ ...waitingForFolderView(false), phase });
  const steps = [ready('DISCOVERING'), ready('PLANNING'), workingView()];
  const launch = t.mock.method(cli, 'stream', async () => { ui.setView(steps.shift()); return ok; });
  ui.document.querySelector('#take-action').click();
  for (let i = 0; i < 20; i += 1) await flush();
  const taken = launch.mock.calls.map((call) => call.arguments[1]);
  assert.equal(taken.length, 3, 'the answer, then a continue for each ready boundary, then nothing while it works');
  assert.ok(taken[0].includes('requirements-review-current=plan'));
  assert.ok(taken.slice(1).every((args) => args.includes('--no-chat')));
});

test('a continue that moves nothing on is not repeated', async (t) => {
  const ui = await openChat(t, waitingForFolderView(false));
  const launch = t.mock.method(cli, 'stream', async () => ok);
  [...ui.document.querySelectorAll('#app button')].find((button) => button.textContent === 'Continue').click();
  for (let i = 0; i < 4; i += 1) await flush();
  assert.equal(launch.mock.callCount(), 1, 'the same ready boundary after a continue is not taken again');
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

test('the live diff the view names is offered while the Builder streams its log', async (t) => {
  const working = ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--working'];
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    code_checkpoints: { rows: [], working: { id: 'working', label: 'Show what the Builder has changed so far', next_command: working } },
    chat: { phase: null, context: 'live-step', input: null, messages: [], progress: ['Builder working · task M1'],
      now: [{ agent: 'Builder', text: 'editing greet.py' }],
      log: [{ id: 'log-1', agent: 'Builder', kind: 'edit', text: 'edited greet.py', summary: 'edit', status: 'running' }] } };
  const ui = await openChat(t, value);
  const text = () => ui.document.querySelector('#app').textContent;
  assert.ok(text().includes('Builder'), 'the log renders');
  const show = ui.document.querySelector('#working-diff');
  assert.ok(show, "the running Builder's changes are offered while its log streams");
  const calls = [];
  t.mock.method(cli, 'invoke', async (_settings, argv) => {
    calls.push(argv);
    return { ...ok, stdout: JSON.stringify({ since: 'start', changed_files: ['greet.py'],
      patch: '--- checkpoint/greet.py\n+++ current/greet.py\n+print(1)\n', truncated: false }) };
  });
  show.click();
  await flush();
  assert.deepEqual(calls, [working], 'only the named argv runs');
  assert.ok(text().includes('1 file(s) changed since the run started'));
  assert.ok(text().includes('+print(1)'), 'the diff shows under the log');
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
  const headers = [...ui.document.querySelectorAll('#app .live-log .log-agent')].map((node) => node.textContent);
  assert.deepEqual(headers, ['Builder · task M2', 'Plan Reviewer'], 'each agent is named once per run of its steps');
  assert.equal(ui.document.querySelector('#app .live-log .agent'), null, 'no row repeats the agent name');
});

test('the live agent log names an agent once and folds its reasoning to one line', async (t) => {
  // The shape of a live Requirements stage (Ankur's report, 2026-11-10): the stage name was
  // printed above every step and each thought was shown in full with the [partial] marker.
  const thought = '…in actual code, maybe just the top of server.py and app.py to see routes. [partial]';
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    chat: { phase: null, context: 'live-step', input: null, messages: [], progress: ['Requirements working'],
      log: [
        { id: 'r1', agent: 'Requirements', kind: 'tool', text: 'read README.md', status: 'done' },
        { id: 'r2', agent: 'Requirements', kind: 'command', text: 'ls engine tools', status: 'done' },
        { id: 'r3', agent: 'Requirements', kind: 'thinking', text: thought },
        { id: 'r4', agent: 'Requirements', kind: 'command', text: 'head -60 engine/unified/server.py', status: 'running' },
        { id: 'r5', agent: 'Planner', kind: 'tool', text: 'read deploy/entrypoint.sh', status: 'done' },
        { id: 'r6', agent: 'Requirements', kind: 'thinking', text: 'Back to the brief.' },
      ] } };
  const ui = await openChat(t, value);
  const log = () => ui.document.querySelector('#app .live-log');
  const headers = () => [...log().querySelectorAll('.log-agent')].map((node) => node.textContent);
  assert.deepEqual(headers(), ['Requirements', 'Planner', 'Requirements'],
    'consecutive steps of one agent share a header; a new header only when the agent changes');
  assert.equal(log().querySelectorAll('.activity-row').length, 6, 'every step is still listed');
  const folded = log().querySelector('.activity-row.thinking details.thought');
  assert.ok(folded, 'reasoning is a foldable line');
  assert.equal(folded.open, false, 'reasoning starts folded');
  assert.equal(folded.querySelector('summary').textContent, 'Thinking…', 'a thought still arriving says so in words');
  const words = folded.querySelector('.fold-line');
  assert.equal(words.textContent, '…in actual code, maybe just the top of server.py and app.py to see routes.');
  assert.equal(words.title, 'Still arriving', 'the streaming marker becomes a tooltip');
  assert.ok(!log().textContent.includes('[partial]'), 'the core marker is not shown as part of the thought');
  const finished = log().querySelectorAll('details.thought')[1];
  assert.equal(finished.querySelector('summary').textContent, 'Thought');
  assert.equal(finished.querySelector('.fold-line').title, '', 'a finished thought has no streaming tooltip');
  // An unfolded thought stays unfolded when the next read redraws the log.
  folded.open = true;
  folded.dispatchEvent(new ui.window.Event('toggle'));
  const next = structuredClone(value);
  next.chat.log.push({ id: 'r7', agent: 'Requirements', kind: 'command', text: 'cat app.py', status: 'running' });
  await ui.poll(next);
  const redrawn = [...log().querySelectorAll('details.thought')];
  assert.deepEqual(redrawn.map((node) => node.open), [true, false], 'only the thought a person opened stays open');
});

test('the live agent log shows one stage header, folds reasoning and counts repeated tool calls', async (t) => {
  // Ankur's report, 2026-11-11: "Requirements working" above a "Requirements" header, two raw
  // thoughts in full, and "· grep tools" four times with nothing about what was searched.
  const searched = (id, where, what) => ({ id, agent: 'Requirements', kind: 'tool', status: 'done',
    text: `Searched ${where} for “${what}”`, summary: 'Searched code' });
  const value = { status: 'RUNNING', done: false, needs: { kind: 'continue' },
    liveness: { kind: 'supervised' }, next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir}`,
    progress: { headline: 'Requirements working' },
    chat: { phase: null, context: 'live-step', input: null, messages: [], progress: ['Requirements working'],
      log: [
        { id: 'r1', agent: 'Requirements', kind: 'thinking', text: 'Let me read these files.' },
        { id: 'm1', agent: 'Requirements', kind: 'message', text: 'Let me now read the key files.' },
        searched('g1', 'tools', 'def context_packet'),
        searched('g2', 'tools', 'def prepare'),
        { id: 'r2', agent: 'Requirements', kind: 'thinking', text: 'The packet carries the evidence. [partial]' },
        { id: 'r3', agent: 'Requirements', kind: 'thinking', text: 'So the review can drop it.' },
        { id: 'x1', agent: 'Requirements', kind: 'tool', status: 'failed', text: 'lsp a.py', summary: 'Used lsp' },
      ],
      now: [{ agent: 'Requirements', text: 'searching the code' }] } };
  const ui = await openChat(t, value);
  const log = () => ui.document.querySelector('#app .live-log');
  const lines = [...ui.document.querySelectorAll('#app .line')].map((node) => node.textContent);
  assert.ok(!lines.includes('Requirements working'), 'the run headline is not repeated above the log');
  assert.deepEqual([...log().querySelectorAll('.log-agent')].map((node) => node.textContent),
    ['Requirements · searching the code'], 'one header, saying what the stage does now');
  const rows = [...log().querySelectorAll('.activity-row')];
  assert.deepEqual(rows.map((row) => row.dataset.kind), ['thinking', 'message', 'tool', 'thinking', 'tool']);
  const tools = log().querySelector('details.tool-group');
  assert.equal(tools.querySelector('summary').textContent, 'Searched code ×2', 'repeats are one counted line');
  assert.equal(tools.open, false);
  assert.deepEqual([...tools.querySelectorAll('.fold-line')].map((node) => node.textContent),
    ['Searched tools for “def context_packet”', 'Searched tools for “def prepare”'], 'each search is kept behind it');
  const thoughts = [...log().querySelectorAll('details.thought')];
  assert.deepEqual(thoughts.map((node) => node.querySelector('summary').textContent), ['Thought', 'Thinking… ×2'],
    'consecutive thoughts fold into one line that says it is still thinking');
  assert.ok(thoughts.every((node) => !node.open), 'reasoning starts folded');
  assert.equal(rows[4].className.includes('failed'), true);
  assert.equal(rows[4].textContent, '✗lsp a.py', 'one tool call is shown as it is');
  assert.deepEqual(rows.map((row) => row.querySelector('.activity-mark').title),
    ['Reasoning', 'Said', 'Used a tool', 'Reasoning', 'Failed'], 'each mark says what it stands for');
  // A tool group a person opened stays open when the next read redraws the log.
  tools.open = true;
  tools.dispatchEvent(new ui.window.Event('toggle'));
  const next = structuredClone(value);
  next.chat.log.push(searched('g3', 'docs', 'evidence'), searched('g4', 'docs', 'report'));
  await ui.poll(next);
  const grown = [...log().querySelectorAll('details.tool-group')];
  assert.deepEqual(grown.map((node) => [node.querySelector('summary').textContent, node.open]),
    [['Searched code ×2', true], ['Searched code ×2', false]], 'searches after another tool start their own line');
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
    next_command: `autocode --answer route-glm=MODEL --resolver-token - --workspace ${run.workspace} --run-dir ${run.runDir}`,
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
  assert.deepEqual(env, {}, 'the named answer command reads its token from the run (#2958)');
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

  for (const status of ['PAUSED_STALE_VALIDATION', 'RUNNING']) test(`a chat the core cannot continue ends in ${surface} at ${status} with a new conversation, not a client gap`, async (t) => {
    // Ankur, 2026-11-11: a chat saved under the old route names showed "No action the client can take",
    // since its next_command only explains the stop (--explain). The core now marks it stopped.
    const reason = 'This run was saved before AutoCode named its stages after their jobs. Its status and saved reports stay readable; start a new task to continue the work.';
    const view = {
      status, done: false,
      needs: { kind: 'resume', reason, recovery_hint: reason, new_run_required: true, edit_required: false, action: 'fresh_run', feedback_action: null },
      // While an earlier runtime's worker still runs it, the view names no command at all.
      next_command: status === 'RUNNING' ? null : `autocode --run-dir ${run.runDir} --workspace ${run.workspace} --explain`,
      recovery: { category: 'stopped', title: "This chat can't continue", what_happened: reason,
        retained: '0 finished attempt(s) remain in the history.',
        actions: [{ kind: 'inspect', label: 'Inspect saved work' },
          { kind: 'new_conversation', label: 'Start a new conversation' }] },
    };
    const ui = surface === 'chat' ? await openChat(t, view) : await openPanel(t, view);
    const text = ui.document.querySelector('#app').textContent;
    assert.ok(text.includes("This chat can't continue"));
    assert.ok(text.includes('start a new task'));
    assert.ok(!text.includes('No action the client can take'));
    assert.ok(ui.document.querySelector('#start-new-conversation'));
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
  const command = 'autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message MESSAGE';
  view.chat.input = { kind: 'resolver_response', label: 'Send recovery reply', next_command: command };
  await controller.sendMessage(view, run, 'I corrected the fixture', 'chat-1');
  assert.deepEqual(launch.mock.calls[0].arguments[1], [
    '--resolver-request', 'req-1', '--resolver-token', '-', '--resolver-response', 'provide_information',
    '--resolver-message', 'I corrected the fixture', '--workspace', run.workspace, '--run-dir', run.runDir,
  ]);
  assert.deepEqual(launch.mock.calls[0].arguments[2], {}, 'the client holds no token value (#2958)');
  for (const bad of [command.replace('req-1', 'req-old'), `AUTOCODE_RESOLVER_TOKEN=current-token ${command}`,
    command + ' --resume-paused', command.replace('provide_information', 'retry')]) {
    view.chat.input.next_command = bad;
    await assert.rejects(controller.sendMessage(view, run, 'reply', 'chat-1'), /Unsupported message action/);
  }
  assert.equal(launch.mock.callCount(), 1);
});

test('Run details points to the bound operational reply instead of claiming no action exists', async (t) => {
  const view = chatView({ status: 'WAITING_FOR_USER', done: false, needs: {
    kind: 'answer', resolver_scope: 'operational_exhaustion', resolver_request_id: 'req-1', resolver_token: 'current-token',
  }, next_command: 'autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message "WHAT CHANGED"' });
  view.chat.input = { kind: 'resolver_response', label: 'Send recovery reply',
    next_command: 'autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message MESSAGE' };
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
            { id: 'step', label: 'Show what this step changed',
              next_command: ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--step', 'cp-1'] },
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
    ['cp-1:compare', 'cp-1:step', 'cp-1:restore'],
    'unavailable rows offer no actions',
  );
});

test('a saved step shows what that step itself changed', async (t) => {
  const ui = await openChat(t, checkpointView());
  const invoke = t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: JSON.stringify({
    version: 1, checkpoint_id: 'cp-1', since: 'start', changed_files: ['app.txt'],
    patch: 'diff --step app.txt\n+hello', truncated: false,
  }) }));
  ui.document.querySelector('[data-checkpoint-action="step"]').click();
  await flush(); await flush();
  assert.deepEqual(invoke.mock.calls[0].arguments[1],
    ['checkpoint', '--workspace', run.workspace, '--run-dir', run.runDir, '--step', 'cp-1']);
  const text = ui.document.querySelector('#app').textContent;
  assert.match(text, /1 file\(s\) changed in this step/);
  assert.ok(text.includes('+hello'));
  assert.ok(!text.includes('differ from this checkpoint'), 'a step view is not a compare');
});

test('a raise confirmation names the open dialog in the chat while it pends', async (t) => {
  host.config.set('confirmPersonOnly', false);
  const ownController = new RunController(host.log);
  const ui = await openChat(t, heldCostView('cost:current'), run, { controller: ownController });
  const field = ui.document.querySelector('[data-question="raise:max_cost_usd"]');
  field.value = '5'; field.dispatchEvent(new ui.window.Event('input'));
  let release;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => new Promise((resolve) => { release = resolve; }));
  ui.document.querySelector('#raise-bound').click();
  await flush();
  const hint = () => ui.document.querySelector('#send-hint').textContent;
  assert.ok(hint().includes('answer the dialog that opened'), `the chat names the open dialog: ${hint()}`);
  assert.ok(hint().includes('Set the cost cap to $5'), `the hint is the dialog's own question: ${hint()}`);
  release(undefined);
  await flush(); await flush();
  assert.ok(!hint().includes('answer the dialog that opened'), `the hint reverts once the dialog is answered: ${hint()}`);
});

test('a restore confirmation names the open dialog in the chat while it pends', async (t) => {
  const ui = await openChat(t, checkpointView());
  t.mock.method(cli, 'invoke', async () => ({ ...ok, stdout: JSON.stringify({
    version: 1, checkpoint_id: 'cp-1', expected_token: 'tok-1', changed_files: ['app.txt'], patch: '', truncated: false,
  }) }));
  let release;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => new Promise((resolve) => { release = resolve; }));
  ui.document.querySelector('[data-checkpoint-action="restore"]').click();
  await flush(); await flush();
  const hint = () => ui.document.querySelector('#send-hint').textContent;
  assert.ok(hint().includes('answer the dialog that opened'), `the chat names the open dialog: ${hint()}`);
  assert.ok(hint().includes('Restore'), `the hint is the dialog's own question: ${hint()}`);
  release(undefined);
  await flush(); await flush();
  assert.ok(!hint().includes('answer the dialog that opened'), `the hint reverts once the dialog is answered: ${hint()}`);
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

test('a first message with no folder open says to open one and keeps the message', async (t) => {
  host.vscode.workspace.workspaceFolders = undefined;
  const ui = await openChat(t, null, false, { actions: { pickWorkspace } });
  ui.chat.show();
  await flush();
  const start = t.mock.method(controller, 'start', async () => run);

  ui.type('hi');
  ui.submit();
  await flush(); await flush();

  assert.equal(start.mock.callCount(), 0, 'nothing is launched without a folder');
  assert.equal(ui.document.querySelector('#chat-error').textContent, NO_FOLDER);
  assert.equal(ui.document.querySelector('#message').value, 'hi', 'the message stays in the box');
});

test('the workspace is the open folder, or the one picked from several', async (t) => {
  t.after(() => { host.vscode.workspace.workspaceFolders = undefined; });
  const folder = (name) => ({ name, uri: { fsPath: `/${name}` } });
  host.vscode.workspace.workspaceFolders = [folder('one')];
  assert.equal(await pickWorkspace(), '/one');
  host.vscode.workspace.workspaceFolders = [folder('one'), folder('two')];
  t.mock.method(host.vscode.window, 'showQuickPick', async (items) => items[1]);
  assert.equal(await pickWorkspace(), '/two');
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

test('History archive, unarchive and delete take the row’s named action, and delete asks first', async (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const performed = [];
  t.mock.method(RunController.prototype, 'registryAction', async (target, action) => {
    performed.push({ target, action });
  });
  let confirm = true;
  const warnings = [];
  t.mock.method(host.vscode.window, 'showWarningMessage', async (message, options, choice) => {
    warnings.push({ message, options, choice });
    return confirm ? choice : undefined;
  });
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));

  const base = { id: 'chat-1', workspace: run.workspace, runDir: run.runDir, taskId: null,
    availability: 'available', status: 'TASK_COMPLETE', title: 'Greeting chat' };
  const archiveAction = { id: 'archive', label: 'Archive',
    nextCommand: ['registry', 'archive', '--workspace', run.workspace, '--run-dir', run.runDir] };
  const unarchiveAction = { id: 'unarchive', label: 'Unarchive',
    nextCommand: ['registry', 'unarchive', '--workspace', run.workspace, '--run-dir', run.runDir] };
  const deleteAction = { id: 'delete', label: 'Delete',
    nextCommand: ['registry', 'delete', '--workspace', run.workspace, '--run-dir', run.runDir, '--yes'] };

  await host.commands.get('autocode.archiveRun')(new RunItem({ ...base, actions: [archiveAction] }));
  assert.deepEqual(performed[0], { target: { workspace: run.workspace, runDir: run.runDir }, action: archiveAction });

  await host.commands.get('autocode.unarchiveRun')(new RunItem({ ...base, archived: true, actions: [unarchiveAction] }));
  assert.deepEqual(performed[1], { target: { workspace: run.workspace, runDir: run.runDir }, action: unarchiveAction });

  await host.commands.get('autocode.deleteRun')(new RunItem({ ...base, actions: [deleteAction] }));
  assert.match(warnings[0].message, /Delete the chat "Greeting chat"\?/);
  assert.match(warnings[0].options.detail, /permanently removes/);
  assert.deepEqual(performed[2], { target: { workspace: run.workspace, runDir: run.runDir }, action: deleteAction });

  confirm = false;
  await host.commands.get('autocode.deleteRun')(new RunItem({ ...base, actions: [deleteAction] }));
  assert.equal(performed.length, 3, 'a cancelled delete runs nothing');
});

test('a deleted chat’s tab closes and leaves the other chats', async (t) => {
  const ui = await openChat(t, chatView());
  const other = { ...run, runDir: '/ws/.autocode/runs/other' };
  ui.chat.show(other);
  await flush();
  assert.equal(ui.document.querySelectorAll('#chat-tabs > *').length, 2);
  ui.chat.forget(run);
  await flush();
  assert.equal(ui.document.querySelectorAll('#chat-tabs > *').length, 1);
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

test('a failed status read keeps the status bar up saying so instead of hiding it', async (t) => {
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
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  let failing = true;
  t.mock.method(RunController.prototype, 'status', async () => {
    if (failing) throw new Error('autocode timed out after 30000ms');
    return { status: 'RUNNING', done: false, needs: { kind: 'continue' }, progress: { headline: 'Builder working' } };
  });

  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));

  await host.commands.get('autocode.openRun')(run);
  await flush();
  assert.equal(statusVisible, true, 'the bar stays up while the run\'s status cannot be read');
  assert.match(statusText, /status unavailable/);
  failing = false;
  t.mock.timers.tick(5000);
  await flush();
  assert.equal(statusVisible, true);
  assert.ok(statusText.includes('Builder working'), 'the bar shows the run again as soon as a read succeeds');
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

test('the run header lists every stage the run launched in one aligned table, with time, tokens, cost and model (#2610 parity)', async (t) => {
  // Ankur, 2026-11-11: a second Stages box above the composer repeated the header in two-line rows
  // with whole provider ids; each stage is now one row of the header's table, and that box is gone.
  const ui = await openChat(t, meteredView());
  assert.equal(ui.document.getElementById('stages'), null, 'no second Stages box above the composer');
  const rows = [...ui.document.querySelectorAll('#pipeline tbody tr')];
  assert.deepEqual(rows.map((row) => row.dataset.job), ['Requirements', 'Planner', 'Plan Reviewer', 'Builder', 'Tester']);
  const builder = rows[3];
  assert.equal(builder.className, 'ran');
  assert.deepEqual([...builder.querySelectorAll('td')].map((cell) => cell.textContent),
    ['✓', 'Builderclaude-haiku-4-5 medium', 'claude-haiku-4-5 medium', '30s', '1.2k', '$0.31']);
  const tester = rows[4];
  assert.equal(tester.className, 'working retrying');
  assert.equal(tester.querySelector('.mark').textContent, '↻');
  const badge = tester.querySelector('.runs');
  assert.deepEqual([badge.textContent, badge.className, badge.title], ['×2', 'runs failed', '2 attempts, 1 failed']);
  assert.deepEqual([...tester.querySelectorAll('td.num')].map((cell) => cell.textContent), ['42s', '2.4k', '$0.20']);
  assert.match(tester.title, /last one answer rejected/);
  assert.equal(rows[0].querySelector('.runs'), null, 'a stage that launched once has no count');
  assert.deepEqual([...ui.document.querySelectorAll('#pipeline tfoot td.num')].map((cell) => cell.textContent), ['', '651k', '$1.21']);
});

test('a stage on a later attempt shows a struggling warning in the working card', async (t) => {
  const ui = await openChat(t, meteredView());
  const warn = ui.document.getElementById('struggling');
  assert.ok(warn, 'the working card carries the warning');
  assert.equal(warn.textContent, '⚠ Tester · attempt 2 · 42s · last answer rejected');
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

test('a new chat welcomes with one short line and no example list', async (t) => {
  const ui = await openChat(t, null, false);
  const welcome = ui.document.getElementById('welcome');
  assert.ok(welcome, 'a new chat shows the welcome');
  assert.equal(welcome.querySelectorAll('p').length, 1, 'one line, not two paragraphs');
  assert.match(welcome.textContent, /Describe a change, or ask about this code\. Changes are planned for your approval, then built and tested\./);
  assert.doesNotMatch(welcome.textContent, /Try asking/);
  assert.equal(welcome.querySelector('button'), null, 'no example requests to click');
  assert.equal(welcome.querySelector('.kinds'), null, 'every task takes one path, so there is no job picker');
  assert.equal(ui.document.getElementById('message').value, '');
  assert.equal(ui.document.getElementById('run-header').hidden, true);
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

// #3025: the approval surfaces show the plan's declared milestones and end-to-end flow.
const MILESTONES = [{ id: 'M1', objective: 'Build the core', acceptance_criteria: ['AC1'] }];
const FLOW = ['Step one', 'Step two'];

function shownText(document) {
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script, style').forEach((node) => node.remove());
  return clone.textContent;
}

function planWithOrder(extra = {}) {
  const view = approvalView();
  view.displayed_plan = { ...view.displayed_plan, milestones: MILESTONES, end_to_end_flow: FLOW, ...extra };
  return view;
}

test('test_ac1_state_for_carries_milestones_and_flow', () => {
  const state = stateFor(planWithOrder(), run, controller);
  assert.deepEqual(state.plan.milestones, MILESTONES);
  assert.deepEqual(state.plan.flow, FLOW);
});

test('test_ac2_cards_render_milestones_and_flow', async (t) => {
  for (const surface of ['chat', 'Run details']) {
    await t.test(surface, async (t) => {
    const view = planWithOrder();
    const ui = surface === 'chat' ? await openChat(t, chatView(view)) : await openPanel(t, view);
    const root = ui.document.body;
    const text = shownText(ui.document);
    const button = ui.document.querySelector('#take-action') || ui.take();
    for (const expected of ['M1', 'Build the core', 'AC1', 'Step one', 'Step two']) {
      assert.ok(text.includes(expected), `${surface} missing ${expected}`);
    }
    const flowNode = [...root.querySelectorAll('*')].find((n) => n.children.length === 0 && n.textContent.includes('Step two'));
    assert.ok(flowNode, `${surface} flow element`);
    assert.ok(flowNode.compareDocumentPosition(button) & 4, `${surface}: flow must precede the approval button`);
    });
  }
});

test('test_ac3_plan_without_fields_renders_unchanged', async (t) => {
  const state = stateFor(approvalView(), run, controller);
  assert.deepEqual(state.plan.milestones, []);
  assert.deepEqual(state.plan.flow, []);
  for (const surface of ['chat', 'Run details']) {
    await t.test(surface, async (t) => {
    const ui = surface === 'chat' ? await openChat(t, chatView(approvalView())) : await openPanel(t, approvalView());
    const text = shownText(ui.document);
    assert.ok(!/milestone/i.test(text), `${surface} shows a milestone heading`);
    assert.ok(!/end-to-end|flow/i.test(text), `${surface} shows a flow heading`);
    });
  }
});

test('test_ac4_milestone_and_flow_text_not_html', async (t) => {
  for (const surface of ['chat', 'Run details']) {
    await t.test(surface, async (t) => {
    const view = planWithOrder({
      milestones: [{ id: 'M1', objective: '<img src=x onerror=alert(1)>', acceptance_criteria: ['AC1'] }],
      end_to_end_flow: ['<b>bold</b>'],
    });
    const ui = surface === 'chat' ? await openChat(t, chatView(view)) : await openPanel(t, view);
    const root = ui.document.body;
    assert.equal(root.querySelectorAll('img').length, 0, `${surface} img element`);
    assert.equal(root.querySelectorAll('b').length, 0, `${surface} b element`);
    assert.ok(shownText(ui.document).includes('<img src=x onerror=alert(1)>'), `${surface} literal objective`);
    assert.ok(shownText(ui.document).includes('<b>bold</b>'), `${surface} literal flow`);
    });
  }
});

test('test_ac5_approval_stays_bound_to_plan_token', () => {
  const plain = stateFor(approvalView(), run, controller);
  const ordered = stateFor(planWithOrder(), run, controller);
  assert.equal(ordered.token, 'r1:abc');
  assert.equal(ordered.token, plain.token);
  assert.equal(ordered.canApprove, true);
  assert.equal(ordered.canApprove, plain.canApprove);
  assert.deepEqual(controller.plan(planWithOrder(), run), controller.plan(approvalView(), run));
});

// A bare copy of tracked source has no out-test (compiled output) and maybe no node_modules. Then the
// helper's host.load is replaced here by one that loads src/*.ts with Node's type stripping, so the
// same shipped modules run. A checkout that has out-test keeps the helper's own load unchanged.
function useTypeScriptSourcesWhenNotCompiled(target) {
  const fs = require('node:fs');
  const nodePath = require('node:path');
  const Module = require('node:module');
  const root = nodePath.resolve(__dirname, '..');
  if (fs.existsSync(nodePath.join(root, 'out-test/src/panel.js'))) return;
  const src = nodePath.join(root, 'src');
  const cache = new Map();
  // Plain-text stand-in for `marked`, used only when it is not installed: one paragraph per block.
  const marked = { lexer: (text) => String(text).split(/\n{2,}/).filter((part) => part.trim()).map((part) => (
    { type: 'paragraph', raw: part, text: part, tokens: [{ type: 'text', raw: part, text: part }] })) };
  function toCommonJs(code) {
    const names = [];
    let out = Module.stripTypeScriptTypes(code, { mode: 'transform' });
    out = out.replace(/^import\s+\*\s+as\s+(\w+)\s+from\s+(["'][^"']+["']);?/gm, 'const $1 = require($2);');
    out = out.replace(/^import\s*\{([^}]*)\}\s*from\s+(["'][^"']+["']);?/gm, (_m, list, from) => {
      const parts = list.split(',').map((x) => x.trim()).filter(Boolean).map((x) => x.replace(/\s+as\s+/, ': '));
      return parts.length ? `const { ${parts.join(', ')} } = require(${from});` : '';
    });
    out = out.replace(/^export\s+(?:async\s+)?(?:function\*?|class|const|let|var|enum)\s+(\w+)/gm, (m, name) => {
      names.push(name);
      return m.replace(/^export\s+/, '');
    });
    out = out.replace(/^export\s+abstract\s+class\s+(\w+)/gm, (_m, name) => { names.push(name); return `class ${name}`; });
    return `${out}\n${names.map((name) => `exports.${name} = ${name};`).join('\n')}\n`;
  }
  function resolveSource(from, request) {
    const base = nodePath.resolve(nodePath.dirname(from), request.replace(/\.js$/, ''));
    for (const candidate of [`${base}.ts`, nodePath.join(base, 'index.ts')]) if (fs.existsSync(candidate)) return candidate;
    throw new Error(`Cannot find TypeScript source for ${request} from ${from}`);
  }
  function loadSource(file) {
    if (cache.has(file)) return cache.get(file).exports;
    const mod = { exports: {} };
    cache.set(file, mod);
    const require_ = (id) => {
      if (id === 'vscode') return target.vscode;
      if (id.startsWith('.')) return loadSource(resolveSource(file, id));
      if (id === 'marked') { try { return require(id); } catch { return marked; } }
      return require(id);
    };
    const code = toCommonJs(fs.readFileSync(file, 'utf8'));
    new Function('exports', 'require', 'module', '__filename', '__dirname', code)(mod.exports, require_, mod, file, nodePath.dirname(file));
    return mod.exports;
  }
  target.load = (name) => loadSource(resolveSource(`${src}/x`, `./${name}`));
}

// Dependency-free DOM stand-in, used only when jsdom is not installed. It parses the generated
// webview HTML and runs the real generated script against it, so assertions are on rendered nodes.
const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr']);
const BOOLEAN_PROPS = ['disabled', 'hidden', 'checked', 'open', 'readOnly', 'selected'];
const STRING_PROPS = ['id', 'title', 'type', 'href', 'src', 'name', 'placeholder', 'htmlFor', 'role', 'rel', 'target', 'tabIndex'];

class MiniEvent {
  constructor(type, init = {}) { Object.assign(this, init); this.type = type; this.defaultPrevented = false; this.bubbles = init.bubbles !== false; }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
  stopImmediatePropagation() { this.stopped = true; }
}

class MiniNode {
  constructor(doc) { this.ownerDocument = doc; this.parentNode = null; this.listeners = {}; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((x) => x !== fn); }
  dispatchEvent(event) {
    event.target ||= this;
    for (let node = this; node && !event.stopped; node = node.parentNode || (node === this.ownerDocument ? null : null)) {
      event.currentTarget = node;
      for (const fn of [...(node.listeners[event.type] || [])]) fn.call(node, event);
      if (!event.bubbles) break;
      if (!node.parentNode && node.nodeType === 9 && node.defaultView) {
        for (const fn of [...(node.defaultView.listeners[event.type] || [])]) fn.call(node.defaultView, event);
      }
    }
    return !event.defaultPrevented;
  }
}

class MiniText extends MiniNode {
  constructor(doc, data) { super(doc); this.nodeType = 3; this.data = String(data); }
  get textContent() { return this.data; }
  set textContent(value) { this.data = String(value); }
  get nodeValue() { return this.data; }
  cloneNode() { return new MiniText(this.ownerDocument, this.data); }
  remove() { this.parentNode?.removeChild(this); }
}

class MiniElement extends MiniNode {
  constructor(doc, tag) {
    super(doc);
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.localName = tag.toLowerCase();
    this.childNodes = []; this.attrs = {}; this.style = {}; this.value = ''; this.scrollTop = 0; this.scrollHeight = 0;
    const self = this;
    this.dataset = new Proxy({}, {
      get: (_t, key) => self.attrs[`data-${String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`],
      set: (_t, key, value) => { self.attrs[`data-${String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`] = String(value); return true; },
      deleteProperty: (_t, key) => { delete self.attrs[`data-${String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`]; return true; },
      has: (_t, key) => `data-${String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}` in self.attrs,
    });
    this.classList = {
      add: (...names) => { const set = new Set(self.className.split(/\s+/).filter(Boolean)); names.forEach((n) => set.add(n)); self.className = [...set].join(' '); },
      remove: (...names) => { self.className = self.className.split(/\s+/).filter((n) => n && !names.includes(n)).join(' '); },
      contains: (name) => self.className.split(/\s+/).includes(name),
      toggle: (name, force) => { const has = self.classList.contains(name); const want = force === undefined ? !has : force;
        if (want && !has) self.classList.add(name); else if (!want && has) self.classList.remove(name); return want; },
    };
  }
  get className() { return this.attrs.class || ''; }
  set className(value) { this.attrs.class = String(value); }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes.at(-1) || null; }
  get firstElementChild() { return this.children[0] || null; }
  get nextSibling() { const list = this.parentNode?.childNodes || []; return list[list.indexOf(this) + 1] || null; }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(value) { this.childNodes.forEach((n) => { n.parentNode = null; }); this.childNodes = []; if (value !== '' && value != null) this.appendChild(new MiniText(this.ownerDocument, value)); }
  get innerText() { return this.textContent; }
  set innerText(value) { this.textContent = value; }
  get innerHTML() { return this.childNodes.map((n) => (n.nodeType === 3 ? n.data : n.outerHTML)).join(''); }
  set innerHTML(value) {
    if (value !== '') throw new Error('MiniDom: innerHTML only clears');
    this.textContent = '';
  }
  get outerHTML() {
    const attrs = Object.entries(this.attrs).map(([k, v]) => ` ${k}="${v}"`).join('');
    return `<${this.localName}${attrs}>${this.innerHTML}</${this.localName}>`;
  }
  appendChild(node) {
    if (node.nodeType === 11) { [...node.childNodes].forEach((child) => this.appendChild(child)); return node; }
    node.parentNode?.removeChild(node); node.parentNode = this; this.childNodes.push(node); return node;
  }
  append(...nodes) { nodes.forEach((n) => this.appendChild(typeof n === 'string' ? new MiniText(this.ownerDocument, n) : n)); }
  prepend(...nodes) { [...nodes].reverse().forEach((n) => this.insertBefore(n, this.firstChild)); }
  insertBefore(node, ref) {
    if (!ref) return this.appendChild(node);
    node.parentNode?.removeChild(node); node.parentNode = this;
    this.childNodes.splice(this.childNodes.indexOf(ref), 0, node); return node;
  }
  removeChild(node) { const i = this.childNodes.indexOf(node); if (i >= 0) { this.childNodes.splice(i, 1); node.parentNode = null; } return node; }
  replaceChildren(...nodes) { this.textContent = ''; this.append(...nodes); }
  remove() { this.parentNode?.removeChild(this); }
  setAttribute(name, value) { this.attrs[name] = String(value); if (name === 'value') this.value = String(value); }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  hasAttribute(name) { return name in this.attrs; }
  removeAttribute(name) { delete this.attrs[name]; }
  click() { if (this.disabled) return; this.dispatchEvent(new MiniEvent('click', { cancelable: true })); }
  focus() { this.ownerDocument.activeElement = this; }
  blur() {}
  scrollIntoView() {}
  select() {}
  contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  cloneNode(deep) {
    const copy = new MiniElement(this.ownerDocument, this.localName);
    Object.assign(copy.attrs, this.attrs); copy.value = this.value;
    if (deep) this.childNodes.forEach((n) => copy.appendChild(n.cloneNode(true)));
    return copy;
  }
  allElements() { const out = []; const walk = (n) => n.children.forEach((c) => { out.push(c); walk(c); }); walk(this); return out; }
  querySelectorAll(selector) { return this.allElements().filter((node) => matchesSelector(node, selector, this)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  matches(selector) { return matchesSelector(this, selector, null); }
  closest(selector) { for (let n = this; n && n.nodeType === 1; n = n.parentNode) if (n.matches(selector)) return n; return null; }
  compareDocumentPosition(other) {
    if (other === this) return 0;
    if (this.contains(other)) return 16 | 4;
    if (other.contains?.(this)) return 8 | 2;
    const order = [this.ownerDocument.documentElement, ...this.ownerDocument.documentElement.allElements()];
    return order.indexOf(other) > order.indexOf(this) ? 4 : 2;
  }
}
for (const name of BOOLEAN_PROPS) {
  Object.defineProperty(MiniElement.prototype, name, {
    get() { return name in this.attrs; },
    set(value) { if (value) this.attrs[name] = ''; else delete this.attrs[name]; },
  });
}
for (const name of STRING_PROPS) {
  const attr = name === 'htmlFor' ? 'for' : name === 'tabIndex' ? 'tabindex' : name;
  Object.defineProperty(MiniElement.prototype, name, {
    get() { return this.attrs[attr] ?? ''; },
    set(value) { this.attrs[attr] = String(value); },
  });
}

function matchesCompound(node, compound) {
  const re = /(#[\w-]+|\.[\w-]+|\[[^\]]+\]|:not\([^)]*\)|[\w-]+|\*)/g;
  for (const part of compound.match(re) || []) {
    if (part === '*') continue;
    if (part[0] === '#') { if (node.attrs.id !== part.slice(1)) return false; }
    else if (part[0] === '.') { if (!node.classList.contains(part.slice(1))) return false; }
    else if (part[0] === '[') {
      const m = /^\[([\w-]+)(?:([~^$*|]?=)["']?(.*?)["']?)?\]$/.exec(part);
      if (!m || !(m[1] in node.attrs)) return false;
      if (m[2] === '=' && node.attrs[m[1]] !== m[3]) return false;
      if (m[2] === '*=' && !node.attrs[m[1]].includes(m[3])) return false;
      if (m[2] === '^=' && !node.attrs[m[1]].startsWith(m[3])) return false;
      if (m[2] === '~=' && !node.attrs[m[1]].split(/\s+/).includes(m[3])) return false;
    } else if (part.startsWith(':not(')) { if (matchesCompound(node, part.slice(5, -1))) return false; }
    else if (node.localName !== part.toLowerCase()) return false;
  }
  return true;
}

function matchesSelector(node, selector, scope) {
  return selector.split(',').some((single) => {
    const parts = single.trim().replace(/\s*>\s*/g, ' > ').split(/\s+/);
    const test = (current, index) => {
      const part = parts[index];
      if (part === '>') return false;
      if (!matchesCompound(current, part)) return false;
      if (index === 0) return true;
      if (parts[index - 1] === '>') return current.parentElement && test(current.parentElement, index - 2);
      for (let up = current.parentElement; up && up !== (scope?.parentNode || null); up = up.parentElement) if (test(up, index - 1)) return true;
      return false;
    };
    return test(node, parts.length - 1);
  });
}

function parseMiniHtml(doc, html) {
  const root = new MiniElement(doc, 'html');
  const stack = [root];
  const token = /<!--[\s\S]*?-->|<!doctype[^>]*>|<\/([\w-]+)\s*>|<([\w-]+)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+|<)/gi;
  const decode = (text) => text.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
  let m;
  while ((m = token.exec(html))) {
    const top = stack.at(-1);
    if (m[1]) { const i = stack.findLastIndex((n) => n.localName === m[1].toLowerCase()); if (i > 0) stack.length = i; }
    else if (m[2]) {
      const tag = m[2].toLowerCase();
      if (tag === 'html') { Object.assign(root.attrs, {}); continue; }
      const node = new MiniElement(doc, tag);
      for (const a of m[3].matchAll(/([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) node.attrs[a[1]] = decode(a[2] ?? a[3] ?? a[4] ?? '');
      if ('value' in node.attrs) node.value = node.attrs.value;
      top.appendChild(node);
      if ((tag === 'script' || tag === 'style') && !m[4]) {
        const end = html.toLowerCase().indexOf(`</${tag}`, token.lastIndex);
        node.appendChild(new MiniText(doc, html.slice(token.lastIndex, end)));
        token.lastIndex = html.indexOf('>', end) + 1;
      } else if (!VOID_TAGS.has(tag) && !m[4]) stack.push(node);
    } else if (m[5] && !/^<!/.test(m[0])) top.appendChild(new MiniText(doc, decode(m[5])));
  }
  return root;
}

class MiniDom {
  constructor(html) {
    const window = { listeners: {}, Event: MiniEvent, MessageEvent: MiniEvent, CustomEvent: MiniEvent, close() {},
      addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
      removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((x) => x !== fn); },
      dispatchEvent(event) { event.target ||= this; for (const fn of [...(this.listeners[event.type] || [])]) fn.call(this, event); return true; },
      getSelection: () => ({ toString: () => '' }), matchMedia: () => ({ matches: false, addEventListener() {} }),
      requestAnimationFrame: (fn) => setTimeout(fn, 0), cancelAnimationFrame: (id) => clearTimeout(id),
      navigator: { clipboard: { writeText: async () => {} } }, console,
      MutationObserver: class { observe() {} disconnect() {} }, ResizeObserver: class { observe() {} disconnect() {} },
      IntersectionObserver: class { observe() {} disconnect() {} } };
    const doc = { nodeType: 9, defaultView: window, activeElement: null, listeners: {}, execCommand: () => true };
    doc.documentElement = parseMiniHtml(doc, html);
    doc.body = doc.documentElement.querySelector('body') || doc.documentElement;
    doc.createElement = (tag) => new MiniElement(doc, tag);
    doc.createTextNode = (text) => new MiniText(doc, text);
    doc.getElementById = (id) => doc.documentElement.querySelector(`#${id}`);
    doc.querySelector = (s) => doc.documentElement.querySelector(s);
    doc.querySelectorAll = (s) => doc.documentElement.querySelectorAll(s);
    doc.addEventListener = (type, fn) => { (doc.listeners[type] ||= []).push(fn); };
    doc.removeEventListener = () => {};
    window.document = doc; window.window = window; window.self = window; window.globalThis = window;
    for (const name of ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval', 'URL', 'Date', 'Math', 'JSON', 'Object', 'Array', 'Promise',
      'String', 'Number', 'Boolean', 'Set', 'Map', 'Error', 'RegExp', 'parseInt', 'parseFloat', 'isNaN', 'Symbol', 'encodeURIComponent']) window[name] = globalThis[name];
    const vm = require('node:vm');
    vm.createContext(window);
    window.eval = (code) => vm.runInContext(code, window);
    this.window = window;
  }
}
// Decision inputs that explain themselves: a typed number says why it cannot be taken yet,
// the core's proposed default is preselected, and Enter sends what the button would.
function choiceView() {
  return { status: 'WAITING_FOR_USER', done: false,
    needs: { kind: 'answer', questions: [{ id: 'q1', question: 'Include the migration test?',
      options: ['Yes', 'No', 'Ask me later'], proposed_default: 'Yes' }] },
    next_command: `autocode --workspace ${run.workspace} --run-dir ${run.runDir} --answer q1=ANSWER` };
}

for (const surface of ['chat', 'Run details']) {
  test(`an out-of-range number says why beside its field in ${surface}`, async (t) => {
    host.config.set('confirmPersonOnly', false);
    const ownController = new RunController(host.log);
    const ui = surface === 'chat' ? await openChat(t, totalTimeView(), run, { controller: ownController }) : await openPanel(t, totalTimeView(), undefined, ownController);
    const input = ui.document.querySelector('[data-question="max_seconds"]');
    const hint = () => input.parentElement.querySelector('.range-hint');
    input.value = '3010'; input.dispatchEvent(new ui.window.Event('input'));
    assert.equal(hint().textContent, 'Enter at least 3011');
    assert.equal(ui.document.querySelector('#take-action').disabled, true);
    input.value = '5400.5'; input.dispatchEvent(new ui.window.Event('input'));
    assert.equal(hint().textContent, 'Enter a whole number');
    input.value = '5400'; input.dispatchEvent(new ui.window.Event('input'));
    assert.equal(hint().textContent, '');
    assert.equal(ui.document.querySelector('#take-action').disabled, false);
  });

  test(`a proposed default is preselected and one click sends it in ${surface}`, async (t) => {
    host.config.set('confirmPersonOnly', false);
    const ownController = new RunController(host.log);
    const ui = surface === 'chat' ? await openChat(t, choiceView(), run, { controller: ownController }) : await openPanel(t, choiceView(), undefined, ownController);
    const select = ui.document.querySelector('[data-question="q1"]');
    assert.ok(select, 'the choice renders');
    assert.equal(select.tagName, 'SELECT');
    assert.equal(select.value, 'Yes');
    assert.equal(ui.document.querySelector('#take-action').disabled, false);
    const launch = t.mock.method(cli, 'stream', async () => ok);
    ui.document.querySelector('#take-action').click(); await flush();
    assert.equal(launch.mock.callCount(), 1);
    const args = launch.mock.calls[0].arguments[1];
    assert.equal(args[args.indexOf('--answer') + 1], 'q1=Yes');
    // A person can still choose otherwise: the preselection is a starting point, not an answer of ours.
    await ui.poll(choiceView());
    const again = t.mock.method(cli, 'stream', async () => ok);
    const field = ui.document.querySelector('[data-question="q1"]');
    field.value = 'No'; field.dispatchEvent(new ui.window.Event('change'));
    ui.document.querySelector('#take-action').click(); await flush();
    assert.equal(again.mock.callCount(), 1);
    const resent = again.mock.calls[0].arguments[1];
    assert.equal(resent[resent.indexOf('--answer') + 1], 'q1=No');
  });

  test(`Enter in an answer field takes the action only while its button could, in ${surface}`, async (t) => {
    host.config.set('confirmPersonOnly', false);
    const ownController = new RunController(host.log);
    const ui = surface === 'chat' ? await openChat(t, totalTimeView(), run, { controller: ownController }) : await openPanel(t, totalTimeView(), undefined, ownController);
    const input = ui.document.querySelector('[data-question="max_seconds"]');
    const launch = t.mock.method(cli, 'stream', async () => ok);
    const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => 'Set total and continue');
    input.value = '3010'; input.dispatchEvent(new ui.window.Event('input'));
    input.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flush();
    assert.equal(launch.mock.callCount(), 0, 'Enter must not take a number the button refuses');
    input.value = '5400'; input.dispatchEvent(new ui.window.Event('input'));
    input.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await flush();
    assert.equal(launch.mock.callCount(), 1);
    const args = launch.mock.calls[0].arguments[1];
    assert.equal(args[args.indexOf('--max-seconds') + 1], '5400');
  });
}

test('Enter in the bound-raise field raises through its named command when the number is in range', async (t) => {
  host.config.set('confirmPersonOnly', false);
  const ownController = new RunController(host.log);
  const ui = await openPanel(t, heldCostView(), undefined, ownController);
  const field = ui.document.querySelector('[data-question="raise:max_cost_usd"]');
  const hint = () => field.parentElement.querySelector('.range-hint');
  field.value = '2'; field.dispatchEvent(new ui.window.Event('input'));
  assert.equal(hint().textContent, 'Enter at least 3');
  const launch = t.mock.method(cli, 'stream', async () => ok);
  const confirm = t.mock.method(host.vscode.window, 'showWarningMessage', async () => 'Raise limit and continue');
  field.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await flush();
  assert.equal(launch.mock.callCount(), 0, 'Enter must not raise a number the button refuses');
  field.value = '5'; field.dispatchEvent(new ui.window.Event('input'));
  assert.equal(hint().textContent, '');
  field.dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await flush();
  assert.equal(launch.mock.callCount(), 1);
  const args = launch.mock.calls[0].arguments[1];
  assert.equal(args[args.indexOf('--max-cost-usd') + 1], '5');
});

// The tab strip works from the keyboard: a roving tabindex, arrows (and Home/End) that select
// the same chat a click selects, and a close that is a real button, which the host presses.
test('chat tabs are keyboard-reachable: arrows select and the close is a button', async (t) => {
  const views = new Map([[alpha.runDir, workingView()], [beta.runDir, workingView()]]);
  const ui = await openChat(t, workingView(), alpha, { statusFor: (target) => views.get(target.runDir) });
  ui.chat.show(beta);
  await flush();
  const tabs = () => [...ui.document.querySelectorAll('.chat-tab')];
  const active = () => ui.document.querySelector('.chat-tab.active');
  assert.equal(tabs().length, 2);
  assert.equal(tabs()[1].tabIndex, 0, 'the selected tab holds the strip\'s place in the tab order');
  assert.equal(tabs()[0].tabIndex, -1, 'the other tabs are reached with the arrow keys, not Tab');
  const keyOf = (ref) => ref.workspace + '\n' + ref.runDir;
  const selects = () => ui.sent.filter((message) => message.type === 'select_tab');

  tabs()[1].focus();
  tabs()[1].dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
  await flush();
  assert.equal(selects().length, 1, 'the arrow selects exactly the chat a click would');
  assert.equal(selects()[0].tabKey, keyOf(alpha));
  assert.equal(ui.document.querySelectorAll('.chat-tab')[0].className, 'chat-tab active');
  assert.equal(ui.document.activeElement, active(), 'a selection that rebuilds the strip keeps focus on it');

  active().dispatchEvent(new ui.window.KeyboardEvent('keydown', { key: 'End', bubbles: true }));
  await flush();
  assert.equal(selects()[1].tabKey, keyOf(beta), 'End selects the last chat');

  const close = ui.document.querySelectorAll('.chat-tab')[1].querySelector('.close');
  assert.equal(close.tagName, 'BUTTON', 'the close is a button, so the host can press it');
  assert.equal(close.type, 'button');
  assert.ok(close.getAttribute('aria-label').startsWith('Close chat '), 'the close names its chat');
  close.click();
  await flush();
  assert.ok(ui.sent.some((message) => message.type === 'close_tab' && message.tabKey === keyOf(beta)),
    'closing acts on that chat only');
});
