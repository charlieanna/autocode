// Browser-level AC15 companion: the beside-composer model-route entry is
// visibly usable in a real browser for a running conversation. The credited
// case (tests/test_autocode_stop.py::test_ac15_conversation_controls_beside_composer)
// invokes this check because the synthetic VM harness cannot observe computed
// CSS or open a real <details> disclosure. The fixture is disposable only.
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {dashboardReadinessExpression, waitForReadiness} = require('./browser_readiness');

const root = path.resolve(__dirname, '../../..');
const fixture = path.join(__dirname, 'unified_browser_fixture.py');
const evidenceRoot = process.env.DASHBOARD_COMPOSER_MODELS_EVIDENCE_DIR
  ? path.resolve(root, process.env.DASHBOARD_COMPOSER_MODELS_EVIDENCE_DIR)
  : path.join(root, '.autocode', 'evidence', 'composer-models-browser', 'run-' + process.pid);
fs.mkdirSync(evidenceRoot, {recursive: true});
let session = 'dashboard-composer-models-' + process.pid;
let server;
let sessionHasPage = false;

function browser(...args) {
  // A screenshot can leave the local bridge briefly unavailable. Retrying only
  // that documented EAGAIN condition preserves real dashboard failures.
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

function evaluate(script) {
  return JSON.parse(JSON.parse(browser('eval', script).trim()));
}

function data(body) {
  return evaluate('JSON.stringify((' + body + ')())');
}

function dashboardReadiness() {
  return data(dashboardReadinessExpression());
}

function browserFailureDiagnostics() {
  try {
    return {
      page_errors: browser('errors').trim() || 'No page errors.',
      console: browser('console').trim() || 'No browser console messages.',
    };
  } catch (error) {
    return {diagnostic_error: String(error?.stack || error)};
  }
}

function waitForDashboardReadiness(label, attempts = 48) {
  return waitForReadiness({
    label,
    attempts,
    probe: dashboardReadiness,
    wait: () => browser('wait', '250'),
    onTimeout: browserFailureDiagnostics,
  });
}

function waitForText(selector, phrase, label, attempts = 20) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    const text = data('()=>document.querySelector(' + JSON.stringify(selector) + ')?.textContent||""');
    if (text.includes(phrase)) return text;
    browser('wait', '250');
  }
  assert.fail(label + ' did not render ' + JSON.stringify(phrase) + ' within ' + attempts * 250 + ' ms');
}

function auditBrowserSession(label) {
  if (!sessionHasPage) return;
  const errors = browser('errors').trim();
  assert.match(errors, /^(?:|\[\]|No page errors\.?|No errors\.?)$/i, label + ' browser page errors: ' + errors);
  const consoleMessages = browser('console').trim();
  assert.ok(!/\b(?:warn(?:ing)?|error)\b/i.test(consoleMessages), label + ' browser console warnings or errors: ' + consoleMessages);
  sessionHasPage = false;
}

function fixtureInfo() {
  return new Promise((resolve, reject) => {
    let output = '', errors = '';
    const deadline = setTimeout(() => reject(Error('Timed out waiting for the disposable composer-models fixture.')), 15000);
    server.stdout.on('data', chunk => {
      output += String(chunk);
      const match = output.match(/^FIXTURE=(.+)$/m);
      if (!match) return;
      clearTimeout(deadline);
      try { resolve(JSON.parse(match[1])); } catch (error) { reject(error); }
    });
    server.stderr.on('data', chunk => { errors += String(chunk); });
    server.once('error', reject);
    server.once('exit', code => {
      if (code) reject(Error('Composer-models fixture exited with ' + code + ': ' + (errors || output)));
    });
  });
}

function sourceSnapshot() {
  const files = [
    'tools/dashboard/dashboard.html',
    'tools/dashboard/dashboard.css',
    'tools/dashboard/dashboard_app.js',
    'tools/dashboard/tests/browser_readiness.js',
    'tools/dashboard/tests/test_composer_models_browser_ui.js',
    'tools/dashboard/tests/unified_browser_fixture.py',
  ];
  return files.map(file => ({
    file,
    sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex'),
  }));
}

// Computed-visibility snapshot of everything AC15 places beside the composer.
// `rendered` observes real layout: element.hidden, computed display/visibility
// and a non-empty bounding rect — exactly what the synthetic VM cannot see —
// and honors the engine's disclosure mechanics (content of a closed <details>
// is not rendered; this engine still reports non-zero rects for skipped
// content-visibility subtrees, so the enclosing open state gates it). The
// desktop composer intentionally flattens its intermediate boxes
// (#composer-controls/#composer-run-controls are display:contents), so the
// row geometry compares the model entry against the real Pause button box.
// An optional prepare snippet runs first inside the same synchronous eval, so
// a state change followed by its observation cannot be interleaved by the
// dashboard's background refresh the way a fixed wait between two evals can.
function composerModelSnapshot(prepare = '') {
  return data('()=>{' + prepare +
    'const rendered=e=>{if(!e||e.hidden)return false;for(let node=e.parentElement;node;node=node.parentElement){if(node.tagName==="DETAILS"&&!node.hasAttribute("open")){if(!(e.tagName==="SUMMARY"&&node===e.parentElement))return false;}}const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
    'const box=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {left:Math.round(r.left),top:Math.round(r.top),right:Math.round(r.right),bottom:Math.round(r.bottom),width:Math.round(r.width),height:Math.round(r.height)}};' +
    'const live=document.querySelector("#live-controls"),composerSurface=document.querySelector("#task-detail .composer"),input=document.querySelector("#change-text"),pause=document.querySelector("#pause-run"),stop=document.querySelector("#stop-run"),resume=document.querySelector("#continue-run"),models=document.querySelector("#composer-models"),summary=models?.querySelector("summary"),settings=document.querySelector("#task-model-settings");' +
    'const overlap=(a,b)=>a&&b?Math.max(0,Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)):0;' +
    'const modelsBox=box(models),surfaceBox=box(composerSurface),pauseBox=box(pause);' +
    'return {live:{visible:rendered(live)},input:{visible:rendered(input)},surface:box(composerSurface),' +
    'pause:{visible:rendered(pause),disabled:pause?.disabled??null,label:(pause?.textContent||"").trim(),box:pauseBox},' +
    'stop:{visible:rendered(stop),disabled:stop?.disabled??null,separate:!!stop&&!!pause&&stop!==pause,title:stop?.getAttribute("title")||""},' +
    'resume:{present:!!resume,hidden:resume?.hidden??null},' +
    'models:{visible:rendered(models),box:modelsBox,open:models?.hasAttribute("open")??null,disclosure:getComputedStyle(models,"::details-content")?.contentVisibility??null},' +
    'summary:{present:!!summary,visible:rendered(summary),disabled:summary?.disabled===true,tabIndex:summary?.tabIndex??null,label:(summary?.textContent||"").trim()},' +
    'settings:{present:!!settings,visible:rendered(settings),text:(settings?.textContent||"").trim()},' +
    'geometry:{modelsInsideSurface:!!modelsBox&&!!surfaceBox&&modelsBox.left>=surfaceBox.left-1&&modelsBox.right<=surfaceBox.right+1&&modelsBox.top>=surfaceBox.top-1&&modelsBox.bottom<=surfaceBox.bottom+1,rowOverlapWithPause:overlap(modelsBox,pauseBox)}};' +
  '}');
}

(async () => {
  const captures = [];
  try {
    server = spawn('python3', [fixture], {
      cwd: root,
      env: {...process.env, AUTOCODE_FIXTURE_ROOT: path.join(evidenceRoot, 'fixture-state')},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const info = await fixtureInfo();

    // The running scenario: a saved RUNNING conversation with a verified-alive
    // worker, so the conversation view shows its beside-composer controls.
    browser('set', 'viewport', '1440', '1024');
    const url = new URL(info.scenarios.running);
    url.searchParams.set('fixture_reload', '1');
    browser('open', url.toString());
    sessionHasPage = true;
    waitForDashboardReadiness('running scenario application initialization');
    waitForText('#task-title', 'interrupted-task recovery', 'running scenario render');

    // Closed state: the model entry's summary is the always-visible affordance
    // beside the composer; its content stays hidden until opened.
    let snapshot = composerModelSnapshot();
    assert.equal(snapshot.live.visible, true, 'the composer section is part of the conversation view');
    assert.equal(snapshot.input.visible, true, 'the chat composer is visible');
    assert.equal(snapshot.pause.visible, true, 'the pause control shows for a running conversation');
    assert.equal(snapshot.pause.disabled, false, 'the pause control is actionable');
    assert.equal(snapshot.pause.label, 'Pause after current step',
      'the pause control keeps its after-current-step label');
    assert.equal(snapshot.stop.visible, true, 'the stop control shows beside pause for a running conversation');
    assert.equal(snapshot.stop.separate, true, 'Stop is a separate control from Pause');
    assert.equal(snapshot.resume.present, true, 'the Continue/Resume control stays present while the conversation runs');
    assert.equal(snapshot.resume.hidden, true, 'Continue waits hidden while the conversation runs');
    assert.equal(snapshot.models.visible, true,
      'the model-route entry renders visibly beside the composer (computed CSS, non-empty rect)');
    assert.equal(snapshot.models.open, false, 'the model entry starts closed');
    assert.equal(snapshot.models.disclosure, 'hidden',
      'the engine declares the closed disclosure content hidden');
    assert.equal(snapshot.summary.present, true, 'the model entry exposes a summary toggle');
    assert.equal(snapshot.summary.visible, true, 'the model entry summary is visible beside the composer');
    assert.equal(snapshot.summary.disabled, false, 'the model entry summary is not disabled');
    assert.ok(snapshot.summary.tabIndex >= 0, 'the model entry summary is keyboard focusable');
    assert.match(snapshot.summary.label, /Models & reasoning/, 'the summary is labeled Models & reasoning');
    assert.equal(snapshot.settings.present, true, 'the model settings panel exists inside the entry');
    assert.equal(snapshot.settings.visible, false,
      'a closed disclosure truthfully hides its settings content: ' + JSON.stringify(snapshot));
    assert.equal(snapshot.geometry.modelsInsideSurface, true,
      'the model entry sits inside the composer surface of the conversation view');
    assert.ok(snapshot.geometry.rowOverlapWithPause > 0,
      'the model entry occupies the same beside-composer row band as the Pause control: ' + JSON.stringify(snapshot.geometry));
    let shot = path.join(evidenceRoot, 'composer-models-closed-desktop.png');
    browser('screenshot', shot);
    browser('wait', '100');
    captures.push({name: 'composer-models-closed-desktop', viewport: {width: 1440, height: 1024}, screenshot: path.relative(root, shot)});

    // Usability: open the disclosure through its real summary toggle.
    browser('click', '#composer-models summary');
    browser('wait', '--fn', 'document.querySelector("#composer-models")?.hasAttribute("open")===true');
    snapshot = composerModelSnapshot();
    assert.equal(snapshot.models.open, true, 'clicking the summary opens the model entry');
    assert.equal(snapshot.models.disclosure, 'visible',
      'the engine renders the opened disclosure content');
    assert.equal(snapshot.models.visible, true, 'the opened model entry stays visible beside the composer');
    assert.equal(snapshot.settings.visible, true, 'opening the entry reveals the saved model settings');
    assert.ok(snapshot.settings.text.includes('gpt-5.6-terra'),
      'the revealed settings read the saved model configuration: ' + snapshot.settings.text.slice(0, 200));
    const opened = data('()=>{' +
      'const rendered=e=>{if(!e||e.hidden)return false;for(let node=e;node;node=node.parentElement){if(node.tagName==="DETAILS"&&!node.hasAttribute("open"))return false;}const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
      'const form=document.querySelector("#task-reasoning-form"),status=document.querySelector("#task-reasoning-status"),save=document.querySelector("#save-task-reasoning"),select=document.querySelector("#task-terra-reasoning");' +
      'return {form:rendered(form),status:(status?.textContent||"").trim(),saveDisabled:save?.disabled??null,selectPresent:!!select,selectDisabled:select?.disabled??null};' +
    '}');
    assert.equal(opened.form, true, 'the reasoning form is visible inside the opened entry');
    assert.equal(opened.selectPresent, true, 'the starting-rung overrides render inside the opened entry');
    assert.equal(opened.selectDisabled, true,
      'while a step is active the reasoning overrides wait for the step boundary (true running state, not a decorative form)');
    assert.equal(opened.saveDisabled, true, 'the reasoning save action is gated while the step runs');
    assert.match(opened.status, /current step/, 'the opened entry explains its running-state gating');
    shot = path.join(evidenceRoot, 'composer-models-open-desktop.png');
    browser('screenshot', shot);
    browser('wait', '100');
    captures.push({name: 'composer-models-open-desktop', viewport: {width: 1440, height: 1024}, screenshot: path.relative(root, shot)});

    // The Stop control reflects real runner capability: this fixture runner is
    // not stop-capable, so Stop renders visibly but disabled; seeding the same
    // capability marker the real stop-capable runner publishes makes it
    // actionable beside Pause. (The durable submission itself is proven by the
    // credited CLI case through dashboard_backend.intervene.)
    assert.equal(snapshot.stop.disabled, true,
      'Stop honestly reports its non-stop-capable fixture runner while staying visible');
    assert.match(snapshot.stop.title, /does not support Stop|Finish and save/i,
      'the disabled Stop control explains its capability gate');
    // Seeding the same capability marker the real stop-capable runner publishes,
    // re-rendering through the real renderPrimaryAction and snapshotting the
    // controls happen inside one synchronous page eval: the dashboard's
    // two-second refresh re-renders from the fixture's honestly non-capable
    // served record, so a fixed wait between separate mutation and observation
    // evals could race that refresh and read the reset control. (The durable
    // submission itself is proven by the credited CLI case through
    // dashboard_backend.intervene.)
    const capable = composerModelSnapshot(
      'latestRun.interventions={...(latestRun.interventions||{}),stop_capable:true};renderPrimaryAction(latestRun);');
    assert.equal(capable.stop.disabled, false,
      'a stop-capable runner makes the beside-composer Stop control actionable');
    assert.equal(capable.pause.disabled, false, 'Pause keeps its separate actionable semantics');
    assert.equal(capable.pause.visible && capable.stop.visible && capable.models.visible, true,
      'Pause, Stop and the model entry remain visible together beside the composer');
    shot = path.join(evidenceRoot, 'composer-controls-stop-capable-desktop.png');
    browser('screenshot', shot);
    browser('wait', '100');
    captures.push({name: 'composer-controls-stop-capable-desktop', viewport: {width: 1440, height: 1024}, screenshot: path.relative(root, shot)});

    auditBrowserSession('final');
    const manifest = {
      generated_at: new Date().toISOString(),
      fixture: 'tools/dashboard/tests/unified_browser_fixture.py',
      scenario: 'running',
      disposable: true,
      screenshots: captures,
      source_snapshot: sourceSnapshot(),
      coverage: [
        'computed-CSS visibility of the beside-composer model-route entry, its summary toggle and the run controls for a running conversation',
        'summary focusability, click-to-open disclosure behavior and revealed saved-model content',
        'true running-state gating of the reasoning overrides while a step is active',
        'Stop capability gating beside a separate actionable Pause',
      ],
    };
    const manifestPath = path.join(evidenceRoot, 'composer-models-manifest.json');
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
    console.log('Browser-level beside-composer model-entry checks passed. Manifest: ' + path.relative(root, manifestPath));
  } finally {
    if (server && !server.killed) server.kill('SIGINT');
    try { browser('close'); } catch {}
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
