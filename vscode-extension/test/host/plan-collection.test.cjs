// The actual extension reads a plan and source-bound historical evidence from the real CLI.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');
const { privateHostRoot, privateHostEnvironment, settleFixture } = require('../owned-fixture-cleanup.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const venv = path.join(REPO, '.venv/bin/python');
const python = process.env.AUTOCODE_HOST_PYTHON || (fs.existsSync(venv) ? venv : 'python3');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(label, probe, timeout = 60_000) {
  const deadline = Date.now() + timeout;
  let last;
  do {
    try { last = await probe(); if (last) return last; } catch (error) { last = error; }
    await sleep(250);
  } while (Date.now() < deadline);
  throw new Error(`Timed out waiting for ${label}: ${last instanceof Error ? last.message : JSON.stringify(last)}`);
}

async function executable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const { downloadAndUnzipVSCode } = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({ version: '1.147.0', cachePath: path.join(EXTENSION, '.vscode-test') });
}

test('real editor distinguishes full pytest collection from a passing baseline at plan approval', { timeout: 240_000 }, async () => {
  const root = privateHostRoot('autocode-plan-collection-host-');
  const tmp = path.join(root, 'tmp'); fs.mkdirSync(tmp);
  const reads = path.join(root, 'reads'); fs.writeFileSync(reads, '');
  const env = { ...privateHostEnvironment(process.env, root), TMPDIR: tmp,
    AUTOCODE_HOST_READS: reads, AUTOCODE_HOST_HOLD: path.join(root, 'unheld') };
  for (const key of Object.keys(env)) {
    if (key === 'GIT_CONFIG_COUNT' || /^(GIT_CONFIG_KEY_|GIT_CONFIG_VALUE_)/.test(key)) delete env[key];
  }
  assert.equal(env.HOME, process.env.HOME);
  assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
  let app, win, fixture, editorPid, editorStarted, testError;
  const cli = path.join(__dirname, 'held_cli.py');
  const invoke = (args, timeout = 30_000) => {
    const result = spawnSync(python, args, { cwd: fixture?.workspace ?? REPO, env, encoding: 'utf8', timeout });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  const status = () => invoke([cli, '--workspace', fixture.workspace, '--run-dir', fixture.run_dir, '--status', '--json']).view;
  try {
    fixture = invoke([path.join(REPO, 'tests/plan_collection_vscode_fixture.py'), root], 120_000);
    assert.ok(fs.lstatSync(env.AUTOCODE_HOME).isDirectory());
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python, 'autocode.cliArgs': [cli], 'autocode.pollSeconds': 2,
      'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false, 'chat.disableAIFeatures': true,
      'update.mode': 'none', 'extensions.autoUpdate': false, 'telemetry.telemetryLevel': 'off',
    }));
    const { _electron } = require('playwright-core');
    app = await _electron.launch({ executablePath: await executable(), env, args: [
      '--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
      '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
      `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace,
    ] });
    editorPid = app.process().pid;
    const cleanup = path.join(REPO, 'tests/status_retry_host_cleanup.py');
    editorStarted = invoke([cleanup, 'identity', String(editorPid)], 15_000).started;
    assert.equal(typeof editorStarted, 'number');
    win = await app.firstWindow(); await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    await openHistoryRow(win.locator('.monaco-list-row', { hasText: fixture.title }).first(), chat, fixture.title);
    let frame = await chat();
    await until('the plan action', async () => {
      frame = await chat();
      return await frame.$('details.plan-evidence > summary') && await frame.$('#take-action');
    });
    const before = status();
    assert.equal(before.needs.kind, 'approve_plan');
    assert.equal(before.needs.token, before.displayed_plan.token);
    assert.deepEqual(before.displayed_plan.suite_reports.map(({ kind, selected }) => [kind, selected]), [
      ['collection', undefined], ['execution', true],
    ]);
    await frame.locator('details.plan-evidence > summary').click();
    const planText = async () => {
      const details = (await chat()).locator('details.plan-evidence[open] .plan-full');
      return (await details.count()) ? details.innerText() : '';
    };
    const shown = await until('the expanded collection evidence and preserved claim', async () => {
      const text = await planText();
      return text.includes('Collection lists tests without running them.') && text.includes('The full suite passes.') ? text : '';
    });
    assert.ok(shown.includes('Reported test observations from the Planner (not independently verified)'));
    assert.ok(shown.includes('Reported collected 2 tests — ' + fixture.collection));
    assert.ok(shown.includes('Reported passing 1 selected test — ' + fixture.selection));
    assert.ok(shown.includes('Reported paths: test_baseline.py'));
    assert.ok(shown.includes('Reported current behavior (not independently verified)'));
    assert.doesNotMatch(shown, /Reported passing 2/);

    fs.appendFileSync(path.join(fixture.workspace, 'test_baseline.py'), '\n# after observation\n');
    await until('expired report to disappear while the same plan remains', async () => {
      const text = await planText();
      return text.includes('The full suite passes.') && !text.includes('Reported collected');
    });
    const after = status();
    const { suite_reports, ...unchangedPlan } = before.displayed_plan;
    assert.deepEqual(after.displayed_plan, unchangedPlan, 'observation text grants no approval authority');
  } catch (error) { testError = error; }

  try {
    await settleFixture([
      ['capture failed editor', async () => {
        if (!testError || !win) return;
        fs.mkdirSync(path.join(EXTENSION, '.vscode-test'), { recursive: true });
        await win.screenshot({ path: path.join(EXTENSION, '.vscode-test/host-plan-collection.png'), timeout: 5_000 });
      }],
      ['close editor', async () => {
        if (!app) return;
        let timer;
        const needsStop = await Promise.race([app.close().then(() => false, () => true),
          new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5_000); })]);
        clearTimeout(timer);
        if (needsStop) {
          assert.ok(editorPid && Number.isFinite(editorStarted), 'fixture owns the editor process');
          invoke([path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop',
            String(editorPid), String(editorStarted)], 15_000);
        }
      }],
      ['stop private warm hosts', () => {
        if (fixture) invoke([path.join(REPO, 'tests/host_reader_cleanup.py'), '--stop-source-hosts', root], 15_000);
      }],
      ['observe owned readers', () => until('the fixture readers to exit', () =>
        invoke([path.join(REPO, 'tests/host_reader_cleanup.py'), reads, root], 15_000).running.length === 0, 20_000)],
    ], () => fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 }), testError);
  } catch (error) {
    console.error(`Preserved plan evidence host fixture at ${root}`);
    throw error;
  }
});
