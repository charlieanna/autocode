// A real Extension Host approves the reconstructed, offline SemVer M1 task.
// All proof decisions and actions come from the public CLI; the scripted
// provider supplies no proof authority or live model calls.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {randomUUID} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const {openHistoryRow} = require('./open-history.cjs');
const {privateHostEnvironment, ownedWarmHostSteps, settleFixture} = require('../owned-fixture-cleanup.cjs');

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
function noCredentials(env) {
  for (const key of Object.keys(env)) {
    if (key === 'GIT_CONFIG_COUNT' || /^(GIT_CONFIG_KEY_|GIT_CONFIG_VALUE_|OPENAI_|ANTHROPIC_|KILO_API_|CODEX_API_|ZAI_API_|AWS_|AZURE_|GOOGLE_)/.test(key) || /(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$/.test(key)) delete env[key];
  }
  return env;
}

test('real editor drives the scoped SemVer reference repair', {timeout: 1_200_000, skip: process.platform !== 'linux'}, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'autocode-semver-reference-host-'));
  const owners = path.join(root, 'owned-processes.jsonl');
  const actions = path.join(root, 'editor-actions.jsonl');
  const readerLog = path.join(root, 'readers.log'); fs.writeFileSync(readerLog, '');
  const tempDir = path.join(root, 'tmp'); fs.mkdirSync(tempDir);
  const env = noCredentials(privateHostEnvironment({...process.env, TMPDIR: tempDir, AUTOCODE_SEMVER_OWNERS: owners, AUTOCODE_SEMVER_ACTIONS: actions}, path.join(root, 'run')));
  let fixture, app, win, editorPid, editorStarted, passed = false, failure, ownsWarmHost = false, phase = 'preparing fixture';
  function status() {
    const result = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), '--run-dir', fixture.runDir,
      '--workspace', fixture.workspace, '--status', '--json'], {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 40_000});
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout).view;
  }
  try {
    assert.equal(path.resolve(env.AUTOCODE_HOME), path.resolve(root, 'run/registry'));
    ownsWarmHost = true;
    const made = spawnSync(python(), [path.join(REPO, 'tests/semver_reference_vscode_fixture.py'), path.join(root, 'run')],
      {cwd: REPO, env, encoding: 'utf8', timeout: 300_000, maxBuffer: 16 * 1024 * 1024});
    assert.equal(made.status, 0, `SemVer fixture setup failed:\n${made.stderr}`);
    fixture = JSON.parse(made.stdout); Object.assign(env, fixture.env);
    assert.equal(path.resolve(env.AUTOCODE_HOME), path.resolve(root, 'run/registry'));
    assert.ok(fs.lstatSync(env.AUTOCODE_HOME).isDirectory());
    assert.equal(env.HOME, process.env.HOME, 'preserve operator HOME');
    assert.equal(env.CODEX_HOME, process.env.CODEX_HOME, 'preserve operator CODEX_HOME');
    const manifest = JSON.parse(fs.readFileSync(path.join(root, 'run/fixture-manifest.json'), 'utf8'));
    const provider = path.join(REPO, 'tests/semver_reference_provider.py');
    const providerHash = () => crypto.createHash('sha256').update(fs.readFileSync(provider)).digest('hex');
    assert.equal(providerHash(), manifest.provider_sha256, 'fixture provider is frozen');
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), {recursive: true});
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python(), 'autocode.cliArgs': [path.join(__dirname, 'semver-reference-cli.py')], 'autocode.pollSeconds': 2,
      'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false, 'chat.disableAIFeatures': true,
      'update.mode': 'none', 'extensions.autoUpdate': false, 'telemetry.telemetryLevel': 'off',
    }));
    const {_electron} = require('playwright-core');
    phase = 'launching VS Code';
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
    phase = 'opening scoped SemVer history';
    await openHistoryRow(win.locator('.monaco-list-row', {hasText: 'Implement the python-semver library JSON interchange'}).first(), chat, 'scoped SemVer M1');
    let frame;
    await until('the reviewed plan and approval action', async () => {
      frame = await chat(); const text = await frame.evaluate(() => document.body.innerText);
      return text.includes('dumps_version') && await frame.$('#take-action') ? true : null;
    });
    const before = status();
    const review = JSON.parse(fs.readFileSync(fixture.review, 'utf8'));
    assert.equal(before.needs.kind, 'approve_plan');
    assert.equal(before.needs.token, review.token);
    assert.deepEqual(before.displayed_plan, review.plan, 'the operating agent reviewed the exact current plan');
    assert.match(review.delegation, /not personal owner approval/);
    phase = 'approving exact reviewed plan';
    await frame.locator('#take-action').click();
    phase = 'observing genuine reference rejection and test repair';
    const transitionsPath = path.join(root, 'run/writer-transitions.jsonl');
    const handoff = await until('the reference-repair handoff', () => {
      if (fs.existsSync(transitionsPath)) {
        const transition = fs.readFileSync(transitionsPath, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse)
          .find((row) => row.file === 'broad-diagnostics-compatible.py' && row.feedback_seen);
        if (transition) return {transition};
      }
      const view = status();
      const kind = view.needs?.kind;
      if (view.done || view.status?.startsWith('PAUSED_') || (kind && !['continue', 'approve_plan'].includes(kind))) {
        return {unexpected: {status: view.status, needs: kind, reason: view.stop_reason}};
      }
      return null;
    }, 720_000);
    assert.equal(handoff.unexpected, undefined, `run stopped before reference repair: ${JSON.stringify(handoff.unexpected)}`);
    const repair = handoff.transition;
    assert.match(JSON.stringify(repair.runner_feedback), /original-reference runner classified/);
    assert.match(JSON.stringify(repair.runner_feedback), /test_ac6_package_exports/);
    const feedback = Array.isArray(repair.runner_feedback) ? repair.runner_feedback.join('\n') : String(repair.runner_feedback);
    const receipt = feedback.match(/Runner evidence: (.+?) \(sha256 ([a-f0-9]{64})\)/);
    assert.ok(receipt, 'the real runner feedback names its rejection receipt and digest');
    const receiptPath = fs.realpathSync(receipt[1]);
    assert.ok(receiptPath.startsWith(fs.realpathSync(fixture.runDir) + path.sep), 'receipt belongs to the owned run');
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(receiptPath)).digest('hex'), receipt[2]);
    assert.equal(providerHash(), manifest.provider_sha256, 'same provider bytes throughout the run');
    phase = 'observing resulting public and editor state';
    const after = await until('the repaired run to stop', () => {
      const view = status();
      return view.done || (view.needs && view.needs.kind !== 'continue') ? view : null;
    }, 720_000);
    fs.writeFileSync(path.join(root, 'final-public-view.json'), JSON.stringify(after, null, 2));
    assert.equal(after.status, 'TASK_COMPLETE', JSON.stringify({status: after.status, reason: after.stop_reason, needs: after.needs}));
    const results = after.acceptance_tests?.results || {};
    for (const id of ['AC1', 'AC2', 'AC3', 'AC4', 'AC5', 'AC6']) {
      assert.equal(results[id], 'passes candidate; fails original reference', `named proof for ${id}`);
    }
    assert.equal(after.delivery?.verified_complete, true, 'finished task has verified delivery');
    await until('the editor to show the resulting public state', async () => {
      frame = await chat(); const text = await frame.evaluate(() => document.body.innerText);
      return (await frame.locator('#finish-summary .result').count()) && text.includes(after.progress.headline);
    });
    const approval = await until('the real editor approval action to finish', () => {
      if (!fs.existsSync(actions)) return null;
      const rows = fs.readFileSync(actions, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
      return rows.find((row) => row.kind === 'approve_plan') || null;
    });
    assert.ok([0, 2].includes(approval.returncode), 'real editor approval result was captured');
    passed = true;
  } catch (error) {
    failure = error;
    console.error(`SemVer reference editor failure during ${phase}: ${error.stack || error.message}`);
    if (fixture) try { const view = status(); console.error(`Final public state: ${JSON.stringify({status: view.status, needs: view.needs?.kind, reason: view.stop_reason})}`); } catch (statusError) { console.error(statusError.message); }
  } finally {
    if (!passed && fixture) try {
      const stop = status().pause?.stop_now?.next_command;
      if (Array.isArray(stop)) spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), ...stop.map((arg) => arg === 'REQUEST_ID' ? randomUUID() : arg)],
        {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000});
    } catch (error) { console.error(`Owned run stop observation: ${error.message}`); }
    if (!passed && win) await win.screenshot({path: path.join(EXTENSION, '.vscode-test/host-semver-reference.png'), timeout: 5000}).catch(() => {});
    try {
      await settleFixture([
        ['close owned editor', async () => {
      if (!app) return;
      let timer; const timedOut = await Promise.race([app.close().then(() => false, () => true), new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5000); })]); clearTimeout(timer);
      if (timedOut) {
        const stopped = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop', String(editorPid), String(editorStarted)], {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
        assert.equal(stopped.status, 0, stopped.stderr);
      }
    }],
        ...ownedWarmHostSteps(ownsWarmHost, () => {
          const stop = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), 'serve', '--stop', '--json'], {cwd: fixture?.workspace || REPO, env, encoding: 'utf8', timeout: 15_000});
          assert.equal(stop.status, 0, stop.stderr);
        }, () => {
          const checked = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), 'serve', '--status', '--json'], {cwd: fixture?.workspace || REPO, env, encoding: 'utf8', timeout: 15_000});
          assert.equal(checked.status, 0, checked.stderr);
          assert.equal(JSON.parse(checked.stdout).running, false);
        }),
        ['stop owned process identities', () => {
      if (!fs.existsSync(owners)) return;
      let previous = '';
      for (let pass = 0; pass < 4; pass++) {
        const snapshot = fs.readFileSync(owners, 'utf8');
        for (const row of snapshot.trim().split('\n').filter(Boolean).map(JSON.parse)) {
          assert.ok(Number.isInteger(row.pid) && row.pid > 1 && Number.isFinite(row.started));
          const inspect = () => { const r = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(row.pid)], {cwd: REPO, env, encoding: 'utf8', timeout: 15_000}); assert.equal(r.status, 0, r.stderr); return JSON.parse(r.stdout); };
          if (inspect().started !== row.started) continue;
          const stopped = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop', String(row.pid), String(row.started)], {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
          assert.equal(stopped.status, 0, stopped.stderr); assert.notEqual(inspect().started, row.started);
        }
        if (snapshot === previous && fs.readFileSync(owners, 'utf8') === snapshot) break;
        previous = snapshot; assert.ok(pass < 3, 'owned process registry stopped changing');
      }
    }],
        ['check unrecorded fixture readers', async () => {
          if (!ownsWarmHost) return;
          await until('no unrecorded Python process tied to the owned fixture', () => {
            const found = spawnSync(python(), [path.join(REPO, 'tests/host_reader_cleanup.py'), readerLog, root],
              {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
            assert.equal(found.status, 0, found.stderr);
            return JSON.parse(found.stdout).running.length === 0;
          }, 15_000);
        }],
      ], () => fs.rmSync(root, {recursive: true, force: true, maxRetries: 3}), failure || (!passed ? new Error('editor test did not complete') : undefined));
    } catch (error) {
      console.error(`Preserved SemVer editor fixture at ${root}`);
      throw error;
    }
  }

});
