// Real extension and core, with immediate timeouts injected at the status observation seam.
// No provider is launched. The timeout simulation does not test worker process cleanup.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');

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

for (const kind of ['job', 'repeat']) {
  test(`real editor shows ${kind} source observation timeout and recovery`, async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), `autocode-${kind}-host-`));
    const marker = path.join(root, 'timeout');
    const log = path.join(root, 'observations');
    let app;
    let win;
    let env;
    let lastState;
    try {
      const temp = path.join(root, 'tmp');
      fs.mkdirSync(temp);
      env = { ...process.env, HOME: path.join(root, 'home'), TMPDIR: temp };
      delete env.GIT_CONFIG_COUNT;
      delete env.GIT_CONFIG_KEY_0;
      delete env.GIT_CONFIG_VALUE_0;
      const made = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_fixture.py'), root, kind], { cwd: REPO, encoding: 'utf8', env });
      assert.equal(made.status, 0, `offline ${kind} fixture: ${made.stderr}`);
      const fixture = JSON.parse(made.stdout);
      Object.assign(env, {
        AUTOCODE_HOME: fixture.registry,
        AUTOCODE_HOST_OBSERVATION_TIMEOUT: marker,
        AUTOCODE_HOST_OBSERVATION_LOG: log,
        PYTHONPATH: [path.join(REPO, 'tests/host_status_site'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      });
      const userData = path.join(root, 'user-data');
      fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
      fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
        'autocode.cliPath': python,
        'autocode.cliArgs': [path.join(REPO, 'tests/status_retry_host_cli.py')],
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
        executablePath: await executable(), env,
        args: ['--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
          '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
          `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace],
      });
      win = await app.firstWindow();
      await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
      const chat = () => until('chat', async () => {
        for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
        return null;
      });
      const state = async () => {
        lastState = await (await chat()).evaluate(() => ({
          text: document.body.textContent,
          action: document.querySelector('#take-action')?.textContent ?? null,
        }));
        return lastState;
      };
      await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
      const title = `Offline ${kind} status deadline verification`;
      await openHistoryRow(win.locator('.monaco-list-row', { hasText: title }).first(), chat, title);
      const retryLabel = kind === 'job' ? 'Retry the inspected step' : 'Retry the failed step once';
      const retryVisible = (shown) => kind === 'job'
        ? ['Continue', retryLabel].includes(shown.action) && shown.text.includes('The step failed.')
        : shown.action === retryLabel;
      await until('healthy retry action', async () => retryVisible(await state()));
      fs.writeFileSync(marker, '');
      const operation = kind === 'job' ? 'job_retry' : 'failure_retry';
      await until('injected timeout', () => fs.existsSync(log) && fs.readFileSync(log, 'utf8').includes(`timeout ${operation}`));
      if (kind === 'job') {
        await until('blocked job in the chat', async () => {
          const shown = await state();
          return shown.text.includes("The failed job's source could not be checked") && !shown.action;
        });
      } else {
        // Do not require the known ineffective Continue: a later fix may remove it.
        const blocked = await until('repeated failure without a retry offer', async () => {
          const shown = await state();
          return shown.action !== retryLabel && !shown.text.includes('choose “Retry the failed step once”')
            && (shown.text.includes('An ordinary resume cannot grant another attempt')
              || shown.text.includes("The repeated failure's source could not be checked")) ? shown : null;
        });
        console.log('Repeated failure timeout action:', blocked.action ?? '(none)');
      }
      fs.rmSync(marker);
      await until('recovered retry action', async () => retryVisible(await state()));
    } catch (error) {
      console.log(`Host diagnostics for ${kind}:`, JSON.stringify({
        lastState, observations: fs.existsSync(log) ? fs.readFileSync(log, 'utf8') : '(no wrapper log)',
      }));
      await win?.screenshot({ path: path.join(EXTENSION, '.vscode-test', 'host-last.png'), timeout: 5_000 }).catch(() => {});
      throw error;
    } finally {
      fs.rmSync(marker, { force: true });
      await app?.close().catch(() => {});
      if (env) {
        // The default warm host has a unique TMPDIR/socket owned by this fixture.
        const stopped = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cli.py'), 'serve', '--stop', '--json'],
          { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
        const checked = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cli.py'), 'serve', '--status', '--json'],
          { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
        assert.equal(stopped.status, 0, `stop isolated warm host: ${stopped.stderr}`);
        assert.equal(checked.status, 0, `check isolated warm host: ${checked.stderr}`);
        assert.equal(JSON.parse(checked.stdout).running, false, 'isolated warm host exited');
        if (fs.existsSync(log)) {
          const lines = fs.readFileSync(log, 'utf8').trim().split('\n');
          const pids = lines.filter((line) => line.startsWith('status ')).map((line) => Number(line.split(' ')[1]));
          await until('all owned status CLI processes to exit', () => pids.every((pid) => {
            try { process.kill(pid, 0); return false; } catch (error) { if (error.code === 'ESRCH') return true; throw error; }
          }), 15_000);
        }
      }
      fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 });
    }
  });
}
