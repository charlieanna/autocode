// A real held offline Tester report recovers through the actual editor.
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const {openHistoryRow} = require('./open-history.cjs');
const {privateHostRoot, privateHostEnvironment, settleFixture} = require('../owned-fixture-cleanup.cjs');

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

test('the editor automatically revalidates the exact held Tester report', {timeout: 900_000}, async () => {
  const root = privateHostRoot('autocode-report-host-');
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
  const guardedCli = path.join(REPO, 'tests/retained_report_vscode_cli.py');
  const cli = path.join(REPO, 'tools/autocode.py');
  const trace = () => fs.readFileSync(fixture.trace, 'utf8').trim().split('\n').filter(Boolean).map((line) => JSON.parse(line));
  function status() {
    const result = spawnSync(python, [guardedCli, '--workspace', fixture.workspace, '--run-dir', fixture.run_dir, '--status', '--json'],
      {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000});
    assert.equal(result.status, 0, result.stderr); return JSON.parse(result.stdout).view;
  }
  try {
    const made = spawnSync(python, [path.join(REPO, 'tests/retained_report_vscode_fixture.py'), root],
      {cwd: REPO, env, encoding: 'utf8', timeout: 600_000});
    assert.equal(made.status, 0, `offline fixture failed: ${made.stderr}`);
    fixture = JSON.parse(made.stdout);
    assert.equal(path.resolve(fixture.registry), path.join(root, 'registry'));
    assert.ok(fs.lstatSync(fixture.registry).isDirectory());
    assert.equal(path.resolve(fixture.env.AUTOCODE_HOME), path.join(root, 'registry'));
    assert.equal(path.resolve(fixture.env.TMPDIR), tmp);
    assert.equal(fixture.env.AUTOCODE_SOURCE_HOST, '0');
    Object.assign(env, fixture.env);
    assert.equal(env.HOME, process.env.HOME); assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
    const before = status();
    assert.equal(before.status, 'PAUSED_REPEATED_FAILURE');
    assert.equal(before.needs?.automatic_reconciliation, true);
    assert.match(before.next_command, /--resume-paused --expected-recovery-token -/);
    assert.equal(before.report_repair?.attempts, 2);
    assert.equal(before.cost?.cap_usd, 5);
    assert.deepEqual(trace().filter((row) => row.stage === 'tester'), fixture.initial_tester_calls);
    assert.equal(trace().some((row) => row.stage === 'report_revalidated'), false);
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), {recursive: true});
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python, 'autocode.cliArgs': [guardedCli], 'autocode.pollSeconds': 2,
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
    await openHistoryRow(win.locator('.monaco-list-row', {hasText: fixture.brief}).first(), chat, 'retained report');
    const after = await until('automatic recovery and the completed public run', () => {
      const view = status();
      if (view.status === 'TASK_COMPLETE') return view;
      if (view.status !== 'PAUSED_REPEATED_FAILURE' && !view.progress?.continuing && view.needs?.kind !== 'continue') {
        return view;
      }
      return null;
    }, 300_000);
    assert.equal(after.status, 'TASK_COMPLETE', JSON.stringify({status: after.status, need: after.needs?.kind}));
    assert.equal(after.done, true);
    assert.equal(after.summary?.state, 'done');
    assert.equal(after.cost?.cap_usd, 5);
    const events = trace();
    assert.deepEqual(events.filter((row) => row.stage === 'tester'), fixture.initial_tester_calls,
      'the original Tester and its two repairs were not launched again');
    const accepted = events.findIndex((row) => row.stage === 'report_revalidated');
    const nextStage = events.findIndex((row) => row.stage === 'next_stage_start');
    assert.ok(accepted >= 0 && nextStage > accepted, 'the CLI reported acceptance before its next stage');
    assert.ok(events.some((row) => row.stage === 'review_completion'), 'the following provider was the offline fake');
    assert.equal(events.filter((row) => row.stage === 'report_revalidated').length, 1);
    await until('the actual editor to show completion', async () => {
      const frame = await chat();
      const result = frame.locator('#finish-summary .result');
      return await result.count() && (await result.innerText()).trim() === `✓ ${after.progress.headline}` ? true : null;
    });
    passed = true;
  } catch (error) { testError = error; console.error(`Retained-report host failed: ${error.stack || error}`); }
  try { await settleFixture([
    ['capture failed editor', async () => { if (!passed && win) await win.screenshot({path: path.join(EXTENSION, '.vscode-test/host-retained-report.png'), timeout: 5_000}); }],
    ['stop owned run if offered', () => {
      if (!fixture || passed) return;
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
    ['check all fixture readers exited', async () => {
      await until('owned status readers to exit', () => {
        const checked = spawnSync(python, [path.join(REPO, 'tests/host_reader_cleanup.py'), reads, root],
          {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
        assert.equal(checked.status, 0, checked.stderr); return JSON.parse(checked.stdout).running.length === 0;
      }, 30_000);
    }],
    ['check the editor read status', () => {
      if (passed) assert.match(fs.readFileSync(reads, 'utf8'), /^start \d+ /m);
    }],
  ], () => fs.rmSync(root, {recursive: true, force: true}), testError); }
  catch (error) { console.error(`Preserved retained-report fixture at ${root}:`, require('node:util').inspect(error, {depth: 6})); throw error; }
});
