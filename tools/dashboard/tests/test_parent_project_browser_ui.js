'use strict';
// Real browser + production HTTP/application. No client globals are changed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn, execFileSync} = require('node:child_process');
const source = process.env.AUTOCODE_TEST_SOURCE_ROOT;
const python = process.env.AUTOCODE_TEST_PYTHON;
const evidence = process.env.PARENT_PROJECT_EVIDENCE_DIR;
for (const [name, value] of Object.entries({AUTOCODE_TEST_SOURCE_ROOT: source,
  AUTOCODE_TEST_PYTHON: python, PARENT_PROJECT_EVIDENCE_DIR: evidence,
  AGENT_BROWSER_CONFIG: process.env.AGENT_BROWSER_CONFIG,
  AGENT_BROWSER_EXECUTABLE_PATH: process.env.AGENT_BROWSER_EXECUTABLE_PATH})) {
  assert.ok(value && path.isAbsolute(value), name + ' must be explicitly bound');
}
const {dashboardReadinessExpression, waitForReadiness} = require(
  path.join(source, 'tools/dashboard/tests/browser_readiness.js'));
fs.mkdirSync(evidence, {recursive: false});
const session = 'parent-project-809-' + process.pid;
const browser = (...args) => execFileSync('agent-browser', ['--session', session, ...args],
  {cwd: source, encoding: 'utf8', timeout: 45000});
const evaluate = expression => JSON.parse(JSON.parse(browser('eval',
  'JSON.stringify((' + expression + ')())').trim()));
const wait = expression => browser('wait', '--fn', expression);
const digest = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const app = path.join(source, 'tools/dashboard/dashboard_app.js');
const sourceHash = digest(app);
let server, fixture;
const captures = [], checks = [];

function start() {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Fixture readiness exceeded 15 seconds')), 15000);
    server = spawn(python, ['-B', process.env.PARENT_PROJECT_FIXTURE ||
      path.join(__dirname, 'parent_project_browser_fixture.py')], {
      cwd: source, env: {...process.env, AUTOCODE_FIXTURE_ROOT: path.join(evidence, 'fixture')},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    server.stdout.on('data', chunk => {
      fs.appendFileSync(path.join(evidence, 'fixture.stdout.log'), chunk);
      output += chunk;
      const row = output.split('\n').find(line => line.startsWith('FIXTURE='));
      if (row) { clearTimeout(timer); resolve(JSON.parse(row.slice(8))); }
    });
    server.stderr.on('data', chunk => fs.appendFileSync(path.join(evidence, 'fixture.stderr.log'), chunk));
    server.on('error', error => { clearTimeout(timer); reject(error); });
    server.on('exit', code => { clearTimeout(timer); reject(new Error('Fixture exited: ' + code)); });
  });
}
const requests = () => JSON.parse(fs.readFileSync(fixture.request_log, 'utf8'));
const cssAttribute = value => JSON.stringify(value);
const runRow = () => '.conversation-nav-row[data-conversation-key=' + cssAttribute('run:' + fixture.run) + ']';
async function api(route, body) {
  const response = await fetch(fixture.url + route, {signal: AbortSignal.timeout(30000),
    ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)})});
  const value = await response.json();
  assert.ok(response.ok, JSON.stringify({route, status: response.status, value}));
  return value;
}
function shot(name) {
  const file = path.join(evidence, name + '.png');
  browser('screenshot', file);
  captures.push({name, file, sha256: digest(file)});
}
function header() {
  return evaluate('()=>{const p=document.querySelector("#task-project"),b=document.querySelector("#breadcrumb-title");' +
    'return {project:p.textContent,title:p.title,breadcrumb:b.textContent,breadcrumbTitle:b.title}}');
}
function assertHeader(expected) {
  const actual = header(), name = path.basename(expected);
  assert.equal(actual.project, name);
  assert.equal(actual.title, expected);
  assert.equal(actual.breadcrumb, name);
  assert.equal(actual.breadcrumbTitle, expected);
}
function visible(selector) {
  const result = evaluate('()=>{const e=document.querySelector(' + JSON.stringify(selector) +
    '),r=e.getBoundingClientRect(),s=getComputedStyle(e);return {width:r.width,height:r.height,' +
    'inside:r.right>0&&r.bottom>0&&r.left<innerWidth&&r.top<innerHeight,' +
    'visibility:s.visibility,display:s.display}}');
  assert.ok(result.width > 0 && result.height > 0 && result.inside &&
    result.visibility === 'visible' && result.display !== 'none', selector + ' must be rendered onscreen');
}
function openTask() {
  browser('open', fixture.url + '/#' + new URLSearchParams({task: fixture.workspace, run: fixture.run}));
  wait('document.querySelector("#task-project")?.title===' + JSON.stringify(fixture.project) +
    '&&document.querySelector("#task-removed-banner").hidden');
  wait('document.querySelector("#task-load-notice")?.hidden&&document.querySelector("#sync-state")?.textContent==="Task checked just now"');
}

(async () => {
  fixture = await start();
  assert.notEqual(fixture.project, fixture.workspace);
  const served = await fetch(fixture.url + '/static/app.js', {signal: AbortSignal.timeout(30000)});
  assert.equal(served.status, 200);
  assert.ok((await served.text()).includes(fs.readFileSync(app, 'utf8')),
    'the production handler serves the exact candidate application bytes');
  const linkedBefore = await api('/api/conversation?id=' + encodeURIComponent(fixture.conversation));
  const markerHash = digest(fixture.parent_marker);
  assert.equal(digest(fixture.child_marker), markerHash);
  browser('set', 'viewport', '1440', '900');
  browser('open', fixture.url);
  waitForReadiness({label: 'parent project', probe: () => evaluate(dashboardReadinessExpression()),
    wait: () => browser('wait', '250')});
  wait('document.querySelector(' + JSON.stringify(runRow()) + ')!==null');
  const groups = evaluate('()=>[...document.querySelectorAll("#projects .project-group")].map(x=>x.dataset.project)');
  assert.ok(groups.includes(fixture.project));
  assert.ok(!groups.includes(fixture.workspace), 'execution worktree is not a second project');
  const parentGroup = '.project-group[data-project=' + cssAttribute(fixture.project) + ']';
  if (evaluate('()=>document.querySelector(' + JSON.stringify(parentGroup + ' .project-group-toggle') + ').getAttribute("aria-expanded")') === 'false')
    browser('click', parentGroup + ' .project-group-toggle');
  browser('click', runRow());
  wait('document.querySelector("#task-project")?.title===' + JSON.stringify(fixture.project) +
    '&&!document.querySelector("#stop-run").disabled');
  assertHeader(fixture.project);
  visible(parentGroup + ' .project-group-name');
  visible('#task-project');
  assert.equal(evaluate('()=>document.querySelector("[data-new-task]").getAttribute("aria-label")'),
    'New conversation in ' + path.basename(fixture.project));
  assert.ok(requests().some(row => row.kind === 'api_run' &&
    row.query.workspace?.[0] === fixture.workspace && row.query.run?.[0] === fixture.run));
  shot('parent-sidebar-header');
  // The shipped desktop layout hides the utility topbar. Check its breadcrumb
  // in the actual phone layout, then return to desktop for the action sequence.
  browser('set', 'viewport', '390', '844');
  visible('#breadcrumb-title');
  assert.equal(header().breadcrumb, path.basename(fixture.project));
  assert.equal(header().breadcrumbTitle, fixture.project);
  shot('parent-mobile-breadcrumb');
  browser('set', 'viewport', '1440', '900');
  visible('#task-project');
  checks.push('parent sidebar/header/breadcrumb and execution run query');

  browser('click', '#stop-run');
  wait('document.querySelector("#stop-run").textContent==="Stop requested"');
  const stops = requests().filter(row => row.kind === 'api_action' && row.body.action === 'stop');
  assert.equal(stops.length, 1);
  assert.equal(stops[0].body.workspace, fixture.workspace);
  assert.equal(stops[0].body.run, fixture.run);
  const submits = requests().filter(row => row.kind === 'runner' && row.argv[0] === 'intervention' && row.argv[1] === 'submit');
  assert.equal(submits.length, 1);
  const argv = submits[0].argv;
  assert.equal(argv[argv.indexOf('--workspace') + 1], fixture.workspace);
  assert.equal(argv[argv.indexOf('--run-dir') + 1], fixture.run);
  assert.equal(argv[argv.indexOf('--kind') + 1], 'stop');
  assert.equal(argv[argv.indexOf('--request-id') + 1], stops[0].body.request_id);
  checks.push('actual Stop HTTP input and controlled runner submission preserve execution identity');

  const oldDocs = (await api('/api/runs')).conversations.map(doc => doc.id);
  browser('click', parentGroup + ' .project-group-new');
  wait('document.querySelector("#draft-conversation")?.classList.contains("scoped-start")');
  const created = (await api('/api/runs')).conversations.filter(doc => !oldDocs.includes(doc.id));
  assert.equal(created.length, 1);
  assert.equal(created[0].project_workspace, fixture.project);
  const creates = requests().filter(row => row.kind === 'api_conversations' &&
    row.body.request_id !== 'fixture-linked-parent');
  assert.equal(creates.length, 1);
  assert.equal(creates[0].body.empty, true);
  assert.equal(creates[0].body.workspace, fixture.project);
  shot('parent-scoped-new');
  checks.push('real empty New conversation is saved under parent');

  openTask();
  browser('click', '#task-detail .task-detail-navigation > details > summary');
  wait('document.querySelector("#task-detail .task-detail-navigation > details").open');
  browser('click', '#task-detail .task-project-details > summary');
  wait('document.querySelector("#task-detail .task-project-details").open');
  assert.equal(evaluate('()=>document.querySelector("#task-project-actions .project-path").textContent'), fixture.project);
  browser('click', '#task-project-actions .remove-project-button');
  wait('document.querySelector("#project-removal-dialog").open');
  assert.equal(evaluate('()=>document.querySelector("#project-removal-path").textContent'), fixture.project);
  browser('click', '#project-removal-confirm');
  wait('!document.querySelector("#task-removed-banner").hidden');
  const removed = await api('/api/runs');
  assert.ok(removed.removed_projects.some(row => row.workspace === fixture.project));
  assert.ok(!removed.removed_projects.some(row => row.workspace === fixture.workspace));
  assert.equal(evaluate('()=>document.querySelector("#task-removed-banner .project-path").textContent'), fixture.project);
  browser('press', 'Escape');
  wait('!document.querySelector("#task-detail .context-menu[open]")');
  shot('removed-parent');
  browser('click', '#task-removed-banner button');
  wait('document.querySelector("#task-removed-banner").hidden&&document.querySelector("#task-project").title===' + JSON.stringify(fixture.project));
  assertHeader(fixture.project);
  let changes = requests().filter(row => row.kind === 'api_projects');
  assert.deepEqual(changes.map(row => row.body), [
    {workspace: fixture.project, action: 'remove'}, {workspace: fixture.project, action: 'restore'}]);
  checks.push('task Project details removes and restores parent');

  // Install the historical child-only exclusion through the same public API.
  // The linked parent conversation remains visible and must not retarget Restore.
  await api('/api/projects', {workspace: fixture.workspace, action: 'remove'});
  const legacy = await api('/api/runs');
  assert.ok(legacy.conversations.some(doc => doc.id === fixture.conversation &&
    doc.attachment?.project_workspace === fixture.project));
  assert.ok(!legacy.removed_projects.some(row => row.workspace === fixture.project));
  browser('reload');
  wait('!document.querySelector("#task-removed-banner").hidden');
  assertHeader(fixture.workspace);
  assert.equal(evaluate('()=>document.querySelector("#task-removed-banner .project-path").textContent'), fixture.workspace);
  shot('legacy-child-only-removal');
  browser('click', '#task-removed-banner button');
  wait('document.querySelector("#task-removed-banner").hidden&&document.querySelector("#task-project").title===' + JSON.stringify(fixture.project));
  changes = requests().filter(row => row.kind === 'api_projects');
  assert.deepEqual(changes.slice(2).map(row => row.body), [
    {workspace: fixture.workspace, action: 'remove'}, {workspace: fixture.workspace, action: 'restore'}]);
  const final = await api('/api/runs');
  assert.equal(final.removed_projects.length, 0);
  assert.ok(final.runs.some(row => row.run === fixture.run && row.workspace === fixture.workspace));
  assert.ok(final.conversations.some(doc => doc.id === fixture.conversation));
  const linkedAfter = await api('/api/conversation?id=' + encodeURIComponent(fixture.conversation));
  assert.deepEqual(linkedAfter.messages, linkedBefore.messages);
  assert.deepEqual(linkedAfter.attachment, linkedBefore.attachment);
  assert.equal(digest(fixture.parent_marker), markerHash);
  assert.equal(digest(fixture.child_marker), markerHash);
  assert.equal(requests().filter(row => row.kind === 'unexpected_provider').length, 0);
  assert.equal(requests().filter(row => row.kind === 'api_action').length, 1,
    'project visibility and scoped New never submit another task action');
  const allowedPosts = ['/api/action', '/api/conversations', '/api/projects'];
  assert.ok(requests().filter(row => row.kind === 'api_post_route').every(row => allowedPosts.includes(row.path)),
    'no task archive, deletion, controller creation, or other mutation endpoint is called');
  assert.ok(requests().filter(row => row.kind === 'api_run').every(row =>
    row.query.workspace?.[0] === fixture.workspace && row.query.run?.[0] === fixture.run),
    'all refreshed public task reads retain the original execution address');
  assert.equal(digest(app), sourceHash);
  checks.push('legacy child-only Restore preserves parent link and both workspaces');
  const errors = browser('errors').trim();
  assert.ok(!errors || /^No (?:page )?errors\.?$/i.test(errors), errors);
  fs.writeFileSync(path.join(evidence, 'result.json'), JSON.stringify({complete: true,
    source_app_sha256: sourceHash, checks, captures, errors,
    scope: 'browser rendering and public routing; no task worker or native/model acceptance'}, null, 2));
  console.log('Parent project browser identity regression passed: ' + evidence);
})().catch(error => {
  console.error(error.stack || error);
  try { shot('failure'); } catch {}
  fs.writeFileSync(path.join(evidence, 'result.json'), JSON.stringify({complete: false,
    source_app_sha256: sourceHash, checks, captures, error: String(error.stack || error)}, null, 2));
  process.exitCode = 1;
}).finally(() => {
  try { browser('close'); } catch {}
  if (server) server.kill('SIGTERM');
});
