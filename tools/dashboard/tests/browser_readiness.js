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

// dashboard_app.js re-reads the selected task 2 s after every refresh. The
// unified fixture settles an uncertain action on the first read after the
// action's own refresh, so under a slow browser bridge that background read can
// settle it before the suite inspects the uncertain state or clicks Retry status
// (#314). Holding it leaves the action's refresh and the user's explicit status
// read as the only reads; a refresh already in flight still completes, so wait
// for `refreshPromise===null` after holding. Every other timer is untouched.
function holdBackgroundRefreshExpression() {
  return `()=>{
    if(!globalThis.__heldRefreshSchedule){
      const schedule=globalThis.setTimeout;
      globalThis.__heldRefreshSchedule=schedule;
      globalThis.setTimeout=(callback,delay,...rest)=>callback===refresh?0:schedule(callback,delay,...rest);
    }
    clearTimeout(refreshTimer);refreshTimer=null;
    return true;
  }`;
}

function releaseBackgroundRefreshExpression() {
  return `()=>{
    const schedule=globalThis.__heldRefreshSchedule;
    if(schedule){globalThis.setTimeout=schedule;delete globalThis.__heldRefreshSchedule;}
    if(refreshPromise===null)refreshTimer=setTimeout(refresh,2000);
    return true;
  }`;
}

module.exports = {
  dashboardReadinessExpression,
  isDashboardReady,
  waitForReadiness,
  holdBackgroundRefreshExpression,
  releaseBackgroundRefreshExpression,
};
