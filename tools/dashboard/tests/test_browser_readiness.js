'use strict';

const assert = require('node:assert/strict');
const vm = require('node:vm');
const {dashboardReadinessExpression, waitForReadiness} = require('./browser_readiness');

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

console.log('Browser readiness regression checks passed.');
