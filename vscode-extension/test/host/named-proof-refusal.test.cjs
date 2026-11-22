// Offline public plan approval and skipped-base proof refusal through a real Extension Host.
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const {openHistoryRow} = require('./open-history.cjs');
const {settleFixture} = require('../owned-fixture-cleanup.cjs');
const {afterApproval} = require('../python-semantic-transition.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(what, probe, timeout = 120_000) {
  const deadline = Date.now() + timeout;
  let last;
  do {
    last = await probe(); if (last) return last;
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

for (const mode of ['skip', 'missing']) test(`editor refuses ${mode} base named proof`, {timeout: 600_000, skip: process.platform !== 'linux'}, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'autocode-named-proof-host-'));
  const tempDir = path.join(root, 'tmp'); fs.mkdirSync(tempDir);
  const reads = path.join(root, 'reads'); fs.writeFileSync(reads, '');
  const env = {...process.env, TMPDIR: tempDir, AUTOCODE_SOURCE_HOST: '0'};
  for (const key of Object.keys(env)) {
    if (key === 'GIT_CONFIG_COUNT' || /^(GIT_CONFIG_KEY_|GIT_CONFIG_VALUE_|OPENAI_|ANTHROPIC_|KILO_API_|CODEX_API_|ZAI_API_|AWS_|AZURE_|GOOGLE_)/.test(key) || /(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$/.test(key)) delete env[key];
  }
  let fixture, app, win, editorPid, editorStarted, testError, passed = false;
  function status() {
    const result = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), '--workspace', fixture.workspace,
      '--run-dir', fixture.runDir, '--status', '--json'], {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 40_000});
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout).view;
  }
  try {
    const made = spawnSync(python(), [path.join(REPO, 'tests/named_proof_vscode_fixture.py'), path.join(root, 'run'), mode],
      {cwd: REPO, env, encoding: 'utf8', timeout: 300_000});
    assert.equal(made.status, 0, `offline named-proof fixture failed:\n${made.stderr}`);
    fixture = JSON.parse(made.stdout); Object.assign(env, fixture.env);
    assert.equal(env.HOME, process.env.HOME); assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
    assert.equal(env.AUTOCODE_SOURCE_HOST, '0', 'fixture does not launch a shared warm host');
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), {recursive: true});
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python(), 'autocode.cliArgs': [path.join(REPO, 'tools/autocode.py')], 'autocode.pollSeconds': 2,
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
    const identity = spawnSync(python(), [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)],
      {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
    assert.equal(identity.status, 0, identity.stderr); editorStarted = JSON.parse(identity.stdout).started;
    win = await app.firstWindow(); await win.locator('.monaco-workbench').waitFor({timeout: 120_000});
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    await openHistoryRow(win.locator('.monaco-list-row', {hasText: 'Add the new feature returning 42'}).first(), chat, 'named-proof refusal');
    let frame = await chat();
    await until('the reviewed plan action', async () => {
      frame = await chat(); const text = await frame.evaluate(() => document.body.innerText);
      return text.includes('The new feature returns 42') && await frame.$('#take-action') ? true : null;
    });
    await frame.locator('details.plan-evidence > summary').click();
    await until('the full reviewed criterion in the editor', async () => {
      frame = await chat(); const details = frame.locator('details.plan-evidence[open]');
      return await details.count() && (await details.innerText()).includes('import app; assert app.new() == 42') ? true : null;
    });
    const before = status(); const review = JSON.parse(fs.readFileSync(fixture.planReview, 'utf8'));
    assert.equal(before.needs.kind, 'approve_plan');
    assert.equal(before.needs.token, fixture.planToken);
    assert.equal(before.displayed_plan.token, fixture.planToken);
    assert.equal(review.token, fixture.planToken);
    assert.deepEqual(before.displayed_plan, review.plan, 'the operating agent reviewed this complete exact plan');
    assert.match(review.review, /Operating agent review/);
    await frame.locator('#take-action').click();
    const isSettled = afterApproval(before);
    const outcome = await until('the unverified task outcome', () => {
      const view = status();
      const settled = isSettled(view);
      if (settled && view.recovery?.outcome?.result === 'could_not_finish') return {view};
      const kind = view.needs?.kind;
      if (settled) {
        return {unexpected: {status: view.status, needs: kind, reason: view.stop_reason,
          continuing: view.progress?.continuing, controller: view.controller?.kind, recovery: view.recovery?.category,
          outcome: view.recovery?.outcome?.result, proof: view.evidence?.regression_proof?.verdict}};
      }
      return null;
    }, 480_000);
    assert.equal(outcome.unexpected, undefined, `unexpected public outcome: ${JSON.stringify(outcome.unexpected)}`);
    const after = outcome.view;
    fs.writeFileSync(path.join(root, 'final-public-view.json'), JSON.stringify(after, null, 2));
    assert.notEqual(after.delivery?.verified_complete, true, 'skipped base proof did not deliver');
    const proof = after.evidence?.regression_proof;
    assert.equal(proof?.verdict, 'FAIL');
    assert.deepEqual(proof.fail_to_pass, []);
    const finding = (after.evidence?.findings || []).find((row) => row.source === 'runner' &&
      typeof row.evidence === 'string' && row.evidence.startsWith('Runner regression proof receipt: '));
    assert.ok(finding, 'the public view names the genuine failed proof receipt');
    const receiptPath = fs.realpathSync(finding.evidence.slice('Runner regression proof receipt: '.length));
    assert.ok(receiptPath.startsWith(fs.realpathSync(fixture.runDir) + path.sep), 'receipt belongs to the owned run');
    const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'));
    assert.equal(receipt.verdict, 'FAIL');
    assert.equal(receipt.source_revision, proof.source_revision);
    assert.deepEqual(receipt.fail_to_pass, []);
    const id = 'test_feature::test_new';
    const base = receipt.checks.regression_on_base.results;
    if (mode === 'skip') assert.ok(base.skipped.includes(id), 'real base pytest skipped the candidate ID');
    else {
      assert.ok(base.passed.includes('test_feature::test_present'), 'real base pytest ran the sibling');
      assert.ok(!base.skipped.includes(id) && !base.passed.includes(id), 'candidate ID was absent on base');
    }
    assert.ok(!base.failed.includes(id));
    assert.ok(receipt.checks.regression_on_candidate.results.passed.includes(id));
    assert.ok(receipt.not_run_on_base.includes(id));
    assert.ok(after.recovery.retained.includes('attempt'), 'failed and repair attempts remain recorded');
    assert.ok((after.recovery.outcome.evidence.rounds || []).length >= 1, 'real repair history is retained');
    console.log('Named proof negative evidence', JSON.stringify({mode, verdict: receipt.verdict, source_revision: receipt.source_revision, base, candidate: receipt.checks.regression_on_candidate.results, fail_to_pass: receipt.fail_to_pass, recovery_rounds: after.recovery.outcome.evidence.rounds.length}));
    await until('the editor to display the unverified outcome', async () => {
      frame = await chat(); const text = await frame.evaluate(() => document.body.innerText);
      return text.includes('could not finish this task') ? true : null;
    });
    assert.equal(await frame.$('#take-action'), null, 'the stopped task offers no false completion');
    passed = true;
  } catch (error) { testError = error; }
  try {
    await settleFixture([
      ['capture failed editor', async () => {
        if (passed || !win) return;
        await win.screenshot({path: path.join(EXTENSION, `.vscode-test/host-named-proof-refusal-${mode}.png`), timeout: 5_000});
      }],
      ['request named stop', () => {
        if (passed || !fixture) return;
        const command = status().pause?.stop_now?.next_command;
        if (!Array.isArray(command)) return;
        const argv = command.map((arg) => arg === 'REQUEST_ID' ? randomUUID() : arg);
        const result = spawnSync(python(), [path.join(REPO, 'tools/autocode.py'), ...argv],
          {cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000});
        assert.equal(result.status, 0, result.stderr);
      }],
      ['close owned editor', async () => {
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
      }],
      ['check owned CLI processes', async () => {
        await until('owned CLI processes to exit', () => {
          const result = spawnSync(python(), [path.join(REPO, 'tests/host_reader_cleanup.py'), reads, root],
            {cwd: REPO, env, encoding: 'utf8', timeout: 15_000});
          assert.equal(result.status, 0, result.stderr);
          return JSON.parse(result.stdout).running.length === 0;
        }, 15_000);
      }],
    ], () => fs.rmSync(root, {recursive: true, force: true, maxRetries: 3}), testError);
  } catch (error) {
    console.error(`Preserved named-proof editor fixture at ${root}`);
    for (const failure of (error instanceof AggregateError ? error.errors : [error]).slice(0, 8)) {
      console.error(`Editor or cleanup failure: ${String(failure?.stack || failure).slice(0, 4000)}`);
    }
    throw error;
  }
});
