// A real Extension Host approves an offline Rust task; its scripted OpenCode
// provider runs the installed Cargo proxy through the core's contained shell.
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const {openHistoryRow} = require('./open-history.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(what, probe, timeout = 120_000) {
  const deadline = Date.now() + timeout;
  let last;
  do {
    try { last = await probe(); if (last) return last; } catch (error) { last = error; }
    await sleep(250);
  } while (Date.now() < deadline);
  throw new Error(`Timed out waiting for ${what}; last: ${last instanceof Error ? last.message : JSON.stringify(last)}`);
}
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

for (const absoluteCargo of [false, true]) test(`real editor runs ${absoluteCargo ? 'explicit off-PATH' : 'named'} offline Cargo through the contained Rustup toolchain`, {timeout: 600_000, skip: process.platform !== 'linux'}, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'autocode-rustup-host-'));
  const release = path.join(root, 'release');
  const record = path.join(root, 'cargo-result.json');
  const providerDiagnostic = path.join(root, 'provider-interpreter.json');
  const owners = path.join(root, 'owned-processes.jsonl');
  assert.equal(spawnSync('mkfifo', [release]).status, 0, 'create owned release FIFO');
  const tempDir = path.join(root, 'tmp'); fs.mkdirSync(tempDir);
  const env = {...process.env, TMPDIR: tempDir,
    RUSTUP_HOME: process.env.RUSTUP_HOME || path.join(os.homedir(), '.rustup'),
    AUTOCODE_FIXTURE_RUSTUP_RECORD: record, AUTOCODE_FIXTURE_RUSTUP_RELEASE: release, AUTOCODE_FIXTURE_RUSTUP_OWNERS: owners,
    AUTOCODE_FIXTURE_RUSTUP_DIAGNOSTIC: providerDiagnostic};
  if (absoluteCargo) env.AUTOCODE_FIXTURE_ABSOLUTE_CARGO = '1';
  else delete env.AUTOCODE_FIXTURE_ABSOLUTE_CARGO;
  for (const key of ['OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY', 'KILO_API_KEY', 'ZAI_API_KEY']) delete env[key];
  let fixture, app, win, editorPid, editorStarted, passed = false, testError, phase = "preparing fixture";
  const cleanupErrors = [];
  async function cleanup(label, action) {
    try { await action(); } catch (error) { cleanupErrors.push(new Error(`${label}: ${error.message}`, {cause: error})); }
  }
  function releaseBuilder() {
    try { const fd = fs.openSync(release, fs.constants.O_WRONLY | fs.constants.O_NONBLOCK); try { fs.writeSync(fd, 'x'); } finally { fs.closeSync(fd); } }
    catch (error) { if (error.code !== 'ENXIO') throw error; }
  }
  function status() {
    const result = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), '--run-dir', fixture.runDir,
      '--workspace', fixture.workspace, '--status', '--json'], {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 40_000});
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout).view;
  }
  try {
    const made = spawnSync(python(), [path.join(REPO, 'tests/rustup_vscode_fixture.py'), path.join(root, 'run')],
      {cwd: REPO, env, encoding: 'utf8', timeout: 300_000});
    assert.equal(made.status, 0, `offline Rust fixture failed:\n${made.stderr}`);
    fixture = JSON.parse(made.stdout); Object.assign(env, fixture.env);
    assert.equal(env.HOME, process.env.HOME, 'preserve operator HOME');
    assert.equal(env.CODEX_HOME, process.env.CODEX_HOME, 'preserve operator CODEX_HOME');
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), {recursive: true});
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python(), 'autocode.cliArgs': [path.join(__dirname, 'rustup-cli.py')], 'autocode.pollSeconds': 2,
      'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false, 'chat.disableAIFeatures': true,
      'update.mode': 'none', 'extensions.autoUpdate': false, 'telemetry.telemetryLevel': 'off',
    }));
    const {_electron} = require('playwright-core');
    phase = "launching VS Code";
    app = await _electron.launch({executablePath: await executable(), env, args: [
      '--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
      '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
      `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace,
    ]});
    editorPid = app.process().pid;
    const identity = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)],
      {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
    assert.equal(identity.status, 0, identity.stderr);
    editorStarted = JSON.parse(identity.stdout).started;
    win = await app.firstWindow(); await win.locator('.monaco-workbench').waitFor({timeout: 120_000});
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    phase = "opening fixture history";
    await openHistoryRow(win.locator('.monaco-list-row', {hasText: 'Document this existing Rust crate'}).first(), chat, 'Rustup offline');
    let frame = await chat();
    await until('the Rust plan and its approval action', async () => {
      frame = await chat(); const text = await frame.evaluate(() => document.body.innerText);
      return text.includes(absoluteCargo ? 'cargo --version' : 'cargo test') && await frame.$('#take-action') ? true : null;
    });
    const before = status();
    assert.equal(before.needs.kind, 'approve_plan');
    const review = JSON.parse(fs.readFileSync(fixture.planReview, 'utf8'));
    assert.equal(before.displayed_plan.token, fixture.planToken);
    assert.equal(review.token, fixture.planToken);
    assert.equal(before.displayed_plan.review_markdown, review.review_markdown);
    assert.match(review.review, /Operating agent review/);
    phase = "approving reviewed plan";
    await frame.locator('#take-action').click();
    phase = "waiting for contained Cargo result";
    const observed = await until('real Cargo execution through the selected shell', () =>
      fs.existsSync(record) ? JSON.parse(fs.readFileSync(record, 'utf8')) : null, 240_000);
    assert.equal(observed.exit, 0, observed.stderr);
    assert.equal(observed.explicit_command, env.AUTOCODE_FIXTURE_RUSTUP_COMMAND);
    assert.equal(observed.explicit_exit, 0, observed.explicit_stderr);
    assert.match(observed.explicit_stdout, /cargo \d/);
    assert.match(observed.stdout + observed.stderr, absoluteCargo ? /cargo \d/ : /test result: ok/);
    assert.ok(path.resolve(observed.shell).startsWith(path.join(fixture.workspace, '.autocode/tool-containment-')));
    const during = status();
    assert.equal(during.tool_containment, 'contained');
    assert.equal(during.tool_containment_backend.stage, 'builder');
    assert.ok(fs.existsSync(owners) && fs.readFileSync(owners, 'utf8').trim(), 'the owned actions and Builder announced their identities');
    releaseBuilder();
    phase = "waiting for verified task outcome";
    const after = await until('the resulting Rust task outcome', () => {
      const view = status();
      return view.done || (view.needs && view.needs.kind !== 'continue') ? view : null;
    }, 240_000);
    assert.equal(after.status, 'TASK_COMPLETE', JSON.stringify({status: after.status, reason: after.stop_reason, needs: after.needs}));
    assert.equal(after.delivery?.verified_complete, true, 'the delivered source still matches its proof');
    phase = "waiting for completed editor view";
    await until('the editor to show the current completed outcome', async () => {
      frame = await chat();
      const result = frame.locator('#finish-summary .result');
      return await result.count() && (await result.innerText()).trim() === `✓ ${after.progress.headline}` ? true : null;
    });
    passed = true;
  } catch (error) {
    testError = error;
    console.error(`Rustup host failure during ${phase}: ${error.stack || error.message}`);
    if (fixture) {
      try {
        const view = status();
        console.error(`Rustup final public status: ${JSON.stringify({status: view.status, needs: view.needs?.kind,
          stop_reason: view.stop_reason, stage: view.tool_containment_backend?.stage,
          containment: view.tool_containment})}`);
      } catch (statusError) { console.error(`Rustup public status unavailable: ${statusError.message}`); }
    }
    if (fs.existsSync(record)) {
      try {
        const cargo = JSON.parse(fs.readFileSync(record, 'utf8'));
        console.error(`Rustup contained Cargo result: ${JSON.stringify({exit: cargo.exit, shell: cargo.shell,
          stdout: String(cargo.stdout || '').slice(-2000), stderr: String(cargo.stderr || '').slice(-2000),
          explicit_command: cargo.explicit_command, explicit_exit: cargo.explicit_exit,
          explicit_stdout: String(cargo.explicit_stdout || '').slice(-2000), explicit_stderr: String(cargo.explicit_stderr || '').slice(-2000)})}`);
      } catch (cargoError) { console.error(`Rustup Cargo result unavailable: ${cargoError.message}`); }
    } else console.error('Rustup contained Cargo result was not recorded');
    if (fs.existsSync(providerDiagnostic)) {
      console.error(`Rustup provider interpreter: ${fs.readFileSync(providerDiagnostic, 'utf8')}`);
    } else console.error('Rustup provider did not record its interpreter');
  } finally {
    await cleanup("release Builder", () => releaseBuilder());
    if (!passed && fixture) {
      // Stop only the action the fresh public view names; this fixture owns the run.
      try {
        const stopNow = status().pause?.stop_now?.next_command;
        if (Array.isArray(stopNow)) {
          const argv = stopNow.map((arg) => arg === 'REQUEST_ID' ? randomUUID() : arg);
          spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), ...argv],
            {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000});
        }
      } catch (error) { console.error(`Owned run stop observation: ${error.message}`); }
    }
    if (!passed && win) await win.screenshot({path: path.join(EXTENSION, '.vscode-test/host-rustup.png'), timeout: 5_000}).catch((error) => console.error(`Rustup screenshot unavailable: ${error.message}`));
    await cleanup("close owned editor", async () => {
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
    });
    await cleanup("stop private source host", () => {
      if (!fixture) return;
      const call = (action) => spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), 'serve', action, '--json'],
        {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 15_000});
      const stop = call('--stop'); assert.equal(stop.status, 0, stop.stderr);
      const checked = call('--status'); assert.equal(checked.status, 0, checked.stderr);
      assert.equal(JSON.parse(checked.stdout).running, false, 'owned source host stopped');
    });
    // Stop only PID/birth identities recorded by this fixture before exec or Builder work.
    // Closing the editor first prevents any new action from being launched afterward.
    await cleanup("stop recorded owned processes", async () => {
      if (!fs.existsSync(owners)) return;
      let last = '';
      for (let pass = 0; pass < 4; pass += 1) {
        const snapshot = fs.readFileSync(owners, 'utf8');
        const rows = snapshot.trim().split('\n').filter(Boolean).map((line) => JSON.parse(line));
        for (const row of rows) {
          await cleanup("stop recorded identity", () => {
          assert.ok(Number.isInteger(row.pid) && row.pid > 1 && Number.isFinite(row.started), 'valid owned process identity');
          const inspect = () => {
            const result = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(row.pid)],
              {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
            assert.equal(result.status, 0, result.stderr);
            return JSON.parse(result.stdout);
          };
          if (inspect().started !== row.started) return;
          const stopped = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop', String(row.pid), String(row.started)],
            {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
          assert.equal(stopped.status, 0, stopped.stderr);
          assert.notEqual(inspect().started, row.started, 'the exact owned process must be gone');
          });
        }
        if (snapshot === last && fs.readFileSync(owners, 'utf8') === snapshot) break;
        last = snapshot;
        assert.ok(pass < 3, 'owned process registry stopped changing');
      }
    });
    if (passed && !cleanupErrors.length) {
      await cleanup("remove successful fixture", () => fs.rmSync(root, {recursive: true, force: true, maxRetries: 3}));
    }
    if (!passed || cleanupErrors.length) console.error(`Preserved failed Rustup editor fixture at ${root}`);
  }
  if (testError || cleanupErrors.length) {
    for (const error of cleanupErrors) console.error(`Rustup cleanup failure: ${error.stack || error.message}`);
    throw new AggregateError([...(testError ? [testError] : []), ...cleanupErrors], "Rustup editor test or cleanup failed");
  }
});
