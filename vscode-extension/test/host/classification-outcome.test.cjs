// Offline public plan approval and classification outcome through a real Extension Host.
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {openHistoryRow} = require('./open-history.cjs');
const { privateHostRoot, settleFixture } = require('../owned-fixture-cleanup.cjs');
const { until } = require('./wait.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
function python() {
  if (process.env.AUTOCODE_HOST_PYTHON) return process.env.AUTOCODE_HOST_PYTHON;
  const venv = path.join(REPO, '.venv/bin/python');
  return fs.existsSync(venv) ? venv : 'python3';
}
async function executable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const {downloadAndUnzipVSCode} = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({version: '1.147.0', cachePath: path.join(EXTENSION, '.vscode-test')});
}

test('editor approves a plan and shows an unresolved classification as a stopped outcome', {timeout: 600_000, skip: process.platform !== 'linux'}, async () => {
  const root = privateHostRoot('autocode-classification-host-');
  const tempDir = path.join(root, 'tmp'); fs.mkdirSync(tempDir);
  const reads = path.join(root, 'reads'); fs.writeFileSync(reads, '');
  const env = {...process.env, TMPDIR: tempDir, AUTOCODE_SOURCE_HOST: '0'};
  for (const key of ['OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY', 'KILO_API_KEY', 'ZAI_API_KEY']) delete env[key];
  let fixture, app, win, editorPid, editorStarted, testError, passed = false;
  function status() {
    const result = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), '--workspace', fixture.workspace,
      '--run-dir', fixture.runDir, '--status', '--json'], {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 40_000});
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout).view;
  }
  try {
    const made = spawnSync(python(), [path.join(REPO, 'tests/classification_vscode_fixture.py'), path.join(root, 'run')],
      {cwd: REPO, env, encoding: 'utf8', timeout: 300_000});
    assert.equal(made.status, 0, `offline classification fixture failed:\n${made.stderr}`);
    fixture = JSON.parse(made.stdout); Object.assign(env, fixture.env);
    assert.equal(env.HOME, process.env.HOME); assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
    assert.equal(env.AUTOCODE_SOURCE_HOST, '0', 'fixture does not launch a shared warm host');
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), {recursive: true});
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python(), 'autocode.cliArgs': [path.join(REPO, 'tools/autocode.py')], 'autocode.pollSeconds': 2,
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
    const identity = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)],
      {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
    assert.equal(identity.status, 0, identity.stderr); editorStarted = JSON.parse(identity.stdout).started;
    win = await app.firstWindow(); await win.locator('.monaco-workbench').waitFor({timeout: 120_000});
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    await openHistoryRow(win.locator('.monaco-list-row', {hasText: 'Deliver a CLI that prints hello'}).first(), chat, 'classification outcome');
    let frame = await chat();
    await until('the reviewed plan action', async () => {
      frame = await chat(); const text = await frame.evaluate(() => document.body.innerText);
      return text.includes('CLI prints hello') && await frame.$('#take-action') ? true : null;
    });
    const before = status(); const review = JSON.parse(fs.readFileSync(fixture.planReview, 'utf8'));
    assert.equal(before.needs.kind, 'approve_plan');
    assert.equal(before.displayed_plan.token, fixture.planToken);
    assert.equal(review.token, fixture.planToken);
    assert.equal(before.displayed_plan.review_markdown, review.review_markdown);
    assert.match(review.review, /Operating agent review/);
    await frame.locator('#take-action').click();
    const after = await until('the classified task outcome', () => {
      const view = status();
      return view.recovery?.outcome?.result === 'could_not_finish' ? view : null;
    }, 240_000);
    assert.equal(after.status, 'PAUSED_INTERVENTION');
    assert.equal(after.recovery.outcome.evidence.cause, 'unknown');
    assert.ok(after.recovery.outcome.evidence.investigations.length);
    assert.equal(after.needs, null); assert.equal(after.next_command, null);
    await until('the editor to display the stopped result', async () => {
      frame = await chat(); const text = await frame.evaluate(() => document.body.innerText);
      return text.includes('could not finish this task') ? true : null;
    });
    assert.equal(await frame.$('#take-action'), null, 'the stopped classification offers no Resume');
    const stages = () => fs.readFileSync(fixture.trace, 'utf8').trim().split('\n').filter(Boolean)
      .map((line) => JSON.parse(line)).filter((row) => row.event === 'call').map((row) => row.stage);
    const calls = stages();
    assert.equal(calls.filter((stage) => stage === 'builder').length, 1);
    assert.equal(calls.filter((stage) => stage === 'investigate_stuck').length, 1);
    assert.equal(status().next_command, null); assert.equal(status().needs, null);
    assert.deepEqual(stages(), calls, 'status reads must not relaunch a Builder or Investigator');
    passed = true;
  } catch (error) { testError = error; }
  try {
    await settleFixture([
      ['capture failed editor', async () => {
        if (passed || !win) return;
        await win.screenshot({path: path.join(EXTENSION, '.vscode-test/host-classification-outcome.png'), timeout: 5_000});
      }],
      ['request named stop', () => {
        if (passed || !fixture) return;
        const command = status().pause?.stop_now?.next_command;
        if (!Array.isArray(command)) return;
        const argv = command.map((arg) => arg === 'REQUEST_ID' ? randomUUID() : arg);
        const result = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), ...argv],
          {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000});
        assert.equal(result.status, 0, result.stderr);
      }],
      ['close owned editor', async () => {
        if (!app) return;
        let timer;
        const timedOut = await Promise.race([app.close().then(() => false, () => true),
          new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5_000); })]);
        clearTimeout(timer);
        if (timedOut) {
          const stopped = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop', String(editorPid), String(editorStarted)],
            {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
          assert.equal(stopped.status, 0, stopped.stderr);
        }
      }],
      ['check owned CLI processes', async () => {
        await until('owned CLI processes to exit', () => {
          const result = spawnSync(python(), [path.join(REPO, 'tests/host_reader_cleanup.py'), reads, root],
            {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
          assert.equal(result.status, 0, result.stderr);
          return JSON.parse(result.stdout).running.length === 0;
        }, 15_000);
      }],
    ], () => fs.rmSync(root, {recursive: true, force: true, maxRetries: 3}), testError);
  } catch (error) {
    console.error(`Preserved classification editor fixture at ${root}`);
    throw error;
  }
});
