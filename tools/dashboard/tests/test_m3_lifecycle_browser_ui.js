// Browser-level M3 lifecycle verification against a disposable local fixture.
// The fixture has no real workspace, runner, or provider access.
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {dashboardReadinessExpression, waitForReadiness} = require('./browser_readiness');

const root = path.resolve(__dirname, '../../..');
const fixture = path.join(__dirname, 'unified_browser_fixture.py');
const evidenceRoot = process.env.DASHBOARD_M3_EVIDENCE_DIR
  ? path.resolve(root, process.env.DASHBOARD_M3_EVIDENCE_DIR)
  : path.join(root, '.autocode', 'evidence', 'm3-lifecycles', 'run-' + process.pid);
const fixtureRoot = path.join(evidenceRoot, 'fixture-state');
fs.mkdirSync(evidenceRoot, {recursive: true});
fs.mkdirSync(fixtureRoot, {recursive: true});
let session = 'dashboard-m3-lifecycles-' + process.pid;
let server;
let scenarioReload = 0;
let sessionHasPage = false;
const browserSessionAudits = [];

function browser(...args) {
  // A screenshot can leave the local bridge briefly unavailable. Retrying only
  // that documented EAGAIN preserves real dashboard/browser failures.
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

function auditBrowserSession(label) {
  if (!sessionHasPage) return;
  const errors = browser('errors').trim();
  assert.match(errors, /^(?:|\[\]|No page errors\.?|No errors\.?)$/i, label + ' browser page errors: ' + errors);
  const consoleMessages = browser('console').trim();
  assert.ok(!/\b(?:warn(?:ing)?|error)\b/i.test(consoleMessages), label + ' browser console warnings or errors: ' + consoleMessages);
  browserSessionAudits.push({
    label,
    session,
    page_errors: errors || 'No page errors.',
    console: consoleMessages || 'No browser console messages.',
  });
  sessionHasPage = false;
}

function freshBrowserSession(label) {
  // Long screenshot sequences can leave the local bridge contended. Each
  // fixture route is isolated, so rotate the browser before the separate
  // mutation families and audit the outgoing session first.
  auditBrowserSession('before-' + label);
  try { browser('close', '--all'); } catch {}
  session = 'dashboard-m3-lifecycles-' + process.pid + '-' + label;
}

function sourceSnapshot() {
  const files = [
    'tools/dashboard/dashboard.html',
    'tools/dashboard/dashboard.css',
    'tools/dashboard/dashboard_app.js',
    'tools/dashboard/tests/browser_readiness.js',
    'tools/dashboard/tests/test_browser_readiness.js',
    'tools/dashboard/tests/test_m3_lifecycle_browser_ui.js',
    'tools/dashboard/tests/unified_browser_fixture.py',
  ];
  return files.map(file => ({
    file,
    sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex'),
  }));
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

function waitForCondition(expression, label, attempts = 48) {
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    last = data('()=>{' +
      'const applicationGlobalDeclared=typeof latestRun!=="undefined";' +
      'const run=applicationGlobalDeclared?latestRun:null;' +
      'return {matched:applicationGlobalDeclared&&Boolean(' + expression + '),document_ready_state:document.readyState,application_global_declared:applicationGlobalDeclared,application_root_present:!!document.querySelector(".app-shell"),status:run?.status||null,stage:run?.active_stage?.stage||null,notice:document.querySelector("#dashboard-notice")?.textContent||""};' +
    '}');
    if (last.matched) return;
    if (attempt < attempts - 1) browser('wait', '250');
  }
  assert.fail(label + ' did not become true within ' + attempts * 250 + ' ms: ' + expression + '; final rendered state=' + JSON.stringify(last));
}

function waitForText(selector, phrase, label, attempts = 12) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    const text = data('()=>document.querySelector(' + JSON.stringify(selector) + ')?.textContent||""');
    if (text.includes(phrase)) return text;
    browser('wait', '250');
  }
  assert.fail(label + ' did not render ' + JSON.stringify(phrase) + ' within ' + attempts * 250 + ' ms');
}

function fixtureInfo() {
  return new Promise((resolve, reject) => {
    let output = '', errors = '';
    const deadline = setTimeout(() => reject(Error('Timed out waiting for the disposable M3 fixture.')), 15000);
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
      if (code) reject(Error('M3 fixture exited with ' + code + ': ' + (errors || output)));
    });
  });
}

function openScenario(info, name, width, height) {
  browser('set', 'viewport', String(width), String(height));
  const url = new URL(info.scenarios[name]);
  url.searchParams.set('fixture_reload', String(++scenarioReload));
  browser('open', url.toString());
  sessionHasPage = true;
  waitForDashboardReadiness(name + ' scenario application initialization');
  const expected = name === 'unavailable-model' || name.startsWith('flow-model')
    ? 'unavailable saved planning model'
    : 'interrupted build';
  waitForCondition('document.querySelector("#task-title")?.textContent.includes(' + JSON.stringify(expected) + ')', name + ' scenario render');
  data('()=>{[document.scrollingElement,...document.querySelectorAll(".page,.thread-scroll")].filter(Boolean).forEach(e=>{e.scrollTop=0;e.scrollLeft=0;});return true;}');
}

function capture(captures, name, viewport) {
  const file = path.join(evidenceRoot, name + '.png');
  browser('screenshot', file);
  browser('wait', '100');
  captures.push({name, viewport, screenshot: path.relative(root, file)});
}

function openTaskSettings() {
  data('()=>{const menu=document.querySelector("#task-detail .context-menu");menu.open=true;return menu.open;}');
}

function targetAndOverflow() {
  return data('()=>{' +
    'const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return !e.hidden&&s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
    'const targets=[...document.querySelectorAll("button,input,select,textarea,summary,[role=tab]")].filter(visible).map(e=>{const r=e.getBoundingClientRect();return {name:e.id||e.textContent.trim(),width:Math.round(r.width),height:Math.round(r.height)}});' +
    'return {horizontalOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,targets,undersized:targets.filter(t=>t.width<44||t.height<44)};' +
  '}');
}

function creationCatalogueSnapshot() {
  return data('()=>{' +
    'const visible=e=>{if(!e||e.hidden)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth};' +
    'const box=e=>{const r=e.getBoundingClientRect();return {left:Math.round(r.left),top:Math.round(r.top),right:Math.round(r.right),bottom:Math.round(r.bottom),width:Math.round(r.width),height:Math.round(r.height)}};' +
    'const submit=document.querySelector("#create-submit"),area=document.querySelector("#create-submit-area"),gate=document.querySelector("#create-model-catalogue-gate"),status=document.querySelector("#create-model-catalogue-status"),retry=document.querySelector("#retry-models"),detail=document.querySelector("#model-catalogue-status");' +
    'const submitBox=box(submit),gateBox=box(gate),x=Math.max(0,gateBox.left-submitBox.right,submitBox.left-gateBox.right),y=Math.max(0,gateBox.top-submitBox.bottom,submitBox.top-gateBox.bottom);' +
    'return {submit:{disabled:submit.disabled,description:submit.getAttribute("aria-describedby"),parent:submit.parentElement.id,box:submitBox},area:box(area),gate:{visible:visible(gate),parent:gate.parentElement.id,box:gateBox,distance:Math.round(Math.hypot(x,y))},status:{visible:visible(status),text:status.textContent,role:status.getAttribute("role"),live:status.getAttribute("aria-live"),parent:status.parentElement.id},retry:{visible:visible(retry),disabled:retry.disabled,parent:retry.parentElement.id,text:retry.textContent},detailLive:detail.getAttribute("aria-live"),horizontalOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth};' +
  '}');
}

function setCreationCatalogue(payload) {
  data('()=>{syncModelOptions(' + JSON.stringify(payload) + ');document.querySelector("#create-submit-area").scrollIntoView({block:"center"});return true;}');
  browser('wait', '80');
  return creationCatalogueSnapshot();
}

function assertBlockedCreationCatalogue(snapshot, label, pattern, retryExpected) {
  assert.equal(snapshot.submit.disabled, true, label + ' disables Start conversation');
  assert.equal(snapshot.submit.description, 'create-model-catalogue-status', label + ' associates the visible explanation');
  assert.equal(snapshot.gate.visible, true, label + ' renders a visible creation gate');
  assert.equal(snapshot.status.visible, true, label + ' renders a visible status message');
  assert.match(snapshot.status.text, pattern, label + ' keeps its state explanation visible');
  assert.equal(snapshot.status.role, 'status', label + ' exposes one announced status');
  assert.equal(snapshot.status.live, 'polite', label + ' announces the visible status');
  assert.equal(snapshot.detailLive, null, label + ' does not duplicate the announcement in Models & reasoning');
  assert.equal(snapshot.gate.parent, 'create-submit-area', label + ' keeps the gate beside the submit action');
  assert.equal(snapshot.submit.parent, 'create-submit-area', label + ' keeps the submit action in the same area');
  assert.ok(snapshot.gate.distance <= 16, label + ' keeps the visible gate adjacent to Start conversation: ' + JSON.stringify(snapshot));
  assert.equal(snapshot.retry.visible, retryExpected, label + ' Retry catalogue visibility');
  assert.equal(snapshot.retry.parent, 'create-model-catalogue-gate', label + ' keeps Retry catalogue with its explanation');
  assert.equal(snapshot.retry.disabled, !retryExpected, label + ' Retry catalogue enabled state');
  assert.equal(snapshot.horizontalOverflow, false, label + ' does not create page-level horizontal overflow');
}

function creationCatalogueEvidence(snapshot) {
  return {
    submit_disabled: snapshot.submit.disabled,
    accessible_description: snapshot.submit.description,
    gate_visible: snapshot.gate.visible,
    gate_distance_from_submit: snapshot.gate.distance,
    status_visible: snapshot.status.visible,
    status: snapshot.status.text,
    retry_visible: snapshot.retry.visible,
    retry_disabled: snapshot.retry.disabled,
    horizontal_overflow: snapshot.horizontalOverflow,
  };
}

(async () => {
  const captures = [], observations = [];
  try {
    server = spawn('python3', [fixture], {
      cwd: root,
      env: {...process.env, AUTOCODE_FIXTURE_ROOT: fixtureRoot},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const info = await fixtureInfo();

    // Supported replacement: the local state deliberately marks one saved
    // model unavailable without changing the fixture's persisted saved record.
    openScenario(info, 'unavailable-model', 1440, 1024);
    data('()=>{' +
      'syncModelOptions({usable:true,models:["openai/gpt-5.6-terra","openai/gpt-5.6-sol"],reasoning_levels:{astra:["medium","high"],terra:["medium","high"]}});' +
      'document.querySelector("#task-detail .context-menu").open=true;' +
      'return {saved:latestRun.model_settings.roles.astra,selector:!!document.querySelector("#task-astra-replacement")};' +
    '}');
    browser('wait', '--fn', 'document.querySelector("#task-astra-replacement")?.disabled===false');
    let replacement = data('()=>({text:document.querySelector("#task-model-settings").textContent,selected:document.querySelector("#task-astra-replacement").value,saved:latestRun.model_settings.roles.astra})');
    assert.equal(replacement.saved, 'openai/retired-model');
    assert.match(replacement.text, /Saved model unavailable: openai\/retired-model/);
    assert.equal(replacement.selected, '');
    capture(captures, 'model-supported-before-selection-desktop', {width: 1440, height: 1024});

    data('()=>{const select=document.querySelector("#task-astra-replacement");select.value="openai/gpt-5.6-terra";select.dispatchEvent(new Event("change",{bubbles:true}));return {saved:latestRun.model_settings.roles.astra,text:document.querySelector("#task-model-settings").textContent};}');
    replacement = data('()=>({text:document.querySelector("#task-model-settings").textContent,saved:latestRun.model_settings.roles.astra,confirmDisabled:[...document.querySelectorAll("#task-model-settings button")].find(b=>b.textContent.trim()==="Confirm model replacement")?.disabled})');
    assert.equal(replacement.saved, 'openai/retired-model', 'selection must not rewrite the saved model');
    assert.match(replacement.text, /Proposed model: openai\/gpt-5\.6-terra/);
    assert.equal(replacement.confirmDisabled, false);
    capture(captures, 'model-supported-selected-unconfirmed-desktop', {width: 1440, height: 1024});

    // Confirmation is a separate pending mutation. Keep the saved value visible
    // and prove that a second click cannot create a duplicate request.
    replacement = data('()=>{' +
      'globalThis.__m3ModelRequests=0;api=()=>new Promise(()=>{globalThis.__m3ModelRequests+=1;});' +
      '[...document.querySelectorAll("#task-model-settings button")].find(button=>button.textContent.trim()==="Confirm model replacement").click();' +
      'confirmModelReplacement(latestRun,"astra");' +
      'return {saved:latestRun.model_settings.roles.astra,requests:globalThis.__m3ModelRequests,text:document.querySelector("#task-model-settings").textContent};' +
    '}');
    assert.equal(replacement.saved, 'openai/retired-model');
    assert.equal(replacement.requests, 1, 'confirming prevents a duplicate replacement request');
    assert.match(replacement.text, /Confirmation is being recorded\. Duplicate confirmation is disabled/);
    capture(captures, 'model-supported-confirming-desktop', {width: 1440, height: 1024});

    // Reload the disposable scenario to abandon the browser-only deferred
    // request before exercising the remaining deterministic fixture states.
    openScenario(info, 'unavailable-model', 1440, 1024);
    data('()=>{syncModelOptions({usable:true,models:["openai/gpt-5.6-terra","openai/gpt-5.6-sol"]});document.querySelector("#task-detail .context-menu").open=true;return true;}');

    // A persisted uncertain record has a distinct status-only reconciliation
    // path rather than an automatic mutation retry.
    data('()=>{' +
      'syncModelOptions({usable:true,models:["openai/gpt-5.6-terra"]});' +
      'saveModelReplacement(latestRun,"astra",{state:"unconfirmed",saved:"openai/retired-model",proposed:"openai/gpt-5.6-terra",request_id:"req-model-uncertain"});' +
      'renderTaskModelSettings(latestRun);return document.querySelector("#task-model-settings").textContent;' +
    '}');
    replacement = data('()=>({text:document.querySelector("#task-model-settings").textContent,reconcile:[...document.querySelectorAll("#task-model-settings button")].find(b=>b.textContent.trim()==="Retry status")?.dataset.staleSafe})');
    assert.match(replacement.text, /Confirmation is unconfirmed\. Refresh status and reconcile/);
    assert.equal(replacement.reconcile, 'true');
    capture(captures, 'model-supported-unconfirmed-desktop', {width: 1440, height: 1024});

    // Unsupported remains visibly present, disabled, and described beside the
    // control. The saved value is still the original unavailable value.
    openScenario(info, 'unavailable-model', 1024, 768);
    data('()=>{' +
      'syncModelOptions({usable:true,models:[]});document.querySelector("#task-detail .context-menu").open=true;' +
      'return document.querySelector("#task-model-settings").textContent;' +
    '}');
    replacement = data('()=>{const select=document.querySelector("#task-astra-replacement");return {text:document.querySelector("#task-model-settings").textContent,disabled:select.disabled,description:select.getAttribute("aria-describedby"),saved:latestRun.model_settings.roles.astra};}');
    assert.equal(replacement.saved, 'openai/retired-model');
    assert.equal(replacement.disabled, true);
    assert.equal(replacement.description, 'task-astra-replacement-reason');
    assert.match(replacement.text, /compatible-model catalogue is unavailable\. Retry catalogue/);
    const tabletLayout = data('()=>{const now=document.querySelector(".task-now"),lead=now.children[0],actions=now.querySelector(".task-actions"),step=document.querySelector("#task-subtitle"),box=e=>{const r=e.getBoundingClientRect();return {width:Math.round(r.width),height:Math.round(r.height)}};return {lead:box(lead),actions:box(actions),step:box(step),stepOverflow:step.scrollWidth>step.clientWidth+1,horizontalOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth};}');
    assert.ok(tabletLayout.lead.width >= 300, 'model gate leaves a readable tablet column for the current step');
    assert.equal(tabletLayout.stepOverflow, false);assert.equal(tabletLayout.horizontalOverflow, false);
    capture(captures, 'model-unsupported-disabled-tablet', {width: 1024, height: 768});
    observations.push({name: 'unsupported model tablet layout', ...tabletLayout});

    // A rejected confirmation keeps the unavailable saved model, shows the
    // failure, and requires a fresh explicit confirmation rather than replay.
    openScenario(info, 'unavailable-model', 1440, 1024);
    data('()=>{' +
      'syncModelOptions({usable:true,models:["openai/gpt-5.6-terra"]});document.querySelector("#task-detail .context-menu").open=true;' +
      'saveModelReplacement(latestRun,"astra",{state:"failed",saved:"openai/retired-model",proposed:"openai/gpt-5.6-terra",request_id:"req-model-failed",error:"Fixture confirmation was rejected."});' +
      'renderTaskModelSettings(latestRun);return {saved:latestRun.model_settings.roles.astra,text:document.querySelector("#task-model-settings").textContent};' +
    '}');
    replacement = data('()=>({saved:latestRun.model_settings.roles.astra,text:document.querySelector("#task-model-settings").textContent,confirm:[...document.querySelectorAll("#task-model-settings button")].find(button=>button.textContent.trim()==="Confirm model replacement")?.disabled})');
    assert.equal(replacement.saved, 'openai/retired-model');
    assert.equal(replacement.confirm, false);
    assert.match(replacement.text, /Fixture confirmation was rejected/);
    capture(captures, 'model-supported-failed-desktop', {width: 1440, height: 1024});
    data('()=>saveModelReplacement(latestRun,"astra",null)');

    // Only an actual, separate confirmation request updates the persisted saved
    // value. Run that transition in independent fixture copies at every target
    // viewport, rather than treating a DOM-only specimen as lifecycle evidence.
    const lifecycleViewports = [
      {name: 'desktop', width: 1440, height: 1024},
      {name: 'tablet', width: 1024, height: 768},
      {name: 'mobile', width: 390, height: 844},
    ];
    const confirmedModelFlows = [];
    for (const viewport of lifecycleViewports) {
      openScenario(info, 'flow-model-' + viewport.name, viewport.width, viewport.height);
      data('()=>{syncModelOptions({usable:true,models:["openai/gpt-5.6-terra","openai/gpt-5.6-sol"],reasoning_levels:{astra:["medium","high"]}});document.querySelector("#task-detail .context-menu").open=true;const select=document.querySelector("#task-astra-replacement");select.value="openai/gpt-5.6-terra";select.dispatchEvent(new Event("change",{bubbles:true}));return {saved:latestRun.model_settings.roles.astra,proposed:select.value};}');
      const beforeConfirm = data('()=>({saved:latestRun.model_settings.roles.astra,proposed:document.querySelector("#task-astra-replacement").value,confirmDisabled:[...document.querySelectorAll("#task-model-settings button")].find(button=>button.textContent.trim()==="Confirm model replacement")?.disabled})');
      assert.equal(beforeConfirm.saved, 'openai/retired-model', viewport.name + ' keeps the saved unavailable model before confirmation');
      assert.equal(beforeConfirm.proposed, 'openai/gpt-5.6-terra', viewport.name + ' keeps the proposal in its separate selector');
      assert.equal(beforeConfirm.confirmDisabled, false, viewport.name + ' exposes a separate enabled confirmation action');
      browser('click', '#task-model-settings button.primary');
      browser('wait', '--fn', 'latestRun.model_settings?.roles?.astra==="openai/gpt-5.6-terra"');
      const confirmed = data('()=>({saved:latestRun.model_settings.roles.astra,text:document.querySelector("#task-model-settings").textContent,action:(latestRun.actions||[]).find(action=>action.label?.includes("Confirm model replacement"))||null})');
      assert.equal(confirmed.saved, 'openai/gpt-5.6-terra', viewport.name + ' applies the requested model only after confirmation');
      assert.match(confirmed.text, /Confirmed replacement: openai\/gpt-5\.6-terra/);
      assert.match(confirmed.action?.id || '', /fixture-model-replacement-/);
      capture(captures, 'model-supported-confirmed-' + viewport.name, viewport);
      confirmedModelFlows.push({viewport: viewport.name, before: beforeConfirm, confirmed});
    }
    observations.push({name: 'supported model replacement', flows: confirmedModelFlows});

    // The fixture returns a real failed action response. The original saved
    // model remains visible and only an explicit later confirmation is offered.
    openScenario(info, 'flow-model-failed-desktop', 1440, 1024);
    data('()=>{syncModelOptions({usable:true,models:["openai/gpt-5.6-sol"]});document.querySelector("#task-detail .context-menu").open=true;const select=document.querySelector("#task-astra-replacement");select.value="openai/gpt-5.6-sol";select.dispatchEvent(new Event("change",{bubbles:true}));return true;}');
    browser('click', '#task-model-settings button.primary');
    browser('wait', '--fn', 'document.querySelector("#task-model-settings")?.textContent.includes("Fixture replacement was rejected")');
    const failedModel = data('()=>({saved:latestRun.model_settings.roles.astra,text:document.querySelector("#task-model-settings").textContent,action:(latestRun.actions||[]).find(action=>action.label?.includes("Confirm model replacement"))||null,retryDisabled:[...document.querySelectorAll("#task-model-settings button")].find(button=>button.textContent.trim()==="Confirm model replacement")?.disabled})');
    assert.equal(failedModel.saved, 'openai/retired-model', 'failed confirmation preserves the exact saved model');
    assert.equal(failedModel.action?.status, 'failed', 'failed confirmation records the authoritative failed action response');
    assert.equal(failedModel.retryDisabled, false, 'failed confirmation offers only a fresh explicit retry');
    capture(captures, 'model-supported-failed-action-desktop', {width: 1440, height: 1024});

    // An uncertain response is not retried blindly. Retry status performs an
    // authoritative read that reconciles the original request and its receipt.
    openScenario(info, 'flow-model-uncertain-desktop', 1440, 1024);
    data('()=>{syncModelOptions({usable:true,models:["openai/gpt-5.6-sol"]});document.querySelector("#task-detail .context-menu").open=true;const select=document.querySelector("#task-astra-replacement");select.value="openai/gpt-5.6-sol";select.dispatchEvent(new Event("change",{bubbles:true}));return true;}');
    browser('click', '#task-model-settings button.primary');
    browser('wait', '--fn', 'document.querySelector("#task-model-settings")?.textContent.includes("Confirmation is unconfirmed")');
    const uncertainModel = data('()=>({saved:latestRun.model_settings.roles.astra,text:document.querySelector("#task-model-settings").textContent,action:(latestRun.actions||[]).find(action=>action.label?.includes("Confirm model replacement"))||null,retry:[...document.querySelectorAll("#task-model-settings button")].find(button=>button.textContent.trim()==="Retry status")?.disabled})');
    assert.equal(uncertainModel.saved, 'openai/retired-model', 'uncertain confirmation retains the original saved model');
    assert.equal(uncertainModel.action?.status, 'launch_failed', 'uncertain confirmation records an unconfirmed action response');
    assert.equal(uncertainModel.retry, false, 'uncertain confirmation keeps status reconciliation available');
    capture(captures, 'model-supported-unconfirmed-action-desktop', {width: 1440, height: 1024});
    browser('click', '#task-model-settings button.text-button');
    browser('wait', '--fn', 'latestRun.model_settings?.roles?.astra==="openai/gpt-5.6-sol"');
    const reconciledModel = data('()=>({saved:latestRun.model_settings.roles.astra,text:document.querySelector("#task-model-settings").textContent,receipt:(latestRun.actions||[]).find(action=>action.label?.includes("Confirm model replacement"))?.id||""})');
    assert.equal(reconciledModel.saved, 'openai/gpt-5.6-sol', 'status reconciliation applies the already-recorded requested model');
    assert.match(reconciledModel.text, /Confirmed replacement: openai\/gpt-5\.6-sol/);
    assert.match(reconciledModel.text, /Receipt: fixture-model-uncertain-/);
    assert.match(reconciledModel.receipt, /fixture-model-uncertain-/);
    capture(captures, 'model-supported-reconciled-desktop', {width: 1440, height: 1024});
    observations.push({name: 'model failed and uncertain action responses', failed: failedModel, uncertain: uncertainModel, reconciled: reconciledModel});

    // Catalogue loading, empty, failure, and recovery stay visible beside the
    // blocked creation action at every accepted viewport. The detailed model
    // controls remain closed here, so hidden DOM text cannot satisfy this test.
    for(const viewport of [{name:'desktop',width:1440,height:1024},{name:'tablet',width:1024,height:768},{name:'mobile',width:390,height:844}]){
      browser('set', 'viewport', String(viewport.width), String(viewport.height));browser('wait', '150');
      data('()=>{showNewTask();syncModelOptions({usable:true,models:["openai/gpt-5.6-terra"],reasoning_levels:{terra:["medium","high"]}});const select=document.querySelector("#terra-model");select.value="openai/gpt-5.6-terra";document.querySelector(".models-disclosure").open=false;return select.value;}');
      let catalogue = setCreationCatalogue({loading:true});
      const loading=creationCatalogueEvidence(catalogue);
      assertBlockedCreationCatalogue(catalogue, viewport.name+' loading', /Loading compatible models/, false);
      assert.equal(data('()=>document.querySelector("#terra-model").value'), 'openai/gpt-5.6-terra', viewport.name+' loading preserves the saved selection');
      capture(captures, 'model-catalogue-loading-'+viewport.name, viewport);

      catalogue = setCreationCatalogue({usable:true,models:[]});
      const empty=creationCatalogueEvidence(catalogue);
      assertBlockedCreationCatalogue(catalogue, viewport.name+' empty', /No compatible models are currently available/, true);
      assert.equal(data('()=>document.querySelector("#terra-model").value'), 'openai/gpt-5.6-terra', viewport.name+' empty preserves the saved selection');
      assert.equal(catalogue.retry.text.trim(), 'Retry catalogue');
      capture(captures, 'model-catalogue-empty-'+viewport.name, viewport);

      catalogue = setCreationCatalogue({error:'Fixture catalogue request failed.'});
      const failed=creationCatalogueEvidence(catalogue);
      assertBlockedCreationCatalogue(catalogue, viewport.name+' failed', /Fixture catalogue request failed/, true);
      assert.equal(data('()=>document.querySelector("#terra-model").value'), 'openai/gpt-5.6-terra', viewport.name+' failure preserves the saved selection');
      capture(captures, 'model-catalogue-failed-'+viewport.name, viewport);

      catalogue = setCreationCatalogue({usable:true,models:['openai/gpt-5.6-terra'],reasoning_levels:{terra:['medium','high']}});
      const recovered = data('()=>({selector:document.querySelector("#terra-model").disabled,start:document.querySelector("#create-submit").disabled,description:document.querySelector("#create-submit").getAttribute("aria-describedby"),gateVisible:!document.querySelector("#create-model-catalogue-gate").hidden,value:document.querySelector("#terra-model").value,levels:[...document.querySelector("#terra-reasoning-effort").options].map(o=>o.value)})');
      assert.equal(recovered.selector, false, viewport.name+' recovery re-enables the model selector');
      assert.equal(recovered.start, false, viewport.name+' recovery re-enables Start conversation');
      assert.equal(recovered.description, null, viewport.name+' recovery removes the unavailable-state description');
      assert.equal(recovered.gateVisible, false, viewport.name+' recovery hides the unavailable-state gate');
      assert.equal(recovered.value, 'openai/gpt-5.6-terra', viewport.name+' recovery keeps the saved selection');
      assert.deepEqual(recovered.levels, ['medium', 'high'], viewport.name+' recovery exposes only compatible reasoning levels');
      assert.equal(catalogue.horizontalOverflow, false, viewport.name+' recovery does not create page-level horizontal overflow');
      if(viewport.name==='mobile')assert.equal(catalogue.submit.box.width, catalogue.area.width, 'mobile Start conversation is full width');
      capture(captures, 'model-catalogue-recovered-'+viewport.name, viewport);
      observations.push({name: 'catalogue '+viewport.name, loading, empty, failed, recovered});
    }

    // Dialogs use the safe Cancel focus, trap Tab focus, dismiss through Escape
    // and scrim interaction, then return focus to the invoking control.
    openScenario(info, 'paused', 390, 844);openTaskSettings();
    const archiveInvoker = data('()=>{const button=document.querySelector("#archive-task");button.focus();const active=document.activeElement.id;button.click();return active;}');
    assert.equal(archiveInvoker, 'archive-task');
    let dialog = data('()=>({open:document.querySelector("#task-archive-dialog").open,active:document.activeElement.id,opener:document.querySelector("#task-archive-dialog")._returnFocus?.id||"",openerConnected:!!document.querySelector("#task-archive-dialog")._returnFocus?.isConnected,text:document.querySelector("#task-archive-dialog").textContent})');
    assert.equal(dialog.open, true);assert.equal(dialog.active, 'task-archive-cancel');assert.equal(dialog.opener, 'archive-task');assert.equal(dialog.openerConnected,true);
    assert.match(dialog.text, /Archive task\?/);assert.match(dialog.text, /Files, messages, history, and worker state remain/);assert.match(dialog.text, /A running worker is not stopped/);
    capture(captures, 'archive-dialog-mobile', {width: 390, height: 844});
    browser('press', 'Shift+Tab');assert.equal(data('()=>document.activeElement.id'), 'task-archive-confirm');
    dialog = data('()=>{const node=document.querySelector("#task-archive-dialog");node.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));return {open:node.open,active:document.activeElement.id};}');
    assert.equal(dialog.open, false);assert.equal(dialog.active, 'archive-task', JSON.stringify(dialog));
    data('()=>{const button=document.querySelector("#archive-task");button.focus();button.click();return true;}');
    browser('press', 'Escape');assert.equal(data('()=>document.querySelector("#task-archive-dialog").open'), false, 'native Escape dismisses archive review');
    data('()=>{const button=document.querySelector("#archive-task");button.focus();button.click();return true;}');
    data('()=>{const dialog=document.querySelector("#task-archive-dialog");dialog.dispatchEvent(new MouseEvent("click",{bubbles:true}));return dialog.open;}');
    assert.equal(data('()=>document.querySelector("#task-archive-dialog").open'), false, 'scrim-target dismissal closes archive review');

    data('()=>{archiveUncertain.set(latestRun.run,{action:"archive",requestId:"req-archive-uncertain"});reviewTaskArchive(latestRun);return true;}');
    dialog = data('()=>({confirm:document.querySelector("#task-archive-confirm").disabled,reconcile:document.querySelector("#task-archive-reconcile").hidden,text:document.querySelector("#task-archive-uncertain").textContent})');
    assert.equal(dialog.confirm, true);assert.equal(dialog.reconcile, false);assert.match(dialog.text, /req-archive-uncertain/);
    capture(captures, 'archive-unconfirmed-mobile', {width: 390, height: 844});
    browser('press', 'Escape');

    openScenario(info, 'paused', 390, 844);openTaskSettings();
    const projectInvoker = data('()=>{document.querySelector("#task-detail .task-project-details").open=true;const button=document.querySelector("[aria-label^=\\"Remove project\\"]");button.focus();const active=document.activeElement.dataset.focusKey||document.activeElement.id;button.click();return active;}');
    assert.match(projectInvoker, /project-remove/);
    dialog = data('()=>({open:document.querySelector("#project-removal-dialog").open,active:document.activeElement.id,text:document.querySelector("#project-removal-dialog").textContent})');
    assert.equal(dialog.open, true);assert.equal(dialog.active, 'project-removal-cancel');
    assert.match(dialog.text, /Remove from dashboard\?/);assert.match(dialog.text, /Files, runs, history, registry records, and active work are not deleted/);
    capture(captures, 'remove-project-dialog-mobile', {width: 390, height: 844});
    browser('press', 'Shift+Tab');assert.equal(data('()=>document.activeElement.id'), 'project-removal-confirm');
    dialog = data('()=>{const node=document.querySelector("#project-removal-dialog");node.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));return {open:node.open,active:document.activeElement.dataset.focusKey||document.activeElement.id};}');
    assert.equal(dialog.open, false);assert.match(dialog.active, /project-remove/);

    // These are real POST transitions against separate disposable fixture
    // copies. Each flow proves confirmation, Undo, and a later durable Restore
    // through a fresh task read rather than relying on optimistic DOM state.
    const archiveFlows = [];
    for (const viewport of lifecycleViewports) {
      freshBrowserSession('archive-' + viewport.name);
      const scenario = 'flow-archive-' + viewport.name;
      openScenario(info, scenario, viewport.width, viewport.height);openTaskSettings();
      const archiveIdentity = data('()=>({run:latestRun.run,workspace:latestRun.workspace,task:latestRun.task})');
      const archiveDialogOpened = data('()=>{const button=document.querySelector("#archive-task");button.click();return {disabled:button.disabled,open:document.querySelector("#task-archive-dialog").open,focus:document.activeElement.id};}');
      assert.equal(archiveDialogOpened.disabled, false, viewport.name + ' archive control remains actionable before confirmation');
      assert.equal(archiveDialogOpened.open, true, viewport.name + ' Archive task control opens its real confirmation dialog');
      browser('click', '#task-archive-confirm');
      browser('wait', '--fn', 'document.querySelector("#archive-notice")?.textContent.includes("Task archived")');
      const archived = data(`()=>({notice:document.querySelector("#archive-notice").textContent,archived:(latestData?.archived_tasks||[]).some(row=>row.run===${JSON.stringify(archiveIdentity.run)}),activeTitle:document.querySelector("#task-title")?.textContent||""})`);
      assert.match(archived.notice, /Task archived · Undo/);
      assert.equal(archived.archived, true, viewport.name + ' archive confirmation records the disposable task as archived');
      capture(captures, 'archive-confirmed-' + viewport.name, viewport);
      browser('click', '#archive-notice button');
      browser('wait', '--fn', 'document.querySelector("#archive-notice")?.textContent.includes("Task restored")');
      const undone = data(`()=>({notice:document.querySelector("#archive-notice").textContent,archived:(latestData?.archived_tasks||[]).some(row=>row.run===${JSON.stringify(archiveIdentity.run)})})`);
      assert.match(undone.notice, /Task restored/);assert.equal(undone.archived, false, viewport.name + ' Undo restores the archived task');
      capture(captures, 'archive-undo-' + viewport.name, viewport);
      openScenario(info, scenario, viewport.width, viewport.height);
      const undoRead = data('()=>({archived:!!latestRun.task_archived,title:document.querySelector("#task-title").textContent})');
      assert.equal(undoRead.archived, false, viewport.name + ' fresh task read confirms Undo was durable');

      openTaskSettings();const restoreArchiveDialog = data('()=>{document.querySelector("#archive-task").click();return document.querySelector("#task-archive-dialog").open;}');assert.equal(restoreArchiveDialog, true, viewport.name + ' opens Archive task before durable Restore');browser('click', '#task-archive-confirm');
      browser('wait', '--fn', 'document.querySelector("#archive-notice")?.textContent.includes("Task archived")');
      browser('click', '#archive-notice button:nth-of-type(2)');
      browser('wait', '--fn', '!document.querySelector("#archived")?.hidden&&document.querySelector("#archived-task-list button")');
      browser('click', '#archived-task-list button');
      browser('wait', '--fn', 'document.querySelector("#archive-notice")?.textContent.includes("Task restored")');
      openScenario(info, scenario, viewport.width, viewport.height);
      const restored = data('()=>({archived:!!latestRun.task_archived,title:document.querySelector("#task-title").textContent})');
      assert.equal(restored.archived, false, viewport.name + ' Restore task survives a separate fresh read');
      capture(captures, 'archive-durable-restore-' + viewport.name, viewport);
      archiveFlows.push({viewport: viewport.name, identity: archiveIdentity, archived, undone, undo_read: undoRead, restored});
    }
    observations.push({name: 'archive confirmation, Undo, and durable Restore', flows: archiveFlows});

    const removalFlows = [];
    for (const viewport of lifecycleViewports) {
      freshBrowserSession('remove-' + viewport.name);
      const scenario = 'flow-remove-' + viewport.name;
      openScenario(info, scenario, viewport.width, viewport.height);openTaskSettings();
      const removalIdentity = data('()=>({workspace:latestRun.workspace,run:latestRun.run,task:latestRun.task})');
      data('()=>{document.querySelector("#task-detail .task-project-details").open=true;return true;}');
      const projectDialogOpened = data('()=>{const button=document.querySelector("button[aria-label^=\\"Remove project\\"]");button.click();return {disabled:button.disabled,open:document.querySelector("#project-removal-dialog").open,focus:document.activeElement.id};}');
      assert.equal(projectDialogOpened.disabled, false, viewport.name + ' project removal control remains actionable before confirmation');
      assert.equal(projectDialogOpened.open, true, viewport.name + ' Remove project control opens its real confirmation dialog');
      browser('click', '#project-removal-confirm');
      browser('wait', '--fn', 'document.querySelector("#project-notice")?.textContent.includes("Project removed from dashboard")');
      const removed = data(`()=>({notice:document.querySelector("#project-notice").textContent,removed:(latestData?.removed_projects||[]).some(project=>project.workspace===${JSON.stringify(removalIdentity.workspace)})})`);
      assert.match(removed.notice, /Project removed from dashboard · Undo/);
      assert.equal(removed.removed, true, viewport.name + ' removal confirmation records the project as hidden');
      capture(captures, 'remove-confirmed-' + viewport.name, viewport);
      browser('click', '#project-notice button');
      browser('wait', '--fn', 'document.querySelector("#project-notice")?.textContent.includes("Project restored to dashboard")');
      const removalUndone = data(`()=>({notice:document.querySelector("#project-notice").textContent,removed:(latestData?.removed_projects||[]).some(project=>project.workspace===${JSON.stringify(removalIdentity.workspace)})})`);
      assert.match(removalUndone.notice, /Project restored to dashboard/);assert.equal(removalUndone.removed, false, viewport.name + ' removal Undo restores dashboard discovery');
      capture(captures, 'remove-undo-' + viewport.name, viewport);
      openScenario(info, scenario, viewport.width, viewport.height);
      const removalUndoRead = data('()=>({removed:!!latestRun.project_removed,bannerHidden:document.querySelector("#task-removed-banner").hidden})');
      assert.equal(removalUndoRead.removed, false, viewport.name + ' fresh task read confirms removal Undo was durable');
      assert.equal(removalUndoRead.bannerHidden, true, viewport.name + ' restored project does not show a removed-project banner');

      openTaskSettings();data('()=>{document.querySelector("#task-detail .task-project-details").open=true;return true;}');const restoreProjectDialog = data('()=>{document.querySelector("button[aria-label^=\\"Remove project\\"]").click();return document.querySelector("#project-removal-dialog").open;}');assert.equal(restoreProjectDialog, true, viewport.name + ' opens Remove project before durable Restore');browser('click', '#project-removal-confirm');
      browser('wait', '--fn', 'document.querySelector("#project-notice")?.textContent.includes("Project removed from dashboard")');
      browser('click', '#project-notice button:nth-of-type(2)');
      browser('wait', '--fn', '!document.querySelector("#settings")?.hidden&&document.querySelector("#removed-projects button")');
      const restoreProjectClick = data('()=>{const button=document.querySelector("#removed-projects button");button.click();return {disabled:button.disabled,label:button.textContent.trim()};}');
      assert.equal(restoreProjectClick.disabled, false, viewport.name + ' Restore project remains actionable');
      assert.match(waitForText('#project-notice', 'Project restored to dashboard', viewport.name + ' durable project restore'), /Project restored to dashboard/);
      openScenario(info, scenario, viewport.width, viewport.height);
      const removalRestored = data('()=>({removed:!!latestRun.project_removed,bannerHidden:document.querySelector("#task-removed-banner").hidden})');
      assert.equal(removalRestored.removed, false, viewport.name + ' Restore project survives a separate fresh read');
      assert.equal(removalRestored.bannerHidden, true, viewport.name + ' Restore project returns the normal task view');
      capture(captures, 'remove-durable-restore-' + viewport.name, viewport);
      removalFlows.push({viewport: viewport.name, identity: removalIdentity, removed, undo: removalUndone, undo_read: removalUndoRead, restored: removalRestored});
    }
    observations.push({name: 'project removal confirmation, Undo, and durable Restore', flows: removalFlows});

    // A stale selected task keeps useful content and status reads/navigation but
    // disables mutations with an accessible explanation.
    freshBrowserSession('stale-state');
    openScenario(info, 'paused', 390, 844);openTaskSettings();
    const stale = data('()=>{const title=document.querySelector("#task-title").textContent;unavailableRun("Fixture connection closed before a receipt was returned.");return {title,visible:!document.querySelector("#task-stale-state").hidden,continue:document.querySelector("#continue-run").disabled,archive:document.querySelector("#archive-task").disabled,retry:document.querySelector("#retry-stale-status").disabled,tab:document.querySelector("[data-tab=plan]").disabled,copy:document.querySelector("#task-project-actions button")?.disabled,description:document.querySelector("#archive-task").getAttribute("aria-describedby")};}');
    assert.equal(stale.title, 'Recover the interrupted build without losing its checkpoint');assert.equal(stale.visible, true);
    assert.equal(stale.continue, true);assert.equal(stale.archive, true);assert.equal(stale.retry, false);assert.equal(stale.tab, false);assert.equal(stale.copy, false);assert.match(stale.description, /stale-mutation-reason/);
    const mobile = targetAndOverflow();assert.equal(mobile.horizontalOverflow, false);assert.deepEqual(mobile.undersized, []);
    capture(captures, 'stale-state-mobile', {width: 390, height: 844});
    observations.push({name: 'stale state', mobile});

    auditBrowserSession('final');
    const manifest = {
      generated_at: new Date().toISOString(),
      fixture: 'tools/dashboard/tests/unified_browser_fixture.py',
      disposable: true,
      screenshots: captures,
      observations,
      browser_sessions: browserSessionAudits,
      source_snapshot: sourceSnapshot(),
      coverage: [
        'supported model replacement before-selection, selected-unconfirmed, confirming, real failed action, real unconfirmed action, status reconciliation, and confirmed receipt',
        'unsupported replacement disabled selector and adjacent accessible reason',
        'catalogue loading, empty, failed, and recovered states at desktop, tablet, and mobile with rendered visible status, accessible association, adjacent retry availability, preserved selections, and supported reasoning levels',
        'archive/remove dialogs: safe focus, focus trap, Escape, scrim, focus return, real confirmation, Undo, and durable Restore at desktop, tablet, and mobile',
        'stale mutation restrictions, safe retry/navigation/copy, mobile target size, and overflow',
      ],
    };
    const manifestPath = path.join(evidenceRoot, 'm3-lifecycle-manifest.json');
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
    console.log('M3 model, stale-state, and dialog lifecycle browser checks passed. Manifest: ' + path.relative(root, manifestPath));
  } finally {
    if (server && !server.killed) server.kill('SIGINT');
    try { browser('close', '--all'); } catch {}
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
