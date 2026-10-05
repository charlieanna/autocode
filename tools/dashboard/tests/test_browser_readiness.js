'use strict';

const assert = require('node:assert/strict');
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

// Holding the background refresh drops only dashboard_app.js's 2 s re-read,
// and releasing it restores the original timer and the poll (#314).
const scheduled = [];
const cleared = [];
const originalSetTimeout = (callback, delay) => { scheduled.push({callback, delay}); return scheduled.length; };
const page = vm.createContext({setTimeout: originalSetTimeout, clearTimeout: id => { cleared.push(id); }});
vm.runInContext('let refreshPromise=null,refreshTimer=7;function refresh(){}function restore(){}', page);
const inPage = script => vm.runInContext(script, page);
const pollAfterRefresh = 'refreshTimer=setTimeout(refresh,2000);setTimeout(restore,32);';

assert.equal(inPage('(' + holdBackgroundRefreshExpression() + ')()'), true);
assert.equal(inPage('(' + holdBackgroundRefreshExpression() + ')()'), true, 'holding twice is harmless');
assert.equal(cleared[0], 7, 'holding cancels the pending background read');
inPage(pollAfterRefresh);
assert.deepEqual(scheduled.map(entry => entry.delay), [32], 'only the background read is dropped');
assert.equal(inPage('refreshTimer'), 0);

assert.equal(inPage('(' + releaseBackgroundRefreshExpression() + ')()'), true);
assert.equal(inPage('setTimeout'), originalSetTimeout, 'release restores the original timer');
assert.equal(inPage('refresh'), scheduled.at(-1).callback, 'release re-arms the background read');
assert.equal(scheduled.at(-1).delay, 2000);
assert.equal(inPage('refreshTimer'), scheduled.length);
inPage(pollAfterRefresh);
assert.deepEqual(scheduled.map(entry => entry.delay), [32, 2000, 2000, 32], 'released polls are scheduled again');

// A refresh still in flight at release re-arms the poll itself when it settles.
inPage('(' + holdBackgroundRefreshExpression() + ')();refreshPromise=Promise.resolve();');
const beforeRelease = scheduled.length;
inPage('(' + releaseBackgroundRefreshExpression() + ')()');
assert.equal(scheduled.length, beforeRelease, 'release does not add a second poll beside an in-flight refresh');

console.log('Browser readiness regression checks passed.');
