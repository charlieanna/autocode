'use strict';

// Keep this probe in the browser context: dashboard_app.js declares latestRun
// with `let`, so it is not a globalThis property while its script is loading.
// `typeof` is the only safe way to inspect that missing lexical binding.
function dashboardReadinessExpression() {
  return `()=>{
    const applicationGlobalDeclared=typeof latestRun!=="undefined";
    const run=applicationGlobalDeclared?latestRun:null;
    const documentReadyState=document.readyState;
    const applicationRootPresent=!!document.querySelector(".app-shell");
    return {
      document_ready_state:documentReadyState,
      document_ready:documentReadyState!=="loading",
      application_global_declared:applicationGlobalDeclared,
      application_root_present:applicationRootPresent,
      application_ready:applicationGlobalDeclared&&applicationRootPresent,
      status:run?.status||null,
      stage:run?.active_stage?.stage||null,
      notice:document.querySelector("#dashboard-notice")?.textContent||"",
    };
  }`;
}

function isDashboardReady(snapshot) {
  return Boolean(snapshot?.document_ready && snapshot?.application_ready);
}

function waitForReadiness({label, probe, wait, attempts = 48, onTimeout}) {
  if (!Number.isInteger(attempts) || attempts < 1) {
    throw new TypeError('attempts must be a positive integer');
  }
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    last = probe();
    if (isDashboardReady(last)) return last;
    if (attempt < attempts - 1) wait();
  }
  const error = new Error(
    label + ' dashboard readiness did not become true within ' + attempts +
    ' polls; final readiness=' + JSON.stringify(last || {snapshot_unavailable: true}),
  );
  error.code = 'DASHBOARD_READINESS_TIMEOUT';
  error.readiness = last || null;
  if (typeof onTimeout === 'function') {
    try {
      error.diagnostics = onTimeout(last || null);
      error.message += '; browser diagnostics=' + JSON.stringify(error.diagnostics);
    } catch (diagnosticError) {
      error.diagnostics_error = String(diagnosticError?.stack || diagnosticError);
      error.message += '; browser diagnostics unavailable=' + error.diagnostics_error;
    }
  }
  throw error;
}

module.exports = {dashboardReadinessExpression, isDashboardReady, waitForReadiness};
