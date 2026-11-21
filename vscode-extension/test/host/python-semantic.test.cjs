// An offline task is approved and completed through the real VS Code extension.
const assert = require('node:assert/strict');
const { createHash, randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { openHistoryRow } = require('./open-history.cjs');
const { privateHostEnvironment, ownedWarmHostSteps, settleFixture } = require('../owned-fixture-cleanup.cjs');
const { sameApproval, settledAfterApproval } = require('../python-semantic-transition.cjs');

const EXTENSION = path.resolve(__dirname, '../..');
const REPO = path.resolve(EXTENSION, '..');
const python = process.env.AUTOCODE_HOST_PYTHON || (fs.existsSync(path.join(REPO, '.venv/bin/python')) ? path.join(REPO, '.venv/bin/python') : 'python3');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const canonical = (value) => {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
};
const digest = (value) => createHash('sha256').update(value).digest('hex');

async function until(label, probe, timeout = 120_000) {
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

test('real editor completes an offline task with exact multiline element evidence', { timeout: 600_000 }, async () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'autocode-python-semantic-host-')));
  const tmp = path.join(root, 'tmp'); fs.mkdirSync(tmp);
  const reads = path.join(root, 'reads'); fs.writeFileSync(reads, '');
  const env = { ...privateHostEnvironment(process.env, root), XDG_CONFIG_HOME: path.join(root, 'user-config'), TMPDIR: tmp,
    AUTOCODE_HOST_READS: reads, AUTOCODE_HOST_HOLD: path.join(root, 'no-hold') };
  for (const key of Object.keys(env)) {
    if (key === 'GIT_CONFIG_COUNT' || /^(GIT_CONFIG_KEY_|GIT_CONFIG_VALUE_|OPENAI_|ANTHROPIC_|KILO_API_|CODEX_API_|ZAI_API_)/.test(key)) delete env[key];
  }
  assert.equal(env.HOME, process.env.HOME, 'inherit operator HOME');
  assert.equal(env.CODEX_HOME, process.env.CODEX_HOME, 'inherit operator CODEX_HOME');
  let app, win, fixture, status, editorPid, editorStarted, testError, passed = false;
  let phase = 'preparing offline task';
  const cli = path.join(REPO, 'tools/autocode.py');
  const invokeHost = (action) => spawnSync(python, [cli, 'serve', action, '--json'], {
    cwd: REPO, env, encoding: 'utf8', timeout: 15_000,
  });
  try {
    const made = spawnSync(python, [path.join(REPO, 'tests/python_semantic_vscode_fixture.py'), root], {
      cwd: REPO, env, encoding: 'utf8', timeout: 300_000,
    });
    assert.equal(made.status, 0, `offline semantic fixture: ${made.stderr}`);
    fixture = JSON.parse(made.stdout);
    assert.equal(path.resolve(fixture.registry), path.resolve(root, 'registry'), 'registry belongs to this fixture');
    assert.ok(fs.lstatSync(fixture.registry).isDirectory(), 'private registry is a directory, not a symlink');
    assert.equal(path.resolve(fixture.env.AUTOCODE_HOME), path.resolve(root, 'registry'));
    assert.equal(path.resolve(fixture.env.XDG_CONFIG_HOME), path.resolve(root, 'user-config'));
    assert.equal(path.resolve(fixture.env.TMPDIR), path.resolve(tmp));
    Object.assign(env, fixture.env);
    assert.equal(env.HOME, process.env.HOME);
    assert.equal(env.CODEX_HOME, process.env.CODEX_HOME);
    const reviewed = JSON.parse(fs.readFileSync(fixture.plan_review, 'utf8'));
    assert.match(reviewed.review, /Operating agent review/);
    const userData = path.join(root, 'user-data'); fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
    fs.writeFileSync(path.join(userData, 'User/settings.json'), JSON.stringify({
      'autocode.cliPath': python, 'autocode.cliArgs': [path.join(__dirname, 'held_cli.py')], 'autocode.pollSeconds': 2,
      'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false, 'chat.disableAIFeatures': true,
      'update.mode': 'none', 'extensions.autoUpdate': false, 'telemetry.telemetryLevel': 'off',
    }));
    const { _electron } = require('playwright-core');
    phase = 'opening extension host';
    app = await _electron.launch({ executablePath: await executable(), env, args: [
      '--no-sandbox', '--disable-gpu', '--disable-extensions', '--skip-welcome', '--skip-release-notes',
      '--disable-workspace-trust', `--user-data-dir=${userData}`, `--extensions-dir=${path.join(root, 'extensions')}`,
      `--extensionDevelopmentPath=${EXTENSION}`, fixture.workspace,
    ] });
    editorPid = app.process().pid;
    const identified = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'identity', String(editorPid)], {
      cwd: REPO, env, encoding: 'utf8', timeout: 15_000,
    });
    assert.equal(identified.status, 0, identified.stderr);
    editorStarted = JSON.parse(identified.stdout).started;
    win = await app.firstWindow(); await win.locator('.monaco-workbench').waitFor({ timeout: 120_000 });
    const chat = () => until('chat', async () => {
      for (const frame of win.frames()) if (await frame.$('#chat-tabs').catch(() => null)) return frame;
      return null;
    });
    status = () => {
      const result = spawnSync(python, [cli, '--run-dir', fixture.run_dir, '--workspace', fixture.workspace, '--status', '--json'], {
        cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000,
      });
      assert.equal(result.status, 0, result.stderr);
      return JSON.parse(result.stdout).view;
    };
    await win.locator('.activitybar [aria-label^="AutoCode"]').first().click();
    await openHistoryRow(win.locator('.monaco-list-row', { hasText: 'Extend labels() to append the exact string added' }).first(), chat, 'multiline semantic fixture');
    let frame = await chat();
    await until('reviewed plan in the editor', async () => {
      frame = await chat();
      const text = await frame.evaluate(() => document.body.innerText);
      return text.includes('test_c1_added_label') && await frame.$('#take-action') ? true : null;
    });
    const before = status();
    assert.equal(before.needs.kind, 'approve_plan');
    assert.equal(before.needs.token, reviewed.token);
    assert.deepEqual(before.displayed_plan, reviewed.displayed_plan);
    assert.equal(digest(canonical(before.displayed_plan)), reviewed.plan_sha256, 'entire displayed plan is the reviewed plan');
    phase = 'approving the exact reviewed plan in the editor';
    await frame.locator('#take-action').click();
    phase = 'waiting for the public task outcome';
    let transitioned = false;
    const after = await until('a settled public task outcome', () => {
      const current = status();
      if (!sameApproval(current, before)) transitioned = true;
      return settledAfterApproval(current, before, transitioned) ? current : null;
    }, 300_000);
    assert.equal(after.status, 'TASK_COMPLETE', JSON.stringify({ status: after.status, reason: after.stop_reason, needs: after.needs }));
    assert.equal(after.delivery?.verified_complete, true);
    const assessment = after.verification?.changed_code_evidence;
    assert.equal(assessment?.status, 'PASS');
    const obligation = assessment.obligations.find((row) => row.path === 'labels.py' && row.line === 28);
    assert.ok(obligation, 'the changed multiline child has an exact public obligation');
    assert.equal(obligation.sha256, fixture.source_sha256);
    assert.equal(obligation.source_line, "'added',");
    assert.equal(obligation.status, 'tested');
    assert.equal(obligation.mutation_demonstrated, true);
    assert.deepEqual(obligation.mutation_outcomes, ['killed']);
    const mutation = after.evidence?.mutation_check?.files?.find((row) => row.path === 'labels.py');
    const child = mutation?.mutants?.find((row) => row.line === 28 && row.operator === 'none_element');
    assert.equal(child?.end_line, 28);
    assert.equal(child?.outcome, 'killed');
    assert.equal(child?.source_sha256, fixture.source_sha256);
    assert.deepEqual(child?.failed_tests, ['test_added.Added.test_c1_added_label']);
    assert.deepEqual(child?.evidence, obligation.evidence);
    for (const [name, expected] of [['labels.py', fixture.source_sha256], ['test_added.py', fixture.test_sha256],
      ['test_existing.py', fixture.existing_sha256]]) {
      assert.equal(digest(fs.readFileSync(path.join(fixture.workspace, name))), expected, `${name} retains its reviewed bytes`);
    }
    phase = 'observing the completed editor view';
    await until('the editor to show the verified task outcome', async () => {
      frame = await chat();
      const result = frame.locator('#finish-summary .result');
      return await result.count() && (await result.innerText()).trim() === `✓ ${after.progress.headline}` ? true : null;
    });
    passed = true;
  } catch (error) {
    testError = error;
    console.error(`Python semantic host failed during ${phase}: ${error.stack || error.message}`);
    if (win) {
      fs.mkdirSync(path.join(EXTENSION, '.vscode-test'), { recursive: true });
      await win.screenshot({ path: path.join(EXTENSION, '.vscode-test/host-python-semantic.png'), timeout: 5_000 }).catch(() => {});
    }
  } finally {
    try {
      await settleFixture([['stop owned run if offered', () => {
        if (passed || !status) return;
        const stopNow = status().pause?.stop_now?.next_command;
        if (!Array.isArray(stopNow)) return;
        const args = stopNow.map((arg) => arg === 'REQUEST_ID' ? randomUUID() : arg);
        const stopped = spawnSync(python, [cli, ...args], { cwd: fixture.workspace, env, encoding: 'utf8', timeout: 30_000 });
        assert.equal(stopped.status, 0, stopped.stderr);
      }], ['close owned editor', async () => {
        if (!app) return;
        let timer;
        const timedOut = await Promise.race([app.close().then(() => false, () => true),
          new Promise((resolve) => { timer = setTimeout(() => resolve(true), 5_000); })]);
        clearTimeout(timer);
        if (timedOut) {
          assert.ok(editorPid && Number.isFinite(editorStarted), 'owned editor identity is known');
          const stopped = spawnSync(python, [path.join(REPO, 'tests/status_retry_host_cleanup.py'), 'stop', String(editorPid), String(editorStarted)], {
            cwd: REPO, env, encoding: 'utf8', timeout: 15_000,
          });
          assert.equal(stopped.status, 0, stopped.stderr);
        }
      }], ['check recorded readers', async () => {
        await until('recorded fixture readers to exit', () => {
          const checked = spawnSync(python, [path.join(REPO, 'tests/host_reader_cleanup.py'), reads], {
            cwd: REPO, env, encoding: 'utf8', timeout: 15_000,
          });
          assert.equal(checked.status, 0, checked.stderr);
          return JSON.parse(checked.stdout).running.length === 0;
        }, 15_000);
      }], ...ownedWarmHostSteps(true, () => {
        const stopped = invokeHost('--stop'); assert.equal(stopped.status, 0, stopped.stderr);
      }, () => {
        const checked = invokeHost('--status'); assert.equal(checked.status, 0, checked.stderr);
        assert.equal(JSON.parse(checked.stdout).running, false, 'private source host exited');
      }), ['check unrecorded owned readers', async () => {
        await until('no Python reader tied to the owned fixture', () => {
          const checked = spawnSync(python, [path.join(REPO, 'tests/host_reader_cleanup.py'), reads, root], {
            cwd: REPO, env, encoding: 'utf8', timeout: 15_000,
          });
          assert.equal(checked.status, 0, checked.stderr);
          return JSON.parse(checked.stdout).running.length === 0;
        }, 15_000);
      }]], () => fs.rmSync(root, { recursive: true, force: true, maxRetries: 3 }),
      testError || (!passed ? new Error('editor test did not complete') : undefined));
    } catch (error) {
      console.error(`Preserved Python semantic host fixture at ${root}`);
      throw error;
    }
  }
});
