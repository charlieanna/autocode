// Browser-level M3 workspace verification (AC19 drawers, AC20 keyboard,
// focus, announcements, contrast) against the disposable unified fixture.
// Driven per case by tools/dashboard/tests/test_workspace_lifecycle_ui.py.
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {dashboardReadinessExpression, waitForReadiness} = require('./browser_readiness');

const root = path.resolve(__dirname, '../../..');
const fixture = path.join(__dirname, 'unified_browser_fixture.py');
const evidenceRoot = process.env.DASHBOARD_M3_WORKSPACE_EVIDENCE_DIR
  ? path.resolve(root, process.env.DASHBOARD_M3_WORKSPACE_EVIDENCE_DIR)
  : path.join(root, '.autocode', 'evidence', 'm3-workspace', 'run-' + process.pid);
fs.mkdirSync(evidenceRoot, {recursive: true});
let session = 'dashboard-m3-workspace-' + process.pid;
let server;
let scenarioReload = 0;
let sessionHasPage = false;

function browser(...args) {
  // A screenshot or a busy bridge can leave the local daemon briefly
  // unavailable. Retrying only that documented contention (EAGAIN or a daemon
  // timeout, never an assertion or protocol failure) preserves real
  // dashboard/browser failures.
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
      const transient = /Resource temporarily unavailable.*daemon may be busy|EAGAIN/i.test(output)
        || (error.code === 'ETIMEDOUT' && args[0] !== 'screenshot');
      if (!transient || attempt === 2) throw error;
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

function waitForDashboardReadiness(label, attempts = 48) {
  return waitForReadiness({
    label,
    attempts,
    probe: () => data(dashboardReadinessExpression()),
    wait: () => browser('wait', '250'),
    onTimeout: () => ({
      page_errors: browser('errors').trim() || 'No page errors.',
      console: browser('console').trim() || 'No browser console messages.',
    }),
  });
}

function waitForCondition(expression, label, attempts = 48) {
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    last = data('()=>{' +
      'const applicationGlobalDeclared=typeof latestRun!=="undefined";' +
      'const run=applicationGlobalDeclared?latestRun:null;' +
      'return {matched:applicationGlobalDeclared&&Boolean(' + expression + '),url:location.href.slice(-80),document_ready_state:document.readyState,application_global_declared:applicationGlobalDeclared,application_root_present:!!document.querySelector(".app-shell"),status:run?.status||null,notice:document.querySelector("#dashboard-notice")?.textContent||""};' +
    '}');
    if (last.matched) return;
    if (attempt < attempts - 1) browser('wait', '250');
  }
  assert.fail(label + ' did not become true within ' + attempts * 250 + ' ms: ' + expression + '; final rendered state=' + JSON.stringify(last));
}

function fixtureInfo() {
  return new Promise((resolve, reject) => {
    let output = '', errors = '';
    const deadline = setTimeout(() => reject(Error('Timed out waiting for the disposable M3 workspace fixture.')), 15000);
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
      if (code) reject(Error('M3 workspace fixture exited with ' + code + ': ' + (errors || output)));
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
  waitForCondition('document.querySelector("#task-title")?.textContent.includes("Drawer-safe running build")', name + ' scenario render');
  data('()=>{[document.scrollingElement,...document.querySelectorAll(".page,.thread-scroll")].filter(Boolean).forEach(e=>{e.scrollTop=0;e.scrollLeft=0;});return true;}');
}

function auditBrowserSession(label) {
  if (!sessionHasPage) return;
  const errors = browser('errors').trim();
  assert.match(errors, /^(?:|\[\]|No page errors\.?|No errors\.?)$/i, label + ' browser page errors: ' + errors);
  const consoleMessages = browser('console').trim();
  assert.ok(!/\b(?:warn(?:ing)?|error)\b/i.test(consoleMessages), label + ' browser console warnings or errors: ' + consoleMessages);
  sessionHasPage = false;
}

// Relative-luminance contrast, the same computation the pinned accessibility
// gate (test_shell_a11y_ui.js) uses.
function contrast(foreground, background) {
  const channel = value => {
    const part = value.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!part) return null;
    const alpha = part[4] === undefined ? 1 : Number(part[4]);
    return part.slice(1, 4).map(Number).map(number => number / 255).map(number =>
      number <= 0.03928 ? number / 12.92 : Math.pow((number + 0.055) / 1.055, 2.4)
    ).concat(alpha);
  };
  const front = channel(String(foreground)), back = channel(String(background));
  if (!front || !back) return null;
  const blend = front.slice(0, 3).map((number, index) => number * front[3] + back[index] * (1 - front[3]));
  const luminance = channels => 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  const first = luminance(blend), second = luminance(back.slice(0, 3));
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

function renderedTextProbe(selector) {
  return data('()=>{' +
    'const target=document.querySelector(' + JSON.stringify(selector) + ');if(!target)throw Error("Missing text target: "+' + JSON.stringify(selector) + ');const transparent=value=>value==="transparent"||value==="rgba(0, 0, 0, 0)";let background="";for(let parent=target;parent;parent=parent.parentElement){const candidate=getComputedStyle(parent).backgroundColor;if(!transparent(candidate)){background=candidate;break;}}return {selector:' + JSON.stringify(selector) + ',text:target.textContent.trim(),color:getComputedStyle(target).color,background:background||getComputedStyle(document.documentElement).getPropertyValue("--surface-default").trim()};' +
  '}');
}

function assertNormalTextContrast(selector, label) {
  const probe = renderedTextProbe(selector);
  const ratio = contrast(probe.color, probe.background);
  assert.ok(ratio >= 4.5, label + ' normal text is at least 4.5:1, recorded ' + ratio.toFixed(3) + ' for ' + selector);
  return {selector, label, ratio};
}

// ---- AC19: 390 px drawers keep chat and the draft, and every drawer row and
// creation target is at least 44 px tall.
async function ac19(info) {
  openScenario(info, 'flow-m3-chat', 390, 844);
  const draft = 'half-written thought';
  browser('click', '#change-text');
  browser('type', '#change-text', draft);
  waitForCondition('document.querySelector("#change-text")?.value===' + JSON.stringify(draft), 'the composer holds the unsent draft');
  const saved = data('()=>({draft:changeDrafts.get(latestRun.run)||"",stored:localStorage.getItem("task-draft:"+latestRun.run)||""})');
  assert.equal(saved.draft, draft, 'the draft is saved per conversation before the drawers open');
  const transcriptBefore = data('()=>({text:document.querySelector("#conversation").textContent,messages:document.querySelectorAll("#conversation [data-message-key],#conversation .chat-message").length})');
  assert.ok(transcriptBefore.messages >= 3, 'the saved transcript rendered before the drawer cycles');

  // Project drawer: every conversation row and creation target is >=44 px.
  browser('click', '#nav-toggle');
  waitForCondition('document.querySelector(".app-shell").classList.contains("nav-open")', 'the project drawer opened');
  const projectDrawer = data('()=>{' +
    'const box=e=>{const r=e.getBoundingClientRect();return {name:e.id||e.getAttribute("aria-label")||e.textContent.trim().slice(0,40),width:Math.round(r.width),height:Math.round(r.height)}};' +
    'const rows=[...document.querySelectorAll("#primary-navigation .conversation-nav-row")];' +
    'const targets=[...document.querySelectorAll("#primary-navigation .new-task-button,#primary-navigation .project-group-toggle,#primary-navigation .project-group-new,#primary-navigation .conversation-nav-row,#primary-navigation #conversation-search,#drawer-close")];' +
    'return {rows:rows.map(box),targets:targets.map(box),undersized:targets.map(box).filter(t=>t.height<44||t.width<44)};' +
  '}');
  assert.ok(projectDrawer.rows.length >= 1, 'the project drawer lists its saved conversation rows');
  assert.deepEqual(projectDrawer.undersized, [], 'every conversation row and creation target in the project drawer is at least 44 px: ' + JSON.stringify(projectDrawer.targets));
  browser('click', '#drawer-close');
  waitForCondition('!document.querySelector(".app-shell").classList.contains("nav-open")', 'the project drawer closed');

  // Details drawer: same transcript, same draft, 44 px targets, real close.
  const transcriptAfterProjects = data('()=>({text:document.querySelector("#conversation").textContent,draft:document.querySelector("#change-text").value})');
  assert.equal(transcriptAfterProjects.text, transcriptBefore.text, 'closing the project drawer leaves the transcript intact');
  assert.equal(transcriptAfterProjects.draft, draft, 'closing the project drawer leaves the draft intact');

  browser('click', '#details-drawer-toggle');
  waitForCondition('document.querySelector(".app-shell").classList.contains("details-open")', 'the details drawer opened');
  const detailsDrawer = data('()=>{' +
    'const box=e=>{const r=e.getBoundingClientRect();return {name:e.id||e.getAttribute("aria-label")||e.textContent.trim().slice(0,40),left:Math.round(r.left),top:Math.round(r.top),width:Math.round(r.width),height:Math.round(r.height)}};' +
    'const pane=document.querySelector("#context-pane");const paneBox=box(pane);' +
    'const targets=[...document.querySelectorAll("#details-drawer-close,#context-pane .detail-tabs [role=tab]")];' +
    'return {pane:paneBox,coversViewport:paneBox.left<=0&&paneBox.left+paneBox.width>=innerWidth&&paneBox.top>=0&&paneBox.top+paneBox.height>=innerHeight-1,targets:targets.map(box),undersized:targets.map(box).filter(t=>t.height<44||t.width<44),transcript:document.querySelector("#conversation").textContent,draft:document.querySelector("#change-text").value,chatInert:document.querySelector(".conversation-column").inert===true};' +
  '}');
  assert.equal(detailsDrawer.coversViewport, true, 'the details drawer overlays the narrow viewport: ' + JSON.stringify(detailsDrawer.pane));
  assert.deepEqual(detailsDrawer.undersized, [], 'every control in the details drawer is at least 44 px: ' + JSON.stringify(detailsDrawer.targets));
  assert.equal(detailsDrawer.transcript, transcriptBefore.text, 'the transcript is untouched while the details drawer is open');
  assert.equal(detailsDrawer.draft, draft, 'the draft is untouched while the details drawer is open');
  browser('click', '#details-drawer-close');
  waitForCondition('!document.querySelector(".app-shell").classList.contains("details-open")', 'the details drawer closed');
  const afterDetails = data('()=>({text:document.querySelector("#conversation").textContent,draft:document.querySelector("#change-text").value,focus:document.activeElement.id})');
  assert.equal(afterDetails.text, transcriptBefore.text, 'closing the details drawer leaves the transcript intact');
  assert.equal(afterDetails.draft, draft, 'closing the details drawer leaves the draft intact');
  assert.equal(afterDetails.focus, 'details-drawer-toggle', 'closing the details drawer returns focus to its control');

  // Escape closes the details drawer as well.
  browser('click', '#details-drawer-toggle');
  waitForCondition('document.querySelector(".app-shell").classList.contains("details-open")', 'the details drawer reopened');
  browser('press', 'Escape');
  waitForCondition('!document.querySelector(".app-shell").classList.contains("details-open")', 'Escape closes the details drawer');

  const manifest = {
    generated_at: new Date().toISOString(),
    case: 'ac19',
    viewport: {width: 390, height: 844},
    transcript_before: transcriptBefore,
    project_drawer: projectDrawer,
    details_drawer: detailsDrawer,
    after: afterDetails,
  };
  const manifestPath = path.join(evidenceRoot, 'ac19-manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
  auditBrowserSession('ac19-final');
  console.log('AC19 drawer checks passed. Manifest: ' + path.relative(root, manifestPath));
}

// ---- AC20: keyboard order, visible focus, live-region announcements, and
// contrast at desktop and mobile widths.
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),summary:not([disabled]),[tabindex="0"],[role=tab]:not([disabled])';

function expectedFocusOrder() {
  return data('()=>{' +
    // Closed <details> content is suppressed by content-visibility here, so it
    // reports styled boxes while remaining outside the real focus order.
    'const detailsHidden=e=>{for(let node=e.parentElement;node;node=node.parentElement){if(node.tagName==="DETAILS"&&!node.open){let child=e,parent=e.parentElement;while(parent&&parent!==node){child=parent;parent=parent.parentElement;}if(child.tagName!=="SUMMARY")return true;}}return false;};' +
    'const visible=e=>{if(e.hidden)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0&&e.tabIndex>=0&&e.getAttribute("tabindex")!=="-1"&&!detailsHidden(e)&&!e.closest("[inert]")&&!e.closest("[hidden]")&&!e.closest("dialog:not([open])")};' +
    'const name=e=>e.id||e.dataset.focusKey||e.getAttribute("aria-label")||e.textContent.trim().slice(0,32)||e.tagName;' +
    'return [...document.querySelectorAll(' + JSON.stringify(FOCUSABLE) + ')].filter(visible).map(e=>({name:name(e),tag:e.tagName}));' +
  '}');
}

// The open details drawer must expose only its actually rendered, enabled
// controls: nothing under a hidden pane section or a closed disclosure.
function expectedDrawerFocusOrder() {
  return data('()=>{' +
    'const detailsHidden=e=>{for(let node=e.parentElement;node;node=node.parentElement){if(node.tagName==="DETAILS"&&!node.open){let child=e,parent=e.parentElement;while(parent&&parent!==node){child=parent;parent=parent.parentElement;}if(child.tagName!=="SUMMARY")return true;}}return false;};' +
    'const visible=e=>{if(e.hidden||e.disabled)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0&&e.tabIndex>=0&&e.getAttribute("tabindex")!=="-1"&&!detailsHidden(e)&&!e.closest("[inert]")&&!e.closest("[hidden]")&&!e.closest("dialog:not([open])")};' +
    'const name=e=>e.id||e.dataset.focusKey||e.getAttribute("aria-label")||e.textContent.trim().slice(0,32)||e.tagName;' +
    'return [...document.querySelector("#context-pane").querySelectorAll(' + JSON.stringify(FOCUSABLE) + ')].filter(visible).map(e=>({name:name(e),tag:e.tagName}));' +
  '}');
}

// One real Tab or Shift+Tab press and the resulting focus record.
function pressAndRead(key) {
  browser('press', key);
  const entry = activeFocus();
  assert.ok(entry, 'focus stays inside the open details drawer after ' + key);
  return entry;
}

// Walk the open 390px details drawer forward and backward with wrap. Focus
// starts on Close (the drawer's first control); reverse Tab from it must wrap
// to the last rendered control rather than sticking on Close.
function drawerTraversal() {
  const expected = expectedDrawerFocusOrder();
  assert.ok(expected.length >= 3, 'the open details drawer exposes its rendered controls: ' + JSON.stringify(expected));
  const firstName = expected[0].name + ':' + expected[0].tag;
  const start = activeFocus();
  assert.equal(start.name + ':' + start.tag, firstName,
    'opening the drawer focuses its first control (Close); recorded ' + JSON.stringify(start));
  const forward = [], reverse = [];
  for (let step = 0; step < expected.length; step++) {
    const entry = pressAndRead('Tab');
    forward.push(entry);
  }
  const forwardNames = forward.map(entry => entry.name + ':' + entry.tag);
  const expectedForward = [...expected.slice(1), expected[0]].map(entry => entry.name + ':' + entry.tag);
  assert.deepEqual(forwardNames, expectedForward,
    'forward Tab visits every rendered drawer control in order and wraps last-to-first; expected ' + JSON.stringify(expectedForward) + ' visited ' + JSON.stringify(forwardNames));
  const unwrappedForward = forward.filter(entry => !entry.focusVisible).map(entry => entry.name);
  assert.deepEqual([], unwrappedForward, 'every drawer control reached by forward Tab shows its focus-visible state: ' + JSON.stringify(unwrappedForward));
  const weakForward = forward.filter(entry => entry.focusVisible && (parseFloat(entry.outlineWidth) || 0) < 2 && !/2px|3px|4px/.test(entry.boxShadow)).map(entry => entry.name);
  assert.deepEqual([], weakForward, 'every forward-focused drawer control paints a visible ring of at least 2 px: ' + JSON.stringify(weakForward));
  for (let step = 0; step < expected.length; step++) {
    const entry = pressAndRead('Shift+Tab');
    reverse.push(entry);
  }
  const reverseNames = reverse.map(entry => entry.name + ':' + entry.tag);
  const expectedReverse = [expected.at(-1), ...expected.slice(0, -1).reverse(), expected.at(-1)]
    .slice(0, expected.length).map(entry => entry.name + ':' + entry.tag);
  assert.deepEqual(reverseNames, expectedReverse,
    'reverse Shift+Tab wraps from Close to the last rendered drawer control and walks backward in order; expected ' + JSON.stringify(expectedReverse) + ' visited ' + JSON.stringify(reverseNames));
  const unwrappedReverse = reverse.filter(entry => !entry.focusVisible).map(entry => entry.name);
  assert.deepEqual([], unwrappedReverse, 'every drawer control reached by reverse Shift+Tab shows its focus-visible state: ' + JSON.stringify(unwrappedReverse));
  return {expected, forward: forward.map(entry => entry.name), reverse: reverse.map(entry => entry.name)};
}

function activeFocus() {
  return data('()=>{const e=document.activeElement;if(!e||e===document.body)return null;' +
    'const style=e.matches(":focus-visible")?getComputedStyle(e):null;' +
    'return {name:e.id||e.dataset.focusKey||e.getAttribute("aria-label")||e.textContent.trim().slice(0,32)||e.tagName,tag:e.tagName,focusVisible:e.matches(":focus-visible"),outlineWidth:style?style.outlineWidth:"",outlineColor:style?style.outlineColor:"",boxShadow:style?style.boxShadow:""};}');
}

// The open project drawer must expose only its actually rendered, enabled
// controls: rows inside a collapsed project group stay out of the walk.
function expectedNavigationFocusOrder() {
  return data('()=>{' +
    'const detailsHidden=e=>{for(let node=e.parentElement;node;node=node.parentElement){if(node.tagName==="DETAILS"&&!node.open){let child=e,parent=e.parentElement;while(parent&&parent!==node){child=parent;parent=parent.parentElement;}if(child.tagName!=="SUMMARY")return true;}}return false;};' +
    'const visible=e=>{if(e.hidden||e.disabled)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0&&e.tabIndex>=0&&e.getAttribute("tabindex")!=="-1"&&!detailsHidden(e)&&!e.closest("[inert]")&&!e.closest("[hidden]")&&!e.closest("dialog:not([open])")};' +
    'const name=e=>e.id||e.dataset.focusKey||e.getAttribute("aria-label")||e.textContent.trim().slice(0,32)||e.tagName;' +
    'return [...document.querySelector("#primary-navigation").querySelectorAll(' + JSON.stringify(FOCUSABLE) + ')].filter(visible).map(e=>({name:name(e),tag:e.tagName}));' +
  '}');
}

// Walk the open 390px project drawer forward and backward with wrap, then
// prove a keyboard-collapsed group drops its rows from the wrap boundary:
// reverse Tab from the first control must land on the last rendered control.
function navigationDrawerTraversal() {
  const expected = expectedNavigationFocusOrder();
  assert.ok(expected.length >= 4, 'the open project drawer exposes its rendered controls (search, groups, rows, creation targets): ' + JSON.stringify(expected));
  waitForCondition('document.activeElement&&document.activeElement.closest("#primary-navigation")!==null', 'opening the drawer settles focus on its Close control');
  const start = activeFocus();
  assert.equal(start.name, 'drawer-close',
    'opening the drawer focuses its Close control first; recorded ' + JSON.stringify(start));
  const forward = [];
  for (let step = 0; step < expected.length; step++) {
    browser('press', 'Tab');
    const entry = activeFocus();
    assert.ok(entry, 'focus stays inside the open project drawer after Tab');
    forward.push(entry);
  }
  const forwardNames = forward.map(entry => entry.name + ':' + entry.tag);
  const expectedForward = [...expected.slice(1), expected[0]].map(entry => entry.name + ':' + entry.tag);
  assert.deepEqual(forwardNames, expectedForward,
    'forward Tab visits every rendered project-drawer control in order and wraps last-to-first; expected ' + JSON.stringify(expectedForward) + ' visited ' + JSON.stringify(forwardNames));
  assert.deepEqual([], forward.filter(entry => !entry.focusVisible).map(entry => entry.name),
    'every project-drawer control reached by forward Tab shows its focus-visible state');
  assert.deepEqual([], forward.filter(entry => entry.focusVisible && (parseFloat(entry.outlineWidth) || 0) < 2 && !/2px|3px|4px/.test(entry.boxShadow)).map(entry => entry.name),
    'every forward-focused project-drawer control paints a visible ring of at least 2 px');
  const reverse = [];
  for (let step = 0; step < expected.length; step++) {
    browser('press', 'Shift+Tab');
    const entry = activeFocus();
    assert.ok(entry, 'focus stays inside the open project drawer after Shift+Tab');
    reverse.push(entry);
  }
  const reverseNames = reverse.map(entry => entry.name + ':' + entry.tag);
  const expectedReverse = [expected.at(-1), ...expected.slice(0, -1).reverse(), expected.at(-1)]
    .slice(0, expected.length).map(entry => entry.name + ':' + entry.tag);
  assert.deepEqual(reverseNames, expectedReverse,
    'reverse Shift+Tab wraps from Close to the last rendered project-drawer control and walks backward in order; expected ' + JSON.stringify(expectedReverse) + ' visited ' + JSON.stringify(reverseNames));
  assert.deepEqual([], reverse.filter(entry => !entry.focusVisible).map(entry => entry.name),
    'every project-drawer control reached by reverse Shift+Tab shows its focus-visible state');
  // A keyboard-collapsed group removes its rows from the rendered order, so
  // the wrap boundary must move to the new last rendered control.
  browser('focus', '#primary-navigation .project-group-toggle');
  browser('press', 'Enter');
  browser('wait', '120');
  const collapsed = expectedNavigationFocusOrder();
  assert.ok(collapsed.length < expected.length,
    'collapsing the project group by keyboard removes its rows from the rendered focus order');
  data('()=>{document.querySelector("#drawer-close").focus();return document.activeElement.id;}');
  browser('press', 'Shift+Tab');
  const wrappedPastCollapsed = activeFocus();
  const collapsedLast = collapsed.at(-1);
  assert.equal(wrappedPastCollapsed.name + ':' + wrappedPastCollapsed.tag, collapsedLast.name + ':' + collapsedLast.tag,
    'reverse Tab from Close wraps to the last rendered control once the group is collapsed; expected ' + JSON.stringify(collapsedLast) + ' recorded ' + JSON.stringify(wrappedPastCollapsed));
  browser('focus', '#primary-navigation .project-group-toggle');
  browser('press', 'Enter');
  browser('wait', '120');
  const restored = expectedNavigationFocusOrder();
  assert.deepEqual(restored.map(entry => entry.name + ':' + entry.tag), expected.map(entry => entry.name + ':' + entry.tag),
    'expanding the group restores the full rendered focus order');
  return {expected: expected.map(entry => entry.name), forward: forward.map(entry => entry.name), reverse: reverse.map(entry => entry.name), collapsed: collapsed.map(entry => entry.name)};
}

function tabWalk(label, maxSteps) {
  data('()=>{document.activeElement&&document.activeElement.blur&&document.activeElement.blur();document.body.focus();return document.activeElement.tagName;}');
  const visited = [], indicators = [];
  for (let step = 0; step < maxSteps; step++) {
    browser('press', 'Tab');
    const focused = activeFocus();
    if (!focused) continue;
    if (visited.length && visited[0].name === focused.name && visited[0].tag === focused.tag) break;
    visited.push(focused);
    if (focused.focusVisible) {
      const width = parseFloat(focused.outlineWidth) || 0;
      const shadow = /2px|3px|4px/.test(focused.boxShadow) ? 2 : 0;
      indicators.push({name: focused.name, outline: width, shadow});
    }
  }
  return {label, visited, indicators};
}

async function ac20(info) {
  const measurements = [];
  for (const viewport of [{name: 'desktop', width: 1440, height: 1024}, {name: 'mobile', width: 390, height: 844}]) {
    openScenario(info, 'flow-m3-chat', viewport.width, viewport.height);
    // The chat's model, Pause, Stop and Send targets stay together on a compact
    // row. This behavioral layout guard does not substitute for image review.
    const composerActions = data('()=>["#composer-models>summary","#pause-run","#stop-run","#send-change"].map(selector=>{const e=document.querySelector(selector),r=e.getBoundingClientRect();return {selector,top:r.top,left:r.left,right:r.right,width:r.width,height:r.height};})');
    for (const action of composerActions) {
      assert.ok(action.width >= 44 && action.height >= 44, viewport.name + ' composer action retains a 44px target: ' + JSON.stringify(action));
      assert.ok(Math.abs(action.top - composerActions[0].top) <= 1, viewport.name + ' composer actions share one compact row: ' + JSON.stringify(composerActions));
    }
    measurements.push({viewport: viewport.name, composer_actions: composerActions});
    // Keyboard order: sequential Tab reaches every visible control in DOM order.
    // The closed mobile page carries the approved 423-306 control set: the
    // three-element nav row plus the conversation's own controls. The theme
    // control lives in the project sheet, whose full keyboard order the drawer
    // walk below covers, so the mobile floor is one lower than desktop's.
    const expected = expectedFocusOrder();
    const controlFloor = viewport.width <= 759 ? 7 : 8;
    assert.ok(expected.length >= controlFloor, viewport.name + ' exposes a meaningful control set: ' + JSON.stringify(expected));
    const walk = tabWalk(viewport.name, expected.length + 6);
    const visitedNames = walk.visited.map(entry => entry.name + ':' + entry.tag);
    const expectedNames = expected.map(entry => entry.name + ':' + entry.tag);
    assert.deepEqual(visitedNames, expectedNames,
      viewport.name + ' keyboard navigation reaches every control in order; expected ' + JSON.stringify(expectedNames) + ' visited ' + JSON.stringify(visitedNames));
    // Visible focus indicator on every keyboard-focused control.
    const unfocused = walk.visited.filter(entry => !entry.focusVisible).map(entry => entry.name);
    assert.deepEqual([], unfocused, viewport.name + ' every control reached by keyboard shows its focus-visible state: ' + JSON.stringify(unfocused));
    const weak = walk.indicators.filter(entry => entry.outline < 2 && entry.shadow < 2).map(entry => entry.name);
    assert.deepEqual([], weak, viewport.name + ' every keyboard-focused control paints a visible focus ring of at least 2 px: ' + JSON.stringify(weak));
    measurements.push({viewport: viewport.name, focusables: expected, walk: walk.visited, indicators: walk.indicators});

    // Contrast on the workspace's own text surfaces at this width. The pane
    // surfaces sit behind the details drawer on mobile, so open it first.
    const narrow = viewport.width <= 759;
    if (narrow) {
      browser('click', '#details-drawer-toggle');
      waitForCondition('document.querySelector(".app-shell").classList.contains("details-open")', viewport.name + ' details drawer opened for pane probing');
    }
    const contrasts = [
      assertNormalTextContrast('#task-title', viewport.name + ' task title'),
      assertNormalTextContrast('#task-subtitle', viewport.name + ' current step'),
      assertNormalTextContrast_safe(viewport.name),
      assertNormalTextContrast('.detail-tabs [data-tab="now"]', viewport.name + ' selected pane switch'),
      assertNormalTextContrast(narrow ? '#now .work-summary h2' : '#projects .project-group-name', viewport.name + ' secondary surface text'),
    ];
    measurements.push({viewport: viewport.name + ' contrast', contrasts});
    if (!narrow) {
      const legacySelector = '#primary-navigation div.conversation-nav-row .conversation-nav-title';
      assert.ok(data('()=>!!document.querySelector(' + JSON.stringify(legacySelector) + ')'),
        'desktop fixture contains a preserved legacy conversation row');
      const legacyContrasts = [];
      for (const theme of ['light', 'dark']) {
        data('()=>{applyTheme(' + JSON.stringify(theme) + ');return true;}');
        legacyContrasts.push({theme, contrast: assertNormalTextContrast(legacySelector,
          'desktop preserved conversation title (' + theme + ')')});
      }
      data('()=>{applyTheme("light");return true;}');
      measurements.push({viewport: 'desktop preserved conversation contrast', contrasts: legacyContrasts});
    }
    let drawerWalkRecorded = null;
    if (narrow) {
      // The drawer is already open with pane surfaces probed: walk its whole
      // focus order forward and backward with wrap, then close and confirm
      // the focus hand-back. Re-open first so focus starts on Close.
      browser('click', '#details-drawer-close');
      waitForCondition('!document.querySelector(".app-shell").classList.contains("details-open")', viewport.name + ' details drawer closed before traversal');
      browser('click', '#details-drawer-toggle');
      waitForCondition('document.querySelector(".app-shell").classList.contains("details-open")', viewport.name + ' details drawer reopened for keyboard traversal');
      browser('wait', '120');
      drawerWalkRecorded = drawerTraversal();
      browser('press', 'Escape');
      waitForCondition('!document.querySelector(".app-shell").classList.contains("details-open")', viewport.name + ' Escape closes the traversed details drawer');
      const returned = activeFocus();
      assert.equal(returned && returned.name, 'details-drawer-toggle',
        'Escape returns focus to the details drawer control; recorded ' + JSON.stringify(returned));
      measurements.push({viewport: viewport.name + ' drawer keyboard order', drawer: drawerWalkRecorded});

      // The open mobile project drawer travels by keyboard too: open it from
      // the topbar control with Enter, walk every rendered control forward
      // and backward with wrap (including a keyboard-collapsed group), then
      // verify the announced expanded state, both close paths, focus return,
      // and the sheet's text contrast in light and dark themes. A freshly
      // loaded scenario keeps this long case off a single aged browser page.
      openScenario(info, 'flow-m3-chat', viewport.width, viewport.height);
      browser('focus', '#nav-toggle');
      browser('press', 'Enter');
      waitForCondition('document.querySelector(".app-shell").classList.contains("nav-open")', viewport.name + ' project drawer opened by keyboard');
      assert.equal(data('()=>document.querySelector("#nav-toggle").getAttribute("aria-expanded")'), 'true',
        viewport.name + ' opening the project drawer announces the expanded state');
      browser('wait', '120');
      const projectWalk = navigationDrawerTraversal();
      const sheetContrasts = [];
      for (const theme of ['light', 'dark']) {
        data('()=>{applyTheme(' + JSON.stringify(theme) + ');return true;}');
        sheetContrasts.push({theme, contrasts: [
          assertNormalTextContrast('#drawer-title', viewport.name + ' project sheet title (' + theme + ')'),
          assertNormalTextContrast('#primary-navigation .project-group-name', viewport.name + ' project group name (' + theme + ')'),
          assertNormalTextContrast('#primary-navigation .conversation-nav-row .conversation-nav-title', viewport.name + ' conversation row (' + theme + ')'),
        ]});
      }
      data('()=>{applyTheme("light");return true;}');
      browser('press', 'Escape');
      waitForCondition('!document.querySelector(".app-shell").classList.contains("nav-open")', viewport.name + ' Escape closes the traversed project drawer');
      // The focus hand-back follows the drawer's inert/display transition and
      // can land a tick after the class change: settle before reading it.
      waitForCondition('document.activeElement&&document.activeElement.id==="nav-toggle"', viewport.name + ' Escape returns focus to the Projects control', 8);
      const navEscapeFocus = activeFocus();
      assert.equal(navEscapeFocus && navEscapeFocus.name, 'nav-toggle',
        viewport.name + ' Escape returns focus to the Projects control; recorded ' + JSON.stringify(navEscapeFocus));
      browser('focus', '#nav-toggle');
      browser('press', 'Enter');
      waitForCondition('document.querySelector(".app-shell").classList.contains("nav-open")', viewport.name + ' project drawer reopened by keyboard');
      waitForCondition('document.activeElement&&document.activeElement.id==="drawer-close"', viewport.name + ' reopening the drawer focuses its Close control', 8);
      const reopenFocus = activeFocus();
      assert.equal(reopenFocus && reopenFocus.name, 'drawer-close',
        viewport.name + ' reopening the drawer focuses its Close control; recorded ' + JSON.stringify(reopenFocus));
      browser('click', '#drawer-close');
      waitForCondition('!document.querySelector(".app-shell").classList.contains("nav-open")', viewport.name + ' the Close control dismisses the project drawer');
      waitForCondition('document.activeElement&&document.activeElement.id==="nav-toggle"', viewport.name + ' the Close control returns focus to the Projects control', 8);
      const navClosedFocus = activeFocus();
      assert.equal(navClosedFocus && navClosedFocus.name, 'nav-toggle',
        viewport.name + ' the Close control returns focus to the Projects control; recorded ' + JSON.stringify(navClosedFocus));
      measurements.push({viewport: viewport.name + ' project drawer keyboard order', drawer: projectWalk, sheet_contrasts: sheetContrasts});
    }
  }

  // Live regions: attribute-backed hosts, then a real status change that the
  // conversation live region announces.
  const regions = data('()=>({notice:document.querySelector("#dashboard-notice").getAttribute("role")||"",noticeLive:document.querySelector("#dashboard-notice").getAttribute("aria-live")||"",changeHistory:document.querySelector("#change-history").getAttribute("aria-live")||"",liveError:document.querySelector("#live-error").getAttribute("role")||""})');
  assert.equal(regions.notice, 'alert', 'page-level errors are announced by an alert region');
  assert.equal(regions.changeHistory, 'polite', 'run control status changes are announced politely');
  assert.equal(regions.liveError, 'alert', 'composer errors are announced by an alert region');
  browser('click', '#pause-run');
  browser('wait', '400');
  waitForCondition('(document.querySelector("#change-history")?.textContent||"").includes("Pause")', 'the pause status change is announced in the live region');
  const announcement = data('()=>document.querySelector("#change-history").textContent');

  auditBrowserSession('ac20-final');
  const manifest = {
    generated_at: new Date().toISOString(),
    case: 'ac20',
    measurements,
    live_regions: regions,
    announcement,
  };
  const manifestPath = path.join(evidenceRoot, 'ac20-manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
  console.log('AC20 keyboard, focus, announcement, and contrast checks passed. Manifest: ' + path.relative(root, manifestPath));
}

function assertNormalTextContrast_safe(label) {
  const probe = renderedTextProbe('#conversation .chat-message p, #conversation .chat-message');
  const ratio = contrast(probe.color, probe.background);
  assert.ok(ratio >= 4.5, label + ' chat message text is at least 4.5:1, recorded ' + ratio.toFixed(3));
  return {selector: probe.selector, label, ratio};
}

const CASES = {ac19, ac20};
const name = process.argv[2];
if (!name || !CASES[name]) {
  console.error('usage: node workspace_lifecycle_browser.js <' + Object.keys(CASES).join('|') + '>');
  process.exit(2);
}
(async () => {
  server = spawn('python3', [fixture], {cwd: root, env: {...process.env, AUTOCODE_FIXTURE_ROOT: path.join(evidenceRoot, 'fixture-state')}, stdio: ['ignore', 'pipe', 'pipe']});
  const info = await fixtureInfo();
  try {
    await CASES[name](info);
  } finally {
    if (server && !server.killed) server.kill('SIGINT');
    try { browser('close'); } catch {}
  }
})().catch(error => { console.error(error.stack || error); process.exit(1); });
