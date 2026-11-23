// Two offline workflows in a real VS Code Extension Host (#3860).
//
// The rest of the extension suite substitutes VS Code's APIs (test/helpers/editor.cjs) or renders the
// webview in jsdom. This suite starts an actual VS Code with the built extension under development,
// drives its chat webview, Runs view, command palette, status bar and Run details panel through
// Playwright, and reads what a person would see. The extension launches this checkout's own CLI on a
// scripted run (tests/provider_auth_fixture.py): no model is called and no person focuses a window.
//
//   npm run build && xvfb-run -a npm run test:host
//
// AUTOCODE_VSCODE_EXECUTABLE names a VS Code to use; otherwise the pinned release is downloaded into
// .vscode-test. AUTOCODE_HOST_PYTHON names the Python that runs the CLI (default: the repository's
// .venv, then python3); it needs the CLI's own requirements (psutil).

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { after, before, test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');
const { privateHostRoot, privateHostEnvironment, ownedWarmHostSteps, settleFixture } = require('../owned-fixture-cleanup.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const VSCODE_VERSION = '1.147.0';
// The editor's own read deadline (src/core/cli.ts STATUS_TIMEOUT_MS); the held read must cross it.
const READ_DEADLINE_MS = 30_000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(what, probe, timeout = 60_000) {
  const deadline = Date.now() + timeout;
  let last;
  for (;;) {
    try {
      last = await probe();
      if (last) return last;
    } catch (error) {
      last = error;
    }
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}; last: ${last instanceof Error ? last.message : JSON.stringify(last)}`);
    await sleep(250);
  }
}

function python() {
  if (process.env.AUTOCODE_HOST_PYTHON) return process.env.AUTOCODE_HOST_PYTHON;
  const venv = path.join(REPO, '.venv', 'bin', 'python');
  return fs.existsSync(venv) ? venv : 'python3';
}

async function vscodeExecutable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const { downloadAndUnzipVSCode } = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({ version: VSCODE_VERSION, cachePath: path.join(EXTENSION, '.vscode-test') });
}

/** Status reads the held CLI recorded: started, held, finished, and the ones still outstanding. */
function reads(file) {
  const rows = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map((line) => line.split(' '));
  const started = rows.filter((row) => row[0] === 'start').map((row) => row[1]);
  const ended = new Set(rows.filter((row) => row[0] === 'end').map((row) => row[1]));
  const held = rows.filter((row) => row[0] === 'held').map((row) => row[1]);
  return { rows, started, held, outstanding: started.filter((pid) => !ended.has(pid)) };
}

const root = privateHostRoot('autocode-host-');
const hold = path.join(root, 'hold');
const log = path.join(root, 'reads');
let app;
let win;
let fixture;
let claimedHost = false;
let testError;
let editorPid;
let editorStarted;
const tempDir = path.join(root, 'tmp');
fs.mkdirSync(tempDir);
const environment = { ...privateHostEnvironment(process.env, path.join(root, 'run')), TMPDIR: tempDir,
  AUTOCODE_HOST_READS: log, AUTOCODE_HOST_HOLD: hold, AUTOCODE_SOURCE_HOST: '1' };

function rememberFailure(action) {
  return async () => {
    try { await action(); } catch (error) { testError = error; throw error; }
  };
}

function cli(action) {
  return spawnSync(fixture.cli[0], [...fixture.cli.slice(1), ...action], {
    cwd: fixture.workspace, env: environment, encoding: 'utf8', timeout: 15_000,
  });
}

function ownedEditor(operation) {
  const args = [path.join(REPO, 'tests/status_retry_host_cleanup.py'), operation, String(editorPid)];
  if (operation === 'stop') args.push(String(editorStarted));
  const result = spawnSync(python(), args, { cwd: REPO, env: environment, encoding: 'utf8', timeout: 15_000 });
  assert.equal(result.status, 0, `owned editor ${operation}: ${result.stderr}`);
  return JSON.parse(result.stdout);
}

/** The webview frame that holds ``selector``: the chat view or a Run details panel. */
async function frameWith(selector) {
  return until(`a webview with ${selector}`, async () => {
    for (const frame of win.frames()) {
      if (await frame.$(selector).catch(() => null)) return frame;
    }
    return null;
  });
}
const chat = () => frameWith('#chat-tabs');
const chatText = async () => (await chat()).evaluate(() => document.body.textContent);
const chatError = async () => (await chat()).evaluate(() => document.querySelector('#chat-error')?.textContent ?? '');
const statusBar = () => win.locator('.statusbar').innerText();

async function command(title) {
  await win.keyboard.press('F1');
  const input = win.locator('.quick-input-widget input');
  await input.waitFor({ state: 'visible' });
  await input.fill(`>${title}`);
  await win.locator('.quick-input-list .monaco-list-row', { hasText: title }).first().waitFor();
  await win.keyboard.press('Enter');
}

before(rememberFailure(async () => {
  fs.writeFileSync(log, '');
  const made = spawnSync(python(), [path.join(REPO, 'tests', 'provider_auth_fixture.py'), path.join(root, 'run'), '--inherit-homes'], {
    cwd: REPO, encoding: 'utf8', env: { ...environment, AUTOCODE_SOURCE_HOST: '0' },
  });
  assert.equal(made.status, 0, `the offline Planner 401 run could not be made:\n${made.stderr}`);
  fixture = JSON.parse(made.stdout);
  assert.equal(path.resolve(fixture.env.AUTOCODE_HOME), path.resolve(root, 'run', 'registry'));
  assert.ok(fs.lstatSync(fixture.env.AUTOCODE_HOME).isDirectory(), 'registry is an owned directory, not a symlink');
  Object.assign(environment, fixture.env);
  assert.equal(environment.HOME, process.env.HOME);
  assert.equal(environment.CODEX_HOME, process.env.CODEX_HOME);
  claimedHost = true;
  assert.equal(fixture.view.status, 'PAUSED_PROVIDER_UNCERTAIN', fixture.view.stop_reason);

  const userData = path.join(root, 'user-data');
  fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
  fs.writeFileSync(path.join(userData, 'User', 'settings.json'), JSON.stringify({
    'autocode.cliPath': fixture.cli[0],
    'autocode.cliArgs': [path.join(__dirname, 'held_cli.py')],
    'autocode.pollSeconds': 2,
    'workbench.startupEditor': 'none',
    'security.workspace.trust.enabled': false,
    'chat.disableAIFeatures': true,
    'update.mode': 'none',
    'extensions.autoUpdate': false,
    'telemetry.telemetryLevel': 'off',
  }));
  const { _electron } = require('playwright-core');
  app = await _electron.launch({
    executablePath: await vscodeExecutable(),
    env: environment,
    args: [
      '--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
      '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
      `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace,
    ],
  });
  editorPid = app.process().pid;
  editorStarted = ownedEditor('identity').started;
  assert.equal(typeof editorStarted, 'number', 'owned editor birth identity is available');
  win = await app.firstWindow();
  await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
  // Open the run as a person does: the AutoCode chat, its History, then the run's row.
  await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
  // Opening History replaces the chat webview. A Playwright click waits on the
  // old frame after the handler succeeds and can time out even with the Runs
  // list visible. Dispatch the setup action, then wait for the actual run row.
  const row = win.locator('.monaco-list-row', { hasText: 'Bug report from the finance team' }).first();
  try {
    await openHistoryRow(row, chat, 'Bug report from the finance team');
  } catch (error) {
    // Preserve this host's state separately: later host tests run even if this setup fails.
    console.log('Stock host frames after failed chat setup:', JSON.stringify(win.frames().map((frame) => frame.url().split('?')[0])));
    throw error;
  }
  await until('the run in the chat', async () => (await chatText()).includes('rejected the saved credentials (HTTP 401)'));
}));

after(async () => {
  await win?.screenshot({ path: path.join(EXTENSION, '.vscode-test', 'host-stock.png'), timeout: 5_000 }).catch(() => {});
  const invoke = (args) => {
    const result = cli(args);
    assert.equal(result.status, 0, `${args.join(' ')}: ${result.stderr}`);
    return JSON.parse(result.stdout);
  };
  try {
    await settleFixture([
      ['release held readers', () => fs.rmSync(hold, { force: true })],
      ['close editor', async () => {
        if (!app) return;
        let timer;
        const timedOut = await Promise.race([
          app.close().then(() => false, () => true),
          new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5_000); }),
        ]);
        clearTimeout(timer);
        if (timedOut) ownedEditor('stop');
      }],
      ['check owned readers', async () => {
        if (!fs.existsSync(log)) return;
        await until('owned readers to exit', () => {
          const result = spawnSync(python(), [path.join(REPO, 'tests/host_reader_cleanup.py'), log],
            { cwd: REPO, env: environment, encoding: 'utf8', timeout: 15_000 });
          assert.equal(result.status, 0, `inspect owned readers: ${result.stderr}`);
          return JSON.parse(result.stdout).running.length === 0;
        }, 15_000);
      }],
      ...ownedWarmHostSteps(claimedHost,
        () => invoke(['serve', '--stop', '--json']),
        () => {
          const host = invoke(['serve', '--status', '--json']);
          assert.equal(host.running, false, 'owned warm host exited before fixture removal');
          console.log('Stock owned warm host cleanup:', JSON.stringify(host));
        }),
      ['check unrecorded owned readers', async () => {
        if (!claimedHost) return;
        await until('no Python reader tied to the owned fixture', () => {
          const result = spawnSync(python(), [path.join(REPO, 'tests/host_reader_cleanup.py'), log, root],
            { cwd: REPO, env: environment, encoding: 'utf8', timeout: 15_000 });
          assert.equal(result.status, 0, `inspect fixture processes: ${result.stderr}`);
          return JSON.parse(result.stdout).running.length === 0;
        }, 15_000);
      }],
    ], () => fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 }), testError);
  } catch (error) {
    console.error(`Preserved failed stock host fixture at ${root}`);
    throw error;
  }
});

test('a scripted Planner HTTP 401 shows the failed stage, a blocked chat and why its models cannot change', rememberFailure(async () => {
  const frame = await chat();
  const expand = frame.locator('#header-collapse[aria-expanded="false"]');
  if (await expand.count()) await expand.click();
  const rows = await frame.evaluate(() => Object.fromEntries([...document.querySelectorAll('#pipeline tbody tr')]
    .map((row) => [row.dataset.job, { state: row.className, mark: row.querySelector('.mark')?.textContent }])));
  assert.deepEqual(rows.Requirements, { state: 'ran', mark: '✓' });
  assert.deepEqual(rows.Planner, { state: 'ran stopped', mark: '✗' }, 'the Planner is the visible failed stage');
  assert.equal(rows['Plan Reviewer'].state, 'pending', 'nothing after the Planner ran');

  const text = await chatText();
  assert.match(text, /AutoCode is blocked on internal recovery/);
  assert.match(text, /The model provider rejected the saved credentials \(HTTP 401\)/);
  assert.match(text, /This run cannot continue automatically\./);
  assert.ok(await frame.$('#start-new-conversation'), 'the blocked chat offers a new conversation');

  const models = await frame.evaluate(() => {
    const chip = document.querySelector('#models-picker');
    return { disabled: chip.disabled, reason: chip.title };
  });
  assert.deepEqual(models, { disabled: true, reason: 'The last stage attempt still needs recovery. Its models cannot be changed yet.' });
  await until('the blocked status bar', async () => /blocked on internal recovery/.test(await statusBar()));

  // Run details lists each recorded attempt: the Planner ran exactly once.
  await frame.click('#details');
  const report = await until('the Run details panel', async () => {
    for (const each of win.frames()) {
      const body = await each.evaluate(() => document.body?.textContent ?? '').catch(() => '');
      if (/Stages that ran/i.test(body) && body.includes('Attempts')) return body;
    }
    return null;
  });
  assert.match(report, /Stopped at Planner/);
  assert.match(report, /Planner: ran once/);
  assert.equal(report.match(/\d+\. Planner · /g)?.length, 1, 'one recorded Planner attempt');

  // The editor only read: the saved run still records the one Planner attempt.
  const status = spawnSync(fixture.cli[0], [...fixture.cli.slice(1), '--run-dir', fixture.runDir, '--status', '--json'], {
    cwd: fixture.workspace, encoding: 'utf8', env: environment,
  });
  assert.equal(status.status, 0, status.stderr);
  const view = JSON.parse(status.stdout).view ?? JSON.parse(status.stdout);
  assert.equal(view.usage.accounting.attempts.filter((row) => row.stage === 'draft_plan').length, 1);
  await win.keyboard.press('Control+W');
}));

test('a status read held past its deadline shows unavailable, keeps one reader and the draft, and recovers', rememberFailure(async () => {
  const draft = 'Keep this draft while the status read is held.';
  await (await chat()).fill('#message', draft);
  fs.writeFileSync(hold, '');
  // Reads already running when the hold began finish as usual; the next one to start is held.
  const held = await until('a held status read', async () => reads(log).held[0]);
  const before = reads(log).started.indexOf(held);

  const message = await until('the unavailable state', async () => {
    const shown = await chatError();
    return /did not finish this read within 30s/.test(shown) ? shown : null;
  }, READ_DEADLINE_MS + 30_000);
  assert.match(message, /Waiting for its reader to finish before reading again/);
  await until('the unavailable status bar', async () => /AutoCode: status unavailable/.test(await statusBar()));

  // Refresh while the read is still held: no second reader starts beside it.
  await command('AutoCode: Refresh Runs');
  await sleep(6_000);
  const during = reads(log);
  assert.deepEqual(during.outstanding, [held], 'one outstanding reader, the held one');
  assert.deepEqual(during.started.slice(before), [held], 'no replacement status read while the held one runs');
  assert.equal(await (await chat()).inputValue('#message'), draft, 'the draft survives the unavailable state and the refresh');

  fs.rmSync(hold);
  await until('a fresh read after the held one finished', async () => {
    const now = reads(log);
    const endAt = now.rows.findIndex((row) => row[0] === 'end' && row[1] === held);
    return endAt >= 0 && now.rows.slice(endAt + 1).some((row) => row[0] === 'start');
  });
  const ordered = reads(log).rows;
  const heldAt = ordered.findIndex((row) => row[0] === 'start' && row[1] === held);
  const heldEnd = ordered.findIndex((row) => row[0] === 'end' && row[1] === held);
  assert.ok(ordered.slice(heldAt + 1, heldEnd).every((row) => row[0] !== 'start'), 'no read started until the held reader finished');
  await until('the chat to recover', async () => (await chatError()) === '' && (await chatText()).includes('rejected the saved credentials (HTTP 401)'));
  await until('the status bar to recover', async () => /blocked on internal recovery/.test(await statusBar()));
  assert.equal(await (await chat()).inputValue('#message'), draft, 'the draft is still there after recovery');
}));
