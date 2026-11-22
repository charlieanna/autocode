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
const {conventionsVisible} = require('../requirements-standing-readiness.cjs');

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
    const assumptions = before.requirements?.proposed_assumptions;
    const expected = [
      {id: 'A1', text: "Keep the project's existing exception convention.", ref: 'convention.py:2', why: 'The existing module uses ValueError'},
      {id: 'A2', text: "Keep the project's sorted single-line JSON convention.", ref: 'convention.py:5', why: 'The existing encoder sorts keys without indentation'},
    ];
    assert.equal(before.needs?.kind, 'answer'); assert.ok(question);
    const reviewedConfirmation = before.chat?.review?.spend_confirmation;
    assert.deepEqual(reviewedConfirmation, {
      match: 'trim_lower', title: 'Prepare a reviewed plan?',
      detail: 'Planning uses your selected models and saved limits. Building still waits for your approval of the displayed plan.',
      label: 'Prepare plan',
    });
    assert.equal(assumptions?.length, expected.length);
    for (const item of expected) {
      const assumption = assumptions.find((row) => row.id === item.id);
      assert.equal(assumption?.text, item.text); assert.equal(assumption?.convention_ref, item.ref);
      assert.equal(assumption?.standing, 'convention_not_approved');
    }
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), {recursive: true});
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python, 'autocode.cliArgs': [path.join(REPO, 'tests/requirements_standing_host_cli.py')], 'autocode.pollSeconds': 2,
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
      frame = await chat();
      return await conventionsVisible(frame, expected);
    });
    for (const item of expected) {
      const row = frame.locator('.assumption-row').filter({hasText: item.text});
      assert.equal(await row.locator('.state.done').count(), 0, `${item.id} is not presented as confirmed`);
      assert.ok((await row.textContent()).includes(item.why));
    }
    assert.match(await frame.locator('#take-action').textContent(), /Approve understanding and prepare plan/i);
    assert.match(await frame.locator('body').textContent(), /Build a command that greets a person by name/);
    const fresh = status();
    assert.equal(fresh.needs?.resolver_token, before.needs.resolver_token);
    assert.deepEqual(fresh.requirements?.proposed_assumptions, before.requirements.proposed_assumptions);
    assert.deepEqual(fresh.needs?.questions, before.needs.questions, 'review the exact question before approving it');
    assert.deepEqual(fresh.chat?.review?.spend_confirmation, reviewedConfirmation);
    // Electron's native modal is outside Playwright's webview. Simulate only
    // the exact reviewed confirmation, and fail closed for any other dialog.
    await app.evaluate(({dialog}, input) => {
      const {reviewedChoice} = require(input.helper);
      globalThis.__standingConfirmationCount = 0;
      dialog.showMessageBox = async (...args) => {
        const options = args.length === 1 ? args[0] : args[1];
        const response = reviewedChoice(options, input.expected);
        globalThis.__standingConfirmationCount++;
        return {response, checkboxChecked: false};
      };
    }, {helper: path.join(EXTENSION, 'test/requirements-standing-confirmation.cjs'), expected: reviewedConfirmation});
    await frame.locator('#take-action').click();
    const after = await until('the saved approval in public status', () => {
      const current = status();
      const rows = current.requirements?.proposed_assumptions || [];
      return expected.every((item) => rows.find((row) => row.id === item.id)?.standing === 'approved_with_understanding') ? current : null;
    }, 180_000);
    for (const item of expected) {
      const assumption = after.requirements.proposed_assumptions.find((row) => row.id === item.id);
      assert.equal(assumption.text, item.text);
      assert.notEqual(assumption.standing, 'confirmed_by_person');
    }
    assert.equal(await app.evaluate(() => globalThis.__standingConfirmationCount), 1, 'the exact reviewed confirmation was accepted once');
    await until('the editor to leave the Requirements approval', async () => {
      frame = await chat();
      const action = frame.locator('#take-action');
      return !await action.count() || !/Approve understanding and prepare plan/i.test(await action.textContent());
    });
    passed = true;
  } catch (error) { testError = error; console.error(`Requirements standing host failed: ${error.stack || error}`); }
  try { await settleFixture([
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
  ], () => fs.rmSync(root, {recursive: true, force: true}), testError); }
  catch (error) {
    console.error('Requirements standing cleanup:', require('node:util').inspect(error, {depth: 6}));
    throw error;
  }
});
