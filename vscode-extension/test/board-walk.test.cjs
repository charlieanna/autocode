// The Tasks board against the real `autocode` CLI: the extension's own board code and webview, every read
// and action a real subprocess, two GitHub issues served by a loopback fake and run on the offline fixture
// provider. Set AUTOCODE_WALK_CLI (the interpreter) and AUTOCODE_WALK_SCRIPT (tools/autocode.py of a
// checkout) to run it; it is skipped otherwise, as live-walk.test.cjs is.
//
// It walks what a person does: open the board, press "Run GitHub issues…", tick two issues, keep one
// working at a time, press Start, and watch both arrive under "Needs you" as their own runs, each one's
// button opening its chat. A new chat's run mode starts with Requirements, so each issue first stops to
// confirm what it understood, as any chat does.
const assert = require('node:assert/strict');
const { test, after } = require('node:test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { editor, flush } = require('./helpers/editor.cjs');

const CLI = process.env.AUTOCODE_WALK_CLI;
const SCRIPT = process.env.AUTOCODE_WALK_SCRIPT;
const skip = !CLI || !SCRIPT ? 'set AUTOCODE_WALK_CLI and AUTOCODE_WALK_SCRIPT to walk against a real AutoCode' : false;

const BRIEF = "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
  + "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
  + "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
  + 'Python standard library only.';
// The offline fixture provider's routes (tests/test_taskrun.py FIXTURE_OPTIONS).
const FIXTURE_OPTIONS = ['--evidence-provenance', 'fake', '--engine', 'codex', '--task-planner-model', 'gpt-6-astra',
  '--builder-model', 'gpt-5.6-terra', '--tester-model', 'gpt-5.6-sol', '--completion-model', 'gpt-6-astra',
  '--planner-model', 'gpt-5.6-sol', '--plan-reviewer-model', 'gpt-6-astra'];

async function waitFor(ok, ms = 30000) {
  const until = Date.now() + ms;
  while (!ok() && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 100));
  return ok();
}

function issue(number, title) {
  return { number, title, body: BRIEF, state: 'open', html_url: `https://github.com/acme/widgets/issues/${number}`, labels: [] };
}

test('the board starts two GitHub issues, one working at a time, and shows each waiting for the person', { skip, timeout: 600_000 }, async (t) => {
  const issues = { 7: issue(7, 'Add a greeting CLI'), 8: issue(8, 'Add a greeting CLI too') };
  const server = http.createServer((request, response) => {
    const route = new URL(request.url, 'http://x').pathname;
    const one = /^\/repos\/acme\/widgets\/issues\/(\d+)$/.exec(route);
    const body = route === '/repos/acme/widgets/issues' ? Object.values(issues) : one ? issues[one[1]] : undefined;
    response.writeHead(body ? 200 : 404, { 'content-type': 'application/json' });
    response.end(JSON.stringify(body ?? { message: 'Not Found' }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  after(() => server.close());

  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'board-walk-')));
  after(() => { if (!process.env.AUTOCODE_WALK_KEEP) fs.rmSync(root, { recursive: true, force: true }); });
  const project = path.join(root, 'project');
  fs.mkdirSync(project);
  const git = (...args) => execFileSync('git', ['-C', project, ...args], { stdio: 'pipe' });
  git('init', '-q', '-b', 'main');
  git('config', 'user.email', 'walk@test');
  git('config', 'user.name', 'Walk');
  git('commit', '-q', '--allow-empty', '-m', 'base');
  git('remote', 'add', 'origin', 'https://github.com/acme/widgets.git');
  const bin = path.join(root, 'bin');
  fs.mkdirSync(bin);
  fs.copyFileSync(path.join(path.dirname(SCRIPT), 'live_fixture_provider.py'), path.join(bin, 'codex'));
  fs.chmodSync(path.join(bin, 'codex'), 0o755);
  const saved = { ...process.env };
  Object.assign(process.env, {
    PATH: `${bin}${path.delimiter}${path.dirname(CLI)}${path.delimiter}${process.env.PATH}`,
    AUTOCODE_HOME: path.join(root, 'registry'),
    AUTOCODE_PROJECT_INDEX_HOME: path.join(root, 'index'),
    AUTOCODE_GITHUB_API: `http://127.0.0.1:${server.address().port}`,
    GITHUB_TOKEN: 't',
    GIT_AUTHOR_NAME: 'Walk', GIT_AUTHOR_EMAIL: 'walk@test', GIT_COMMITTER_NAME: 'Walk', GIT_COMMITTER_EMAIL: 'walk@test',
    NO_PROXY: '127.0.0.1,localhost', no_proxy: '127.0.0.1,localhost',
  });
  delete process.env.OPENAI_API_KEY; delete process.env.CODEX_API_KEY; // Codex refuses to start beside an API key
  after(() => { process.env = saved; });

  const host = editor();
  const logged = [];
  host.log.appendLine = (line) => logged.push(line);
  host.log.append = (chunk) => logged.push(chunk);
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  host.config.set('extraOptions', FIXTURE_OPTIONS);
  host.config.set('pollSeconds', 1);
  host.vscode.workspace.workspaceFolders = [{ uri: { fsPath: project }, name: 'project' }];
  const opened = [];
  host.vscode.commands.executeCommand = async (name, ...args) => { opened.push([name, ...args]); };
  const { TasksBoard } = host.load('board.js');
  const board = new TasksBoard(host.workspaceState, host.log, () => project);
  t.after(() => board.dispose());
  await board.show();
  const panel = host.panels.at(-1);
  const dom = new JSDOM(panel.webview.html, { runScripts: 'dangerously', beforeParse(window) {
    window.acquireVsCodeApi = () => ({ postMessage: (message) => panel.webview.receive(message) });
  } });
  panel.webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  t.after(() => dom.window.close());
  const document = dom.window.document;
  const text = () => document.querySelector('.page').textContent;
  const buttons = () => [...document.querySelectorAll('button')];
  const last = () => panel.webview.messages.at(-1) ?? {};
  await flush();

  assert.ok(await waitFor(() => text().includes('Nothing needs you.')), `the empty board: ${text()} ${last().error}`);
  buttons().find((b) => b.textContent === 'Run GitHub issues…').click();
  assert.ok(await waitFor(() => text().includes('Run issues from acme/widgets')), `the picker: ${last().error}`);
  const boxes = [...document.querySelectorAll('.picker input[type=checkbox]')];
  assert.equal(boxes.length, 2);
  for (const box of boxes) { box.checked = true; box.dispatchEvent(new dom.window.Event('change')); }
  const limit = document.querySelector('.picker input[type=number]');
  limit.value = '1';
  limit.dispatchEvent(new dom.window.Event('input'));
  const start = buttons().find((b) => b.textContent === 'Start 2 issues');
  assert.ok(start && !start.disabled, 'Start names how many issues it starts');
  start.click();

  // Each issue runs until Requirements asks the person to confirm what it understood, which frees its slot
  // for the next one.
  const answering = () => buttons().filter((b) => b.textContent === 'Answer').length;
  assert.ok(await waitFor(() => answering() === 2, 420_000),
    `both issues wait for the person: ${text()} ${last().error ?? ''}`);
  const board_ = document.querySelector('#board').textContent;
  assert.ok(board_.includes('#7') && board_.includes('#8'), 'each run is shown with its issue number');
  assert.ok(board_.includes('autocode/issue-7-') && board_.includes('autocode/issue-8-'), `each on its own branch: ${board_}\n${logged.join('\n').slice(-3000)}`);
  // The run is on the board as soon as it exists; its start returns once Requirements has asked.
  const states = () => JSON.parse(execFileSync(CLI, [SCRIPT, 'issue', 'queue', 'status', '--project', project, '--json'],
    { env: process.env, encoding: 'utf8' })).entries.map((entry) => entry.state).join(' ');
  assert.ok(await waitFor(() => states() === 'started started', 120_000), states());
  assert.ok(await waitFor(() => !document.querySelector('#board').textContent.includes('Queued')), 'nothing is left queued');

  buttons().find((b) => b.textContent === 'Answer').click();
  await flush();
  const [name, run] = opened.at(-1);
  assert.equal(name, 'autocode.openRun');
  assert.ok(run.workspace.startsWith(path.join(project, '.autocode', 'issues', 'acme-widgets-')), run.workspace);
  assert.ok(fs.existsSync(path.join(run.runDir, 'state.json')), 'the button opens that run’s own chat');
});
