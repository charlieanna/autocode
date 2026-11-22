// Exercise the real Requirements review and its source-standing label offline.
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const {openHistoryRow} = require('./open-history.cjs');
const {privateHostEnvironment, settleFixture} = require('../owned-fixture-cleanup.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const python = process.env.AUTOCODE_HOST_PYTHON || (fs.existsSync(path.join(REPO, '.venv/bin/python')) ? path.join(REPO, '.venv/bin/python') : 'python3');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(label, probe, timeout = 120_000) {
  const deadline = Date.now() + timeout; let last;
  while (Date.now() < deadline) {
    try { last = await probe(); if (last) return last; } catch (error) { last = error; }
    await sleep(250);
  }
  throw new Error(`Timed out waiting for ${label}: ${last instanceof Error ? last.message : JSON.stringify(last)}`);
}
async function executable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const {downloadAndUnzipVSCode} = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({version: '1.147.0', cachePath: path.join(EXTENSION, '.vscode-test')});
}

test('editor distinguishes a reported convention from approval of the understanding', {timeout: 600_000}, async () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'autocode-standing-host-')));
  const tmp = path.join(root, 'tmp'); fs.mkdirSync(tmp);
  const reads = path.join(root, 'reads'); fs.writeFileSync(reads, '');
  const env = {...privateHostEnvironment(process.env, root), TMPDIR: tmp, AUTOCODE_SOURCE_HOST: '0',
    AUTOCODE_HOST_READS: reads, AUTOCODE_HOST_HOLD: path.join(root, 'no-hold')};
  for (const key of Object.keys(env)) {
    if (key !== 'HOME' && key !== 'CODEX_HOME' &&
      (key.startsWith('GIT_CONFIG_') || /^(OPENAI_|ANTHROPIC_|AWS_|AZURE_|GOOGLE_|KILO_API_|CODEX_API_|ZAI_API_)/.test(key) ||
        /(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$/.test(key))) delete env[key];
  }
  assert.equal(env.HOME, process.env.HOME); assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
  let fixture, app, win, editorPid, editorStarted, testError, passed = false;
  const cli = path.join(REPO, 'tools/autocode.py');
  function status() {
    const result = spawnSync(python, [cli, '--workspace', fixture.workspace, '--run-dir', fixture.run_dir, '--status', '--json'],
      {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000});
    assert.equal(result.status, 0, result.stderr); return JSON.parse(result.stdout).view;
  }
  try {
    const made = spawnSync(python, [path.join(REPO, 'tests/requirements_standing_vscode_fixture.py'), root],
      {cwd: REPO, env, encoding: 'utf8', timeout: 300_000});
    assert.equal(made.status, 0, `offline fixture failed: ${made.stderr}`);
    fixture = JSON.parse(made.stdout);
    assert.equal(path.resolve(fixture.env.AUTOCODE_HOME), path.join(root, 'registry'));
    assert.equal(fixture.env.AUTOCODE_SOURCE_HOST, '0');
    Object.assign(env, fixture.env);
    assert.equal(env.HOME, process.env.HOME); assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
    const before = status();
    const question = before.needs?.questions?.find((row) => row.id.startsWith('requirements-review-'));
    const assumption = before.requirements?.proposed_assumptions?.find((row) => row.id === 'A1');
    assert.equal(before.needs?.kind, 'answer'); assert.ok(question);
    assert.equal(assumption?.text, "Keep the project's existing exception convention.");
    assert.equal(assumption?.convention_ref, 'convention.py:1');
    assert.equal(assumption?.standing, 'convention_not_approved');
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), {recursive: true});
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python, 'autocode.cliArgs': [path.join(__dirname, 'held_cli.py')], 'autocode.pollSeconds': 2,
      'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false, 'chat.disableAIFeatures': true,
      'update.mode': 'none', 'extensions.autoUpdate': false, 'telemetry.telemetryLevel': 'off',
    }));
    const {_electron} = require('playwright-core');
    app = await _electron.launch({executablePath: await executable(), env, args: [
      '--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
      '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
      `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace,
    ]});
    editorPid = app.process().pid;
    const identity = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)],
      {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
    assert.equal(identity.status, 0, identity.stderr); editorStarted = JSON.parse(identity.stdout).started;
    win = await app.firstWindow(); await win.locator('.monaco-workbench').waitFor({timeout: 120_000});
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    await openHistoryRow(win.locator('.monaco-list-row', {hasText: fixture.brief}).first(), chat, 'Requirements standing');
    let frame;
    await until('the reported convention and its review action', async () => {
      frame = await chat(); const row = frame.locator('.assumption-row').filter({hasText: assumption.text});
      return await row.count() && (await row.innerText()).includes('reported convention, not independently verified or approved') &&
        await frame.$('#take-action') ? true : null;
    });
    const row = frame.locator('.assumption-row').filter({hasText: assumption.text});
    assert.equal(await row.locator('.state.done').count(), 0, 'the convention is not presented as confirmed');
    assert.match(await row.innerText(), /The existing module uses ValueError/);
    assert.match(await frame.locator('#take-action').innerText(), /Approve understanding and prepare plan/i);
    assert.match(await frame.locator('body').innerText(), /Build a command that greets a person by name/);
    const fresh = status();
    assert.equal(fresh.needs?.resolver_token, before.needs.resolver_token);
    assert.deepEqual(fresh.requirements?.proposed_assumptions, before.requirements.proposed_assumptions);
    assert.deepEqual(fresh.needs?.questions, before.needs.questions, 'review the exact question before approving it');
    await frame.locator('#take-action').click();
    const after = await until('the saved approval in public status', () => {
      const current = status();
      const value = current.requirements?.proposed_assumptions?.find((item) => item.id === 'A1');
      return value?.standing === 'approved_with_understanding' ? current : null;
    }, 180_000);
    assert.equal(after.requirements.proposed_assumptions.find((item) => item.id === 'A1').text, assumption.text);
    assert.notEqual(after.requirements.proposed_assumptions.find((item) => item.id === 'A1').standing, 'confirmed_by_person');
    await until('the editor to leave the Requirements approval', async () => {
      frame = await chat();
      const action = frame.locator('#take-action');
      return !await action.count() || !/Approve understanding and prepare plan/i.test(await action.innerText());
    });
    passed = true;
  } catch (error) { testError = error; }
  await settleFixture([
    ['capture failed editor', async () => { if (!passed && win) await win.screenshot({path: path.join(EXTENSION, '.vscode-test/host-requirements-standing.png'), timeout: 5_000}); }],
    ['stop owned run if offered', () => {
      if (!fixture) return;
      const command = status().pause?.stop_now?.next_command;
      if (!Array.isArray(command)) return;
      const stopped = spawnSync(python, [cli, ...command.map((arg) => arg === 'REQUEST_ID' ? randomUUID() : arg)],
        {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000});
      assert.equal(stopped.status, 0, stopped.stderr);
    }],
    ['close owned editor', async () => {
      if (!app) return;
      let timer; const timedOut = await Promise.race([app.close().then(() => false, () => true),
        new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5_000); })]); clearTimeout(timer);
      if (timedOut) {
        assert.ok(editorPid && Number.isFinite(editorStarted));
        const stopped = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop', String(editorPid), String(editorStarted)],
          {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
        assert.equal(stopped.status, 0, stopped.stderr);
      }
    }],
    ['check owned readers exited', async () => {
      await until('owned status readers to exit', () => {
        const checked = spawnSync(python, [path.join(REPO, 'tests/host_reader_cleanup.py'), reads, root],
          {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
        assert.equal(checked.status, 0, checked.stderr);
        return JSON.parse(checked.stdout).running.length === 0;
      }, 30_000);
    }],
    ['check the editor read status', () => {
      if (passed) assert.match(fs.readFileSync(reads, 'utf8'), /^start \d+ /m, 'the actual editor made a recorded status read');
    }],
  ], () => fs.rmSync(root, {recursive: true, force: true}), testError);
});
