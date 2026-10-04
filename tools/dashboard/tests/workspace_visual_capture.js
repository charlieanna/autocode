// M3 visual recapture: photographs the implemented workspace at the states
// the approved Figma exports define and records a manifest mapping every
// capture to its read-only reference export. Driven manually or by the M3
// gate runs; not an assertion gate itself.
// Before visual acceptance, compare source_sha256 with the checkout. Missing
// or stale source bindings require recapture; PNG hashes alone are insufficient.
//
//   M3_VISUAL_EVIDENCE_DIR=<dir> node workspace_visual_capture.js
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {dashboardReadinessExpression, waitForReadiness} = require('./browser_readiness');

const root = path.resolve(__dirname, '../../..');
const fixture = path.join(__dirname, 'unified_browser_fixture.py');
const evidenceRoot = process.env.M3_VISUAL_EVIDENCE_DIR
  ? path.resolve(root, process.env.M3_VISUAL_EVIDENCE_DIR)
  : path.join(root, '.autocode', 'evidence', 'm3-visuals-current', 'run-' + process.pid);
fs.mkdirSync(evidenceRoot, {recursive: true});
const session = 'dashboard-m3-visuals-' + process.pid;
let server;
let scenarioReload = 0;

function browser(...args) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return execFileSync('agent-browser', ['--session', session, ...args], {
        cwd: root,
        encoding: 'utf8',
        timeout: 45000,
        env: {...process.env, AGENT_BROWSER_SCREENSHOT_DIR: evidenceRoot},
      });
    } catch (error) {
      lastError = error;
      const output = String(error.stdout || '') + String(error.stderr || '') + String(error.message || '');
      if (!/Resource temporarily unavailable.*daemon may be busy|EAGAIN/i.test(output) || attempt === 2) throw error;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 450 * (attempt + 1));
    }
  }
  throw lastError;
}

function evaluate(script) { return JSON.parse(JSON.parse(browser('eval', script).trim())); }
function data(body) { return evaluate('JSON.stringify((' + body + ')())'); }
function waitForCondition(expression, label, attempts = 48) {
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    last = data('()=>({matched:Boolean(' + expression + '),ready:document.readyState,shell:!!document.querySelector(".app-shell"),title:document.querySelector("#task-title")?.textContent||""})');
    if (last.matched) return;
    if (attempt < attempts - 1) browser('wait', '250');
  }
  throw Error(label + ' did not become true: ' + JSON.stringify(last));
}
function openScenario(info, name, width, height) {
  browser('set', 'viewport', String(width), String(height));
  const url = new URL(info.scenarios[name]);
  url.searchParams.set('fixture_reload', String(++scenarioReload));
  browser('open', url.toString());
  waitForReadiness({label: name + ' initialization', attempts: 48,
    probe: () => data(dashboardReadinessExpression()),
    wait: () => browser('wait', '250')});
  waitForCondition('document.querySelector("#task-title")?.textContent.includes("Drawer-safe running build")', name + ' render');
}
function shot(file) {
  const target = path.join(evidenceRoot, file);
  browser('screenshot', target);
  return path.relative(root, target);
}

const sha256 = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const reference = name => {
  const file = path.join(root, '.autocode-ui', 'approved-design', name);
  return {reference: name, reference_path: path.relative(root, file), reference_sha256: sha256(file)};
};

function fixtureInfo() {
  return new Promise((resolve, reject) => {
    let output = '', errors = '';
    const deadline = setTimeout(() => reject(Error('Timed out waiting for the disposable M3 workspace fixture.')), 20000);
    server.stdout.on('data', chunk => {
      output += String(chunk);
      const match = output.match(/^FIXTURE=(.+)$/m);
      if (!match) return;
      clearTimeout(deadline);
      try { resolve(JSON.parse(match[1])); } catch (error) { reject(error); }
    });
    server.stderr.on('data', chunk => { errors += String(chunk); });
    server.once('error', reject);
    server.once('exit', code => { if (code) reject(Error('M3 workspace fixture exited with ' + code + ': ' + (errors || output))); });
  });
}

(async () => {
  server = spawn('python3', [fixture], {cwd: root, env: {...process.env, AUTOCODE_FIXTURE_ROOT: path.join(evidenceRoot, 'fixture-state')}, stdio: ['ignore', 'pipe', 'pipe']});
  const info = await fixtureInfo();
  const manifest = {generated_at: new Date().toISOString(), source: 'tools/dashboard/tests/workspace_visual_capture.js',
                    workspace_root: path.relative(root, root), captures: []};
  try {
  // Bind visual evidence to the actual served implementation, not just image
  // hashes. An older manifest cannot establish acceptance of later CSS/JS.
  // The list covers the shipped dashboard assets plus every changed M3 test
  // file (modified and new), so the manifest alone establishes the complete
  // declared source snapshot.
  manifest.source_sha256 = Object.fromEntries([
      'dashboard.html','dashboard.css','dashboard_app.js'].map(file => 'tools/dashboard/' + file)
      .concat([
      'workspace_visual_capture.js','workspace_lifecycle_browser.js','workspace_lifecycle_harness.js',
      'workspace_panes_harness.js','sidebar_workspace_harness.js','browser_readiness.js',
      'test_session_checkpoints_ui.js','test_shell_a11y_ui.js','test_m3_lifecycle_browser_ui.js',
      'test_status_ui.js',
      'test_continuous_chat_ui.js','test_persistent_chat_browser_ui.js','test_chat_composer_ui.js',
      'test_resolver_responses_ui.js','test_archive_suggestions_ui.js','test_composer_models_browser_ui.js',
      'test_sidebar_workspace_ui.py','test_workspace_lifecycle_ui.py','test_workspace_panes_ui.py',
      'test_chat_bridge.py','test_console.py','test_conversations.py','test_model_selection.py',
      'unified_browser_fixture.py'].map(file => 'tools/dashboard/tests/' + file))
    .map(file => [file, sha256(path.join(root, file))]));
  manifest.served_assets_sha256 = {};
  for (const [route, file] of [['/static/style.css','dashboard.css'],['/static/app.js','dashboard_app.js']]) {
    const response = await fetch(new URL(route, info.scenarios['flow-m3-chat']));
    if (!response.ok) throw Error('Visual fixture asset unavailable: ' + route);
    const digest = crypto.createHash('sha256').update(await response.text()).digest('hex');
    if (digest !== manifest.source_sha256['tools/dashboard/' + file]) throw Error('Visual fixture serves stale source: ' + file);
    manifest.served_assets_sha256[route] = digest;
  }

    // Desktop building + Work overview (reference 422-1495, 1440x900).
    openScenario(info, 'flow-m3-chat', 1440, 900);
    waitForCondition('document.querySelector("#now .work-summary")?.textContent.length>0', 'Work summary rendered');
    manifest.captures.push({capture: shot('desktop-building-work.png'), viewport: {width: 1440, height: 900}, state: 'desktop building + Work overview', reference_source_canvas: {width:1440,height:900}, reference_export_dimensions: {width:1024,height:640}, comparison_role: 'primary_visual_acceptance', comparison_transform: {scale: 1024 / 1440, output_width: 1024, output_height: 640}, geometry_evidence: '.autocode-ui/approved-design/422-1495.design.txt: sidebar220 + workspace1220, both height900; display this 1440x900 capture at 1024x640 to compare with the scaled reference export', ...reference('422-1495.png')});

    // A 1024x640 CSS viewport exercises responsive behavior. It is not a
    // matched-scale rendering of the 1440x900 design canvas and cannot alone
    // establish visual acceptance against the 1024x640 Figma export.
    openScenario(info, 'flow-m3-chat', 1024, 640);
    waitForCondition('document.querySelector("#now .work-summary")?.textContent.length>0', 'Work summary rendered at export dimensions');
    manifest.captures.push({capture: shot('desktop-building-work-1024x640.png'), viewport: {width: 1024, height: 640}, state: 'desktop building + Work overview at supplementary responsive viewport', reference_source_canvas: {width:1440,height:900}, reference_export_dimensions: {width:1024,height:640}, comparison_role: 'supplementary_responsive_only', geometry_evidence: 'actual 1024x640 CSS viewport; compare the 1440x900 capture displayed at export scale for AC21 visual acceptance', ...reference('422-1495.png')});

    // Mobile conversation foreground (reference 423-306, 390x844).
    openScenario(info, 'flow-m3-chat', 390, 844);
    waitForCondition('document.querySelectorAll("#conversation .chat-message").length>=3', 'mobile transcript rendered');
    manifest.captures.push({capture: shot('mobile-chat.png'), viewport: {width: 390, height: 844}, state: 'mobile conversation foreground', ...reference('423-306.png')});

    // Mobile details sheet above dimmed chat, captured in the Plan state its
    // mapped reference (423-353) shows.
    browser('click', '#details-drawer-toggle');
    waitForCondition('document.querySelector(".app-shell").classList.contains("details-open")', 'details sheet opened');
    browser('click', '.detail-tabs [data-tab="plan"]');
    waitForCondition('document.querySelector("#brief-current")?.textContent.includes("Plan revision")', 'details sheet shows the saved plan state');
    browser('wait', '250');
    manifest.captures.push({capture: shot('mobile-details-drawer.png'), viewport: {width: 390, height: 844}, state: 'mobile details sheet (Plan) over dimmed chat', ...reference('423-353.png')});
    browser('click', '#details-drawer-close');
    waitForCondition('!document.querySelector(".app-shell").classList.contains("details-open")', 'details sheet closed');

    // Mobile project sheet (reference 423-438).
    browser('click', '#nav-toggle');
    waitForCondition('document.querySelector(".app-shell").classList.contains("nav-open")', 'project sheet opened');
    browser('wait', '250');
    manifest.captures.push({capture: shot('mobile-project-drawer.png'), viewport: {width: 390, height: 844}, state: 'mobile project sheet', ...reference('423-438.png')});
    browser('click', '#drawer-close');
    waitForCondition('!document.querySelector(".app-shell").classList.contains("nav-open")', 'project sheet closed');

    const errors = browser('errors').trim();
    if (errors && !/^(?:|\[\]|No page errors\.?|No errors\.?)$/i.test(errors)) throw Error('page errors during captures: ' + errors);
    for (const entry of manifest.captures) entry.sha256 = sha256(path.join(root, entry.capture));
    const manifestPath = path.join(evidenceRoot, 'm3-visual-manifest.json');
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
    console.log('M3 visual captures recorded. Manifest: ' + path.relative(root, manifestPath));
  } finally {
    if (server && !server.killed) server.kill('SIGINT');
    try { browser('close'); } catch {}
  }
})().catch(error => { console.error(error.stack || error); process.exit(1); });
