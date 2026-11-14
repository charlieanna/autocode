// A walk of a real chat against the real `autocode` CLI: the client's own code and webview,
// every read and action a real subprocess. Set AUTOCODE_WALK_CLI (the interpreter) and
// AUTOCODE_WALK_SCRIPT (tools/autocode.py of a checkout) to run it; it is skipped otherwise.
//
// The flows the walk covers are the ones only a live chat can prove together: a reply
// that starts streaming after a finished-only thread, the running Builder's live diff, a
// confirmation dialog that parks while a person answers it elsewhere, and the registry's own
// archive and delete over real runs.
const assert = require('node:assert/strict');
const { test, afterEach, mock } = require('node:test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const { editor, flush } = require('./helpers/editor.cjs');

const CLI = process.env.AUTOCODE_WALK_CLI;
const SCRIPT = process.env.AUTOCODE_WALK_SCRIPT;
const skip = !CLI || !SCRIPT ? 'set AUTOCODE_WALK_CLI and AUTOCODE_WALK_SCRIPT to walk against a real AutoCode' : false;

function isolateWalkProjectIndex(env = process.env) {
  if (env.AUTOCODE_PROJECT_INDEX_HOME) return null;
  const walkIndexHome = fs.mkdtempSync(path.join(os.tmpdir(), 'walk-index-'));
  env.AUTOCODE_PROJECT_INDEX_HOME = walkIndexHome;
  return () => {
    try { fs.rmSync(walkIndexHome, { recursive: true, force: true }); } catch {}
  };
}

if (!skip) {
  const cleanupWalkIndex = isolateWalkProjectIndex();
  if (cleanupWalkIndex) process.on('exit', cleanupWalkIndex);
}

const host = editor();
const { RunController } = host.load('controller.js');
const { ChatView } = host.load('chat.js');
const { StageListCache } = host.load('core/stage-cache.js');
const { RunItem } = host.load('runsView.js');
const { activate } = host.load('extension.js');

// The sealed goal contract the Builder leg works under: contract_identity.sealed() of this body.
const SEALED = 'a391f94a2611978d74b9dacc0df7a4c1f1a03f3a084aebbc19cbb2096c763066';

afterEach(() => { mock.restoreAll(); host.config.clear(); });

/** Poll until `ok` or the deadline: the real CLI takes wall-clock seconds per call. */
async function waitFor(ok, ms = 15000) {
  const until = Date.now() + ms;
  while (!ok() && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 50));
  return ok();
}

/** A git workspace with three runs: one mid-Requirements-reply, one mid-Build, one paused on a bound. */
function buildWorkspace() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'walk-')));
  const git = (...args) => execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
  git('init', '-q');
  git('config', 'user.email', 'walk@test');
  git('config', 'user.name', 'Walk');
  fs.writeFileSync(path.join(root, 'greet.py'), "print('hi')\n");
  git('add', 'greet.py');
  git('commit', '-qm', 'seed');
  const run = (name) => {
    const dir = path.join(root, '.autocode', 'runs', name);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
  };
  const base = {
    version: 3, task: 'Build a greeting CLI', workspace: root,
    iteration: 1, sessions: {}, stages: [], history: [], user_events: [], turns: [],
    created_at: '2026-11-11T08:00:00Z', settings: { provider: 'fake', engine: 'opencode' },
  };
  // A Requirements reply that is still arriving: the events file grows between reads.
  const stream = run('stream');
  fs.writeFileSync(path.join(stream, 'events.jsonl'), '');
  fs.writeFileSync(path.join(stream, 'state.json'), JSON.stringify({
    ...base, status: 'RUNNING', next_stage: 'requirements_gather',
    settings: { ...base.settings, spec_first: true },
    active_stage: { stage: 'requirements_gather', events: path.join(stream, 'events.jsonl'), active: true,
      iteration: 1, output: path.join(stream, 'attempt.log') },
  }));
  // A Builder mid-step, on a sealed approved goal contract: the view names its live diff.
  const builder = run('builder');
  const body = { task_id: 't1', summary: 'Build it', open_blocking_questions: false, acceptance_criteria: [] };
  const contract = { task_id: 't1', revision: 1, body, origin: { kind: 'chat' }, hash: SEALED };
  const approval = { kind: 'goal_approval', token: `r1:${SEALED}`, actor: 'user_cli' };
  contract.approval_status = 'approved';
  contract.approval_event = approval;
  fs.writeFileSync(path.join(builder, 'state.json'), JSON.stringify({
    ...base, status: 'RUNNING', next_stage: 'builder', goal_contract: contract,
    user_events: [approval], active_stage: { stage: 'builder', active: true, iteration: 1, output: path.join(builder, 'attempt.log') },
    code_checkpoints: [],
  }));
  fs.writeFileSync(path.join(root, 'greet.py'), "print('hello, world')\n"); // the Builder's change so far
  // A run paused on its cost bound: its Resume is a person's decision, and opens a dialog.
  const paused = run('paused');
  fs.writeFileSync(path.join(paused, 'state.json'), JSON.stringify({
    ...base, status: 'PAUSED_COST_LIMIT', next_stage: 'builder',
    settings: { ...base.settings, limits: { max_cost_usd: 2 } },
    inherited_spend: { usd: 2.37 },
    recovery: { token: 'cost:current', category: 'paused' },
  }));
  const restore = run('restore');
  writeRestoreRun(root, restore);
  return { root, stream, builder, paused, restore };
}


/** A run paused at a person's request, with one saved step whose receipt matches the real tree. */
function writeRestoreRun(root, runDir) {
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import datetime, hashlib, json, pathlib, secrets, subprocess, sys
sys.path.insert(0, ${JSON.stringify(tools)})
from autocode_code_checkpoints import tree_files
from autocode_util import digest
root = ${JSON.stringify(root)}
run = ${JSON.stringify(runDir)}
seed = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
body = {"task_id": "t1", "summary": "Build it", "open_blocking_questions": False, "acceptance_criteria": []}
contract = {"task_id": "t1", "revision": 1, "body": body, "origin": {"kind": "chat"}}
contract["hash"] = digest({k: contract[k] for k in ("task_id", "revision", "body")})
token = f"r{contract['revision']}:{contract['hash']}"
approval = {"kind": "goal_approval", "token": token, "actor": "user_cli"}
contract["approval_status"] = "approved"
contract["approval_event"] = approval
# The saved step's own commit, off the seed and without moving HEAD: greet.py as the step left it.
def git(*args, env=None, data=None):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True,
                          input=data, env=env).stdout.strip()
index = {**__import__("os").environ, "GIT_INDEX_FILE": str(pathlib.Path(run, "step.index"))}
git("read-tree", seed, env=index)
blob = git("hash-object", "-w", "--stdin", data="print('saved step')\\n")
git("update-index", "--cacheinfo", "100644," + blob + ",greet.py", env=index)
step = git("commit-tree", git("write-tree", env=index), "-p", seed, "-m", "step")
pathlib.Path(run, "step.index").unlink()
receipt = {"id": "cp-1", "commit": step, "run_dir": run, "contract_hash": contract["hash"],
           "source": {"head": seed, "files": tree_files(root, step)}}
path = pathlib.Path(run, "cp-1.json")
path.write_text(json.dumps(receipt))
state = {
  "version": 3, "task": "Build a greeting CLI", "workspace": root,
  "status": "PAUSED_REQUESTED", "iteration": 1, "sessions": {}, "stages": [], "history": [],
  "user_events": [approval], "turns": [], "next_stage": "builder",
  "created_at": "2026-11-11T08:00:00Z", "settings": {"provider": "fake", "engine": "opencode"},
  "goal_contract": contract,
  "code_checkpoints": [{
    "id": "cp-1", "label": "Fixture step", "at": "2026-11-11T08:30:00Z", "changed_files": ["greet.py"],
    "available": True, "receipt": str(path), "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "commit": step, "recorded_check": {"verdict": "PASS"},
  }],
}
pathlib.Path(run, "state.json").write_text(json.dumps(state, indent=2))
`], { stdio: 'pipe' });
}


/** The walk's own provider config: a scripted tool whose reports bind to real capture receipts. */
function writeWalkProvider() {
  const xdg = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'walk-xdg-')));
  const providers = path.join(xdg, 'autocode', 'providers');
  fs.mkdirSync(providers, { recursive: true });
  const tools = path.dirname(SCRIPT);
  const wrapper = path.join(xdg, 'walk_provider.py');
  fs.writeFileSync(wrapper, String.raw`#!/usr/bin/env python3
"""Walk provider: fake_codex's reports, with checks bound to real capture receipts."""
import json, shlex, subprocess, sys
from pathlib import Path

FAKE = ${JSON.stringify(path.join(tools, 'fake_codex.py'))}
AUTOCODE = ${JSON.stringify([CLI, SCRIPT])}
args = sys.argv[1:]
report_path = Path(args[args.index("-o") + 1])
proc = subprocess.run([sys.executable, FAKE, *args], capture_output=True, text=True)
sys.stdout.write(proc.stdout)
sys.stderr.write(proc.stderr)
if proc.returncode != 0 or not report_path.exists():
    raise SystemExit(proc.returncode)
report = json.loads(report_path.read_text())
workspace = Path.cwd()
receipts = {}
n = 0
for check in report.get("checks") or []:
    ref = check.get("evidence_ref")
    if not (isinstance(ref, str) and ref.startswith("event:")):
        continue
    command = check.get("command")
    if not command:
        continue
    if command not in receipts:
        n += 1
        out = workspace / ".autocode" / "evidence" / f"walk-check-{n}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        cap = subprocess.run([*AUTOCODE, "capture", "--output", str(out), "--", *shlex.split(command)],
                             capture_output=True, text=True, cwd=workspace)
        if cap.returncode != 0 or not out.exists():
            sys.stderr.write("capture failed: " + cap.stderr[-400:] + "\n")
            raise SystemExit(1)
        receipts[command] = out
    receipt = json.loads(receipts[command].read_text())
    check["evidence_ref"] = str(receipts[command].relative_to(workspace))
    check["exit_code"] = receipt["exit_code"]
    check["command"] = shlex.join(receipt["command"])

def rewrite(value):
    if isinstance(value, dict):
        return {k: rewrite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v) for v in value]
    if value == "event:check" and receipts:
        return str(next(iter(receipts.values())).relative_to(workspace))
    return value

report = rewrite(report)
report_path.write_text(json.dumps(report))
`);
  const roles = {
    task_planner: 'fake/plan', planner: 'fake/plan', plan_reviewer: 'fake/review',
    builder: 'fake/build', tester: 'fake/test', completion: 'fake/complete',
  };
  const toml = ['name = "fake"', 'output = "report_file"',
    `command = ${JSON.stringify([CLI, wrapper, '--output-schema', '{schema}', '-o', '{report}', '--model', '{model}', '-'])}`,
    '', '[roles]'];
  for (const [role, model] of Object.entries(roles)) toml.push(`${role} = { model = "${model}", effort = "medium" }`);
  fs.writeFileSync(path.join(providers, 'fake.toml'), toml.join('\n') + '\n');
  return { xdg, roles };
}

/** A run in the full shape a resume's build loop reads: paused at a person's request, or mid-step (`working`). */
function writeResumeRun(root, xdg, roles, working = false) {
  const runDir = path.join(root, '.autocode', 'runs', 'resume');
  fs.mkdirSync(runDir, { recursive: true });
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import json, os, pathlib, subprocess, sys
sys.path.insert(0, ${JSON.stringify(tools)})
from autocode_util import digest, criteria_definition
from autocode_providers import resolve
import goal_fixtures
working = ${working ? 'True' : 'False'}
root = ${JSON.stringify(root)}
run = ${JSON.stringify(runDir)}
seed = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
body = goal_fixtures.body()
contract = {"task_id": "t1", "revision": 1, "body": body, "origin": {"kind": "chat"}}
contract["hash"] = digest({k: contract[k] for k in ("task_id", "revision", "body")})
approval = {"kind": "goal_approval", "token": f"r1:{contract['hash']}", "actor": "user_cli"}
contract.update(approval_status="approved", approval_event=approval)
acceptance = [{"id": row["id"], "criterion": row["criterion"], "status": "unverified", "evidence": ""}
              for row in body["acceptance_criteria"]]
roles = ${JSON.stringify(roles)}
provider = resolve("fake")
ident = provider.local_settings(pathlib.Path(os.path.realpath(root)))
state = {
  "version": 3, "task": "Build a greeting CLI", "workspace": os.path.realpath(root),
  "status": "RUNNING" if working else "PAUSED_REQUESTED", "iteration": 1, "sessions": {}, "stages": [], "history": [],
  "user_events": [approval], "turns": [], "next_stage": "builder", "created_at": "2026-11-11T08:00:00Z",
  "current_task": {"id": "t1", "milestone_id": "M1", "kind": "build",
                   "affected_paths": ["greet.py", "test_greeting.py"],
                   "requirements": ["Print Hello, NAME for a nonempty name"],
                   "acceptance_criteria": ["C1"],
                   "validation_plan": ["Execute greeting and invalid-input regression checks"]},
  "acceptance_criteria": acceptance,
  "criteria_revision": digest(criteria_definition(acceptance)),
  "settings": {
    "provider": "fake", "engine": "opencode",
    "roles": {r: {"model": m, "reasoning_effort": "medium"} for r, m in roles.items()},
    "limits": {"iteration_ceiling": 20, "max_seconds": 3600, "max_cost_usd": 2.0,
               "idle_timeout_seconds": 300, "tool_timeout_seconds": 1800, "no_progress_batches": 3},
    "transport_identity": ident, "transport_identities": {"fake": ident},
  },
  "goal_contract": contract, "code_checkpoints": [], "contract_history": [], "planning_history": [],
}
if working:
    state["intervention_capability"] = {"supported": True}
    state["active_stage"] = {"stage": "builder", "active": True, "iteration": 1,
                             "output": str(pathlib.Path(run) / "attempt.log")}
pathlib.Path(run, "state.json").write_text(json.dumps(state, indent=2))
`], { stdio: 'pipe', env: { ...process.env, XDG_CONFIG_HOME: xdg } });
  return runDir;
}


/** The step boundary a controller makes when the step it drives finishes: its save commits the queued pause. */
function landPause(runDir) {
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import datetime, json, pathlib, sys
sys.path.insert(0, ${JSON.stringify(tools)})
import autocode_stop as stop_policy
run = pathlib.Path(${JSON.stringify(runDir)})
state = json.loads((run / "state.json").read_text())
state.pop("active_stage", None)
stop_policy.consume(state, run, pathlib.Path(state["workspace"]),
                     write_json=lambda p, v: p.write_text(json.dumps(v, indent=2)),
                     now=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
print(state.get("status"))
`], { stdio: 'pipe' });
}


/** A supervised Builder: live owner/keeper/provider processes and the supervision record naming them. */
function claimSupervision(runDir) {
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import datetime, json, pathlib, secrets, subprocess, sys
sys.path.insert(0, ${JSON.stringify(tools)})
import autocode_process as processes
run = ${JSON.stringify(runDir)}
def spawn():
    p = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return processes.identity(processes.process_table({p.pid})[p.pid])
owner, keeper, provider = spawn(), spawn(), spawn()
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
receipt = {"schema": 1, "nonce": secrets.token_hex(16), "owner": owner, "keeper": keeper, "provider": provider,
           "phase": "armed", "cause": None, "cleanup_error": None, "observed_at": now,
           "processes": [owner, keeper, provider]}
path = pathlib.Path(run, "supervision.json")
path.write_text(json.dumps(receipt))
state = json.loads(pathlib.Path(run, "state.json").read_text())
state["active_stage"]["supervision"] = {"schema": 1, "nonce": receipt["nonce"], "receipt": str(path),
                                        "owner": owner, "keeper": keeper, "provider": provider}
pathlib.Path(run, "state.json").write_text(json.dumps(state, indent=2))
`], { stdio: 'pipe' });
}

/** A live controller receipt for a run, so the view's liveness says a stage really works. */
function claimController(runDir) {
  const tools = path.dirname(SCRIPT);
  execFileSync(CLI, ['-c', `
import datetime, json, pathlib, secrets, subprocess, sys
sys.path.insert(0, ${JSON.stringify(tools)})
import autocode_process as processes
p = subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
row = processes.process_table({p.pid})[p.pid]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
receipt = {
  "schema": 1, "id": secrets.token_hex(16), "identity": processes.identity(row),
  "started_at": now, "last_checkpoint_at": now, "last_stage": "builder",
  "checkpoint": secrets.token_hex(32), "disposition": "running",
  "exit_code": None, "exited_at": None, "reason": None,
}
pathlib.Path(${JSON.stringify(runDir)}, "controller-owner.json").write_text(json.dumps(receipt))
`], { stdio: 'pipe' });
}

/** A chat against the real CLI: no controller mocks at all, just the webview shell. */
async function openWalkChat(t, root, runDir) {
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  const controller = new RunController(host.log);
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const view = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  const chat = new ChatView(controller, { get: () => undefined, update: async () => {} },
    { pickWorkspace: async () => root, confirmStart: async () => true, changed() {}, selected() {} });
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(view);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.acquireVsCodeApi = () => ({ postMessage: (message) => receive(message), getState: () => undefined, setState() {} });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();
  chat.show({ workspace: root, runDir });
  await chat.refresh();          // the first real --status read lands here
  await flush();
  return { chat, dom, window: dom.window, document: dom.window.document };
}

test('walk: a reply that starts streaming shows at once and grows in place', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  const ui = await openWalkChat(t, root, stream);
  const live = () => ui.document.querySelector('#messages .message.streaming');
  assert.equal(live(), null, 'the thread starts without a provisional row');
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will"}' } }) + '\n');
  await ui.chat.refresh();
  await flush();
  assert.ok(live(), 'a reply that starts streaming after a finished-only thread is shown at once');
  assert.equal(live().querySelector('.message-content').textContent, 'Who will');
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will use it? Are you bui' } }) + '\n');
  await ui.chat.refresh();
  await flush();
  assert.equal(live().querySelector('.message-content').textContent, 'Who will use it? Are you bui',
    'the extract grows in place');
});

test("walk: the running Builder's live diff is offered and shows its real change", { skip }, async (t) => {
  const { root, builder } = buildWorkspace();
  claimController(builder);
  const ui = await openWalkChat(t, root, builder);
  const show = ui.document.querySelector('#working-diff');
  assert.ok(show, "the view names the Builder's live diff while it works");
  show.click();
  await waitFor(() => ui.document.querySelector('#app').textContent.includes('+print'));
  const text = ui.document.querySelector('#app').textContent;
  assert.ok(text.includes('1 file(s) changed'), text.slice(0, 200));
  assert.ok(text.includes("+print('hello, world')"), 'the diff is the real change against the checked-out commit');
});

test('walk: a confirmation that parks names itself in the chat until it is answered', { skip }, async (t) => {
  host.config.set('confirmPersonOnly', true);
  const { root, paused } = buildWorkspace();
  const ui = await openWalkChat(t, root, paused);
  // The bound is attached to the named action: the take button IS the raise.
  const field = ui.document.querySelector('[data-question="max_cost_usd"]');
  const take = ui.document.querySelector('#take-action');
  assert.ok(field && take, 'the bound holding the run names its own raise');
  assert.equal(take.textContent, 'Raise limit and continue');
  field.value = '5';
  field.dispatchEvent(new ui.window.Event('input'));
  let release;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => new Promise((resolve) => { release = resolve; }));
  take.click();
  // The confirmation follows a real --status read; wait for the dialog to open before looking.
  await waitFor(() => release);
  assert.ok(release, 'the confirmation dialog opened');
  const hint = () => ui.document.querySelector('#send-hint').textContent;
  assert.ok(hint().includes('answer the dialog that opened'), `the chat names the open dialog: ${hint()}`);
  release(undefined);
  await flush(); await flush();
  assert.ok(!hint().includes('answer the dialog that opened'), `the hint reverts once the dialog is answered: ${hint()}`);
});

test('walk: archiving and deleting a chat run the named registry commands and stop the status line', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  const home = path.join(root, 'registry-home');
  process.env.AUTOCODE_HOME = home;
  t.after(() => { delete process.env.AUTOCODE_HOME; });
  execFileSync(CLI, [SCRIPT, 'registry', 'import', root], { env: process.env, stdio: 'pipe' });
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  const controller = new RunController(host.log);
  const first = await controller.list();
  // A running chat has no cleanup to name (the core refuses it); the paused one does.
  const pausedRow = first.runs.find((row) => row.runDir.endsWith('paused'));
  assert.ok(pausedRow, 'the imported chat is in the real registry list');
  assert.ok((pausedRow.actions || []).some((a) => a.id === 'archive'), 'the registry names its cleanup');
  assert.equal((first.runs.find((row) => row.runDir.endsWith('builder')) || {}).actions?.length || 0, 0,
    'a running chat offers no cleanup');

  // Archive: the exact command the row named, and the list reflects it.
  await controller.registryAction({ workspace: pausedRow.workspace, runDir: pausedRow.runDir },
    pausedRow.actions.find((a) => a.id === 'archive'));
  // The default list hides archived chats; the archived list marks them.
  assert.equal((await controller.list()).runs.find((row) => row.runDir.endsWith('paused')), undefined,
    'an archived chat is hidden from the default list');
  const after = await controller.list(true);
  const archived = after.runs.find((row) => row.runDir.endsWith('paused'));
  assert.equal(archived && archived.archived, true, 'the archived list marks it');

  // Delete through the extension's own cleanup: its confirmation, the real command, and the
  // status line that must stop naming a chat that no longer exists.
  let statusVisible = false;
  const mockStatus = { show() { statusVisible = true; }, hide() { statusVisible = false; }, dispose() {},
    text: '', tooltip: '', command: undefined };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const deleteRow = archived;
  const item = new RunItem(deleteRow, deleteRow.workspace);
  await host.commands.get('autocode.openRun')(item);
  await waitFor(() => statusVisible);
  assert.equal(statusVisible, true, 'the status line follows the opened chat');
  t.mock.method(host.vscode.window, 'showWarningMessage', async (question, _options, button) => {
    assert.match(question, /Delete the chat/, 'the delete asks first');
    return button;
  });
  await host.commands.get('autocode.deleteRun')(item);
  await waitFor(() => !statusVisible);
  assert.equal(statusVisible, false, 'the status line stops following a chat that no longer exists');
  const gone = (await controller.list()).runs.find((row) => row.runDir.endsWith('paused'));
  assert.equal(gone, undefined, 'the deleted chat is gone from the real registry');
});

test("walk: a saved run shows its whole change for real, and no per-step buttons", { skip }, async (t) => {
  const { root, restore } = buildWorkspace();
  const ui = await openWalkChat(t, root, restore);
  const show = await waitFor(() => ui.document.querySelector('#all-changes-show'));
  assert.ok(show, 'the run offers its whole change');
  assert.equal(ui.document.querySelectorAll('[data-checkpoint-action]').length, 0,
    'no step is offered to compare or go back to (owner decision 2026-11-13)');
  fs.writeFileSync(path.join(root, 'greet.py'), "print('not saved yet')\n");
  show.click();
  await waitFor(() => ui.document.querySelector('#app').textContent.includes("+print('saved step')"));
  const text = ui.document.querySelector('#app').textContent;
  assert.match(text, /1 file\(s\) changed in this run/);
  assert.ok(text.includes("-print('hi')") && text.includes("+print('saved step')"), 'the seed against the saved step: ' + text.slice(-300));
  assert.ok(!text.includes('not saved yet'), 'the change is read from saved code, not the files on disk');
});

test('walk: closing a chat mid-reply returns every poll to the ordinary interval', { skip }, async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const { root, stream, paused } = buildWorkspace();
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will"}' } }) + '\n');
  const ui = await openWalkChat(t, root, stream);
  ui.chat.show({ workspace: root, runDir: paused });
  // Real reads take wall-clock seconds; drain on the call count, never on a mocked timer.
  const cli = host.load('core/cli.js');
  const counted = t.mock.method(cli, 'readStatus');
  const reads = () => counted.mock.callCount();
  const drain = async (ok, ms = 20000) => {
    const until = Date.now() + ms;
    while (!ok() && Date.now() < until) await new Promise((resolve) => setImmediate(resolve));
    return ok();
  };
  // Real reads take wall-clock seconds on a busy machine: wait for the count to stop
  // moving, never for a fixed pause.
  const settle = async () => {
    let last = -1;
    while (last !== reads()) { last = reads(); await drain(() => false, 800); }
  };
  // The reply grows again: an unchanged extract would quiet the pace back to the ordinary
  // interval after a few reads, which is the core's rule, not the bug being walked here.
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will use it?"} ' } }) + '\n');
  // A cycle still in flight from the setup would swallow the tick's refresh (the joinable
  // refresh is right); wait for idle and take one settled cycle that sees the growth.
  await drain(() => !ui.chat.refreshing);
  await ui.chat.refresh();
  await settle();
  const start = reads();
  t.mock.timers.tick(250);
  await drain(() => reads() > start);
  assert.ok(reads() > start, 'a growing reply polls at the streaming pace');
  // The streaming chat's tab is closed while its reply still grows.
  [...ui.document.querySelectorAll('.chat-tab .close')][0].click();
  await drain(() => !ui.chat.refreshing);
  await ui.chat.refresh();                   // a settled cycle after the close
  await settle();                            // and the counts stop moving
  t.mock.timers.tick(250); await settle();
  const settled = reads();
  t.mock.timers.tick(250);
  await settle();
  assert.equal(reads(), settled, 'a closed chat must not hold every poll at the streaming pace');
  t.mock.timers.tick(5000);
  await drain(() => reads() > settled);
  assert.ok(reads() > settled, 'the ordinary interval still polls the chat that is open');
});

test("walk: the Builder's live log streams and its named diff is still offered", { skip }, async (t) => {
  const { root, builder } = buildWorkspace();
  claimSupervision(builder);
  const events = path.join(builder, 'events.jsonl');
  const row = (item) => JSON.stringify({ type: 'item.updated', item }) + '\n';
  fs.writeFileSync(events,
    row({ type: 'command_execution', id: 'c1', command: 'python3 -m unittest -q', status: 'done' })
    + row({ type: 'file_change', id: 'f1', changes: [{ path: path.join(root, 'greet.py'), kind: 'edit' }], status: 'done' }));
  const statePath = path.join(builder, 'state.json');
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  state.active_stage.events = events;
  fs.writeFileSync(statePath, JSON.stringify(state));
  const ui = await openWalkChat(t, root, builder);
  const text = () => ui.document.querySelector('#app').textContent;
  await waitFor(() => text().includes('python3 -m unittest -q'));
  assert.ok(text().includes('edited greet.py'), 'the log shows what the Builder did: ' + text().slice(0, 200));
  assert.ok(text().includes('Builder'), 'the log names its agent');
  // The point of the walk: the named diff is offered WHILE the log streams (the log once
  // took the early return that skipped the diff offer entirely).
  const show = ui.document.querySelector('#working-diff');
  assert.ok(show, "the Builder's named diff is offered while its log streams");
  // The log grows while it works.
  fs.appendFileSync(events, row({ type: 'tool_call', id: 't1', name: 'Read', arguments: { path: 'greet.py' }, status: 'running' }));
  await ui.chat.refresh();
  await waitFor(() => text().includes('Read greet.py'));
  assert.ok(text().includes('reading files'), 'the header says what it does now');
  show.click();
  await waitFor(() => text().includes('+print'));
  assert.ok(text().includes("+print('hello, world')"), 'the diff is the real change against the checked-out commit');
});

test('walk: a CLI that dies mid-reply leaves the reply standing, says why, and recovers', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will"}' } }) + '\n');
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  let statusVisible = false;
  const mockStatus = { show() { statusVisible = true; }, hide() { statusVisible = false; }, dispose() {},
    text: '', tooltip: '', command: undefined };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const chat = host.providers.get('autocode.chat');
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  dom.window.acquireVsCodeApi = () => ({ postMessage: (message) => receive(message), getState: () => undefined, setState() {} });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  await host.commands.get('autocode.openRun')({ workspace: root, runDir: stream });
  const text = () => dom.window.document.querySelector('#app').textContent;
  const live = () => dom.window.document.querySelector('#messages .message.streaming .message-content');
  await waitFor(() => live() && live().textContent === 'Who will');
  await waitFor(() => statusVisible);
  assert.ok(mockStatus.text.includes('AutoCode'), 'the status line follows the chat: ' + mockStatus.text);

  // The CLI dies mid-reply: the run's state stops reading (a half-written state.json).
  const statePath = path.join(stream, 'state.json');
  const saved = fs.readFileSync(statePath, 'utf8');
  fs.writeFileSync(statePath, '{ not json');
  await waitFor(() => text().includes('read the latest status'), 30000);
  assert.ok(text().includes('I couldn'), 'the chat says it could not read the status');
  assert.equal(live().textContent, 'Who will', 'the reply that was arriving is left standing');
  const take = dom.window.document.querySelector('#take-action');
  const diff = dom.window.document.querySelector('#working-diff');
  assert.ok((!take || take.disabled) && (!diff || diff.disabled), 'actions are paused while the status cannot be read');
  await waitFor(() => mockStatus.text.includes('status unavailable'), 30000);
  assert.ok(mockStatus.text.includes('status unavailable'), 'the status line says so too: ' + mockStatus.text);

  // The CLI comes back: the reply resumes growing and both surfaces recover.
  fs.writeFileSync(statePath, saved);
  fs.appendFileSync(path.join(stream, 'events.jsonl'),
    JSON.stringify({ type: 'text', part: { id: 1, text: '{"assistant_reply": "Who will use it?"} ' } }) + '\n');
  await waitFor(() => live() && live().textContent.startsWith('Who will use it?'), 30000);
  assert.ok(!text().includes('read the latest status'), 'the notice clears when the status reads again');
  await waitFor(() => statusVisible && !mockStatus.text.includes('status unavailable'), 30000);
  assert.ok(mockStatus.text.includes('AutoCode'), 'the status line is back on the run: ' + mockStatus.text);
});

test('walk: the status line keeps its liveness without the poll interval, and goes loud when reads are slow', { skip }, async (t) => {
  const { root, stream } = buildWorkspace();
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  host.config.set('pollSeconds', 1);
  t.mock.timers.enable({ apis: ['setInterval'] });   // the poll interval is silenced: the round-14 wart
  let statusText = '';
  const mockStatus = {
    show() {}, hide() {}, dispose() {},
    get text() { return statusText; }, set text(val) { statusText = val; },
    tooltip: undefined, command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  await host.commands.get('autocode.openRun')({ workspace: root, runDir: stream });
  await waitFor(() => statusText.includes('AutoCode'), 30000);

  // The run changes with no interval driving the line: the line's own watchdog keeps it live.
  const statePath = path.join(stream, 'state.json');
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  state.inherited_spend = { usd: 5 };
  fs.writeFileSync(statePath, JSON.stringify(state));
  await waitFor(() => statusText.includes('$5'), 30000);
  assert.ok(statusText.includes('$5'), 'the line tracks the run with the poll interval silenced: ' + statusText);

  // Now the CLI itself is slow (a wedged read takes longer than the line's threshold):
  // the line must say it is stale rather than present the old content as current.
  const slow = path.join(root, 'slow-cli.sh');
  fs.writeFileSync(slow, '#!/bin/sh\nsleep 3\nexec "$@"\n');
  fs.chmodSync(slow, 0o755);
  host.config.set('cliPath', slow);
  host.config.set('cliArgs', [CLI, SCRIPT]);
  await waitFor(() => statusText.includes('stale'), 30000);
  assert.ok(statusText.includes('stale'), 'the line goes loud while a read is slow: ' + statusText);

  // And it comes back to the run when the reads land again.
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  await waitFor(() => statusText.includes('$5') && !statusText.includes('stale'), 30000);
  assert.ok(statusText.includes('$5'), 'the line recovers: ' + statusText);
});

test("walk: two runs racing on one project see each other's work in the live diff", { skip }, async (t) => {
  const { root, builder, restore } = buildWorkspace();
  claimSupervision(builder);
  // One project, one tree: A (the saved step) changed greet.py, B (the Builder) wrote helper.py.
  fs.writeFileSync(path.join(root, 'greet.py'), "print('A changed this')\n");
  fs.writeFileSync(path.join(root, 'helper.py'), "print('B wrote this')\n");
  const ui = await openWalkChat(t, root, restore);
  const text = () => ui.document.querySelector('#app').textContent;

  // B's own diff, in its tab, names A's work just the same.
  ui.chat.show({ workspace: root, runDir: builder });
  await ui.chat.refresh();
  const show = await waitFor(() => {
    const btn = ui.document.querySelector('#working-diff');
    return btn && !btn.disabled ? btn : false;
  });
  assert.ok(show, "B's diff is offered while it works");
  show.click();
  await waitFor(() => text().includes('+print'));
  assert.ok(text().includes('greet.py') || text().includes("A changed this"),
    "B's changes so far include A's file: " + text().slice(-200));
});

test('walk: closing a chat mid-run leaves the run working and the chat comes back to it', { skip }, async (t) => {
  const { root, builder, paused } = buildWorkspace();
  claimSupervision(builder);
  const events = path.join(builder, 'events.jsonl');
  const row = (item) => JSON.stringify({ type: 'item.updated', item }) + '\n';
  fs.writeFileSync(events, row({ type: 'command_execution', id: 'c1', command: 'python3 -m unittest -q', status: 'running' }));
  const statePath = path.join(builder, 'state.json');
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  state.active_stage.events = events;
  fs.writeFileSync(statePath, JSON.stringify(state));
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  let statusText = '';
  const mockStatus = {
    show() {}, hide() {}, dispose() {},
    get text() { return statusText; }, set text(val) { statusText = val; },
    tooltip: undefined, command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const chat = host.providers.get('autocode.chat');
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  // The webview keeps its own state (drafts) the way VS Code does.
  let saved = {};
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => receive(message),
    getState: () => saved,
    setState: (value) => { saved = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  await host.commands.get('autocode.openRun')({ workspace: root, runDir: builder });
  const text = () => dom.window.document.querySelector('#app').textContent;
  await waitFor(() => text().includes('python3 -m unittest -q'), 30000);
  await waitFor(() => statusText.includes('AutoCode'), 30000);
  // An unsent draft in the composer.
  const field = dom.window.document.querySelector('#message');
  field.value = 'A draft that was never sent';
  field.dispatchEvent(new dom.window.Event('input'));
  await flush();
  // A fresh chat as the second tab: switching to it keeps the status line on the run the
  // closed tab showed (a tab with a run of its own would take the line with it).
  await host.commands.get('autocode.startRun')();
  await waitFor(() => dom.window.document.querySelectorAll('.chat-tab').length === 2, 30000);
  const closes = dom.window.document.querySelectorAll('.chat-tab .close');
  closes[0].click();
  await waitFor(() => dom.window.document.querySelectorAll('.chat-tab').length === 1, 30000);
  assert.equal(dom.window.document.querySelectorAll('.chat-tab .close').length, 0,
    'the last tab offers no close button when there is nothing to close');

  // The run works on with no chat open on it: its log grows and its numbers move.
  fs.appendFileSync(events, row({ type: 'command_execution', id: 'c2', command: 'python3 -m pytest -q', status: 'done' }));
  const state2 = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  state2.inherited_spend = { usd: 7 };
  fs.writeFileSync(statePath, JSON.stringify(state2));
  // The window's status line keeps following the run the chat closed.
  await waitFor(() => statusText.includes('$7'), 30000);
  assert.ok(statusText.includes('$7'), 'the status line keeps following the closed chat\'s run: ' + statusText);

  // Reopening comes back to everything that survived.
  await host.commands.get('autocode.openRun')({ workspace: root, runDir: builder });
  await waitFor(() => text().includes('python3 -m pytest -q'), 30000);
  assert.ok(text().includes('python3 -m pytest -q'), "the work done while the tab was closed is there on reopening");
  const reopened = dom.window.document.querySelector('#message');
  assert.equal(reopened.value, 'A draft that was never sent', 'the unsent draft survives the close');
  await waitFor(() => {
    const btn = dom.window.document.querySelector('#working-diff');
    return btn && !btn.disabled ? btn : false;
  }, 30000);
  assert.ok(dom.window.document.querySelector('#working-diff'), "the run's named diff is still offered");
});

test('walk: pausing a working chat stops the run and shows its pause card', { skip }, async (t) => {
  const { root, builder } = buildWorkspace();
  claimSupervision(builder);
  const statePath = path.join(builder, 'state.json');
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  state.intervention_capability = { supported: true };
  fs.writeFileSync(statePath, JSON.stringify(state));
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  let statusText = '';
  const mockStatus = {
    show() {}, hide() {}, dispose() {},
    get text() { return statusText; }, set text(val) { statusText = val; },
    tooltip: undefined, command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const chat = host.providers.get('autocode.chat');
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  let saved = {};
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => receive(message),
    getState: () => saved,
    setState: (value) => { saved = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  await host.commands.get('autocode.openRun')({ workspace: root, runDir: builder });
  const text = () => dom.window.document.querySelector('#app').textContent;
  const pause = dom.window.document.querySelector('#pause-run');
  await waitFor(() => !pause.hidden && pause.textContent === 'Pause', 30000);
  assert.ok(!pause.hidden, 'the working chat offers its Pause');

  // The person pauses: the real intervention lands in the run's inbox.
  pause.click();
  await waitFor(() => dom.window.document.querySelector('#pause-run').textContent === 'Pausing after this step…', 30000);
  assert.ok(true, 'the chat says the pause waits for the step to be saved');

  // The step ends at the boundary — the write a controller makes when a step finishes.
  const state2 = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  delete state2.active_stage;
  state2.status = 'PAUSED_REQUESTED';
  fs.writeFileSync(statePath, JSON.stringify(state2));
  await waitFor(() => text().includes('Paused as you asked'), 30000);
  assert.ok(text().includes('Paused as you asked'), 'the run stops and shows its pause card: ' + text().slice(0, 200));
  const resume = dom.window.document.querySelector('#take-action');
  assert.ok(resume && !resume.disabled, 'the pause card offers its Resume');

  // A clean requested pause resumes without a second dialog, and runs the run's own command.
  let dialog = 0;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => { dialog++; return Promise.resolve('Continue'); });
  resume.click();
  const error = () => dom.window.document.querySelector('#chat-error').textContent;
  await waitFor(() => error().includes('provider config'), 30000);
  assert.equal(dialog, 0, 'the Resume needs no confirmation dialog of its own');
  assert.ok(error().includes('provider config'), 'the resume runs the named command: ' + error());
});

test('walk: the resume after a pause brings the run back to work', { skip }, async (t) => {
  const { root } = buildWorkspace();
  const { xdg, roles } = writeWalkProvider();
  process.env.XDG_CONFIG_HOME = xdg;
  t.after(() => { delete process.env.XDG_CONFIG_HOME; });
  const runDir = writeResumeRun(root, xdg, roles);
  const statePath = path.join(runDir, 'state.json');
  const state = () => JSON.parse(fs.readFileSync(statePath, 'utf8'));

  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  let statusText = '';
  const mockStatus = {
    show() {}, hide() {}, dispose() {},
    get text() { return statusText; }, set text(val) { statusText = val; },
    tooltip: undefined, command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const chat = host.providers.get('autocode.chat');
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  let saved = {};
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => receive(message),
    getState: () => saved,
    setState: (value) => { saved = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  await host.commands.get('autocode.openRun')({ workspace: root, runDir });
  const text = () => dom.window.document.querySelector('#app').textContent;

  // The paused run's card offers its Resume.
  const resume = () => dom.window.document.querySelector('#take-action');
  await waitFor(() => resume() && !resume().disabled, 30000);
  assert.ok(resume() && !resume().disabled, 'the paused chat offers its Resume: ' + text().slice(0, 200));

  // The person resumes: the named command runs with no confirmation dialog of its own.
  let dialog = 0;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => { dialog++; return Promise.resolve('Continue'); });
  resume().click();

  // The run comes back to work: its state leaves the pause and the Builder starts.
  await waitFor(() => state().status !== 'PAUSED_REQUESTED', 120000);
  assert.notEqual(state().status, 'PAUSED_REQUESTED', 'the resume takes the run out of its pause');
  assert.equal(dialog, 0, 'the Resume needs no confirmation dialog of its own');

  // The work happens for real: the Builder writes the greeting into the workspace.
  await waitFor(() => fs.readFileSync(path.join(root, 'greet.py'), 'utf8').includes('Hello, '), 180000);
  assert.ok(fs.readFileSync(path.join(root, 'greet.py'), 'utf8').includes('Hello, '),
    'the run came back and did its work: ' + fs.readFileSync(path.join(root, 'greet.py'), 'utf8'));

  // The action lands where the work stops: a person's gate, with the stages it ran recorded.
  await waitFor(() => /^(PAUSED_|WAITING_FOR_USER)/.test(state().status), 180000);
  const done = state();
  assert.ok(/^(PAUSED_|WAITING_FOR_USER)/.test(done.status),
    'the run stops at a person-facing gate, not in a silent state: ' + done.status);
  assert.ok((done.stages || []).some((row) => row.stage === 'builder'),
    "the Builder's stage ran: " + JSON.stringify((done.stages || []).map((row) => row.stage)));
});

test('walk: a paused chat survives a reload, and resuming runs the run\'s own command', { skip }, async (t) => {
  const { root, restore } = buildWorkspace();
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  // One status item per window: a shared one lets the first window's text (or a late
  // read landing after its dispose) satisfy the second window's assertions.
  const statusItems = [];
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => {
    const item = { show() {}, hide() {}, dispose() {}, text: '', tooltip: undefined, command: undefined };
    statusItems.push(item);
    return item;
  });
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const wsState = host.workspaceState;
  let saved = {};
  const openWindow = () => {
    const context = { subscriptions: [], workspaceState: wsState };
    activate(context);
    const chat = host.providers.get('autocode.chat');
    let receive;
    const webview = {
      html: '', options: {},
      onDidReceiveMessage: (callback) => { receive = callback; },
      postMessage: async (data) => { webview.deliver?.(data); return true; },
    };
    const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
    chat.resolveWebviewView(shell);
    const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
    // The webview's own state (drafts, tabs) persists across a reload, as VS Code keeps it.
    dom.window.acquireVsCodeApi = () => ({
      postMessage: (message) => receive(message),
      getState: () => saved,
      setState: (value) => { saved = value; },
    });
    webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
    dom.window.eval(dom.window.document.querySelector('script').textContent);
    return { context, chat, dom };
  };

  const first = openWindow();
  t.after(() => first.context.subscriptions.forEach((item) => item.dispose?.()));
  t.after(() => first.dom.window.close());
  await first.dom.window.acquireVsCodeApi().postMessage({ type: 'ready' });
  await first.chat.show({ workspace: root, runDir: restore }), await first.chat.refresh();
  const text = () => first.dom.window.document.querySelector('#app').textContent;
  await waitFor(() => text().includes('Show all changes'), 30000);
  await waitFor(() => statusItems[0].text.includes('AutoCode'), 30000);
  assert.ok(statusItems[0].text.includes('AutoCode'), 'the status line follows the opened chat: ' + statusItems[0].text);
  const field = first.dom.window.document.querySelector('#message');
  field.value = 'A draft that must survive the reload';
  field.dispatchEvent(new first.dom.window.Event('input'));
  await flush();
  first.dom.window.document.querySelector('#all-changes-show').click();
  await waitFor(() => text().includes('+print'), 30000);
  assert.ok(text().includes('diff --checkpoint'), 'the run diff is open before the reload: ' + text().slice(-400));

  // The window reloads: the extension and the webview are torn down and rebuilt.
  first.context.subscriptions.forEach((item) => item.dispose?.());
  first.dom.window.close();
  const second = openWindow();
  t.after(() => second.context.subscriptions.forEach((item) => item.dispose?.()));
  t.after(() => second.dom.window.close());
  await second.dom.window.acquireVsCodeApi().postMessage({ type: 'ready' });
  const text2 = () => second.dom.window.document.querySelector('#app').textContent;
  await waitFor(() => text2().includes('Show all changes'), 30000);
  assert.ok(text2().includes('Show all changes'), "the paused chat's card comes back after the reload");
  assert.equal(second.dom.window.document.querySelector('#message').value, 'A draft that must survive the reload',
    'the unsent draft survives the reload');
  await waitFor(() => statusItems[1].text.includes('AutoCode'), 30000);
  assert.ok(statusItems[1].text.includes('AutoCode'), 'the status line comes back on the restored chat: ' + statusItems[1].text);
  assert.ok(!text2().includes('diff --checkpoint'),
    'the shown diff does not survive the reload');

  // Resuming runs the run's own named command; on a host without that provider, the CLI says so
  // and the chat reports it — the resume is the run's, not the client's, to complete.
  const take = second.dom.window.document.querySelector('#take-action');
  assert.ok(take && !take.disabled, 'the paused chat still offers its named action after the reload');
  t.mock.method(host.vscode.window, 'showWarningMessage', () => Promise.resolve('Continue'));
  take.click();
  const error = () => second.dom.window.document.querySelector('#chat-error').textContent;
  await waitFor(() => error().includes('provider config'), 30000);
  assert.ok(error().includes('provider config'),
    'the resume runs the named command and reports this host\'s refusal: ' + error());
});

test("walk: pausing a working run mid-walk and resuming it brings the run back to work", { skip }, async (t) => {
  const { root } = buildWorkspace();
  const { xdg, roles } = writeWalkProvider();
  process.env.XDG_CONFIG_HOME = xdg;
  t.after(() => { delete process.env.XDG_CONFIG_HOME; });
  const runDir = writeResumeRun(root, xdg, roles, true);
  claimSupervision(runDir);
  const statePath = path.join(runDir, 'state.json');
  const state = () => JSON.parse(fs.readFileSync(statePath, 'utf8'));

  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  let statusText = '';
  const mockStatus = {
    show() {}, hide() {}, dispose() {},
    get text() { return statusText; }, set text(val) { statusText = val; },
    tooltip: undefined, command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const chat = host.providers.get('autocode.chat');
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  let saved = {};
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => receive(message),
    getState: () => saved,
    setState: (value) => { saved = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  await host.commands.get('autocode.openRun')({ workspace: root, runDir });
  const text = () => dom.window.document.querySelector('#app').textContent;

  // The run works mid-walk: its Pause is offered and the bar follows the working run.
  const pause = () => dom.window.document.querySelector('#pause-run');
  await waitFor(() => !pause().hidden && pause().textContent === 'Pause', 30000);
  assert.ok(!pause().hidden, 'the working chat offers its Pause: ' + text().slice(0, 200));

  // The person pauses it: the real intervention lands in the run's inbox. The button's
  // "Pausing" mark shows while the action is in flight and while the pause waits in the
  // inbox, so the landed inbox is the only honest sign the submit finished.
  pause().click();
  await waitFor(() => dom.window.document.querySelector('#pause-run').textContent === 'Pausing after this step…', 30000);
  await waitFor(() => fs.existsSync(path.join(runDir, 'interventions.json')), 30000);
  assert.ok(fs.existsSync(path.join(runDir, 'interventions.json')),
    'the pause intervention landed in the run\'s inbox: ' + dom.window.document.querySelector('#chat-error').textContent);

  // The step finishes: the controller's own commit saves the step and applies the queued pause together.
  landPause(runDir);
  await waitFor(() => state().status === 'PAUSED_INTERVENTION', 30000);
  assert.equal(state().status, 'PAUSED_INTERVENTION', 'the landed pause stops the run: ' + state().status);
  await waitFor(() => text().includes('Paused as you asked'), 30000);
  assert.ok(text().includes('Paused as you asked'), 'the pause card says the pause landed: ' + text().slice(0, 200));

  // The person resumes: the named command runs with no confirmation dialog of its own.
  let dialog = 0;
  t.mock.method(host.vscode.window, 'showWarningMessage', () => { dialog++; return Promise.resolve('Continue'); });
  const resume = () => dom.window.document.querySelector('#take-action');
  await waitFor(() => resume() && !resume().disabled, 30000);
  assert.ok(resume() && !resume().disabled, 'the pause card offers its Resume: ' + text().slice(0, 200));
  resume().click();

  // The run comes back to work: the pause is behind it and the Builder starts.
  await waitFor(() => state().status !== 'PAUSED_INTERVENTION', 120000);
  assert.notEqual(state().status, 'PAUSED_INTERVENTION', 'the resume takes the run out of its pause');
  assert.equal(dialog, 0, 'the Resume needs no confirmation dialog of its own');
  assert.ok((state().pause_intent || {}).acknowledged_at, 'the resume acknowledges the pause it returns from');

  // The work happens for real: the Builder writes the greeting into the workspace.
  await waitFor(() => fs.readFileSync(path.join(root, 'greet.py'), 'utf8').includes('Hello, '), 180000);
  assert.ok(fs.readFileSync(path.join(root, 'greet.py'), 'utf8').includes('Hello, '),
    'the run came back and did its work: ' + fs.readFileSync(path.join(root, 'greet.py'), 'utf8'));

  // The action lands where the work stops: a person's gate, with the stages it ran recorded.
  await waitFor(() => /^(PAUSED_|WAITING_FOR_USER)/.test(state().status), 180000);
  const done = state();
  assert.ok(/^(PAUSED_|WAITING_FOR_USER)/.test(done.status),
    'the run stops at a person-facing gate, not in a silent state: ' + done.status);
  assert.ok((done.stages || []).some((row) => row.stage === 'builder'),
    "the Builder's stage ran: " + JSON.stringify((done.stages || []).map((row) => row.stage)));
});

test("walk: an edit sent mid-walk is taken up by the run at its next boundary", { skip }, async (t) => {
  const { root } = buildWorkspace();
  const { xdg, roles } = writeWalkProvider();
  process.env.XDG_CONFIG_HOME = xdg;
  t.after(() => { delete process.env.XDG_CONFIG_HOME; });
  const runDir = writeResumeRun(root, xdg, roles, true);
  claimSupervision(runDir);
  const statePath = path.join(runDir, 'state.json');
  const state = () => JSON.parse(fs.readFileSync(statePath, 'utf8'));
  // The live message channel: a spec-first, message-capable run takes a message mid-walk (#3259).
  const s = state();
  s.settings.spec_first = true;
  s.intervention_capability = { supported: true, supports_messages: true };
  fs.writeFileSync(statePath, JSON.stringify(s));

  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  let statusText = '';
  const mockStatus = {
    show() {}, hide() {}, dispose() {},
    get text() { return statusText; }, set text(val) { statusText = val; },
    tooltip: undefined, command: undefined,
  };
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => mockStatus);
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const context = { subscriptions: [], workspaceState: host.workspaceState };
  activate(context);
  t.after(() => context.subscriptions.forEach((item) => item.dispose?.()));
  const chat = host.providers.get('autocode.chat');
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  t.after(() => { chat.dispose(); });
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  let saved = {};
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => receive(message),
    getState: () => saved,
    setState: (value) => { saved = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  await host.commands.get('autocode.openRun')({ workspace: root, runDir });
  const text = () => dom.window.document.querySelector('#app').textContent;
  const hint = () => dom.window.document.querySelector('#send-hint').textContent;

  // The run works mid-walk and the chat takes a message for its next boundary.
  await waitFor(() => hint().includes('read after the current step'), 30000);
  assert.ok(hint().includes('read after the current step'),
    'the working chat says a message now is read after the step: ' + hint());
  const send = () => dom.window.document.querySelector('#send-message');
  assert.ok(send() && !send().hidden, 'the live message can be sent while the step works');

  // The person edits the run mid-walk.
  const edit = 'Change the greeting to say Goodbye instead of Hello';
  const field = dom.window.document.querySelector('#message');
  field.value = edit;
  field.dispatchEvent(new dom.window.Event('input'));
  dom.window.document.querySelector('#composer').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));

  // The edit lands: the chat's own named command saves it in the run's inbox.
  await waitFor(() => fs.existsSync(path.join(runDir, 'interventions.json')), 30000);
  assert.ok(fs.existsSync(path.join(runDir, 'interventions.json')),
    "the edit landed in the run's inbox: " + dom.window.document.querySelector('#chat-error').textContent);
  await waitFor(() => dom.window.document.querySelector('#message').value === '', 30000);
  assert.equal(dom.window.document.querySelector('#message').value, '', 'the saved edit leaves the composer');

  // The step finishes: the controller's own commit takes the queued edit into the run's conversation.
  landPause(runDir);
  await waitFor(() => (state().user_events || []).some((row) => row.text === edit), 30000);
  const events = (state().user_events || []).filter((row) => row.text === edit);
  assert.ok(events.length > 0, 'the run took the edit into its conversation: ' + JSON.stringify(state().user_events));
  assert.equal(events[0].actor, 'user_intervention', "the edit is recorded as the person's own");
  assert.equal(state().status, 'RUNNING', 'the run keeps working, with the edit read at its next boundary');
  assert.ok(/Message saved/.test(state().stop_reason || ''),
    'the run says the message is saved for its conversation: ' + state().stop_reason);
});


test('walk: two windows on one workspace keep their own drafts and status lines', { skip }, async (t) => {
  const { root, restore } = buildWorkspace();
  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  const bars = [];
  t.mock.method(host.vscode.window, 'createStatusBarItem', () => {
    const bar = { show() {}, hide() {}, dispose() {}, text: '', tooltip: undefined, command: undefined };
    bars.push(bar);
    return bar;
  });
  t.mock.method(RunController.prototype, 'list', async () => ({ runs: [], diagnostics: [] }));
  const wsState = host.workspaceState;   // windows on one workspace share this
  const openWindow = (store) => {
    const context = { subscriptions: [], workspaceState: wsState };
    activate(context);
    const chat = host.providers.get('autocode.chat');
    let receive;
    const webview = {
      html: '', options: {},
      onDidReceiveMessage: (callback) => { receive = callback; },
      postMessage: async (data) => { webview.deliver?.(data); return true; },
    };
    const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
    chat.resolveWebviewView(shell);
    const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
    // Each window keeps its own webview state (drafts), as VS Code does.
    dom.window.acquireVsCodeApi = () => ({
      postMessage: (message) => receive(message),
      getState: () => store.state,
      setState: (value) => { store.state = value; },
    });
    webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
    dom.window.eval(dom.window.document.querySelector('script').textContent);
    return { context, chat, dom };
  };

  const storeA = { state: {} };
  const storeB = { state: {} };
  const a = openWindow(storeA);
  const b = openWindow(storeB);
  t.after(() => { a.context.subscriptions.forEach((item) => item.dispose?.()); b.context.subscriptions.forEach((item) => item.dispose?.()); });
  t.after(() => { a.dom.window.close(); b.dom.window.close(); });

  // Per-window drafts: the same workspace, two windows, two private composers.
  await a.chat.show({ workspace: root, runDir: restore });
  await a.chat.refresh();
  await b.chat.show({ workspace: root, runDir: restore });
  await b.chat.refresh();
  const fieldA = a.dom.window.document.querySelector('#message');
  const fieldB = b.dom.window.document.querySelector('#message');
  fieldA.value = 'Window A draft';
  fieldA.dispatchEvent(new a.dom.window.Event('input'));
  fieldB.value = 'Window B draft';
  fieldB.dispatchEvent(new b.dom.window.Event('input'));
  await flush();
  assert.equal(fieldA.value, 'Window A draft');
  assert.equal(fieldB.value, 'Window B draft', 'each window keeps its own draft');

  // Both windows watch the run: two status lines, each its own.
  await waitFor(() => bars.length >= 2 && bars.every((bar) => bar.text.includes('AutoCode')), 30000);
});

test('walk: a spec-first Requirements interview answers open questions in the composer and keeps spoken context', { skip }, async (t) => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'walk-req-')));
  const xdg = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'walk-req-xdg-')));
  t.after(() => {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(xdg, { recursive: true, force: true });
  });
  const git = (...args) => execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
  git('init', '-q');
  git('config', 'user.email', 'walk@test');
  git('config', 'user.name', 'Walk');
  fs.writeFileSync(path.join(root, 'greet.py'), "print('hi')\n");
  git('add', 'greet.py');
  git('commit', '-qm', 'seed');
  const providers = path.join(xdg, 'autocode', 'providers');
  fs.mkdirSync(providers, { recursive: true });
  const providerScript = path.join(xdg, 'req_provider.py');
  fs.writeFileSync(providerScript, String.raw`#!/usr/bin/env python3
import json, sys
from pathlib import Path

if sys.argv[1:] == ["login", "status"]:
    print("Logged in (offline fixture)")
    raise SystemExit(0)

prompt = sys.stdin.read()
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
report_path = Path(sys.argv[sys.argv.index("-o") + 1])
feedback = [row for row in (data.get("brief_feedback") or []) if isinstance(row, dict) and row.get("text")]
base_extra = {
    "project_conventions": [],
    "ignored_requirements": [],
    "machine_resolutions": [],
    "access_blockers": [],
    "task_kind": "build",
    "workspace_scope": {"spans_repositories": False, "external_parts": []},
}

if len(feedback) == 0:
    # Real glm-5.3 failure mode: undrafted clarification with empty behaviors and omitted acceptance_tests.
    out = {
        **base_extra,
        "job": "none",
        "design_document": "",
        "clarity": "vague",
        "summary": "The user wants to discuss product ideas.",
        "assistant_reply": "I'd love to explore ideas with you. What kind of workflow or problem are you hoping to improve?",
        "intended_outcome": "",
        "requirements": [],
        "required_behaviors": [],
        "constraints": [],
        "source_refs": [],
        "proposed_assumptions": [],
        "open_questions": [{
            "id": "Q1",
            "question": "What kind of workflow or problem are you hoping to improve?",
            "why": "Establish the user's goal before drafting requirements.",
            "options": [],
            "proposed_default": "",
            "kind": "decision",
            "category": "requested_outcome",
            "delegable": False,
        }],
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
    }
elif len(feedback) == 1:
    out = {
        **base_extra,
        "job": "build",
        "design_document": "",
        "clarity": "vague",
        "summary": "Clarifying what the scratchpad idea captures.",
        "assistant_reply": "A lightweight scratchpad sounds useful. What should it save when you jot an idea down?",
        "intended_outcome": "Build a lightweight idea scratchpad",
        "requirements": [],
        "required_behaviors": [],
        "acceptance_tests": [],
        "constraints": [],
        "source_refs": [],
        "proposed_assumptions": [],
        "open_questions": [{
            "id": "Q2",
            "question": "What should it save when you jot an idea down?",
            "why": "Clarify what content each note stores.",
            "options": [],
            "proposed_default": "",
            "kind": "decision",
            "category": "requested_outcome",
            "delegable": False,
        }],
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
    }
else:
    latest = feedback[-1]["text"]
    out = {
        **base_extra,
        "job": "build",
        "design_document": "",
        "clarity": "clear",
        "summary": "Build a scratchpad CLI that saves plain markdown notes.",
        "assistant_reply": "Got it, you want a simple scratchpad CLI that appends plain markdown notes.",
        "intended_outcome": "Build a scratchpad CLI that appends plain markdown notes",
        "requirements": [{"id": "R1", "text": latest, "source_quote": latest}],
        "required_behaviors": ["Append each captured note as a markdown entry"],
        "acceptance_tests": ["Saving one note appends its plain markdown text to the notes file"],
        "constraints": [],
        "source_refs": ["greet.py:1"],
        "proposed_assumptions": [],
        "open_questions": [],
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
    }

report_path.write_text(json.dumps(out))
`);
  fs.chmodSync(providerScript, 0o755);
  const toml = ['name = "fake"', 'output = "report_file"',
    `command = ${JSON.stringify([CLI, providerScript, '--output-schema', '{schema}', '-o', '{report}', '--model', '{model}', '-'])}`,
    '', '[roles]'];
  for (const role of ['task_planner', 'planner', 'plan_reviewer', 'builder', 'tester', 'completion']) {
    toml.push(`${role} = { model = "fake/${role}", effort = "medium" }`);
  }
  fs.writeFileSync(path.join(providers, 'fake.toml'), toml.join('\n') + '\n');

  const prevXdg = process.env.XDG_CONFIG_HOME;
  process.env.XDG_CONFIG_HOME = xdg;
  t.after(() => {
    if (prevXdg === undefined) delete process.env.XDG_CONFIG_HOME;
    else process.env.XDG_CONFIG_HOME = prevXdg;
  });

  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  host.config.set('provider', 'fake');
  host.config.set('confirmPersonOnly', false);
  host.config.set('notify', false);
  host.config.set('pollSeconds', 1);

  const controller = new RunController(host.log);
  let receive;
  const webview = {
    html: '', options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  const chat = new ChatView(controller, { get: () => undefined, update: async () => {} },
    { pickWorkspace: async () => root, confirmStart: async () => true, changed() {}, selected() {} });
  t.after(() => chat.dispose());
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  let saved = {};
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => receive(message),
    getState: () => saved,
    setState: (value) => { saved = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();

  const submit = (value) => {
    const field = dom.window.document.querySelector('#message');
    field.value = value;
    field.dispatchEvent(new dom.window.Event('input'));
    dom.window.document.querySelector('#composer').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
  };
  const messagesText = () => dom.window.document.querySelector('#messages').textContent;
  const hint = () => dom.window.document.querySelector('#send-hint').textContent;
  const diag = () => `error=${dom.window.document.querySelector('#chat-error').textContent} | messages=${messagesText()} | app=${dom.window.document.querySelector('#app').textContent}`;

  assert.ok(
    await waitFor(() => dom.window.document.querySelector('#composer') && !dom.window.document.querySelector('#message').disabled, 15000),
    `composer ready: ${diag()}`,
  );

  submit("let's have a discussion around ideas");
  assert.ok(
    await waitFor(() => messagesText().includes('What kind of workflow or problem are you hoping to improve?') && hint() === 'Send reply and continue', 30000),
    `turn 1 question in #messages: ${diag()}`,
  );
  assert.equal(dom.window.document.querySelector('#chat-error').textContent, '');
  assert.equal(dom.window.document.querySelector('#app .decision-card'), null, 'no duplicate decision card beside composer');
  assert.equal(dom.window.document.querySelector('[data-question]'), null, 'no competing question input in #app');
  assert.equal(hint(), 'Send reply and continue');

  submit('A lightweight idea scratchpad while chatting');
  assert.ok(
    await waitFor(() => messagesText().includes('What should it save when you jot an idea down?') && hint() === 'Send reply and continue', 30000),
    `turn 2 question in #messages: ${diag()}`,
  );
  assert.ok(messagesText().includes("I'd love to explore ideas with you. What kind of workflow or problem are you hoping to improve?"),
    'turn 1 full assistant_reply stays in chat transcript after feedback');
  assert.equal(dom.window.document.querySelector('#app .decision-card'), null);
  assert.equal(dom.window.document.querySelector('[data-question]'), null);

  submit('Save plain markdown notes with no flags');
  assert.ok(
    await waitFor(() => {
      const btn = dom.window.document.querySelector('#take-action');
      return btn && !btn.disabled && btn.textContent.includes('Approve understanding');
    }, 30000),
    `turn 3 approve understanding button: ${diag()}`,
  );
  assert.ok(messagesText().includes('A lightweight scratchpad sounds useful. What should it save when you jot an idea down?'),
    'turn 2 full assistant_reply stays in chat transcript');
  assert.equal(messagesText().split('Build a scratchpad CLI that appends plain markdown notes').length - 1, 1);
  assert.equal(dom.window.document.querySelector('#app pre.understanding'), null, 'understanding is not duplicated in card');
});

test("walk: a new chat's model controls show explicit project GLM/MiMo and extraOptions routes and match the admitted run", { skip }, async (t) => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'walk-f6-')));
  const xdg = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'walk-f6-xdg-')));
  t.after(() => {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(xdg, { recursive: true, force: true });
  });
  const git = (...args) => execFileSync('git', ['-C', root, ...args], { stdio: 'pipe' });
  git('init', '-q');
  git('config', 'user.email', 'walk@test');
  git('config', 'user.name', 'Walk');
  fs.writeFileSync(path.join(root, 'greet.py'), "print('hi')\n");
  fs.mkdirSync(path.join(root, '.autocode'), { recursive: true });
  fs.writeFileSync(
    path.join(root, '.autocode', 'config.toml'),
    'planner-model = "zai-coding-plan/glm-5.3"\n' +
      'planner-reasoning-effort = "high"\n' +
      'builder-model = "xiaomi-token-plan-sgp/mimo-v2.6-pro"\n' +
      'builder-reasoning-effort = "xhigh"\n',
  );
  git('add', 'greet.py', '.autocode/config.toml');
  git('commit', '-qm', 'seed');

  const providers = path.join(xdg, 'autocode', 'providers');
  fs.mkdirSync(providers, { recursive: true });
  const providerScript = path.join(xdg, 'f6_provider.py');
  fs.writeFileSync(
    providerScript,
    String.raw`#!/usr/bin/env python3
import json, sys
from pathlib import Path

if sys.argv[1:] == ["login", "status"]:
    print("Logged in (offline fixture)")
    raise SystemExit(0)

report_path = Path(sys.argv[sys.argv.index("-o") + 1])
out = {
    "project_conventions": [],
    "ignored_requirements": [],
    "machine_resolutions": [],
    "access_blockers": [],
    "task_kind": "build",
    "workspace_scope": {"spans_repositories": False, "external_parts": []},
    "job": "none",
    "design_document": "",
    "clarity": "vague",
    "summary": "The user wants to discuss product ideas.",
    "assistant_reply": "I'd love to explore ideas with you. What kind of workflow or problem are you hoping to improve?",
    "intended_outcome": "",
    "requirements": [],
    "required_behaviors": [],
    "constraints": [],
    "source_refs": [],
    "proposed_assumptions": [],
    "open_questions": [{
        "id": "Q1",
        "question": "What kind of workflow or problem are you hoping to improve?",
        "why": "Establish the user's goal before drafting requirements.",
        "options": [],
        "proposed_default": "",
        "kind": "decision",
        "category": "requested_outcome",
        "delegable": False,
    }],
    "ignored_statements": [],
    "conflicts": [],
    "proposed_reframes": [],
}
report_path.write_text(json.dumps(out))
`,
  );
  fs.chmodSync(providerScript, 0o755);
  const fakeToml = [
    'name = "fake"',
    'output = "report_file"',
    `command = ${JSON.stringify([CLI, providerScript, '--output-schema', '{schema}', '-o', '{report}', '--model', '{model}', '-'])}`,
    `models = ${JSON.stringify([
      'zai-coding-plan/glm-5.3',
      'xiaomi-token-plan-sgp/mimo-v2.6-pro',
      'fake/task_planner',
      'fake/requirements',
      'fake/planner',
      'fake/plan_reviewer',
      'fake/builder',
      'fake/tester',
      'fake/completion',
      'fake/resolver',
    ])}`,
    '',
    '[roles]',
  ];
  for (const role of ['task_planner', 'planner', 'plan_reviewer', 'builder', 'tester', 'completion']) {
    fakeToml.push(`${role} = { model = "fake/${role}", effort = "medium" }`);
  }
  fs.writeFileSync(path.join(providers, 'fake.toml'), fakeToml.join('\n') + '\n');
  const stagesTomlPath = path.join(xdg, 'autocode', 'stages.toml');
  fs.writeFileSync(stagesTomlPath, '[fake]\nplanner = { model = "fake/planner", effort = "medium" }\n');

  const prevXdg = process.env.XDG_CONFIG_HOME;
  const prevFolders = host.vscode.workspace.workspaceFolders;
  process.env.XDG_CONFIG_HOME = xdg;
  host.vscode.workspace.workspaceFolders = [{ uri: { fsPath: root } }];
  t.after(() => {
    if (prevXdg === undefined) delete process.env.XDG_CONFIG_HOME;
    else process.env.XDG_CONFIG_HOME = prevXdg;
    host.vscode.workspace.workspaceFolders = prevFolders;
  });

  host.config.set('cliPath', CLI);
  host.config.set('cliArgs', [SCRIPT]);
  host.config.set('provider', 'fake');
  host.config.set('confirmPersonOnly', false);
  host.config.set('notify', false);
  host.config.set('pollSeconds', 1);

  const controller = new RunController(host.log);
  let selectedRun;
  let receive;
  const webview = {
    html: '',
    options: {},
    onDidReceiveMessage: (callback) => { receive = callback; },
    postMessage: async (data) => { webview.deliver?.(data); return true; },
  };
  const shell = { visible: true, webview, onDidChangeVisibility: () => {}, onDidDispose() {} };
  const chat = new ChatView(
    controller,
    { get: () => undefined, update: async () => {} },
    {
      pickWorkspace: async () => root,
      confirmStart: async () => true,
      changed() {},
      selected: (ref) => { if (ref) selectedRun = ref; },
    },
    new StageListCache(),
  );
  t.after(() => chat.dispose());
  chat.resolveWebviewView(shell);
  const dom = new JSDOM(webview.html, { runScripts: 'outside-only' });
  t.after(() => dom.window.close());
  let saved = {};
  dom.window.acquireVsCodeApi = () => ({
    postMessage: (message) => receive(message),
    getState: () => saved,
    setState: (value) => { saved = value; },
  });
  webview.deliver = (data) => dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data }));
  dom.window.eval(dom.window.document.querySelector('script').textContent);
  await flush();
  chat.show();

  const row = (stage) => dom.window.document.querySelector(`#chat-models [data-stage="${stage}"]`);
  const diag = () => `error=${dom.window.document.querySelector('#chat-error')?.textContent} | models=${dom.window.document.querySelector('#chat-models')?.textContent}`;

  assert.ok(
    await waitFor(() => row('planner')?.querySelector('select.model') && row('builder')?.querySelector('select.model'), 15000),
    `chat models ready: ${diag()}`,
  );

  assert.equal(row('planner').querySelector('select.model').value, 'zai-coding-plan/glm-5.3');
  assert.equal(row('planner').querySelector('select.level').value, 'high');
  assert.equal(row('planner').querySelector('.now').textContent.trim(), 'project');

  assert.equal(row('requirements').querySelector('select.model').value, 'zai-coding-plan/glm-5.3');
  assert.equal(row('requirements').querySelector('select.level').value, 'medium');
  assert.equal(row('requirements').querySelector('.now').textContent.trim(), 'project');

  assert.equal(row('builder').querySelector('select.model').value, 'xiaomi-token-plan-sgp/mimo-v2.6-pro');
  assert.equal(row('builder').querySelector('select.level').value, 'xhigh');
  assert.equal(row('builder').querySelector('.now').textContent.trim(), 'project');

  const glmOption = Array.from(row('plan_reviewer').querySelectorAll('select.model option'))
    .find((opt) => opt.value === 'zai-coding-plan/glm-5.3');
  assert.ok(glmOption && glmOption.disabled, 'plan_reviewer disables planner GLM option');
  assert.match(fs.readFileSync(stagesTomlPath, 'utf8'), /fake\/planner/, 'stages.toml keeps global choice untouched');

  await host.vscode.workspace.getConfiguration('autocode').update(
    'extraOptions',
    ['--tester-model', 'fake/requirements', '--tester-reasoning-effort', 'low'],
  );
  assert.ok(
    await waitFor(() => row('tester')?.querySelector('.now')?.textContent.trim() === 'settings', 15000),
    `tester refreshes from extraOptions: ${diag()}`,
  );
  assert.equal(row('tester').querySelector('select.model').value, 'fake/requirements');
  assert.equal(row('tester').querySelector('select.level').value, 'low');

  const field = dom.window.document.querySelector('#message');
  field.value = "let's have a discussion around ideas";
  field.dispatchEvent(new dom.window.Event('input'));
  dom.window.document.querySelector('#composer').dispatchEvent(new dom.window.Event('submit', { cancelable: true }));

  assert.ok(
    await waitFor(
      () => selectedRun?.runDir && dom.window.document.querySelector('#send-hint')?.textContent === 'Send reply and continue',
      30000,
    ),
    `admitted run settled at Requirements question: ${diag()}`,
  );

  const runStagesJson = execFileSync(
    CLI,
    [SCRIPT, 'stages', 'list', '--run-dir', selectedRun.runDir, '--json'],
    { env: { ...process.env, XDG_CONFIG_HOME: xdg }, encoding: 'utf8' },
  );
  const runById = Object.fromEntries(JSON.parse(runStagesJson).stages.map((s) => [s.id, s]));
  assert.deepEqual(runById.requirements.chosen, { model: 'zai-coding-plan/glm-5.3', effort: 'medium' });
  assert.deepEqual(runById.planner.chosen, { model: 'zai-coding-plan/glm-5.3', effort: 'high' });
  assert.deepEqual(runById.builder.chosen, { model: 'xiaomi-token-plan-sgp/mimo-v2.6-pro', effort: 'xhigh' });
  assert.deepEqual(runById.tester.chosen, { model: 'fake/requirements', effort: 'low' });
});

test('walk project index isolation creates and cleans up a temporary index home without overriding explicit env', () => {
  const unsetEnv = {};
  const cleanup = isolateWalkProjectIndex(unsetEnv);
  assert.equal(typeof cleanup, 'function');
  assert.ok(unsetEnv.AUTOCODE_PROJECT_INDEX_HOME.includes('walk-index-'));
  assert.equal(fs.existsSync(unsetEnv.AUTOCODE_PROJECT_INDEX_HOME), true);
  fs.writeFileSync(path.join(unsetEnv.AUTOCODE_PROJECT_INDEX_HOME, 'projects.json'), '{}');
  cleanup();
  assert.equal(fs.existsSync(unsetEnv.AUTOCODE_PROJECT_INDEX_HOME), false);

  const explicitEnv = { AUTOCODE_PROJECT_INDEX_HOME: '/custom/index-home' };
  assert.equal(isolateWalkProjectIndex(explicitEnv), null);
  assert.equal(explicitEnv.AUTOCODE_PROJECT_INDEX_HOME, '/custom/index-home');
});
