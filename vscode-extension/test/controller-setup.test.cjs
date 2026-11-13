const assert = require('node:assert/strict');
const { test, afterEach, mock } = require('node:test');
const { editor } = require('./helpers/editor.cjs');

const host = editor();
const cli = host.load('core/cli.js');
const settings = host.load('settings.js');
const { ChatView } = host.load('chat.js');
const { readWorktrees } = host.load('core/worktrees.js');
const command = ['worktrees', 'list', '--workspace', '/project', '--json'];
const listing = (ready = false) => ({
  schema: 1, folder: '/project', default: 'new_branch', branch: 'main',
  places: [{ id: 'new_branch', label: 'New worktree and branch', start_args: ['--build-in-worktree'] }],
  controller: { ready, detail: 'Select an AutoCode CLI installed outside this checkout.',
    actions: ready ? [] : [{ id: 'select_cli', label: 'Select isolated AutoCode CLI', next_command: command }] },
});
const blocked = () => readWorktrees(JSON.stringify(listing()));

afterEach(() => { mock.restoreAll(); host.config.clear(); });

test('selection verifies the candidate controller before saving workspace-only CLI settings', async (t) => {
  host.config.set('cliArgs', ['/editable/tools/autocode.py']);
  const selected = '/isolated env/bin/autocode';
  host.vscode.window.showOpenDialog = async () => [{ fsPath: selected }];
  const invoke = t.mock.method(cli, 'invoke', async () => ({ code: 0, stdout: JSON.stringify(listing(true)), stderr: '' }));
  const config = host.vscode.workspace.getConfiguration();
  const update = t.mock.method(config, 'update');
  t.mock.method(host.vscode.workspace, 'getConfiguration', () => config);
  assert.equal(await settings.selectIsolatedController(blocked(), '/project'), true);
  const [options, argv, , timeout] = invoke.mock.calls[0].arguments;
  assert.equal(options.command, selected);
  assert.deepEqual(options.prefix, [], 'old python/script prefix must not leak into selected executable');
  assert.equal(options.cwd, '/project');
  assert.deepEqual(argv, command, 'verify only the core-named public read');
  assert.ok(timeout > 0 && timeout <= 60000);
  assert.deepEqual(update.mock.calls.map((call) => call.arguments), [
    ['cliArgs', [], host.vscode.ConfigurationTarget.Workspace],
    ['cliPath', selected, host.vscode.ConfigurationTarget.Workspace],
  ]);
});

test('cancel, unsafe, failed, old and malformed candidate reads cannot change settings', async (t) => {
  const before = [['cliPath', '/old/autocode'], ['cliArgs', ['old.py']]];
  host.vscode.window.showOpenDialog = async () => undefined;
  const invoke = t.mock.method(cli, 'invoke', async () => { throw new Error('must not run'); });
  assert.equal(await settings.selectIsolatedController(blocked(), '/project'), false);
  assert.equal(invoke.mock.callCount(), 0);
  host.vscode.window.showOpenDialog = async () => [{ fsPath: '/candidate/autocode' }];
  for (const result of [
    { code: 0, stdout: JSON.stringify(listing()), stderr: '' },
    { code: 2, stdout: '', stderr: 'not AutoCode' },
    { code: 0, stdout: JSON.stringify({ ...listing(true), controller: undefined }), stderr: '' },
    { code: 0, stdout: '{}', stderr: '' },
  ]) {
    host.config.clear();
    for (const [key, value] of before) host.config.set(key, value);
    invoke.mock.mockImplementation(async () => result);
    await assert.rejects(settings.selectIsolatedController(blocked(), '/project'));
    assert.deepEqual([...host.config], before);
  }
  const unknown = blocked();
  unknown.controller.action = null;
  await assert.rejects(settings.selectIsolatedController(unknown, '/project'), /names no supported/);
});

test('chat offers the named setup action before allocating a run, and leaves launch for a fresh retry', async (t) => {
  const place = (tab, read) => ChatView.prototype.placement.call({ dialog: (_tab, _title, open) => open() }, tab, read);
  const tab = {};
  const warning = t.mock.method(host.vscode.window, 'showWarningMessage', async () => 'Select isolated AutoCode CLI');
  host.vscode.window.showOpenDialog = async () => [{ fsPath: '/isolated/autocode' }];
  const invoke = t.mock.method(cli, 'invoke', async () => ({ code: 0, stdout: JSON.stringify(listing(true)), stderr: '' }));
  const notice = t.mock.method(host.vscode.window, 'showInformationMessage', async () => undefined);
  assert.equal(await place(tab, { list: blocked() }), undefined);
  assert.equal(warning.mock.calls[0].arguments[0], listing().controller.detail);
  assert.equal(invoke.mock.calls[0].arguments[0].cwd, '/project');
  assert.match(notice.mock.calls[0].arguments[0], /Send your message again/);
  warning.mock.mockImplementation(async () => undefined);
  assert.equal(await place(tab, { list: blocked() }), undefined);
  assert.equal(invoke.mock.callCount(), 1, 'declining setup does not verify a CLI');
});
