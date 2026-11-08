'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('./dashboard_vm');
const {
  dashboardReadinessExpression,
  waitForReadiness,
  holdBackgroundRefreshExpression,
  releaseBackgroundRefreshExpression,
} = require('./browser_readiness');

function evaluateBrowserProbe(context) {
  return vm.runInNewContext('(' + dashboardReadinessExpression() + ')()', context);
}

const documentStub = {
  readyState: 'complete',
  querySelector(selector) {
    return selector === '.app-shell' ? {} : null;
  },
};

// The browser expression must not throw while dashboard_app.js has not yet
// declared its lexical latestRun binding.
const absentGlobal = evaluateBrowserProbe({document: documentStub});
assert.equal(absentGlobal.document_ready, true);
assert.equal(absentGlobal.application_global_declared, false);
assert.equal(absentGlobal.application_ready, false);
assert.equal(absentGlobal.status, null);

// A task can render before the independent registry read adds its sidebar
// controls. Keyboard checks need both reads to finish before taking a snapshot.
const inventoryProbe = dashboardReadinessExpression({requireInventory: true});
const inventoryState = {document: documentStub, latestRun: {status: 'RUNNING'}, latestData: null, refreshPromise: {}};
const probeInventory = () => vm.runInNewContext('(' + inventoryProbe + ')()', inventoryState);
assert.equal(probeInventory().application_ready, false, 'rendered task alone does not settle the navigation');
inventoryState.latestData = {runs: [{run: 'one'}]};
assert.equal(probeInventory().application_ready, false, 'an in-flight render still changes the focus order');
inventoryState.refreshPromise = null;
assert.equal(probeInventory().application_ready, true, 'settled registry and task reads permit the focus snapshot');

// Execute the lifecycle suite's actual render gate against a controlled DOM.
// The title can precede the selected read's restored banner. Waiting must
// cover both assertions, within the existing polling bound.
const lifecycleSource = fs.readFileSync(path.join(__dirname, 'test_m3_lifecycle_browser_ui.js'), 'utf8');
const renderGateSource = lifecycleSource.slice(lifecycleSource.indexOf('function openScenario('), lifecycleSource.indexOf('\nfunction capture('));
const conditionSource = lifecycleSource.slice(lifecycleSource.indexOf('function waitForCondition('), lifecycleSource.indexOf('\nfunction holdBackgroundRefresh('));
function lifecycleRenderProbe({bannerHidden = false, projectRemoved = false, runLoaded = true, settle = true} = {}) {
  let waits = 0;
  const banner = {hidden: bannerHidden};
  const run = {run:'selected-run', project_removed:projectRemoved, status:'PAUSED'};
  let context;
  const document = {
    readyState:'complete', scrollingElement:{}, querySelectorAll:()=>[],
    querySelector(selector) {
      if (selector === '#task-title') return {textContent:'Recover the interrupted build'};
      if (selector === '#task-removed-banner') return banner;
      if (selector === '.app-shell') return {};
      return null;
    },
  };
  context = vm.createContext({
    assert, URL, document, latestRun:runLoaded ? run : null,
    modelCatalogue:{loading:false, usable:true, models:['fixture-model']},
    scenarioReload:0, sessionHasPage:false, waitForDashboardReadiness(){},
    data:body=>vm.runInContext('(' + body + ')()', context),
    browser(action) {
      if (action === 'wait') {
        waits++;
        if (settle && waits === 2) { banner.hidden=true; run.project_removed=false; context.latestRun=run; }
      }
    },
  });
  vm.runInContext(conditionSource + '\n' + renderGateSource, context);
  return {open:()=>context.openScenario({scenarios:{'flow-remove-mobile':'http://fixture/task'}},'flow-remove-mobile',390,844), waits:()=>waits, banner, run};
}
for (const initial of [{bannerHidden:false}, {bannerHidden:true, projectRemoved:true}, {bannerHidden:true, runLoaded:false}]) {
  const pendingRender = lifecycleRenderProbe(initial);
  pendingRender.open();
  assert.equal(pendingRender.waits(),2,'a title alone must not authorize the restore snapshot');
  assert.equal(pendingRender.banner.hidden,true);
  assert.equal(pendingRender.run.project_removed,false);
}
const stuckRestore = lifecycleRenderProbe({settle:false});
assert.throws(()=>stuckRestore.open(), /scenario render did not become true within 12000 ms/,
  'a permanently wrong restored view remains a failure');
assert.equal(stuckRestore.waits(),47,'the existing48-probe bound remains exact');

let eventualProbeCount = 0;
let eventualWaitCount = 0;
const eventuallyReady = waitForReadiness({
  label: 'eventual initialization',
  attempts: 3,
  probe: () => {
    eventualProbeCount++;
    return eventualProbeCount === 1
      ? absentGlobal
      : {
        document_ready: true,
        application_ready: true,
        application_global_declared: true,
        application_root_present: true,
        status: 'RUNNING',
      };
  },
  wait: () => { eventualWaitCount++; },
});
assert.equal(eventualProbeCount, 2);
assert.equal(eventualWaitCount, 1);
assert.equal(eventuallyReady.status, 'RUNNING');

let neverProbeCount = 0;
let neverWaitCount = 0;
assert.throws(
  () => waitForReadiness({
    label: 'never initializes fixture',
    attempts: 3,
    probe: () => {
      neverProbeCount++;
      return {
        document_ready: true,
        application_ready: false,
        application_global_declared: false,
        application_root_present: true,
        status: null,
        notice: '',
      };
    },
    wait: () => { neverWaitCount++; },
    onTimeout: () => ({
      page_errors: 'ReferenceError: fixture application did not initialize',
      console: 'error: fixture bootstrap failed',
    }),
  }),
  error => {
    assert.equal(error.code, 'DASHBOARD_READINESS_TIMEOUT');
    assert.match(error.message, /never initializes fixture dashboard readiness did not become true within 3 polls/);
    assert.match(error.message, /"application_global_declared":false/);
    assert.match(error.message, /browser diagnostics=/);
    assert.deepEqual(error.readiness, {
      document_ready: true,
      application_ready: false,
      application_global_declared: false,
      application_root_present: true,
      status: null,
      notice: '',
    });
    assert.deepEqual(error.diagnostics, {
      page_errors: 'ReferenceError: fixture application did not initialize',
      console: 'error: fixture bootstrap failed',
    });
    return true;
  },
);
assert.equal(neverProbeCount, 3);
assert.equal(neverWaitCount, 2);

// Holding the background refresh drops only dashboard_app.js's 2 s re-read of
// the selected task, in the exact form refresh() schedules it (a detail-only
// arrow around refresh; test_console.py pins that text), and releasing it
// restores the original timer and the poll (#314). The 10 s task-list read and
// every other timer keep running while held.
const scheduled = [];
const cleared = [];
const originalSetTimeout = (callback, delay) => { scheduled.push({callback, delay}); return scheduled.length; };
const page = vm.createContext({setTimeout: originalSetTimeout, clearTimeout: id => { cleared.push(id); }});
vm.runInContext('let refreshPromise=null,refreshTimer=7,refreshListTimer=null;function refresh(){}function restore(){}', page);
const inPage = script => vm.runInContext(script, page);
const pollAfterRefresh = 'refreshTimer=setTimeout(()=>refresh(false,true),2000);refreshListTimer=setTimeout(()=>refresh(true,false),10000);setTimeout(restore,32);';
const isDetailPoll = callback => String(callback).includes('refresh(false,true)');

assert.equal(inPage('(' + holdBackgroundRefreshExpression() + ')()'), true);
assert.equal(inPage('(' + holdBackgroundRefreshExpression() + ')()'), true, 'holding twice is harmless');
assert.equal(cleared[0], 7, 'holding cancels the pending background read');
inPage(pollAfterRefresh);
assert.deepEqual(scheduled.map(entry => entry.delay), [10000, 32], 'only the wrapped detail read is dropped; the list read and other timers stay');
assert.equal(inPage('refreshTimer'), 0);
inPage('setTimeout(refresh,2000)');
assert.deepEqual(scheduled.map(entry => entry.delay), [10000, 32], 'a bare refresh callback is dropped too');

assert.equal(inPage('(' + releaseBackgroundRefreshExpression() + ')()'), true);
assert.equal(inPage('setTimeout'), originalSetTimeout, 'release restores the original timer');
assert.equal(isDetailPoll(scheduled.at(-1).callback), true, 'release re-arms the detail read as dashboard_app.js schedules it');
assert.equal(scheduled.at(-1).delay, 2000);
assert.equal(inPage('refreshTimer'), scheduled.length);
inPage(pollAfterRefresh);
assert.deepEqual(scheduled.map(entry => entry.delay), [10000, 32, 2000, 2000, 10000, 32], 'released polls are scheduled again');

// A refresh still in flight at release re-arms the poll itself when it settles.
inPage('(' + holdBackgroundRefreshExpression() + ')();refreshPromise=Promise.resolve();');
const beforeRelease = scheduled.length;
inPage('(' + releaseBackgroundRefreshExpression() + ')()');
assert.equal(scheduled.length, beforeRelease, 'release does not add a second poll beside an in-flight refresh');

console.log('Browser readiness regression checks passed.');
