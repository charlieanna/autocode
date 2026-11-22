// Exercise an offline synthetic Kilo-shaped provider through the actual extension and core.
// This does not execute live Kilo or a live GLM model.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');
const { routeDecisionReady } = require('../job-route-readiness.cjs');
const { privateHostEnvironment, ownedWarmHostSteps, settleFixture } = require('../owned-fixture-cleanup.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const python = process.env.AUTOCODE_HOST_PYTHON || (fs.existsSync(path.join(REPO, '.venv/bin/python')) ? path.join(REPO, '.venv/bin/python') : 'python3');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(label, probe, timeout = 60_000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try { last = await probe(); if (last) return last; } catch (error) { last = error; }
    await sleep(250);
  }
  throw new Error(`Timed out waiting for ${label}: ${last instanceof Error ? last.message : JSON.stringify(last)}`);
}

async function executable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const { downloadAndUnzipVSCode } = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({ version: '1.147.0', cachePath: path.join(EXTENSION, '.vscode-test') });
}

for (const mode of ['changed-effort', 'unset-effort']) test(`real editor offers a synthetic Kilo model and effort decision (${mode})`, { timeout: 180_000 }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'autocode-synthetic-kilo-'));
  let app;
  let win;
  let env;
  let editorPid;
  let editorStarted;
  let lastState;
  let lastStatus;
  let expectedToken;
  let passed = false;
  let testError;
  let ownsWarmHost = false;
  try {
    const temp = path.join(root, 'tmp');
    const actions = path.join(root, 'actions.jsonl');
    fs.mkdirSync(temp);
    // Preserve the runner's inherited home and credential configuration.
    // The fake provider is credential-free and this test runs on a disposable CI host.
    env = { ...privateHostEnvironment(process.env, root), TMPDIR: temp };
    assert.equal(env.HOME, process.env.HOME);
    assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
    for (const key of Object.keys(env)) {
      if (key === 'GIT_CONFIG_COUNT' || /^(GIT_CONFIG_KEY_|GIT_CONFIG_VALUE_|OPENAI_|ANTHROPIC_|KILO_API_|CODEX_API_|ZAI_API_)/.test(key)) delete env[key];
    }
    const made = spawnSync(python, [path.join(REPO, 'tests/kilo_route_vscode_fixture.py'), root, mode], { cwd: REPO, encoding: 'utf8', env, timeout: 90_000 });
    assert.equal(made.status, 0, `offline quota fixture: ${made.stderr}`);
    const fixture = JSON.parse(made.stdout);
    assert.equal(path.resolve(fixture.registry), path.resolve(root, 'registry'), 'registry is the owned fixture path');
    assert.ok(fs.lstatSync(fixture.registry).isDirectory(), 'owned registry is a directory, not a symlink');
    Object.assign(env, { AUTOCODE_ROUTE_HOST_ACTIONS: actions,
      JOB_RUNTIME: REPO, JOB_CALLS: fixture.calls,
      KILO_AUDIT: fixture.audit, REFUSE_KIND: 'quota', REFUSE_MODEL: 'gpt-6-sol', JOB_MODE: 'exit42',
      PATH: [path.join(fixture.workspace, '.autocode/bin'), env.PATH].join(path.delimiter) });
    ownsWarmHost = true;
    const userData = path.join(root, 'user-data');
    fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python, 'autocode.cliArgs': [path.join(REPO, 'tests/job_route_vscode_cli.py')], 'autocode.pollSeconds': 2,
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
    const identified = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)],
      { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
    assert.equal(identified.status, 0, identified.stderr);
    editorStarted = JSON.parse(identified.stdout).started;
    win = await app.firstWindow();
    await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    const publicStatus = () => {
      const result = spawnSync(python, [path.join(REPO, 'tools/autocode.py'), '--run-dir', fixture.run_dir,
        '--workspace', fixture.workspace, '--status', '--json'], { cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000 });
      assert.equal(result.status, 0, result.stderr);
      const view = JSON.parse(result.stdout).view;
      lastStatus = { kind: view.needs?.kind, action: view.next_command ? 'named' : null,
        tokenRotated: expectedToken ? Boolean(view.needs?.job_retry_token && view.needs.job_retry_token !== expectedToken) : null,
        model: view.routes?.architect?.model, effort: view.routes?.architect?.reasoning_effort };
      return view;
    };
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    await openHistoryRow(win.locator('.monaco-list-row', { hasText: 'Offline synthetic Kilo effort recovery' }).first(), chat, 'synthetic Kilo effort');
    const field = await until('the model and effort answer in the editor', async () => {
      const frame = await chat();
      lastState = await frame.evaluate(() => ({ text: document.body.innerText, action: document.querySelector('#take-action')?.textContent }));
      const element = frame.locator('[data-question="route-architect"]');
      const visible = await element.count() > 0 && await element.isVisible();
      return routeDecisionReady({ visible, text: lastState.text }) ? element : null;
    });
    const before = publicStatus();
    assert.equal(before.needs.kind, 'retry_job');
    assert.equal(before.needs.effort_drift, true);
    assert.equal(before.routes.architect.reasoning_effort ?? null, mode === 'unset-effort' ? null : 'high');
    assert.equal(before.recovery.what_happened, "The Design Reviewer's saved model settings changed. Review the model and reasoning level before retrying.");
    assert.equal(before.progress.needs_you, 'review the model and reasoning level for the Design Reviewer, then retry it');
    assert.match(before.next_command ?? '', /--answer route-architect=MODEL/);
    assert.ok(before.needs.job_retry_token);
    expectedToken = before.needs.job_retry_token;
    assert.equal(before.recovery.actions.some((action) => action.kind === 'retry_job'), false);
    const originalCalls = fs.readFileSync(fixture.calls, 'utf8');
    assert.match(lastState.text, /model|reasoning/i);
    assert.equal(fs.existsSync(fixture.audit) && fs.readFileSync(fixture.audit, 'utf8').includes('models'), false, 'status does not list models');
    await field.fill('zhipuai-coding-plan/glm-5.3@medium');
    const frame = await chat();
    await frame.locator('#take-action').click();
    const after = await until('the concrete bound answer to be accepted', () => {
      const view = publicStatus();
      return view.needs?.job_retry_token && view.needs.job_retry_token !== before.needs.job_retry_token ? view : null;
    });
    assert.equal(after.routes.architect.model, 'zhipuai-coding-plan/glm-5.3');
    const audit = fs.readFileSync(fixture.audit, 'utf8');
    assert.match(audit, /(^|\n)models(\n|$)/, 'the named answer validates the provider model');
    assert.doesNotMatch(audit, /(^|\n)run(\s|$)/, 'the answer does not launch a model');
    assert.equal(after.routes.architect.reasoning_effort, 'medium');
    assert.equal(fs.readFileSync(fixture.calls, 'utf8'), originalCalls, 'answering the question launches no model');
    passed = true;
  } catch (error) {
    testError = error;
    const actions = path.join(root, 'actions.jsonl');
    console.error('Quota route host diagnostics:', JSON.stringify({ root, lastState, lastStatus,
      actions: fs.existsSync(actions) ? fs.readFileSync(actions, 'utf8') : '(no answer action)' }));
    await win?.screenshot({ path: path.join(EXTENSION, '.vscode-test', 'host-synthetic-kilo-route.png'), timeout: 5_000 }).catch(() => {});
  } finally {
    try {
      await settleFixture([['close editor', async () => {
        if (!app) return;
        let timer;
        const timedOut = await Promise.race([app.close().then(() => false, () => true),
          new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5_000); })]);
        clearTimeout(timer);
        if (timedOut) {
          const stopped = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop', String(editorPid), String(editorStarted)],
            { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
          assert.equal(stopped.status, 0, `stop owned editor: ${stopped.stderr}`);
        }
      }], ...ownedWarmHostSteps(ownsWarmHost, () => {
        const cli = path.join(REPO, 'tools/autocode.py');
        const stop = spawnSync(python, [cli, 'serve', '--stop', '--json'], { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
        assert.equal(stop.status, 0, stop.stderr);
      }, () => {
        const cli = path.join(REPO, 'tools/autocode.py');
        const check = spawnSync(python, [cli, 'serve', '--status', '--json'], { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
        assert.equal(check.status, 0, check.stderr);
        assert.equal(JSON.parse(check.stdout).running, false, 'owned warm host exited');
      })], () => fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 }),
      testError || (!passed ? new Error('editor test did not complete') : undefined));
    } catch (error) {
      console.error(`Preserved failed quota route fixture at ${root}`);
      throw error;
    }
  }
});
