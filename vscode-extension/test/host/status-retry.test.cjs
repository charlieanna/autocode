// Real extension and core, with a fixture worker held past the production deadline.
// No provider is launched. Check the owned worker's exit as well as the recovery card.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');
const { privateHostRoot, privateHostEnvironment, settleFixture } = require('../owned-fixture-cleanup.cjs');
const { frameWith, until } = require('./wait.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const python = process.env.AUTOCODE_HOST_PYTHON || (fs.existsSync(path.join(REPO, '.venv/bin/python')) ? path.join(REPO, '.venv/bin/python') : 'python3');

async function executable() {
  if (process.env.AUTOCODE_VSCODE_EXECUTABLE) return process.env.AUTOCODE_VSCODE_EXECUTABLE;
  const { downloadAndUnzipVSCode } = require('@vscode/test-electron');
  return downloadAndUnzipVSCode({ version: '1.147.0', cachePath: path.join(EXTENSION, '.vscode-test') });
}

for (const kind of ['job', 'repeat']) {
  test(`real editor recovers from a timed-out ${kind} source worker`, { timeout: 240_000 }, async () => {
    const root = privateHostRoot(`autocode-${kind}-host-`);
    const marker = path.join(root, 'timeout');
    const log = path.join(root, 'observations');
    const operation = kind === 'job' ? 'job_retry' : 'failure_retry';
    let app;
    let win;
    let env;
    let editorPid;
    let editorStarted;
    let lastState;
    let ownsWarmHost = false;
    let testError;
    try {
      const temp = path.join(root, 'tmp');
      fs.mkdirSync(temp);
      env = { ...privateHostEnvironment(process.env, root), TMPDIR: temp };
      assert.equal(env.HOME, process.env.HOME);
      assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
      delete env.GIT_CONFIG_COUNT;
      delete env.GIT_CONFIG_KEY_0;
      delete env.GIT_CONFIG_VALUE_0;
      const made = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_fixture.py'), root, kind],
        { cwd: REPO, encoding: 'utf8', env, timeout: 30_000 });
      assert.equal(made.status, 0, `offline ${kind} fixture: ${made.stderr}`);
      const fixture = JSON.parse(made.stdout);
      assert.equal(fixture.registry, env.AUTOCODE_HOME);
      assert.ok(fs.lstatSync(fixture.registry).isDirectory(), 'fixture owns its private registry');
      ownsWarmHost = true;
      Object.assign(env, {
        AUTOCODE_HOST_OBSERVATION_TIMEOUT: marker,
        AUTOCODE_HOST_OBSERVATION_OPERATION: operation,
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
      editorPid = app.process().pid;
      const identified = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)],
        { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
      assert.equal(identified.status, 0, `identify owned editor: ${identified.stderr}`);
      editorStarted = JSON.parse(identified.stdout).started;
      assert.equal(typeof editorStarted, 'number', 'owned editor identity is available');
      win = await app.firstWindow();
      await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
      const chat = () => frameWith(win, '#chat-tabs', 'chat');
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
      const publicStatus = () => {
        const result = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cli.py'), '--run-dir', fixture.run_dir,
          '--workspace', fixture.workspace, '--status', '--json'], { cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000 });
        assert.equal(result.status, 0, `read public fixture status: ${result.stderr}`);
        const payload = JSON.parse(result.stdout);
        return payload.view ?? payload;
      };
      const assertNamedRetry = () => {
        const view = publicStatus();
        assert.match(view.next_command ?? '', /--retry-failed-stage/);
        if (kind === 'job') {
          assert.equal(view.needs.kind, 'retry_job');
          assert.ok(view.needs.job_retry_token);
          assert.match(view.next_command, /--job-retry-token -/);
        }
      };
      const retryLabel = kind === 'job' ? 'Retry the inspected step' : 'Retry the failed step once';
      const retryVisible = (shown) => kind === 'job'
        ? ['Continue', retryLabel].includes(shown.action) && shown.text.includes('The step failed.')
        : shown.action === retryLabel;
      await until('healthy retry action', async () => retryVisible(await state()));
      assertNamedRetry();
      fs.writeFileSync(marker, '');
      await until('the owned worker to exceed its deadline', () => {
        const events = fs.readFileSync(log, 'utf8');
        return /^child \d+ \d+(?:\.\d+)?$/m.test(events) && events.includes(`timeout ${operation}`);
      });
      if (kind === 'job') {
        await until('blocked job in the chat', async () => {
          const shown = await state();
          return shown.text.includes("The failed job's source could not be checked") && !shown.action;
        });
      } else {
        await until('blocked repeated failure in the chat', async () => {
          const shown = await state();
          return shown.text.includes("The repeated failure's source could not be checked") && !shown.action;
        });
      }
      const unavailable = publicStatus();
      assert.equal(unavailable.next_command, null, 'timeout does not name a dead continuation');
      fs.rmSync(marker);
      await until('recovered retry action', async () => retryVisible(await state()));
      assertNamedRetry();
      if (kind === 'repeat') {
        // An observed source edit is distinguishable from an unavailable observation.
        fs.writeFileSync(path.join(fixture.workspace, 'changed.py'), 'changed = True\n');
        const changed = publicStatus();
        assert.match(changed.next_command ?? '', /--resume-paused/);
        assert.doesNotMatch(changed.next_command, /--retry-failed-stage/);
        const continuation = changed.recovery?.actions?.find((action) => action.kind === 'resume' &&
          action.args?.includes('--resume-paused') && !action.args.includes('--retry-failed-stage'));
        await until('plain continuation for the changed source', async () =>
          (await state()).action === (continuation?.label ?? 'Continue'));
      }
    } catch (error) {
      testError = error;
      console.log(`Host diagnostics for ${kind}:`, JSON.stringify({
        lastState, observations: fs.existsSync(log) ? fs.readFileSync(log, 'utf8') : '(no wrapper log)',
      }));
      fs.mkdirSync(path.join(EXTENSION, '.vscode-test'), { recursive: true });
      await win?.screenshot({ path: path.join(EXTENSION, '.vscode-test', `host-status-${kind}.png`), timeout: 5_000 }).catch(() => {});
    } finally {
      try {
        await settleFixture([
          ['release held workers', () => fs.rmSync(marker, { force: true })],
          ['close editor', async () => {
            if (!app) return;
            let timer;
            const timedOut = await Promise.race([
              app.close().then(() => false, () => true),
              new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5_000); }),
            ]);
            clearTimeout(timer);
            if (timedOut) {
              const stopped = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'),
                'stop', String(editorPid), String(editorStarted)], { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
              assert.equal(stopped.status, 0, `stop owned editor: ${stopped.stderr}`);
            }
          }],
          ['stop private warm hosts', () => {
            if (!ownsWarmHost) return;
            const result = spawnSync(python, [path.join(REPO, 'tests/host_reader_cleanup.py'), '--stop-source-hosts', root],
              { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
            assert.equal(result.status, 0, result.stderr);
          }],
          ['check owned readers and workers', async () => {
            if (!fs.existsSync(log)) return;
            await until('all owned status readers and workers to exit', () => {
              const result = spawnSync(python, [path.join(REPO, 'tests/host_reader_cleanup.py'), log, root],
                { cwd: REPO, env, encoding: 'utf8', timeout: 15_000 });
              assert.equal(result.status, 0, result.stderr);
              return JSON.parse(result.stdout).running.length === 0;
            }, 20_000);
          }],
        ], () => fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 }), testError);
      } catch (error) {
        console.error(`Preserved failed ${kind} status host fixture at ${root}`);
        throw error;
      }
    }
  });
}
