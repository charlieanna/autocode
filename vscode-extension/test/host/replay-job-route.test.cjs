// Exercise the recorded job route and retry through the real extension.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');
const { privateHostEnvironment, ownedWarmHostSteps, settleFixture } = require('../owned-fixture-cleanup.cjs');
const { until } = require('./wait.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const python = process.env.AUTOCODE_HOST_PYTHON || (fs.existsSync(path.join(REPO, '.venv/bin/python')) ? path.join(REPO, '.venv/bin/python') : 'python3');

async function executable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const { downloadAndUnzipVSCode } = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({ version: '1.147.0', cachePath: path.join(EXTENSION, '.vscode-test') });
}

test('real editor follows a recorded job route and retry', { timeout: 300_000 }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'autocode-replay-job-'));
  let app, win, env, editorPid, editorStarted, lastStatus, lastText, failure;
  let ownsWarmHost = false, passed = false;
  try {
    const tmp = path.join(root, 'tmp');
    fs.mkdirSync(tmp);
    env = { ...privateHostEnvironment(process.env, root), TMPDIR: tmp };
    assert.equal(env.HOME, process.env.HOME);
    assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
    for (const key of Object.keys(env)) {
      if (key === 'GIT_CONFIG_COUNT' || /^(GIT_CONFIG_KEY_|GIT_CONFIG_VALUE_|OPENAI_|ANTHROPIC_|KILO_API_|CODEX_API_|ZAI_API_)/.test(key)) delete env[key];
    }
    const made = spawnSync(python, [path.join(REPO, 'tests/replay_job_vscode_fixture.py'), root],
      { cwd: REPO, encoding: 'utf8', env, timeout: 120_000 });
    assert.equal(made.status, 0, `offline recorded replay fixture: ${made.stderr}`);
    const fixture = JSON.parse(made.stdout);
    assert.equal(fs.realpathSync(fixture.registry), fs.realpathSync(path.join(root, 'registry')));
    Object.assign(env, { AUTOCODE_HOME: fixture.registry, XDG_CONFIG_HOME: path.join(root, 'config'),
      AUTOCODE_ROUTE_HOST_ACTIONS: path.join(root, 'actions.jsonl'), JOB_RUNTIME: REPO, JOB_CALLS: fixture.calls,
      JOB_MODE: 'exit42', REFUSE_KIND: 'quota', REFUSE_MODEL: 'gpt-6-sol',
      PATH: [path.join(fixture.workspace, '.autocode/bin'), env.PATH].join(path.delimiter) });
    const calls = () => fs.existsSync(fixture.calls) ? fs.readFileSync(fixture.calls, 'utf8') : '';
    const beforeCalls = calls();
    const status = () => {
      const result = spawnSync(python, [path.join(REPO, 'tools/autocode.py'), '--workspace', fixture.workspace,
        '--run-dir', fixture.run_dir, '--status', '--json'], { cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000 });
      assert.equal(result.status, 0, result.stderr);
      const view = JSON.parse(result.stdout).view;
      lastStatus = { status: view.status, need: view.needs?.kind, model: view.routes?.architect?.model,
        consumed: view.replay_progress?.consumed, next: view.replay_progress?.next_dispatch };
      return view;
    };
    const first = status();
    assert.equal(first.needs.kind, 'continue');
    assert.equal(first.needs.replay_job, true);
    assert.equal(first.routes.architect.model, 'gpt-6-sol');
    assert.equal(first.replay_progress.consumed, 1);
    assert.match(first.next_command, /--answer route-architect=gpt-6-luna@medium/);
    const userData = path.join(root, 'user-data');
    fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python, 'autocode.cliArgs': [path.join(REPO, 'tests/job_route_vscode_cli.py')],
      'autocode.pollSeconds': 2, 'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false,
      'chat.disableAIFeatures': true, 'update.mode': 'none', 'extensions.autoUpdate': false, 'telemetry.telemetryLevel': 'off',
    }));
    ownsWarmHost = true;
    const { _electron } = require('playwright-core');
    app = await _electron.launch({ executablePath: await executable(), env, args: [
      '--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
      '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
      `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace,
    ] });
    editorPid = app.process().pid;
    const identity = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)],
      { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
    assert.equal(identity.status, 0, identity.stderr);
    editorStarted = JSON.parse(identity.stdout).started;
    let windowTimer;
    try {
      win = await Promise.race([app.firstWindow(), new Promise((_, reject) => {
        windowTimer = setTimeout(() => reject(new Error('Owned editor did not open its first window')), 90_000);
      })]);
    } finally { clearTimeout(windowTimer); }
    await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    await openHistoryRow(win.locator('.monaco-list-row', { hasText: 'Implement the approved design' }).first(), chat, 'recorded job');
    const frame = await chat();
    const action = await until('recorded route Continue', async () => {
      lastText = await frame.locator('body').innerText();
      const button = frame.locator('#take-action');
      return await button.isVisible() ? button : null;
    });
    assert.equal(await frame.locator('[data-question="route-architect"]').count(), 0, 'the replay asks for no new model');
    await action.click();
    const routed = await until('saved recorded route', () => {
      const view = status();
      return view.routes?.architect?.model === 'gpt-6-luna' && view.needs?.kind === 'continue' ? view : null;
    });
    assert.equal(routed.needs.replay_job, true);
    assert.equal(routed.replay_progress.consumed, 1);
    assert.equal(calls(), beforeCalls, 'saving the recorded answer invokes no model');
    const actions = fs.readFileSync(path.join(root, 'actions.jsonl'), 'utf8').split('\n').filter(Boolean).map(JSON.parse);
    assert.ok(actions.some((entry) => entry.event === 'finished' && entry.exit_code === 0 &&
      entry.answer === 'route-architect=gpt-6-luna@medium'), 'the editor saved the exact recorded route');
    await until('recorded retry Continue', async () => {
      const button = (await chat()).locator('#take-action');
      return await button.isVisible() && await button.isEnabled() ? button : null;
    }).then((button) => button.click());
    const stopped = await until('recorded conflict', () => {
      const view = status();
      return view.status === 'PAUSED_DESIGN_CONFLICT' ? view : null;
    });
    assert.equal(stopped.replay_progress.consumed, 2);
    assert.equal(stopped.replay_progress.next_dispatch, 'end');
    assert.equal(calls(), beforeCalls, 'replay never invokes the scripted provider');
    await until('the editor shows the saved stopped result', async () => {
      lastText = await (await chat()).locator('body').innerText();
      return /couldn.t finish this step/i.test(lastText) && /work so far is saved/i.test(lastText);
    });
    passed = true;
  } catch (error) {
    failure = error;
    console.error('Recorded job host diagnostics:', JSON.stringify({ root, lastStatus, lastText }));
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
        const stop = spawnSync(python, [path.join(REPO, 'tools/autocode.py'), 'serve', '--stop', '--json'],
          { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
        assert.equal(stop.status, 0, stop.stderr);
      }, () => {
        const check = spawnSync(python, [path.join(REPO, 'tools/autocode.py'), 'serve', '--status', '--json'],
          { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
        assert.equal(check.status, 0, check.stderr);
        assert.equal(JSON.parse(check.stdout).running, false);
      })], () => fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 }), failure || (!passed ? new Error('editor test did not complete') : undefined));
    } catch (error) {
      console.error(`Preserved failed recorded job fixture at ${root}`);
      throw error;
    }
  }
});
