// Offline source-recovery workflow in a real VS Code Extension Host.
// The fixture stages a stopped run after a real scratch-copy refusal; the
// extension observes and acts solely through this checkout's public CLI.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { privateHostRoot } = require('../owned-fixture-cleanup.cjs');
const { after, before, test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');
const { until } = require('./wait.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const root = privateHostRoot('autocode-source-host-');
const started = path.join(root, 'action-started');
const release = path.join(root, 'action-release');
const finished = path.join(root, 'action-finished');
assert.equal(spawnSync('mkfifo', [release]).status, 0, 'create owned release FIFO');
const statusReads = path.join(root, 'status-reads');
let app;
let win;
let fixture;
let environment;
let passed = false;

function releaseAction() {
  try {
    const fd = fs.openSync(release, fs.constants.O_WRONLY | fs.constants.O_NONBLOCK);
    try { fs.writeSync(fd, 'x'); } finally { fs.closeSync(fd); }
  } catch (error) {
    if (error.code !== 'ENXIO') throw error; // No action is waiting.
  }
}

function completedStatusReads() {
  if (!fs.existsSync(statusReads)) return [];
  return fs.readFileSync(statusReads, 'utf8').trim().split('\n').filter(Boolean);
}

function waitForStatusReads(count) {
  return new Promise((resolve, reject) => {
    let watcher;
    const timer = setTimeout(() => {
      watcher.close();
      reject(new Error(`Timed out waiting for ${count} completed extension status reads`));
    }, 60_000);
    const check = () => {
      const reads = completedStatusReads();
      if (reads.length < count) return;
      clearTimeout(timer);
      watcher.close();
      try { assert.ok(reads.every((result) => result === '0'), 'extension status reads succeeded'); resolve(); }
      catch (error) { reject(error); }
    };
    watcher = fs.watch(root, check);
    check();
  });
}

function python() {
  if (process.env.AUTOCODE_HOST_PYTHON) return process.env.AUTOCODE_HOST_PYTHON;
  const venv = path.join(REPO, '.venv', 'bin', 'python');
  return fs.existsSync(venv) ? venv : 'python3';
}

async function executable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const { downloadAndUnzipVSCode } = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({ version: '1.147.0', cachePath: path.join(EXTENSION, '.vscode-test') });
}

async function chat() {
  return until('chat webview', async () => {
    for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
    return null;
  });
}

function status() {
  const result = spawnSync(fixture.cli[0], [...fixture.cli.slice(1), '--run-dir', fixture.runDir,
    '--workspace', fixture.workspace, '--status', '--json'], {
    cwd: fixture.workspace, env: environment, encoding: 'utf8', timeout: 40_000,
  });
  assert.equal(result.status, 0, result.stderr);
  const payload = JSON.parse(result.stdout);
  return payload.view ?? payload;
}

before(async () => {
  const made = spawnSync(python(), [path.join(REPO, 'tests', 'source_recovery_vscode_fixture.py'), path.join(root, 'run')], {
    cwd: REPO, encoding: 'utf8', env: { ...process.env, HOME: path.join(root, 'home') }, timeout: 120_000,
  });
  assert.equal(made.status, 0, `offline source fixture failed:\n${made.stderr}`);
  fixture = JSON.parse(made.stdout);
  assert.equal(fixture.view.status, 'PAUSED_SOURCE_UNAVAILABLE');
  assert.equal(fixture.view.next_command, null);
  const userData = path.join(root, 'user-data');
  fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
  fs.writeFileSync(path.join(userData, 'User', 'settings.json'), JSON.stringify({
    'autocode.cliPath': fixture.cli[0],
    'autocode.cliArgs': [path.join(__dirname, 'source-recovery-cli.py')],
    'autocode.pollSeconds': 2,
    'workbench.startupEditor': 'none',
    'security.workspace.trust.enabled': false,
    'chat.disableAIFeatures': true,
    'update.mode': 'none',
    'extensions.autoUpdate': false,
    'telemetry.telemetryLevel': 'off',
  }));
  const tempDir = path.join(root, 'tmp');
  fs.mkdirSync(tempDir);
  environment = { ...process.env, ...fixture.env, HOME: path.join(root, 'home'), TMPDIR: tempDir,
    AUTOCODE_SOURCE_ACTION_STARTED: started, AUTOCODE_SOURCE_ACTION_RELEASE: release,
    AUTOCODE_SOURCE_ACTION_FINISHED: finished, AUTOCODE_SOURCE_STATUS_READS: statusReads };
  for (const key of ['OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY']) delete environment[key];
  const { _electron } = require('playwright-core');
  app = await _electron.launch({ executablePath: await executable(), env: environment, args: [
    '--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
    '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
    `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace,
  ] });
  win = await app.firstWindow();
  await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
  await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
  const row = win.locator('.monaco-list-row', { hasText: 'Bug report from the finance team' }).first();
  await openHistoryRow(row, chat, 'source recovery fixture');
});

after(async () => {
  // Always release any held action before closing its owned host.
  releaseAction();
  if (fs.existsSync(started) && !fs.existsSync(finished)) {
    await until('the released action to finish', () => fs.existsSync(finished), 90_000).catch(() => {});
  }
  fs.mkdirSync(path.join(EXTENSION, '.vscode-test'), { recursive: true });
  await win?.screenshot({ path: path.join(EXTENSION, '.vscode-test', 'host-last.png') }).catch(() => {});
  await app?.close().catch(() => {});
  if (environment && fixture) {
    const invoke = (action) => spawnSync(fixture.cli[0], [...fixture.cli.slice(1), 'serve', action, '--json'], {
      cwd: fixture.workspace, env: environment, encoding: 'utf8', timeout: 15_000,
    });
    const stop = invoke('--stop');
    assert.equal(stop.status, 0, stop.stderr);
    const host = invoke('--status');
    assert.equal(host.status, 0, host.stderr);
    assert.equal(JSON.parse(host.stdout).running, false, 'owned source host stopped');
  }
  if (passed) fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 });
  else console.error(`Preserved failed source recovery fixture at ${root}`);
});

test('unsafe source blocks the actual chat until the source is restored', { timeout: 180_000 }, async () => {
  let frame = await chat();
  const blocked = await until('the blocked source view', async () => {
    frame = await chat();
    const text = await frame.evaluate(() => document.body.textContent);
    return text.includes('AutoCode cannot read the task source') ? text : null;
  });
  assert.match(blocked, /AutoCode could not read the task.s source/);
  assert.equal(await frame.$('#take-action'), null, 'no Resume is offered for the unsafe parent');
  const held = status();
  assert.equal(held.status, 'PAUSED_SOURCE_UNAVAILABLE');
  assert.equal(held.next_command, null);
  const previousReads = completedStatusReads().length;
  await waitForStatusReads(previousReads + 2);
  assert.equal(fs.existsSync(started), false, 'the extension did not take an unnamed action');

  const shared = path.join(fixture.workspace, 'shared');
  fs.unlinkSync(shared);
  fs.renameSync(path.join(fixture.workspace, 'old-shared'), shared);
  await until('the extension to take the named source recovery', () => fs.existsSync(started));
  const argv = fs.readFileSync(started, 'utf8').split('\n');
  assert.ok(argv.includes('--resume-paused'));
  assert.ok(argv.includes('--expected-recovery-token'));
  const ready = status();
  assert.equal(ready.status, 'PAUSED_SOURCE_UNAVAILABLE');
  assert.equal(ready.needs.kind, 'continue');
  assert.equal(ready.recovery.category, 'recovery');
  await until('the editor to leave its blocked source view', async () => {
    frame = await chat();
    const text = await frame.evaluate(() => document.body.textContent);
    return text && !text.includes('AutoCode cannot read the task source') ? text : null;
  });
  releaseAction();
  await until('the offline Resume action to finish', () => fs.existsSync(finished), 90_000);
  const advanced = await until('source recovery to advance', () => {
    const view = status();
    return view.status !== 'PAUSED_SOURCE_UNAVAILABLE' ? view : null;
  }, 90_000);
  assert.equal(advanced.status, 'AWAITING_GOAL_APPROVAL');
  await until('the editor to show the resulting plan', async () => {
    frame = await chat();
    const text = await frame.evaluate(() => document.body.textContent);
    return /approve|review.*plan/i.test(text) ? text : null;
  });
  passed = true;
});
